#!/usr/bin/env python3
"""
Lafayette, IN Dispatch Monitor — OpenMHz Data Pipeline
Polls the Tippecanoe County feed, transcribes audio with Whisper,
extracts address/call type, geocodes, and stores to SQLite.

Setup:
    pip install openai-whisper requests geopy schedule
    # Install Ollama: https://ollama.com  then:
    # ollama pull llama3.2   (fast, good enough)
    # ollama pull mistral    (slower, better extraction)

Usage:
    python poller.py            # run continuously
    python poller.py --backfill # pull last 100 calls, then watch live
"""

import sqlite3
import json
import os
import re
import sys
import time
import hashlib
import logging
import tempfile
import argparse
import schedule
import requests
import whisper
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
from datetime import datetime, timezone
from pathlib import Path

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
OPENMHZ_SYSTEM   = "tippco"          # Tippecanoe County shortName
OPENMHZ_BASE     = "https://api.openmhz.com"
POLL_INTERVAL_S  = 30                # seconds between API polls
DB_PATH          = os.environ.get("DB_PATH", "dispatch.db")
WHISPER_MODEL    = "base.en"         # tiny.en / base.en / small.en / medium.en

# ── Ollama config ──────────────────────────────────────────────────────────
OLLAMA_URL       = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL     = "llama3.2"        # or: mistral, llama3.1, gemma2, phi3
#   Model recommendations for this task on Apple Silicon:
#     llama3.2   (2GB)  — fast, good JSON, great for short transcripts
#     mistral    (4GB)  — slightly better address parsing, still quick
#     llama3.1   (5GB)  — best accuracy if you have 16GB+ unified RAM
#     phi3-mini  (2GB)  — very fast, decent but misses tricky addresses
# ──────────────────────────────────────────────────────────────────────────

CITY_CONTEXT     = "Lafayette, Indiana"
GEOCODE_SUFFIX   = ", Lafayette, IN, USA"

# Talkgroups to track (fire/EMS/police dispatch channels)
# Leave empty list [] to capture ALL talkgroups
TALKGROUP_FILTER = []  # e.g. ["LFD DISP", "LPD DISP", "TIPCO EMS"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("poller.log")],
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS calls (
            id            TEXT PRIMARY KEY,
            timestamp     TEXT NOT NULL,
            talkgroup     TEXT,
            talkgroup_tag TEXT,
            raw_transcript TEXT,
            address       TEXT,
            call_type     TEXT,
            units         TEXT,
            notes         TEXT,
            lat           REAL,
            lon           REAL,
            confidence    TEXT,
            audio_url     TEXT,
            created_at    TEXT DEFAULT (datetime('now'))
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON calls(timestamp)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_call_type ON calls(call_type)")
    con.commit()
    con.close()
    log.info(f"Database ready: {DB_PATH}")

def call_exists(call_id: str) -> bool:
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT 1 FROM calls WHERE id=?", (call_id,)).fetchone()
    con.close()
    return row is not None

def insert_call(data: dict):
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        INSERT OR IGNORE INTO calls
          (id, timestamp, talkgroup, talkgroup_tag, raw_transcript,
           address, call_type, units, notes, lat, lon, confidence, audio_url)
        VALUES (:id,:timestamp,:talkgroup,:talkgroup_tag,:raw_transcript,
                :address,:call_type,:units,:notes,:lat,:lon,:confidence,:audio_url)
    """, data)
    con.commit()
    con.close()

# ─────────────────────────────────────────────
# OPENMHZ API
# ─────────────────────────────────────────────
def fetch_recent_calls(limit: int = 20, filter_time: int = None) -> list:
    """Fetch most recent calls from Tippecanoe County feed."""
    params = {"limit": limit}
    if filter_time:
        params["filter-time"] = filter_time
    if TALKGROUP_FILTER:
        params["filter-talkgroup"] = ",".join(TALKGROUP_FILTER)

    try:
        url = f"{OPENMHZ_BASE}/{OPENMHZ_SYSTEM}/calls"
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("calls", [])
    except requests.RequestException as e:
        log.error(f"OpenMHz API error: {e}")
        return []

def download_audio(url: str) -> str | None:
    """Download call audio to a temp file. Returns path or None."""
    try:
        resp = requests.get(url, timeout=30, stream=True)
        resp.raise_for_status()
        suffix = ".mp3" if "mp3" in url else ".wav"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        for chunk in resp.iter_content(chunk_size=8192):
            tmp.write(chunk)
        tmp.close()
        return tmp.name
    except Exception as e:
        log.error(f"Audio download failed ({url}): {e}")
        return None

# ─────────────────────────────────────────────
# TRANSCRIPTION (Whisper)
# ─────────────────────────────────────────────
_whisper_model = None
def get_whisper():
    global _whisper_model
    if _whisper_model is None:
        log.info(f"Loading Whisper model: {WHISPER_MODEL} (first run, may take a moment)")
        _whisper_model = whisper.load_model(WHISPER_MODEL)
    return _whisper_model

def transcribe(audio_path: str) -> str:
    """Transcribe a radio call audio file. Returns transcript text."""
    try:
        model = get_whisper()
        result = model.transcribe(
            audio_path,
            language="en",
            fp16=False,          # set True if you have a GPU
            initial_prompt=(
                "Lafayette Indiana fire EMS dispatch tone-out. "
                "Engine 1 Engine 3 Medic 2 Ladder 1 respond to structure fire. "
                "Addresses: 1200 South 18th Street, intersection of Main and 9th, "
                "Sagamore Parkway, Teal Road, Greenbush Street, McCarty Lane, "
                "Union Hospital, Franciscan Health, Purdue University campus."
            )
        )
        return result["text"].strip()
    except Exception as e:
        log.error(f"Whisper error: {e}")
        return ""

# ─────────────────────────────────────────────
# ADDRESS + CALL TYPE EXTRACTION (Ollama)
# ─────────────────────────────────────────────

EXTRACTION_PROMPT = """You extract dispatch information from noisy emergency radio transcripts.

Return ONLY a raw JSON object — no markdown, no explanation, no code fences.
Keys:
  "address"   : street address or intersection (Title Case, no city/state), or null
  "call_type" : one of: Medical, Fire, Traffic Accident, Welfare Check, Theft,
                Assault, Disturbance, Shooting, Drug, Burglary, Suspicious,
                Traffic Stop, Other — or null if unclear

Example output:
{"address": "1200 South 18th Street", "call_type": "Medical"}

Transcript: {transcript}"""

def _call_ollama(prompt: str) -> str:
    """Send a prompt to Ollama and return the response text."""
    payload = {
        "model":  OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.0,   # deterministic — we want consistent JSON
            "num_predict": 150,   # need more tokens for units list + notes
        }
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get("response", "").strip()

def extract_dispatch_info(transcript: str) -> dict:
    """
    Extract address + call type from a radio transcript.
    Tries regex first (fast, zero cost), falls back to Ollama for hard cases.
    """
    if not transcript:
        return {"address": None, "call_type": None, "confidence": "none"}

    # ── Fast regex pass — no model needed for clear transcripts ────────
    regex_result = _regex_extract(transcript)
    if regex_result["address"] and regex_result["call_type"]:
        regex_result["confidence"] = "regex"
        return regex_result

    # ── Ollama for ambiguous / noisy transcripts ────────────────────────
    try:
        prompt = EXTRACTION_PROMPT.format(transcript=transcript[:600])
        raw = _call_ollama(prompt)

        # Strip accidental ```json fences some models add
        raw = re.sub(r"```json|```", "", raw).strip()

        # Sometimes the model prints text before the JSON — grab first {...}
        json_match = re.search(r"\{.*?\}", raw, re.DOTALL)
        if not json_match:
            raise ValueError(f"No JSON in response: {raw!r}")

        parsed = json.loads(json_match.group())
        parsed["confidence"] = "llm"
        return parsed

    except requests.exceptions.ConnectionError:
        log.warning("Ollama not reachable — is it running? (ollama serve)")
        regex_result["confidence"] = "regex-fallback"
        return regex_result
    except Exception as e:
        log.warning(f"Ollama extraction failed ({e}), using regex fallback")
        regex_result["confidence"] = "regex-fallback"
        return regex_result

def _regex_extract(text: str) -> dict:
    """Simple regex patterns for common dispatch address formats."""
    result = {"address": None, "call_type": None}

    # Address patterns
    addr_patterns = [
        r"\b(\d{1,5}\s+(?:North|South|East|West|N\.?|S\.?|E\.?|W\.?)?\s*[A-Z][a-zA-Z\s]{2,30}(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Boulevard|Blvd|Court|Ct|Way|Place|Pl)\.?)\b",
        r"\b(intersection of [A-Z][a-zA-Z\s]+ and [A-Z][a-zA-Z\s]+)\b",
        r"\b([A-Z][a-zA-Z\s]+ and [A-Z][a-zA-Z\s]+(?:Street|St|Avenue|Ave|Road|Rd))\b",
    ]
    for pat in addr_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            result["address"] = m.group(1).strip()
            break

    # Call type keywords
    call_keywords = {
        "Medical": ["cardiac", "medical", "breathing", "unconscious", "seizure", "ems", "ambulance", "overdose", "trauma"],
        "Fire": ["fire", "smoke", "flames", "structure fire", "vehicle fire", "brush fire"],
        "Traffic Accident": ["accident", "crash", "collision", "mvc", "mva", "hit and run", "traffic"],
        "Welfare Check": ["welfare", "well-being", "check on", "mental health", "suicidal"],
        "Shooting": ["shots fired", "shooting", "gunshot", "firearm"],
        "Assault": ["assault", "fight", "battery", "domestic"],
        "Theft": ["theft", "stolen", "shoplifting", "robbery"],
        "Burglary": ["burglary", "break-in", "breaking and entering"],
        "Drug": ["drug", "narcotics", "overdose"],
        "Disturbance": ["disturbance", "noise complaint", "loud", "disorderly"],
        "Suspicious": ["suspicious", "prowler", "trespassing"],
        "Traffic Stop": ["traffic stop", "vehicle stop"],
    }
    text_lower = text.lower()
    for ctype, keywords in call_keywords.items():
        if any(kw in text_lower for kw in keywords):
            result["call_type"] = ctype
            break

    return result

# ─────────────────────────────────────────────
# GEOCODING
# ─────────────────────────────────────────────
_geocoder = Nominatim(user_agent="lafayette-dispatch-monitor/1.0")

def geocode(address: str) -> tuple[float | None, float | None]:
    """Convert an address string to (lat, lon). Returns (None, None) on failure."""
    if not address:
        return None, None
    query = address + GEOCODE_SUFFIX
    try:
        loc = _geocoder.geocode(query, timeout=5)
        if loc:
            return loc.latitude, loc.longitude
        # Try without city suffix
        loc = _geocoder.geocode(address, timeout=5)
        if loc:
            return loc.latitude, loc.longitude
    except GeocoderTimedOut:
        log.warning(f"Geocoder timeout for: {address}")
    except Exception as e:
        log.warning(f"Geocoding error for '{address}': {e}")
    return None, None

# ─────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────
def process_call(call: dict):
    """Full pipeline for a single OpenMHz call object."""
    call_id = call.get("id") or call.get("_id") or hashlib.md5(
        json.dumps(call, sort_keys=True).encode()
    ).hexdigest()

    if call_exists(call_id):
        return  # already processed

    audio_url  = call.get("url") or call.get("audio_url")
    timestamp  = call.get("start_time") or call.get("time") or datetime.now(timezone.utc).isoformat()
    talkgroup  = call.get("talkgroup") or call.get("tg")
    tg_tag     = call.get("talkgroupTag") or call.get("talkgroup_tag") or ""

    log.info(f"Processing call {call_id} | TG: {tg_tag or talkgroup} | {timestamp}")

    # 1) Download audio
    transcript = ""
    if audio_url:
        audio_path = download_audio(audio_url)
        if audio_path:
            # 2) Transcribe
            transcript = transcribe(audio_path)
            os.unlink(audio_path)  # clean up temp file
            log.info(f"  Transcript: {transcript[:120]}...")

    # 3) Extract address + call type + units
    info = extract_dispatch_info(transcript)
    address   = info.get("address")
    call_type = info.get("call_type") or "Unknown"
    units     = json.dumps(info.get("units") or [])
    notes     = info.get("notes")
    confidence = info.get("confidence", "none")

    # 4) Geocode
    lat, lon = geocode(address) if (address and _looks_like_address(address)) else (None, None)
    if address and not _looks_like_address(address):
        log.info(f"  Rejected bad address: {address!r}")
        address = None
    if lat:
        log.info(f"  Geocoded: {address} → ({lat:.4f}, {lon:.4f})")
    else:
        log.info(f"  No geocode for: {address!r}")

    units_list = info.get("units") or []
    log.info(f"  Units: {units_list or "none"}")

    # 5) Store
    insert_call({
        "id":             call_id,
        "timestamp":      timestamp,
        "talkgroup":      str(talkgroup) if talkgroup else None,
        "talkgroup_tag":  tg_tag,
        "raw_transcript": transcript,
        "address":        address,
        "call_type":      call_type,
        "units":          units,
        "notes":          notes,
        "lat":            lat,
        "lon":            lon,
        "confidence":     confidence,
        "audio_url":      audio_url,
    })
    log.info(f"  Saved: {call_type} @ {address or 'unknown'} | units: {units_list} (confidence: {confidence})")

_last_poll_time = None

def poll_once():
    global _last_poll_time
    log.info("Polling OpenMHz...")
    calls = fetch_recent_calls(limit=25, filter_time=_last_poll_time)
    if calls:
        _last_poll_time = int(time.time() * 1000)
        new_count = 0
        for call in reversed(calls):  # oldest first
            if not call_exists(call.get("id") or call.get("_id", "")):
                process_call(call)
                new_count += 1
        if new_count:
            log.info(f"Processed {new_count} new call(s).")
        else:
            log.info("No new calls.")

# ─────────────────────────────────────────────
# SIMPLE JSON EXPORT (for dashboard)
# ─────────────────────────────────────────────
def export_json(output: str = os.environ.get("CALLS_JSON", "calls.json"), limit: int = 500):
    """Export recent geocoded calls to JSON for the dashboard."""
    con = sqlite3.connect(DB_PATH)
    rows = con.execute("""
        SELECT id, timestamp, talkgroup_tag, address, call_type, units, notes,
               raw_transcript, lat, lon, confidence, audio_url
        FROM calls
        ORDER BY timestamp DESC
        LIMIT ?
    """, (limit,)).fetchall()
    con.close()

    keys = ["id","timestamp","talkgroup","address","call_type","units","notes",
            "transcript","lat","lon","confidence","audio_url"]
    data = []
    for row in rows:
        d = dict(zip(keys, row))
        try:
            d["units"] = json.loads(d["units"]) if d["units"] else []
        except Exception:
            d["units"] = []
        data.append(d)
    with open(output, "w") as f:
        json.dump(data, f, indent=2)
    log.info(f"Exported {len(data)} calls to {output}")
    return data

# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lafayette IN dispatch monitor")
    parser.add_argument("--backfill", action="store_true",
                        help="Pull last 100 calls before watching live")
    parser.add_argument("--export-only", action="store_true",
                        help="Just export calls.json and exit")
    args = parser.parse_args()

    init_db()

    if args.export_only:
        export_json()
        sys.exit(0)

    if args.backfill:
        log.info("Backfilling last 100 calls...")
        calls = fetch_recent_calls(limit=100)
        for call in reversed(calls):
            process_call(call)
        export_json()

    # Watch live
    log.info(f"Starting live poll every {POLL_INTERVAL_S}s. Press Ctrl+C to stop.")
    schedule.every(POLL_INTERVAL_S).seconds.do(poll_once)
    schedule.every(2).minutes.do(lambda: export_json())  # refresh JSON for dashboard

    poll_once()  # run immediately
    while True:
        schedule.run_pending()
        time.sleep(1)

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
import cloudscraper
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
WHISPER_MODEL    = os.environ.get("WHISPER_MODEL", "base.en")  # tiny.en / base.en / small.en / medium.en

# ── Ollama config ──────────────────────────────────────────────────────────
OLLAMA_URL       = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL     = os.environ.get("OLLAMA_MODEL", "llama3.2")  # or: mistral, llama3.1, gemma2, phi3
#   Model recommendations for this task on Apple Silicon:
#     llama3.2   (2GB)  — fast, good JSON, great for short transcripts
#     mistral    (4GB)  — slightly better address parsing, still quick
#     llama3.1   (5GB)  — best accuracy if you have 16GB+ unified RAM
#     phi3-mini  (2GB)  — very fast, decent but misses tricky addresses
# ──────────────────────────────────────────────────────────────────────────

CITY_CONTEXT     = "Lafayette, Indiana"
GEOCODE_SUFFIX   = ", Lafayette, IN, USA"

# Talkgroups to track — fire/EMS only (no police)
# Adjust these to match the exact talkgroup tags shown on OpenMHz for tippco
TALKGROUP_FILTER = [
    "LFD DISP",    # Lafayette Fire Dept dispatch
    "WLFD DISP",   # West Lafayette Fire Dept dispatch
    "TIPCO EMS",   # Tippecanoe County EMS
    "LFD OPS",     # LFD operations
    "WLFD OPS",    # WLFD operations
    "EMS OPS",     # EMS operations
]

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
# OPENMHZ API  (cloudscraper bypasses Cloudflare)
# ─────────────────────────────────────────────
_scraper = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False}
)

def fetch_recent_calls(limit: int = 20, filter_time: int = None) -> list:
    """Fetch most recent calls from Tippecanoe County feed."""
    params = {"limit": limit}
    if filter_time:
        params["filter-time"] = filter_time
    if TALKGROUP_FILTER:
        params["filter-talkgroup"] = ",".join(TALKGROUP_FILTER)

    try:
        url = f"{OPENMHZ_BASE}/{OPENMHZ_SYSTEM}/calls"
        resp = _scraper.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("calls", [])
    except Exception as e:
        log.error(f"OpenMHz API error: {e}")
        return []

def download_audio(url: str) -> str | None:
    """Download call audio to a temp file. Returns path or None."""
    try:
        resp = _scraper.get(url, timeout=30, stream=True)
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
                "Lafayette Indiana emergency dispatch. Fire EMS police. "
                "Units: Engine 1 Engine 2 Engine 3 Engine 4 Engine 5 Engine 6 Engine 7 "
                "Medic 1 Medic 2 Medic 3 Medic 4 Medic 5 Ladder 1 Ladder 2 "
                "Battalion 1 Squad 1 Rescue 1 Tanker 1 "
                "Engine 11 Engine 12 Engine 13 Medic 11 "
                "respond to structure fire cardiac arrest traffic accident. "
                "Streets: Sagamore Parkway South 18th Street Teal Road McCarty Lane "
                "Greenbush Street Union Street Main Street State Street "
                "Schuyler Avenue Columbian Avenue Concord Road "
                "South 9th Street North 9th Street Ferry Street Brown Street "
                "Creasy Lane Klondike Road Yeager Road Cumberland Avenue "
                "South River Road North Salisbury Street Beck Lane "
                "US 231 State Road 38 State Road 26 Interstate 65. "
                "Locations: Union Hospital Franciscan Health IU Health Arnett "
                "Purdue University Ross-Ade Stadium Happy Hollow Park "
                "Tippecanoe Mall Wabash River. "
                "Incidents: cardiac arrest unconscious not breathing seizure overdose "
                "structure fire vehicle fire brush fire smoke investigation "
                "motor vehicle accident head-on collision pedestrian struck."
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
  "call_type" : one of: Medical, Fire, Traffic Accident, Welfare Check,
                Hazmat, Water Rescue, Other — or null if unclear
  "units"     : list of responding unit identifiers mentioned (e.g. ["Engine 1", "Medic 2"]), or []
  "notes"     : one concise sentence describing the incident (max 15 words), or null

Example output:
{"address": "1200 South 18th Street", "call_type": "Medical", "units": ["Medic 2", "Engine 3"], "notes": "Unresponsive male patient, possible cardiac arrest."}

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
    Extract address, call type, units, and notes from a radio transcript.
    Regex runs first (fast, zero cost). LLM fills any gaps regex missed.
    Results are merged: regex units are always kept, LLM fills missing fields.
    """
    if not transcript:
        return {"address": None, "call_type": None, "units": [], "notes": None, "confidence": "none"}

    # ── Fast regex pass ─────────────────────────────────────────────────
    regex_result = _regex_extract(transcript)

    # If regex got both address and call_type, we're done (units already extracted)
    if regex_result["address"] and regex_result["call_type"]:
        regex_result["confidence"] = "regex"
        return regex_result

    # ── Ollama for gaps ──────────────────────────────────────────────────
    try:
        prompt = EXTRACTION_PROMPT.format(transcript=transcript[:700])
        raw = _call_ollama(prompt)

        # Strip accidental ```json fences some models add
        raw = re.sub(r"```(?:json)?", "", raw).strip()

        # Grab first {...} block in case model adds preamble text
        json_match = re.search(r"\{.*?\}", raw, re.DOTALL)
        if not json_match:
            raise ValueError(f"No JSON in response: {raw!r}")

        parsed = json.loads(json_match.group())

        # Merge: prefer LLM for missing fields, always keep regex units
        merged = {
            "address":   parsed.get("address")   or regex_result.get("address"),
            "call_type": parsed.get("call_type") or regex_result.get("call_type"),
            "notes":     parsed.get("notes"),
            "units":     _merge_units(regex_result.get("units", []), parsed.get("units", [])),
            "confidence": "llm",
        }
        return merged

    except requests.exceptions.ConnectionError:
        log.warning("Ollama not reachable — is it running? (ollama serve)")
        regex_result["confidence"] = "regex-fallback"
        return regex_result
    except Exception as e:
        log.warning(f"Ollama extraction failed ({e}), using regex fallback")
        regex_result["confidence"] = "regex-fallback"
        return regex_result


def _merge_units(regex_units: list, llm_units: list) -> list:
    """Combine unit lists from regex and LLM, deduplicating."""
    seen = set()
    merged = []
    for u in (regex_units or []) + (llm_units or []):
        key = u.lower().strip()
        if key not in seen:
            seen.add(key)
            merged.append(u)
    return merged

def _regex_extract(text: str) -> dict:
    """Regex patterns for common dispatch address formats, call types, and units."""
    result = {"address": None, "call_type": None, "units": [], "notes": None}

    # Address patterns — ordered from most to least specific
    addr_patterns = [
        # Full numbered address with direction and street type
        r"\b(\d{1,5}\s+(?:North|South|East|West|N\.?|S\.?|E\.?|W\.?)\s+[A-Z][a-zA-Z]{2,25}\s+(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Boulevard|Blvd|Court|Ct|Way|Place|Pl|Parkway|Pkwy)\.?)\b",
        # Numbered address without direction
        r"\b(\d{1,5}\s+[A-Z][a-zA-Z]{2,25}\s+(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Boulevard|Blvd|Court|Ct|Way|Place|Pl|Parkway|Pkwy)\.?)\b",
        # Highway/US route references
        r"\b(\d{1,5}\s+(?:US|State|Indiana|IN)\s+(?:Highway|Hwy|Route|Road|Rd)?\s*\d+)\b",
        r"\b(US\s+(?:Highway\s+)?\d+(?:\s+(?:North|South|East|West))?)\b",
        r"\b(State\s+Road\s+\d+)\b",
        # Named intersections
        r"\b(intersection of [A-Za-z\s]{3,25} and [A-Za-z\s]{3,25})\b",
        r"\b([A-Z][a-zA-Z\s]{2,20} and [A-Z][a-zA-Z\s]{2,20}(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln))\b",
        # Named locations (hospitals, landmarks)
        r"\b((?:Union Hospital|Franciscan Health|IU Health Arnett|Purdue University|Tippecanoe Mall|Ross-Ade Stadium)(?:\s+[A-Za-z\s]{0,20})?)\b",
    ]
    for pat in addr_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            addr = m.group(1).strip()
            # Title-case it
            result["address"] = " ".join(w.capitalize() for w in addr.split())
            break

    # Call type keywords — fire/EMS focused, ordered by specificity
    call_keywords = {
        "Fire":            ["structure fire", "house fire", "vehicle fire", "brush fire", "grass fire",
                            "building fire", "apartment fire", "flames", "smoke showing",
                            "fire alarm", "odor of smoke", "fire in"],
        "Medical":         ["cardiac arrest", "heart attack", "chest pain", "not breathing",
                            "unconscious", "unresponsive", "breathing difficulty", "shortness of breath",
                            "seizure", "stroke", "overdose", "medical emergency", "ems", "ambulance",
                            "fall victim", "trauma", "diabetic", "allergic reaction", "choking",
                            "hemorrhage", "bleeding", "burn", "electrocution", "drowning"],
        "Traffic Accident":["motor vehicle accident", "vehicle accident", "traffic accident",
                            "mva", "mvc", "crash", "collision", "rollover",
                            "pedestrian struck", "bicycle struck", "head-on", "entrapment"],
        "Welfare Check":   ["welfare check", "well-being check", "check on subject",
                            "mental health", "suicidal", "person in crisis", "not answering door"],
        "Hazmat":          ["hazmat", "hazardous material", "gas leak", "chemical spill",
                            "carbon monoxide", "co detector", "fuel spill", "natural gas"],
        "Water Rescue":    ["water rescue", "swift water", "drowning", "flood rescue",
                            "wabash river", "person in water"],
    }
    text_lower = text.lower()
    for ctype, keywords in call_keywords.items():
        if any(kw in text_lower for kw in keywords):
            result["call_type"] = ctype
            break

    # Unit extraction
    result["units"] = _extract_units(text)

    return result


def _extract_units(text: str) -> list[str]:
    """Extract responding unit designations from a dispatch transcript."""
    unit_patterns = [
        r"\b(Engine\s+\d+[A-Z]?)\b",
        r"\b(Ladder\s+\d+[A-Z]?)\b",
        r"\b(Medic\s+\d+[A-Z]?)\b",
        r"\b(Battalion\s+\d+)\b",
        r"\b(Rescue\s+\d+)\b",
        r"\b(Tanker\s+\d+)\b",
        r"\b(Squad\s+\d+)\b",
        r"\b(Truck\s+\d+)\b",
        r"\b(Tower\s+\d+)\b",
        r"\b(Brush\s+\d+)\b",
        r"\b(Air\s+\d+)\b",
        r"\b(Car\s+\d{2,3})\b",
        r"\b(Unit\s+\d+)\b",
    ]
    seen = set()
    units = []
    for pat in unit_patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            # Normalize: collapse whitespace, title-case
            unit = " ".join(m.group(1).split()).title()
            if unit not in seen:
                seen.add(unit)
                units.append(unit)
    return units


def _looks_like_address(text: str) -> bool:
    """Sanity-check that an extracted string is plausibly a real address."""
    if not text or len(text) < 5:
        return False
    # Named landmarks are always valid
    landmarks = ["hospital", "university", "stadium", "mall", "health", "park"]
    if any(lm in text.lower() for lm in landmarks):
        return True
    # Must have a digit OR be a clear intersection
    has_number = bool(re.search(r"\d", text))
    is_intersection = bool(re.search(r"\band\b", text, re.IGNORECASE))
    # Should also have a street-type word
    has_street_type = bool(re.search(
        r"\b(st|ave|rd|dr|ln|blvd|ct|way|pl|pkwy|highway|hwy|road|street|avenue"
        r"|drive|lane|boulevard|court|place|parkway|route)\b",
        text, re.IGNORECASE
    ))
    return (has_number or is_intersection) and (has_street_type or has_number)

# ─────────────────────────────────────────────
# GEOCODING
# ─────────────────────────────────────────────
_geocoder = Nominatim(user_agent="lafayette-dispatch-monitor/1.0")
_geocode_cache: dict[str, tuple] = {}  # address → (lat, lon)

def geocode(address: str) -> tuple[float | None, float | None]:
    """Convert an address string to (lat, lon). Returns (None, None) on failure."""
    if not address:
        return None, None
    key = address.lower().strip()
    if key in _geocode_cache:
        return _geocode_cache[key]

    result = (None, None)
    query = address + GEOCODE_SUFFIX
    try:
        loc = _geocoder.geocode(query, timeout=5)
        if loc:
            result = (loc.latitude, loc.longitude)
        else:
            # Try without city suffix
            loc = _geocoder.geocode(address, timeout=5)
            if loc:
                result = (loc.latitude, loc.longitude)
    except GeocoderTimedOut:
        log.warning(f"Geocoder timeout for: {address}")
    except Exception as e:
        log.warning(f"Geocoding error for '{address}': {e}")

    _geocode_cache[key] = result
    return result

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

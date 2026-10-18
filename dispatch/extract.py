"""Pull incident details out of a dispatch transcript.

Two extractors share one output shape:
  * llm_extract   — Ollama with a JSON-schema constrained response (accurate)
  * rules_extract — regex + keyword taxonomy (no dependencies, used when the
                    LLM is disabled/unreachable)
"""

from __future__ import annotations

import json
import logging
import re
import time

import requests

from . import config
from .taxonomy import ALL_NATURES, category_for, nature_from_keywords

log = logging.getLogger(__name__)

EMPTY = {
    "is_dispatch": False, "address": None, "place": None, "nature": None,
    "units": [], "patient_age": None, "patient_sex": None, "summary": None,
}

SCHEMA = {
    "type": "object",
    "properties": {
        "is_dispatch": {"type": "boolean"},
        "address": {"type": ["string", "null"]},
        "place": {"type": ["string", "null"]},
        "nature": {"type": "string", "enum": ALL_NATURES},
        "units": {"type": "array", "items": {"type": "string"}},
        "patient_age": {"type": ["integer", "null"]},
        "patient_sex": {"type": ["string", "null"], "enum": ["male", "female", None]},
        "summary": {"type": ["string", "null"]},
    },
    "required": ["is_dispatch", "address", "place", "nature", "units", "patient_age", "patient_sex", "summary"],
}

SYSTEM_PROMPT = f"""You read speech-to-text transcripts of fire/EMS radio in Tippecanoe County, Indiana \
(Lafayette, West Lafayette, Purdue University) and extract the incident being dispatched.

Transcripts are noisy: words are misheard, and dispatchers usually repeat the address. \
Units are called by station or number ("Station 3", "Engine 1502", "Medic 5", "Westside Medic", "Central").

Fields:
- is_dispatch: true ONLY if the dispatcher is sending units to a new incident at a location. \
false for acknowledgements, en route / on scene / clearing reports, status checks, tests, traffic stops, \
and anything without an incident location.
- address: the street address as spoken, house number first, without apartment/room/city \
("2741 North Salisbury Street"). For an intersection use "Street A and Street B". \
Fix obvious mishearing of local street names (Sagamore Parkway, Salisbury, Teal Road, Creasy Lane, \
Greenbush, Union, Kossuth, Concord Road, McCarty Lane, Yeager Road, Klondike Road, Cumberland Avenue, \
Northwestern Avenue, Stadium Avenue, State Street, River Road). null if none.
- place: a named business/facility/landmark if given ("Westminster Village", "Central Presbyterian Church", \
"Purdue Police Department", "LTHC"), else null.
- nature: the single best match from the allowed list. Use "Other" only if none fits.
- units: unit or station names being dispatched, e.g. ["Station 7", "Medic 5"].
- patient_age / patient_sex: if stated.
- summary: one short plain sentence describing the incident (max 15 words), no address.

Allowed natures: {", ".join(ALL_NATURES)}."""

EXAMPLES = [
    ("Station 7, stand by for a medical run, the 3106 Eagles Way Drive, Apartment 1309. Station 7, 3106 Eagles "
     "Way Drive. I have a 73-year-old male who fell in the bathroom, he's now stuck between the wall and the "
     "shower chair. Go channel 7.",
     {"is_dispatch": True, "address": "3106 Eagles Way Drive", "place": None, "nature": "Fall",
      "units": ["Station 7"], "patient_age": 73, "patient_sex": "male",
      "summary": "73-year-old male fell in bathroom, stuck between wall and shower chair."}),
    ("Medic 16 right sir, 16 is clearing PD, back in service.",
     {"is_dispatch": False, "address": None, "place": None, "nature": "Other", "units": ["Medic 16"],
      "patient_age": None, "patient_sex": None, "summary": None}),
]


def _messages(transcript: str):
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    for t, out in EXAMPLES:
        msgs.append({"role": "user", "content": f"Transcript: {t}"})
        msgs.append({"role": "assistant", "content": json.dumps(out)})
    msgs.append({"role": "user", "content": f"Transcript: {transcript[:1500]}"})
    return msgs


class LLMUnavailable(Exception):
    pass


def ensure_model(wait_s: int = 0, on_status=None) -> bool:
    """Pull the configured model if this Ollama doesn't have it (fresh container).
    Waits up to wait_s for the Ollama server to come up first."""
    deadline = time.time() + wait_s
    while True:
        try:
            tags = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=10).json().get("models", [])
            break
        except Exception as e:
            if time.time() >= deadline:
                log.warning(f"Ollama not reachable at {config.OLLAMA_URL} ({e}); using keyword rules until it is")
                return False
            time.sleep(5)
    names = {m.get("name") for m in tags} | {m.get("model") for m in tags}
    want = config.OLLAMA_MODEL if ":" in config.OLLAMA_MODEL else f"{config.OLLAMA_MODEL}:latest"
    if want in names:
        return True
    log.info(f"Downloading {config.OLLAMA_MODEL} into Ollama (one-time, a few GB)…")
    if on_status:
        on_status(f"Downloading AI model {config.OLLAMA_MODEL} (first start only)")
    try:
        r = requests.post(f"{config.OLLAMA_URL}/api/pull", json={"model": config.OLLAMA_MODEL, "stream": False},
                          timeout=3600)
        r.raise_for_status()
        log.info(f"Pulled {config.OLLAMA_MODEL}")
        return True
    except Exception as e:
        log.warning(f"Could not pull {config.OLLAMA_MODEL}: {e}")
        return False


def llm_extract(transcript: str) -> dict:
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": _messages(transcript),
        "stream": False,
        "format": SCHEMA,
        "keep_alive": "24h",
        "options": {"temperature": 0, "num_predict": 220, "num_ctx": 4096},
    }
    try:
        r = requests.post(f"{config.OLLAMA_URL}/api/chat", json=payload, timeout=config.OLLAMA_TIMEOUT_S)
    except requests.exceptions.ConnectionError as e:
        raise LLMUnavailable(str(e)) from e
    if r.status_code == 404 and "not found" in r.text:
        raise LLMUnavailable(f"model {config.OLLAMA_MODEL} not pulled: run `ollama pull {config.OLLAMA_MODEL}`")
    r.raise_for_status()
    data = json.loads(r.json()["message"]["content"])
    return _clean(data)


def _clean(d: dict) -> dict:
    out = dict(EMPTY)
    out.update({k: d.get(k) for k in EMPTY})
    for k in ("address", "place", "summary"):
        v = out[k]
        out[k] = v.strip() if isinstance(v, str) and v.strip() and v.strip().lower() not in ("null", "none", "unknown") else None
    if out["place"] and re.match(r"^(apartment|apt|room|unit|suite|lot|floor|door|building)\b", out["place"], re.I):
        out["place"] = None        # a unit number, not a landmark
    if out["nature"] not in ALL_NATURES:
        out["nature"] = "Other"
    out["units"] = [u.strip() for u in (out["units"] or []) if isinstance(u, str) and u.strip()][:12]
    if out["patient_sex"] not in ("male", "female"):
        out["patient_sex"] = None
    if not isinstance(out["patient_age"], int) or not 0 <= out["patient_age"] <= 110:
        out["patient_age"] = None
    out["is_dispatch"] = bool(out["is_dispatch"])
    return out


# ─────────────────────────────────────────────
# Rules
# ─────────────────────────────────────────────
_STREET_TYPES = (r"Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Boulevard|Blvd|Court|Ct|Way|Place|Pl|"
                 r"Parkway|Pkwy|Trail|Trl|Circle|Cir|Highway|Hwy|Pike|Terrace|Crossing|Loop")
_DIR = r"(?:North|South|East|West|[NSEW]\.?)"
ADDRESS_RES = [
    # 2741 North Salisbury Street / 3106 Eagles Way Drive
    re.compile(rf"\b(\d{{1,5}}\s+(?:{_DIR}\s+)?(?:[A-Z0-9][\w'-]*\s+){{0,3}}?(?:{_STREET_TYPES})\b(?:\s+{_DIR}(?=\W))?)", re.I),
    # county roads: 4500 East 100 North / County Road 500 East
    re.compile(rf"\b(\d{{1,5}}\s+{_DIR}\s+\d{{2,4}}\s+{_DIR})(?=\W|$)", re.I),
    re.compile(rf"\b(\d{{1,5}}\s+(?:State Road|US|U\.S\.|Highway|Interstate)\s*\d{{1,3}})\b", re.I),
    # 2741 North Salisbury (no suffix) - direction + word
    re.compile(rf"\b(\d{{1,5}}\s+{_DIR}\s+[A-Z0-9][\w'-]+)", re.I),
]
INTERSECTION_RE = re.compile(
    rf"\b((?:{_DIR}\s+)?[A-Z0-9][\w'-]+(?:\s+(?:{_STREET_TYPES}))?\s+(?:and|&)\s+(?:{_DIR}\s+)?[A-Z0-9][\w'-]+(?:\s+(?:{_STREET_TYPES}))?)\b")
UNIT_RE = re.compile(
    r"\b((?:Engine|Ladder|Truck|Tower|Medic|Squad|Rescue|Tanker|Brush|Battalion|Chief|Station|Car|Unit)\s+\d{1,4}[A-Z]?|"
    r"(?:North|South|East|West|Central|Northside|Southside|Eastside|Westside)\s?(?:side\s)?Medic)\b", re.I)
DISPATCH_CUES = re.compile(
    r"\b(stand ?by for|respond(?:ing)? to|be en ?route|en ?route to|medical run|fire run|you(?:'re| are) going to|"
    r"go(?:ing)? to channel|go channel|for a medical|for a fire|for an? (?:alarm|accident|crash|fall)|"
    r"dispatch to|request for|time out|timeout)\b", re.I)


def rules_extract(transcript: str) -> dict:
    out = dict(EMPTY)
    text = transcript or ""
    for rx in ADDRESS_RES:
        m = rx.search(text)
        if m:
            out["address"] = " ".join(m.group(1).split())
            break
    if not out["address"]:
        m = INTERSECTION_RE.search(text)
        if m and re.search(_STREET_TYPES, m.group(1), re.I):
            out["address"] = " ".join(m.group(1).split())
    out["nature"] = nature_from_keywords(text) or "Other"
    seen, units = set(), []
    for m in UNIT_RE.finditer(text):
        u = " ".join(m.group(1).split()).title()
        if u.lower() not in seen:
            seen.add(u.lower())
            units.append(u)
    out["units"] = units
    m = re.search(r"\b(\d{1,3})[- ](?:year|yr)s?[- ]old\s+(male|female|man|woman|boy|girl)", text, re.I)
    if m:
        out["patient_age"] = int(m.group(1))
        out["patient_sex"] = "male" if m.group(2).lower() in ("male", "man", "boy") else "female"
    out["is_dispatch"] = bool(out["address"]) and (bool(DISPATCH_CUES.search(text)) or out["nature"] != "Other")
    return out


def looks_relevant(transcript: str) -> bool:
    """Cheap pre-filter: only spend LLM time on transcripts that might be a dispatch."""
    t = transcript or ""
    if len(t.split()) < 6:
        return False
    return bool(re.search(r"\d{2,}", t) or DISPATCH_CUES.search(t) or nature_from_keywords(t)
                or re.search(r"\b(?:" + "|".join(w for w in ("one", "two", "three", "four", "five", "six", "seven",
                                                              "eight", "nine")) + r")\b.*\b(?:" + _STREET_TYPES + r")\b", t, re.I))


def extract(transcript: str, use_llm: bool = True) -> tuple[dict, str]:
    """Returns (fields, method) where method is 'llm', 'rules', or 'skipped'."""
    if not transcript or not looks_relevant(transcript):
        return dict(EMPTY), "skipped"
    if use_llm and config.LLM_ENABLED:
        out = llm_extract(transcript)   # LLMUnavailable propagates so the worker can retry later
        return ground(out, transcript), "llm"
    return rules_extract(transcript), "rules"


_SPOKEN_DIGITS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                  "six": "6", "seven": "7", "eight": "8", "nine": "9"}


def _digits_in(text: str) -> str:
    """All digits of a transcript, with spoken digits converted, as one string per word."""
    words = re.findall(r"[a-z]+|\d+", text.lower())
    return " ".join(_SPOKEN_DIGITS.get(w, w) for w in words)


def ground(out: dict, transcript: str) -> dict:
    """Small models occasionally invent or 'correct' details. Keep only what the
    transcript supports, falling back to the rules extractor's address."""
    t_low = transcript.lower()
    rules_addr = rules_extract(transcript)["address"]
    addr = out["address"]
    if addr:
        m = re.match(r"^\s*(\d[\d\s.-]*)\s+(.*)$", addr)
        if not re.search(r"[a-z]{3,}", addr, re.I):           # "1501", "1.57"
            addr = None
        elif m:
            number = re.sub(r"\D", "", m.group(1))
            spoken = _digits_in(transcript)
            number_ok = number in re.sub(r"\D", "", transcript) or number in spoken.replace(" ", "")
            words = [w for w in re.findall(r"[a-z]{3,}", m.group(2).lower())
                     if w not in ("north", "south", "east", "west", "street", "avenue", "road", "drive",
                                  "lane", "court", "boulevard", "parkway", "trail", "place", "circle", "highway")]
            words_ok = all(w in t_low for w in words) if words else True
            if not number_ok:
                addr = rules_addr
            elif not words_ok and rules_addr and rules_addr.split()[0] == number:
                addr = rules_addr          # model swapped the street name; trust what was said
        elif out["place"] is None and not re.search(r"\b(and|&)\b", addr):
            out["place"], addr = addr, None  # "co-rec" is a place, not an address
    if out["is_dispatch"] and not addr and not out["place"]:
        addr = rules_addr
    out["address"] = addr
    # Drop units the model invented ("Medic 16" when nobody said 16)
    kept = []
    for u in out["units"]:
        nums = re.findall(r"\d+", u)
        if nums and all(n in transcript for n in nums):
            kept.append(u)
        elif not nums and u.lower().split()[0] in t_low:
            kept.append(u)
    out["units"] = kept
    return out


def category(fields: dict) -> str:
    return category_for(fields.get("nature"))

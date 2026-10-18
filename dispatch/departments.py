"""Fix misheard names in transcripts and work out which departments a
dispatch went to.

Whisper hears "TEAS, Wea Fire" as "Tees, we all fire". Fixing the transcript
before the LLM reads it keeps "Tees" out of the unit list, and the corrected
names (plus unit numbers on shared county channels) tell us which township
department was actually toned out instead of just "county fire".
"""

from __future__ import annotations

import re
from functools import lru_cache

from . import config
from .counties import Department

# Dispatch vocabulary misheard the same way in every county.
COMMON_FIXES: tuple[tuple[str, str], ...] = (
    (r"\b(?:ellipticist|elepticist|elliptic(?:al)? assist|lifted assist|lift a cyst|left assist)\b", "lift assist"),
    (r"\b(?:ultra[- ]?mental|alter mental|altar mental|altered metal)\s+status\b", "altered mental status"),
    # "10 times 0705", "12 times, 1349", "first on time 1809" -> "tone time 0705"
    (r"\b(?:10|ten|12|twelve|turn|tune|tom|first on|tone|tonne|some|till and)[- ]?times?\b(?=,?\s*(?:is\s+)?\d{3,4}\b)",
     "tone time"),
    (r"\b(?:be|being|b|bn|cn|sienna?)\s+(?:on\s+)?route\b", "be en route"),
    (r"\bdifficulty following (?:spans|stands)\b", "difficulty following commands"),
)


@lru_cache(maxsize=1)
def _compiled():
    p = config.PROFILE
    fixes = [(re.compile(rx, re.I), rep) for rx, rep in COMMON_FIXES + p.transcript_fixes]
    names = []
    for d in p.departments:
        for rx in d.aliases:
            names.append((re.compile(rx, re.I), d))
    detect = [(re.compile(rx, re.I), d) for d in p.departments for rx in d.detect]
    return fixes, names, detect


def normalize(transcript: str) -> str:
    """Rewrite misheard department names and common phrases to their real form."""
    if not transcript:
        return transcript
    fixes, names, _ = _compiled()
    from .corrections import apply_word_fixes
    t = apply_word_fixes(transcript)            # "heard -> actually" fixes added in the dashboard
    for _ in range(2):       # second pass: "T.E.'s about to go on fire" only reads as TEAS once Battle Ground is fixed
        for rx, d in names:
            if d.spoken:
                t = rx.sub(_keep_lead(d.spoken), t)
    for rx, rep in fixes:
        t = rx.sub(rep, t)
    t = re.sub(r"\b(TEAS)(?:[,.]?\s+TEAS\b)+", r"\1", t)   # "TEAS, TEAS" from overlapping fixes
    t = join_house_numbers(t)
    return re.sub(r"[ \t]{2,}", " ", t).strip()


_STREET_WORD = (r"(?:street|st|avenue|ave|road|rd|drive|dr|lane|ln|boulevard|blvd|court|ct|way|place|pl|parkway|"
                r"pkwy|trail|trl|circle|cir|highway|hwy|pike|terrace|crossing|run|pass|loop|row|point|ridge|bend|"
                r"trace|commons|plaza|square|cove|path|walk|north|south|east|west)")
# "12999, Pennsylvania Street" / "101, West Main Street" / "12,201, East 166th Street"
_NUM_COMMA_STREET = re.compile(
    r"\b(\d{1,6}),\s+(?=(?:north|south|east|west|n|s|e|w)?\.?\s*(?:[a-z][\w'.-]*|\d+(?:st|nd|rd|th))"
    r"(?:\s+[\w'.-]+){0,3}?\s+" + _STREET_WORD + r"\b)", re.I)
_UNIT_WORD_BEFORE = re.compile(r"\b(?:engine|ladder|truck|tower|medic|squad|rescue|tanker|brush|battalion|chief|"
                               r"station|car|unit|ambulance|utility|grass|safety|edo|apartment|apt|room|suite|lot|"
                               r"time|timeout|channel|ops|zone)\s*$", re.I)


def join_house_numbers(t: str) -> str:
    """Whisper writes the dispatcher's pause after a house number as a comma,
    which the LLM then reads as a separate number. Put the address back together."""
    t = re.sub(r"\b(\d{1,3}),(\d{3})\b(?!,\d)", r"\1\2", t)              # "12,201" -> "12201"
    # "9730 Prairie Lake, Boulevard East": the street type split off by a pause
    t = re.sub(r"\b(\d{1,6} (?:(?:north|south|east|west) )?[A-Z][\w']*(?: [A-Z][\w']*){0,2}), "
               r"(Boulevard|Drive|Street|Road|Avenue|Lane|Court|Way|Parkway|Place|Circle|Trail|Pike|Run)\b",
               r"\1 \2", t, flags=re.I)
    t = re.sub(r"\b\d(?:-\d){2,4}\b", lambda m: m.group(0).replace("-", ""), t)   # "9-9-7-9" -> "9979"

    def join(m: re.Match) -> str:
        if _UNIT_WORD_BEFORE.search(t[:m.start()]):
            return m.group(0)                                              # "Medic 355, Main Street"
        return m.group(1) + " "
    return _NUM_COMMA_STREET.sub(join, t)


def _keep_lead(spoken: str):
    """Replacement that keeps any leading punctuation/space a regex consumed."""
    def rep(m: re.Match) -> str:
        lead = re.match(r"^\W*(?:attention,?\s+)?", m.group(0), re.I).group(0)
        if m.group(0)[len(lead):].lower() == spoken.lower():
            return m.group(0)               # already right; don't rewrite (keeps fixes idempotent)
        return lead + spoken
    return rep


_UNIT_NUM = re.compile(
    r"\b(?:engine|medic|rescue|squad|tanker|brush|ladder|truck|tower|chief|car|unit|utility|grass|battalion|"
    r"ambulance|boat|support|command|edo|duty officer)[\s,]*(\d{2,4})\b", re.I)
# Bare unit numbers in units the LLM returned: "924", "Station 1305"
_BARE_NUM = re.compile(r"^\D*?(\d{2,4})\D*$")


def _by_unit_number(num: str) -> Department | None:
    """'1305' -> the department whose pattern ('13xx') fits; the most specific pattern wins."""
    best = None
    for d in config.PROFILE.departments:
        for pat in d.unit_prefixes:
            if len(pat) == len(num) and all(p in ("x", n) for p, n in zip(pat.lower(), num)):
                fixed = len(pat.lower().rstrip("x"))
                if best is None or fixed > best[1]:
                    best = (d, fixed)
    return best[0] if best else None


def detect(transcript: str, units: list[str] | None = None, channel=None) -> list[str]:
    """Departments dispatched in one transmission, most specific first.

    Uses names in the (normalized) transcript, unit numbers when the channel is
    shared by several departments, and finally the channel's own department."""
    _, names, detects = _compiled()
    t = transcript or ""
    found: list[str] = []

    def add(name):
        if name and name not in found:
            found.append(name)

    for rx, d in names:
        if rx.search(t):
            add(d.name)
    for rx, d in detects:
        if rx.search(t):
            add(d.name)
    if channel is not None and getattr(channel, "shared", False):
        nums = [m.group(1) for m in _UNIT_NUM.finditer(t)]
        for u in units or []:
            m = _BARE_NUM.match(u)
            if m:
                nums.append(m.group(1))
        # Full unit numbers ("Engine 383") are strong evidence; two-digit ones are weak:
        # crews do shorten ("Ladder 72" = 372) but Whisper also drops digits
        # ("Engine 33" for Engine 383). A weak one only counts on its own or
        # when it agrees with the full numbers on the same call.
        strong = [d.name for n in nums if len(n) >= 3 for d in [_by_unit_number(n)] if d]
        weak = [d.name for n in nums if len(n) < 3 for d in [_by_unit_number(n)] if d]
        for name in strong:
            add(name)
        for name in weak:
            if not strong or name in strong:
                add(name)
    if channel is not None and channel.default_department:
        add(channel.default_department)     # the Lafayette FD channel always dispatches Lafayette FD
    return found


_GENERIC_UNITS = {"fire", "ems", "medic", "medics", "rescue", "engine", "units", "unit", "station", "dispatch",
                  "all units", "county", "tees", "teas", "t", "tgs"}


def clean_unit(unit: str) -> str | None:
    """'Purdue Fire Medic 17' -> 'Medic 17'; 'TEAS' / 'Buck Creek Fire' / 'Fire' -> None."""
    u = (unit or "").strip(" ,.")
    if not u or is_department_name(u):
        return None
    for d in config.PROFILE.departments:
        for name in {d.name, d.spoken or d.name}:
            if u.lower().startswith(name.lower() + " "):
                u = u[len(name):].strip(" ,.")
    if u.lower() in _GENERIC_UNITS or u.isdigit() and len(u) == 4:    # "1050" is a radio code / time
        return None
    return u


def is_department_name(text: str) -> bool:
    """True for things like "TEAS" or "Buck Creek Fire" that the LLM puts in units."""
    t = (text or "").strip()
    if not t:
        return False
    _, names, _ = _compiled()
    if any(rx.fullmatch(t) or rx.search(t) and not re.search(r"\d", t) for rx, _ in names):
        return True
    return any(t.lower() == (d.spoken or d.name).lower() or t.lower() == d.name.lower()
               for d in config.PROFILE.departments)

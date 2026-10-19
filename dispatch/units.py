"""Unit status from radio traffic, for the "Active now" view.

Units report what they're doing ("Engine 41 is on scene", "Medic 605 en route
to IU with one", "Ladder 72, back in service"). Each report becomes an event
matched to the incident the unit was dispatched to, so the dashboard can show
which calls are still open and where every unit is.
"""

from __future__ import annotations

import json
import re
import time

from . import config, db

# Most specific first: "en route to the hospital" is a transport, not responding.
STATUSES: list[tuple[str, re.Pattern]] = [
    ("out of service", re.compile(r"\bout of service\b", re.I)),
    ("transporting", re.compile(r"\b(?:transport(?:ing)?|en ?route to|in ?route to|headed to|heading to|on the way to)\b"
                                r"[^.]{0,40}\b(?:hospital|iu|riverview|franciscan|arnett|unity|st\.? ?vincent|"
                                r"ascension|community|methodist|eskenazi|riley|er)\b|\bwith (?:one|1|two|2)\b", re.I)),
    ("at hospital", re.compile(r"\b(?:arrived|arriving|at)\b[^.]{0,20}\b(?:the hospital|iu|riverview|franciscan|arnett|"
                               r"unity|st\.? ?vincent|ascension|community|methodist|eskenazi|riley)\b", re.I)),
    ("cleared", re.compile(r"\b(?:clear(?:ing|ed)?(?! to)|back in service|back at service|in service|available|"
                           r"returning|back (?:at|to|in) (?:the )?(?:station|quarters)|back in quarters|"
                           r"terminat(?:e|ing|ed)|command is terminated)\b", re.I)),
    ("on scene", re.compile(r"\b(?:on scene|on location|arriv(?:ed|ing)|at scene|on the scene)\b", re.I)),
    ("en route", re.compile(r"\b(?:en ?route|in ?route|on route|responding)\b", re.I)),
]
# When was a unit last seen, and is a call still open
ACTIVE_HOURS = 3
STALE_MIN = 90          # a call with no unit traffic for this long is treated as done

_UNIT = re.compile(r"\b(engine|ladder|truck|tower|medic|squad|rescue|tanker|brush|grass|battalion|chief|utility|"
                   r"ambulance|safety|edo|car|boat)[\s,]*(\d{2,4}[a-z]?)\b", re.I)
# A status report that names the unit only by number: "Dispatch, 924. 924 is arriving."
_BARE = re.compile(r"(?:^|[.,]\s*)(?:dispatch|control|county|tippecanoe)?,?\s*(\d{3,4})\b(?=[^.]*\b(?:on scene|arriv|"
                   r"en ?route|in ?route|clear|in service|available|on location))", re.I)


def _number(n: str) -> str:
    return re.sub(r"\D", "", n)


def parse(transcript: str) -> list[tuple[str, str, str]]:
    """[(unit label, number, status)] for each unit a status is reported for."""
    out, last = [], []
    for clause in re.split(r"(?<=[.;!?])\s+", transcript or ""):
        units = [(f"{m.group(1).title()} {m.group(2).upper()}", _number(m.group(2))) for m in _UNIT.finditer(clause)]
        if not units:
            units = [(m.group(1), m.group(1)) for m in _BARE.finditer(clause)]
        # "Control, Ladder 72. ... we're back at service.": the status is about the unit named just before
        units = units or last
        last = units
        status = next((name for name, rx in STATUSES if rx.search(clause)), None)
        if not status:
            continue
        for label, num in units:
            if (label, num, status) not in out:
                out.append((label, num, status))
    return out


def same_unit(heard: str, listed: str) -> bool:
    """'72' (shorthand on the air) matches '372' in Hamilton; otherwise numbers must match."""
    if heard == listed:
        return True
    pre = config.PROFILE.unit_shorthand_prefix
    return bool(pre) and len(heard) == 2 and listed == pre + heard


def record(tx, transcript: str, fields: dict, incident_id: int | None):
    """Store the status events in one transmission. A dispatch records 'dispatched'
    for its units; reports are matched to the open incident the unit is on."""
    c = db.conn()
    now = tx["time_ms"]
    events = []
    if fields.get("is_dispatch") and incident_id:
        for u in fields.get("units") or []:
            num = _number(u)
            if num:
                events.append((u, num, "dispatched", incident_id))
    for label, num, status in parse(transcript):
        if any(e[1] == num and e[2] == "dispatched" for e in events):
            continue                      # "be en route" in a tone-out is the dispatch itself
        events.append((label, num, status, find_incident(num, now)))
    for label, num, status, inc in events:
        c.execute("INSERT INTO unit_events(tx_id, time_ms, unit, number, status, incident_id) VALUES (?,?,?,?,?,?)",
                  (tx["id"], now, label, num, status, inc))


def find_incident(number: str, at_ms: int) -> int | None:
    """The most recent incident in the last few hours that this unit was sent to."""
    since = at_ms - ACTIVE_HOURS * 3_600_000
    for row in db.conn().execute("SELECT id, units FROM incidents WHERE first_ms BETWEEN ? AND ? "
                                 "ORDER BY first_ms DESC", (since, at_ms + 60_000)):
        for u in json.loads(row["units"] or "[]"):
            if same_unit(number, _number(u)):
                return row["id"]
    return None


def timeline(incident_id: int) -> list[dict]:
    return [dict(r) for r in db.conn().execute(
        "SELECT time_ms, unit, status FROM unit_events WHERE incident_id=? ORDER BY time_ms", (incident_id,))]


def active(now_ms: int | None = None) -> list[dict]:
    """Open incidents: dispatched in the last few hours, not every unit cleared, and
    some unit traffic recently. Each comes with every unit's latest status."""
    now_ms = now_ms or int(time.time() * 1000)
    c = db.conn()
    out = []
    for inc in c.execute("SELECT id, first_ms, last_ms, nature, category, matched, address, place, agencies, units, "
                         "lat, lon FROM incidents WHERE first_ms >= ? ORDER BY first_ms DESC",
                         (now_ms - ACTIVE_HOURS * 3_600_000,)):
        latest: dict[str, dict] = {}
        for u in json.loads(inc["units"] or "[]"):
            latest[u] = {"unit": u, "status": "dispatched", "since_ms": inc["first_ms"]}
        last_activity = inc["last_ms"]
        for ev in c.execute("SELECT time_ms, unit, number, status FROM unit_events WHERE incident_id=? "
                            "ORDER BY time_ms", (inc["id"],)):
            key = next((u for u in latest if same_unit(ev["number"], _number(u))), ev["unit"])
            latest[key] = {"unit": key, "status": ev["status"], "since_ms": ev["time_ms"]}
            last_activity = max(last_activity, ev["time_ms"])
        units = list(latest.values())
        done = units and all(u["status"] in ("cleared", "out of service") for u in units)
        if done or now_ms - last_activity > STALE_MIN * 60_000:
            continue
        d = dict(inc)
        d["agencies"] = json.loads(d["agencies"])
        d["units"] = units
        d["last_activity_ms"] = last_activity
        out.append(d)
    return out

"""Corrections made in the dashboard, and what the app learns from them.

When someone fixes an incident (call type, location, departments) the fix is
applied right away, locked against later automatic changes, and kept so that:

  * a corrected location is remembered for the address and place names heard
    on that call, per department; the next call with the same (mis)heard
    address or place goes straight there
  * a corrected call type becomes a worked example the LLM sees for similar
    transcripts
  * "heard → actually" word fixes ("Tablix" → "Talblick") are applied to every
    new transcript before anything else reads it

Corrections are stored against the call's transmissions, not the incident id,
so `reprocess` (which rebuilds incidents) re-applies them.
"""

from __future__ import annotations

import json
import re
import threading
import time

from . import config, db
from .taxonomy import ALL_NATURES, category_for

MANUAL = 1_000_000          # learned_places.n for locations a person set (never overridden automatically)

_cache: dict = {"at": 0.0, "fixes": []}
_lock = threading.Lock()


# ─────────────────────────────────────────────
# Word fixes
# ─────────────────────────────────────────────
def word_fixes() -> list[tuple[re.Pattern, str]]:
    with _lock:
        if time.time() - _cache["at"] > 30:
            try:
                rows = db.conn().execute("SELECT heard, actual FROM word_fixes").fetchall()
            except Exception:
                rows = []
            _cache["fixes"] = [(re.compile(r"(?<![\w])" + re.escape(h) + r"(?![\w])", re.I), a) for h, a in rows]
            _cache["at"] = time.time()
        return _cache["fixes"]


def apply_word_fixes(text: str) -> str:
    for rx, actual in word_fixes():
        text = rx.sub(actual, text)
    return text


# ─────────────────────────────────────────────
# Worked examples for the LLM
# ─────────────────────────────────────────────
def _tokens(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", (t or "").lower())}


def examples_for(transcript: str, k: int = 3) -> list[tuple[str, dict]]:
    """Corrected call types most similar to this transcript (by shared words)."""
    try:
        rows = db.conn().execute("SELECT transcript, extracted FROM corrections WHERE field='nature' "
                                 "AND transcript IS NOT NULL ORDER BY created_ms DESC LIMIT 300").fetchall()
    except Exception:
        return []
    mine = _tokens(transcript)
    if not mine:
        return []
    scored = []
    for t, ex in rows:
        theirs = _tokens(t)
        overlap = len(mine & theirs) / (len(mine | theirs) or 1)
        if overlap >= 0.15:
            scored.append((overlap, t, json.loads(ex)))
    scored.sort(key=lambda x: -x[0])
    return [(t, ex) for _, t, ex in scored[:k]]


# ─────────────────────────────────────────────
# Applying corrections
# ─────────────────────────────────────────────
def addr_key(address: str | None) -> str:
    from .geo import clean_address
    a = clean_address(address or "").lower()
    return "addr:" + re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", a)).strip() if a.strip() else ""


def correct(inc_id: int, body: dict) -> list[str]:
    """Apply a correction from the dashboard. Returns the fields changed.
    body: {nature, address, lat, lon, departments, word_fixes: [{heard, actual}]}"""
    from . import pipeline
    c = db.conn()
    row = c.execute("SELECT * FROM incidents WHERE id=?", (inc_id,)).fetchone()
    if not row:
        raise LookupError("No such incident")
    txs = c.execute("SELECT id, transcript, extracted FROM transmissions WHERE incident_id=? ORDER BY time_ms",
                    (inc_id,)).fetchall()
    tx_ids = [t["id"] for t in txs]
    now = int(time.time() * 1000)
    changed = []

    for wf in body.get("word_fixes") or []:
        heard, actual = (wf.get("heard") or "").strip(), (wf.get("actual") or "").strip()
        if heard and actual and heard.lower() != actual.lower():
            if len(heard) < 3:
                raise ValueError("A misheard word needs at least 3 letters")
            c.execute("INSERT OR REPLACE INTO word_fixes(heard, actual, created_ms) VALUES (?,?,?)",
                      (heard, actual, now))
            changed.append("word_fixes")
    if "word_fixes" in changed:
        _cache["at"] = 0.0

    if body.get("nature"):
        nature = body["nature"]
        if nature not in ALL_NATURES:
            raise ValueError(f"Unknown call type {nature!r}")
        changed.append("nature")
        for t in txs:
            ex = json.loads(t["extracted"] or "{}")
            if not ex.get("is_dispatch"):
                continue
            ex.update(nature=nature, is_dispatch=True)
            keep = {k: ex.get(k) for k in ("is_dispatch", "address", "place", "nature", "units", "patient_age",
                                           "patient_sex", "summary")}
            c.execute("INSERT INTO corrections(tx_ids, field, value, transcript, extracted, created_ms) "
                      "VALUES (?,?,?,?,?,?)", (json.dumps(tx_ids), "nature", json.dumps(nature), t["transcript"],
                                               json.dumps(keep), now))
        _record(c, tx_ids, "nature", nature, now)

    if body.get("departments") is not None:
        depts = [d for d in body["departments"] if isinstance(d, str) and d.strip()]
        if not depts:
            raise ValueError("Pick at least one department")
        changed.append("departments")
        _record(c, tx_ids, "departments", depts, now)

    loc = None
    if body.get("lat") is not None and body.get("lon") is not None:
        lat, lon = float(body["lat"]), float(body["lon"])
        from .geo import in_county
        if not in_county(lat, lon):
            raise ValueError("That point is outside the county")
        loc = {"lat": lat, "lon": lon, "matched": (body.get("address") or "").strip() or "Pinned on map",
               "precision": "address" if (body.get("address") or "").strip() else "place", "zip": None}
    elif (body.get("address") or "").strip():
        agencies = json.loads(row["agencies"])
        town, anchors = pipeline._department_towns(agencies)
        g = pipeline.geocoder().geocode(body["address"].strip(), None, town or config.PROFILE.name, anchors, [],
                                        fresh=True)
        if not g:
            raise ValueError("Couldn't find that address. Use \"Pick on map\" instead.")
        loc = {"lat": g.lat, "lon": g.lon, "matched": g.matched, "precision": g.precision, "zip": g.zip}
    if loc:
        changed.append("location")
        _record(c, tx_ids, "location", loc, now)
        _learn_location(c, row, txs, loc, now)

    if changed:
        apply_to_incident(inc_id)
    return changed


def _record(c, tx_ids, field, value, now):
    """One active correction per field per call: a newer one replaces the older."""
    key = json.dumps(sorted(tx_ids))
    c.execute("DELETE FROM corrections WHERE field=? AND tx_ids=? AND transcript IS NULL", (field, key))
    c.execute("INSERT INTO corrections(tx_ids, field, value, created_ms) VALUES (?,?,?,?)",
              (key, field, json.dumps(value), now))


def _learn_location(c, row, txs, loc, now):
    """Remember the corrected spot for every address and place name heard on this call."""
    from .geo import learn_key
    names = set()
    if row["place"]:
        names.add(learn_key(row["place"]))
    for t in txs:
        ex = json.loads(t["extracted"] or "{}")
        if ex.get("place"):
            names.add(learn_key(ex["place"]))
        if ex.get("address"):
            names.add(addr_key(ex["address"]))
    if row["address"]:
        names.add(addr_key(row["address"]))
    names.discard("")
    for dept in json.loads(row["agencies"]):
        for name in names:
            c.execute("INSERT OR REPLACE INTO learned_places(name, dept, lat, lon, matched, n, updated_ms) "
                      "VALUES (?,?,?,?,?,?,?)", (name, dept, loc["lat"], loc["lon"], loc["matched"], MANUAL, now))


def apply_to_incident(inc_id: int):
    """Re-apply every stored correction that belongs to this incident's transmissions."""
    c = db.conn()
    tx_ids = {r[0] for r in c.execute("SELECT id FROM transmissions WHERE incident_id=?", (inc_id,))}
    if not tx_ids:
        return
    fields = {}
    for field, value, ids in c.execute("SELECT field, value, tx_ids FROM corrections WHERE transcript IS NULL "
                                       "ORDER BY created_ms"):
        if tx_ids & set(json.loads(ids)):
            fields[field] = json.loads(value)        # newest wins
    if not fields:
        return
    upd = {}
    if "nature" in fields:
        upd.update(nature=fields["nature"], category=category_for(fields["nature"]))
    if "departments" in fields:
        upd["agencies"] = json.dumps(fields["departments"])
    if "location" in fields:
        loc = fields["location"]
        upd.update(lat=loc["lat"], lon=loc["lon"], matched=loc["matched"], precision=loc["precision"],
                   zip=loc.get("zip"), geo_source="manual", confidence="confirmed")
    upd["corrected"] = ",".join(sorted(fields))
    c.execute(f"UPDATE incidents SET {', '.join(f'{k}=?' for k in upd)} WHERE id=?", (*upd.values(), inc_id))


def reapply_all():
    """After `reprocess` rebuilt the incidents."""
    c = db.conn()
    ids = set()
    for (tx_ids,) in c.execute("SELECT tx_ids FROM corrections WHERE transcript IS NULL"):
        for tx in json.loads(tx_ids):
            r = c.execute("SELECT incident_id FROM transmissions WHERE id=?", (tx,)).fetchone()
            if r and r[0]:
                ids.add(r[0])
    for inc in ids:
        apply_to_incident(inc)
    return len(ids)


# ─────────────────────────────────────────────
# What it learned (for the Settings panel)
# ─────────────────────────────────────────────
def learned() -> dict:
    c = db.conn()
    return {
        "word_fixes": [dict(r) for r in c.execute("SELECT heard, actual, created_ms FROM word_fixes ORDER BY heard")],
        "locations": [dict(r) for r in c.execute(
            "SELECT name, dept, matched, updated_ms FROM learned_places WHERE n >= ? ORDER BY updated_ms DESC",
            (MANUAL,))],
        "call_types": c.execute("SELECT COUNT(*) FROM corrections WHERE field='nature' AND transcript IS NOT NULL"
                                ).fetchone()[0],
    }


def forget(kind: str, key: str, dept: str | None = None) -> bool:
    c = db.conn()
    if kind == "word":
        n = c.execute("DELETE FROM word_fixes WHERE heard=?", (key,)).rowcount
        _cache["at"] = 0.0
    elif kind == "location":
        n = c.execute("DELETE FROM learned_places WHERE name=? AND dept=? AND n >= ?", (key, dept, MANUAL)).rowcount
    else:
        raise ValueError("kind must be word or location")
    return bool(n)

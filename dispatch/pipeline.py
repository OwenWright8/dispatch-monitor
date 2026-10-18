"""Poller, worker, and incident grouping.

The transmissions table doubles as a durable work queue: the poller inserts
rows as 'pending', the worker processes them newest-first, so live calls are
handled immediately and history backfills in the gaps. A restart loses nothing.
"""

from __future__ import annotations

import difflib
import json
import logging
import math
import os
import re
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import config, db, extract, openmhz, transcribe
from .geo import Geocoder, GeoResult
from .taxonomy import category_for

log = logging.getLogger(__name__)
TZ = ZoneInfo(config.TIMEZONE)
PRECISION_RANK = {"address": 4, "intersection": 3, "place": 2, "street": 1, None: 0}

status = {
    "last_poll": None, "last_poll_error": None, "llm_ok": None, "llm_error": None,
    "last_processed": None, "started": time.time(),
}

_geocoder: Geocoder | None = None


def geocoder() -> Geocoder:
    global _geocoder
    if _geocoder is None:
        _geocoder = Geocoder(db.conn)
    return _geocoder


# ─────────────────────────────────────────────
# Poller
# ─────────────────────────────────────────────
def _insert(calls: list[dict]) -> int:
    c = db.conn()
    n = 0
    for call in calls:
        tg = call.get("talkgroupNum")
        ch = config.CHANNELS.get(tg)
        if ch is None:
            continue
        cur = c.execute(
            "INSERT OR IGNORE INTO transmissions(id, talkgroup, agency, time_ms, len_s, url, status) "
            "VALUES (?,?,?,?,?,?,?)",
            (call["_id"], tg, ch.agency, openmhz.to_ms(call["time"]), call.get("len"), call.get("url"),
             "pending" if (call.get("len") or 0) >= config.MIN_CALL_SECONDS else "short"))
        n += cur.rowcount
    return n


def _page_down(start_batch: list[dict], floor_ms: int, max_pages: int = 2000) -> int:
    """Insert start_batch, then page older until reaching floor_ms."""
    added = _insert(start_batch)
    batch = start_batch
    for _ in range(max_pages):
        if not batch:
            break
        oldest = min(openmhz.to_ms(c["time"]) for c in batch)
        if oldest <= floor_ms:
            break
        time.sleep(1.0)          # be polite to a free community service
        batch = openmhz.older(oldest)
        added += _insert(batch)
    return added


def poll_once():
    c = db.conn()
    newest = c.execute("SELECT MAX(time_ms) FROM transmissions").fetchone()[0]
    now_ms = int(time.time() * 1000)
    floor = newest if newest else now_ms - int(config.BACKFILL_HOURS * 3600_000)
    added = _page_down(openmhz.latest(), floor)
    status["last_poll"] = time.time()
    status["last_poll_error"] = None
    if added:
        log.info(f"Queued {added} new transmission(s)")


def extend_history():
    """If BACKFILL_HOURS reaches further back than what we hold, fetch the rest."""
    c = db.conn()
    oldest = c.execute("SELECT MIN(time_ms) FROM transmissions").fetchone()[0]
    if not oldest:
        return
    floor = int(time.time() * 1000) - int(config.BACKFILL_HOURS * 3600_000)
    if oldest - floor > 3600_000 and db.get_state("history_exhausted_ms") != str(oldest):
        log.info(f"Backfilling history back to {datetime.fromtimestamp(floor / 1000)}")
        batch = openmhz.older(oldest)
        if not batch:
            db.set_state("history_exhausted_ms", oldest)   # OpenMHz retention reached
            return
        n = _page_down(batch, floor)
        log.info(f"Backfill queued {n} transmission(s)")


def poller_loop(stop: threading.Event):
    try:
        poll_once()
        extend_history()
    except Exception as e:
        log.error(f"Initial poll failed: {e}")
        status["last_poll_error"] = str(e)
    while not stop.wait(config.POLL_INTERVAL_S):
        try:
            poll_once()
        except Exception as e:
            status["last_poll_error"] = str(e)
            log.error(f"Poll failed: {e}")


# ─────────────────────────────────────────────
# Incident grouping
# ─────────────────────────────────────────────
def _local(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(TZ)


def _dist_m(a, b) -> float:
    dy = (a[0] - b[0]) * 111_000
    dx = (a[1] - b[1]) * 111_000 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


def _norm_addr(a: str | None) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (a or "").lower()).strip()


def _same_incident(row, fields, geo: GeoResult | None) -> bool:
    if geo and row["lat"] is not None and _dist_m((geo.lat, geo.lon), (row["lat"], row["lon"])) < 150:
        return True
    a, b = _norm_addr(fields.get("address")), _norm_addr(row["address"])
    if a and b:
        if a == b:
            return True
        na, nb = a.split()[0], b.split()[0]
        if na.isdigit() and na == nb and difflib.SequenceMatcher(None, a, b).ratio() > 0.6:
            return True
    pa, pb = _norm_addr(fields.get("place")), _norm_addr(row["place"])
    return bool(pa and pa == pb)


def attach(time_ms: int, agency: str, fields: dict, geo: GeoResult | None, method: str) -> int | None:
    if not fields.get("is_dispatch") or not (fields.get("address") or fields.get("place")):
        return None
    c = db.conn()
    window = config.INCIDENT_WINDOW_MIN * 60_000
    nature = fields.get("nature") or "Other"
    rows = c.execute("SELECT * FROM incidents WHERE first_ms BETWEEN ? AND ? ORDER BY ABS(first_ms - ?)",
                     (time_ms - window, time_ms + window, time_ms)).fetchall()
    for row in rows:
        if not _same_incident(row, fields, geo):
            continue
        agencies = sorted(set(json.loads(row["agencies"])) | {agency})
        units = list(dict.fromkeys(json.loads(row["units"]) + fields.get("units", [])))
        upd = {
            "first_ms": min(row["first_ms"], time_ms), "last_ms": max(row["last_ms"], time_ms),
            "agencies": json.dumps(agencies), "units": json.dumps(units), "n_tx": row["n_tx"] + 1,
            "address": row["address"] or fields.get("address"), "place": row["place"] or fields.get("place"),
            "patient_age": row["patient_age"] or fields.get("patient_age"),
            "patient_sex": row["patient_sex"] or fields.get("patient_sex"),
            "summary": row["summary"] or fields.get("summary"),
        }
        if row["nature"].startswith("Other") and not nature.startswith("Other"):
            upd["nature"], upd["category"] = nature, category_for(nature)
        if geo and PRECISION_RANK.get(geo.precision, 0) > PRECISION_RANK.get(row["precision"], 0):
            upd.update(lat=geo.lat, lon=geo.lon, precision=geo.precision, matched=geo.matched,
                       zip=geo.zip, geo_source=geo.source)
        lt = _local(upd["first_ms"])
        upd.update(local_date=lt.strftime("%Y-%m-%d"), local_hour=lt.hour, local_dow=lt.weekday())
        c.execute(f"UPDATE incidents SET {', '.join(f'{k}=?' for k in upd)} WHERE id=?",
                  (*upd.values(), row["id"]))
        return row["id"]
    lt = _local(time_ms)
    cur = c.execute(
        "INSERT INTO incidents(first_ms, last_ms, local_date, local_hour, local_dow, address, place, matched, "
        "lat, lon, precision, geo_source, zip, category, nature, agencies, units, patient_age, patient_sex, "
        "summary, n_tx, method) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)",
        (time_ms, time_ms, lt.strftime("%Y-%m-%d"), lt.hour, lt.weekday(), fields.get("address"),
         fields.get("place"), geo.matched if geo else None, geo.lat if geo else None, geo.lon if geo else None,
         geo.precision if geo else None, geo.source if geo else None, geo.zip if geo else None,
         category_for(nature), nature, json.dumps([agency]), json.dumps(fields.get("units", [])),
         fields.get("patient_age"), fields.get("patient_sex"), fields.get("summary"), method))
    return cur.lastrowid


# ─────────────────────────────────────────────
# Worker
# ─────────────────────────────────────────────
_llm_warned = 0.0


def _extract(transcript: str) -> tuple[dict, str]:
    global _llm_warned
    try:
        fields, method = extract.extract(transcript, use_llm=True)
        if method == "llm":
            status["llm_ok"], status["llm_error"] = True, None
        return fields, method
    except Exception as e:     # Ollama down / model missing / bad JSON: degrade to rules
        status["llm_ok"], status["llm_error"] = False, str(e)[:300]
        if time.time() - _llm_warned > 300:
            log.warning(f"LLM extraction unavailable ({e}); using keyword rules")
            _llm_warned = time.time()
        return extract.extract(transcript, use_llm=False)


def _archive_path(tx) -> str:
    d = _local(tx["time_ms"])
    p = config.AUDIO_DIR / d.strftime("%Y/%m/%d")
    p.mkdir(parents=True, exist_ok=True)
    return str(p / f"{tx['talkgroup']}-{tx['id']}.m4a")


def handle_transcript(tx, transcript: str) -> tuple[dict, str, int | None]:
    ch = config.CHANNELS.get(tx["talkgroup"])
    fields, method = _extract(transcript)
    geo = None
    if fields["is_dispatch"] and (fields["address"] or fields["place"]):
        geo = geocoder().geocode(fields["address"], fields["place"], ch.city_hint if ch else "Lafayette")
    inc = attach(tx["time_ms"], tx["agency"], fields, geo, method)
    return fields, method, inc


def process(tx):
    c = db.conn()
    fd, tmp = tempfile.mkstemp(suffix=".m4a")
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(openmhz.download(tx["url"]))
        transcript = transcribe.transcribe(tmp)
        fields, method, inc = handle_transcript(tx, transcript)
        audio_path = None
        if inc and config.KEEP_AUDIO:
            audio_path = _archive_path(tx)
            shutil.move(tmp, audio_path)
        c.execute("UPDATE transmissions SET status='done', transcript=?, method=?, extracted=?, is_dispatch=?, "
                  "incident_id=?, audio_path=?, error=NULL WHERE id=?",
                  (transcript, method, json.dumps(fields), int(bool(fields["is_dispatch"])), inc, audio_path, tx["id"]))
        if inc:
            r = c.execute("SELECT nature, matched, address, place, precision FROM incidents WHERE id=?", (inc,)).fetchone()
            log.info(f"[{tx['agency']}] incident #{inc}: {r['nature']} @ {r['matched'] or r['address'] or r['place']}"
                     f" ({r['precision'] or 'not mapped'})")
        status["last_processed"] = time.time()
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def worker_loop(stop: threading.Event):
    c = db.conn()
    geocoder()    # build/load street index up front
    if config.LLM_ENABLED:
        status["llm_ok"] = extract.ensure_model() or None
    while not stop.is_set():
        tx = c.execute("SELECT * FROM transmissions WHERE status='pending' AND attempts < 4 "
                       "ORDER BY time_ms DESC LIMIT 1").fetchone()
        if tx is None:
            stop.wait(2)
            continue
        c.execute("UPDATE transmissions SET attempts = attempts + 1 WHERE id=?", (tx["id"],))
        try:
            process(tx)
        except Exception as e:
            log.warning(f"Transmission {tx['id']} failed: {e}")
            c.execute("UPDATE transmissions SET error=?, status=CASE WHEN attempts >= 4 THEN 'error' ELSE status END "
                      "WHERE id=?", (str(e)[:500], tx["id"]))
            stop.wait(5)


def reprocess(use_llm: bool = True):
    """Rebuild every incident from stored transcripts (after prompt/taxonomy/geocoder changes)."""
    c = db.conn()
    c.execute("DELETE FROM incidents")
    c.execute("DELETE FROM geocache")      # geocoder may have improved since these were cached
    c.execute("UPDATE transmissions SET incident_id=NULL")
    rows = c.execute("SELECT * FROM transmissions WHERE status='done' AND transcript IS NOT NULL "
                     "ORDER BY time_ms").fetchall()
    log.info(f"Re-extracting {len(rows)} transmissions")
    for i, tx in enumerate(rows, 1):
        if use_llm:
            fields, method, inc = handle_transcript(tx, tx["transcript"])
        else:
            ch = config.CHANNELS.get(tx["talkgroup"])
            fields, method = extract.extract(tx["transcript"], use_llm=False)
            geo = geocoder().geocode(fields["address"], fields["place"], ch.city_hint if ch else "Lafayette") \
                if fields["is_dispatch"] else None
            inc = attach(tx["time_ms"], tx["agency"], fields, geo, method)
        c.execute("UPDATE transmissions SET method=?, extracted=?, is_dispatch=?, incident_id=? WHERE id=?",
                  (method, json.dumps(fields), int(bool(fields["is_dispatch"])), inc, tx["id"]))
        if i % 50 == 0:
            log.info(f"  {i}/{len(rows)}")
    log.info(f"Done: {c.execute('SELECT COUNT(*) FROM incidents').fetchone()[0]} incidents")

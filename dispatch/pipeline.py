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

from . import config, db, departments, extract, openmhz, settings, transcribe
from .geo import Geocoder, GeoResult, Hints, parse_cross_streets
from .taxonomy import category_for

log = logging.getLogger(__name__)
TZ = ZoneInfo(config.TIMEZONE)
PRECISION_RANK = {"address": 4, "intersection": 3, "place": 2, "street": 1, None: 0}

status = {
    "last_poll": None, "last_poll_error": None, "poll_failures": 0, "next_poll": None,
    "llm_ok": None, "llm_error": None,
    "last_processed": None, "started": time.time(),
    "backfill": {"running": False, "hours": 0, "queued": 0, "error": None},
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
def _insert(calls: list[dict], min_ms: int = 0) -> int:
    c = db.conn()
    n = 0
    for call in calls:
        tg = call.get("talkgroupNum")
        ch = settings.enabled_channels().get(tg)
        t_ms = openmhz.to_ms(call["time"])
        if ch is None or t_ms < min_ms:
            continue
        cur = c.execute(
            "INSERT OR IGNORE INTO transmissions(id, talkgroup, agency, time_ms, len_s, url, status) "
            "VALUES (?,?,?,?,?,?,?)",
            (call["_id"], tg, ch.agency, t_ms, call.get("len"), call.get("url"),
             "pending" if (call.get("len") or 0) >= settings.get("MIN_CALL_SECONDS") else "short"))
        n += cur.rowcount
    return n


def _page_down(start_batch: list[dict], floor_ms: int, min_ms: int = 0, max_pages: int = 2000) -> int:
    """Insert start_batch, then page older until reaching floor_ms (skipping calls before min_ms)."""
    added = _insert(start_batch, min_ms)
    batch = start_batch
    for _ in range(max_pages):
        if not batch:
            break
        oldest = min(openmhz.to_ms(c["time"]) for c in batch)
        if oldest <= floor_ms:
            break
        time.sleep(1.0)          # be polite to a free community service
        batch = openmhz.older(oldest)
        added += _insert(batch, min_ms)
    return added


def poll_once():
    c = db.conn()
    newest = c.execute("SELECT MAX(time_ms) FROM transmissions").fetchone()[0]
    now_ms = int(time.time() * 1000)
    if newest:
        added = _page_down(openmhz.latest(), newest)      # fills any gap since the last poll
    else:                                                 # fresh install: live only; history is backfill's job
        recent = now_ms - 15 * 60_000
        added = _insert(openmhz.latest(), min_ms=recent)
    status["last_poll"] = time.time()
    status["last_poll_error"] = None
    if added:
        log.info(f"Queued {added} new transmission(s)")


_backfill_lock = threading.Lock()


def backfill(hours: float, _locked: bool = False) -> int:
    """Queue every transmission from the last `hours` that we don't have yet.

    Runs beside the live poller, and the worker always takes the newest clip
    first, so live calls never wait behind history."""
    if not _locked and not _backfill_lock.acquire(blocking=False):
        return 0                                   # one at a time
    b = status["backfill"]
    try:
        floor = int((time.time() - hours * 3600) * 1000)
        b.update(running=True, hours=hours, queued=0, error=None)
        oldest = db.conn().execute("SELECT MIN(time_ms) FROM transmissions").fetchone()[0]
        start = oldest or int(time.time() * 1000)
        if start <= floor:
            return 0                               # already have that much history
        log.info(f"Backfilling the last {hours:g} h (back to {datetime.fromtimestamp(floor / 1000):%Y-%m-%d %H:%M})")
        n = _page_down(openmhz.older(start), floor, min_ms=floor)
        b["queued"] = n
        log.info(f"Backfill queued {n} transmission(s); live calls are still processed first")
        return n
    except Exception as e:
        b["error"] = str(e)[:300]
        log.error(f"Backfill failed: {e}")
        return 0
    finally:
        b["running"] = False
        _backfill_lock.release()


def start_backfill(hours: float) -> bool:
    """Start a backfill in the background; False if one is already running."""
    # Calls older than the detail retention would only be deleted again.
    hours = min(hours, settings.get("DETAIL_RETENTION_DAYS") * 24)
    if not _backfill_lock.acquire(blocking=False):
        return False
    status["backfill"]["running"] = True
    threading.Thread(target=backfill, args=(hours, True), name="backfill", daemon=True).start()
    return True


def poller_loop(stop: threading.Event):
    """Polls for as long as the app runs. Failures never stop it: it backs off
    (up to 5 minutes), the OpenMHz client switches session and browser identity,
    and a backfill that failed is retried once the feed works again."""
    backfill_due = config.BACKFILL_HOURS > 0
    last_backfill_retry = 0.0
    failures = 0
    while True:
        try:
            poll_once()
            if failures:
                log.info(f"Feed recovered after {failures} failed poll(s) (identity {openmhz.identity()})")
            failures = 0
            bf = status["backfill"]
            if backfill_due:
                backfill_due = not start_backfill(config.BACKFILL_HOURS)   # in its own thread
            elif bf["error"] and not bf["running"] and time.time() - last_backfill_retry > 1800:
                last_backfill_retry = time.time()
                log.info("Retrying the backfill that failed earlier")
                start_backfill(bf["hours"] or config.BACKFILL_HOURS)
        except Exception as e:
            failures += 1
            status["last_poll_error"] = str(e)[:300]
            (log.warning if failures < 5 else log.error)(f"Poll failed ({failures} in a row): {e}")
        status["poll_failures"] = failures
        interval = settings.get("POLL_INTERVAL_S")
        delay = interval if not failures else min(interval * 2 ** min(failures, 6), 300)
        status["next_poll"] = time.time() + delay
        if stop.wait(delay):
            return


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


# Channel labels used when a shared county channel names no department; dropped
# as soon as a transmission identifies the real one.
_PLACEHOLDERS = {ch.agency for ch in config.CHANNELS.values() if not ch.default_department}
# A later transmission saying it's an actual fire beats the alarm it started as.
_ESCALATES = {"Fire Alarm": {"Structure Fire"}, "Smoke Investigation": {"Structure Fire", "Other Fire",
              "Vehicle Fire", "Brush / Grass Fire", "Outside / Trash Fire"}, "Crash": {"Crash with Injuries",
              "Crash with Entrapment", "Pedestrian / Cyclist Struck"}, "Crash with Injuries": {"Crash with Entrapment"}}


def _merge_agencies(old: list[str], new: list[str]) -> list[str]:
    out = list(dict.fromkeys(old + new))
    real = [a for a in out if a not in _PLACEHOLDERS]
    return real or out


def attach(time_ms: int, depts: list[str], fields: dict, geo: GeoResult | None, method: str) -> int | None:
    if not fields.get("is_dispatch") or not (fields.get("address") or fields.get("place")):
        return None
    c = db.conn()
    window = settings.get("INCIDENT_WINDOW_MIN") * 60_000
    nature = fields.get("nature") or "Other"
    rows = c.execute("SELECT * FROM incidents WHERE first_ms BETWEEN ? AND ? ORDER BY ABS(first_ms - ?)",
                     (time_ms - window, time_ms + window, time_ms)).fetchall()
    for row in rows:
        if not _same_incident(row, fields, geo):
            continue
        agencies = _merge_agencies(json.loads(row["agencies"]), depts)
        units = list(dict.fromkeys(json.loads(row["units"]) + fields.get("units", [])))
        upd = {
            "first_ms": min(row["first_ms"], time_ms), "last_ms": max(row["last_ms"], time_ms),
            "agencies": json.dumps(agencies), "units": json.dumps(units), "n_tx": row["n_tx"] + 1,
            "address": row["address"] or fields.get("address"), "place": row["place"] or fields.get("place"),
            "patient_age": row["patient_age"] or fields.get("patient_age"),
            "patient_sex": row["patient_sex"] or fields.get("patient_sex"),
            "summary": row["summary"] or fields.get("summary"),
        }
        if (row["nature"].startswith("Other") and not nature.startswith("Other")
                or nature in _ESCALATES.get(row["nature"], ())):
            upd["nature"], upd["category"] = nature, category_for(nature)
        if geo and PRECISION_RANK.get(geo.precision, 0) > PRECISION_RANK.get(row["precision"], 0):
            upd.update(lat=geo.lat, lon=geo.lon, precision=geo.precision, matched=geo.matched,
                       zip=geo.zip, geo_source=geo.source)
        elif geo and row["lat"] is not None and geo.precision == row["precision"]:
            # The pre-alert said "690 Washington Street", the tone-out "690 Waldron Street":
            # keep whichever is clearly in the dispatched departments' area.
            h = Hints(_department_towns(agencies)[1])
            if h.anchors and h.anchor_dist((geo.lat, geo.lon)) + 1_000 < h.anchor_dist((row["lat"], row["lon"])):
                upd.update(lat=geo.lat, lon=geo.lon, matched=geo.matched, zip=geo.zip, geo_source=geo.source,
                           address=fields.get("address") or row["address"])
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
         category_for(nature), nature, json.dumps(_merge_agencies([], depts)), json.dumps(fields.get("units", [])),
         fields.get("patient_age"), fields.get("patient_sex"), fields.get("summary"), method))
    return cur.lastrowid


# ─────────────────────────────────────────────
# Worker
# ─────────────────────────────────────────────
_llm_warned = 0.0
_llm_last_prepare = 0.0


def _set_llm_status(msg: str):
    status["llm_ok"], status["llm_error"] = False, msg


def _prepare_llm(wait_s: int = 0):
    """Wait for Ollama and download the model if needed (shown in the dashboard header)."""
    global _llm_last_prepare
    _llm_last_prepare = time.time()
    if extract.ensure_model(wait_s=wait_s, on_status=_set_llm_status):
        status["llm_ok"], status["llm_error"] = None, None   # becomes True on the first successful call


def llm_settings_changed():
    """Ollama address/model changed in the dashboard: re-check it on the next call."""
    global _llm_last_prepare
    status["llm_ok"], status["llm_error"] = False, "Settings changed; connecting…"
    _llm_last_prepare = 0.0


def _extract(transcript: str) -> tuple[dict, str]:
    global _llm_warned
    # Ollama was down or the model missing: try again every 2 minutes.
    if status["llm_ok"] is False and settings.get("LLM_ENABLED") and time.time() - _llm_last_prepare > 120:
        _prepare_llm()
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


def handle_transcript(tx, transcript: str, use_llm: bool = True) -> tuple[dict, str, int | None]:
    """transcript must already be normalized (departments.normalize)."""
    ch = config.CHANNELS.get(tx["talkgroup"])
    fields, method = _extract(transcript) if use_llm else extract.extract(transcript, use_llm=False)
    fields["departments"] = departments.detect(transcript, fields.get("units"), ch) or [ch.agency if ch else tx["agency"]]
    fields["cross_streets"] = parse_cross_streets(transcript)
    geo = None
    if fields["is_dispatch"] and (fields["address"] or fields["place"]):
        town, anchors = _department_towns(fields["departments"])
        city = town or (ch.city_hint if ch else config.PROFILE.name)
        if not anchors and ch:
            anchors = [c for c in [config.PROFILE.city_centers.get(ch.city_hint.lower())] if c]
        geo = geocoder().geocode(fields["address"], fields["place"], city, anchors, fields["cross_streets"])
    inc = attach(tx["time_ms"], fields["departments"], fields, geo, method)
    return fields, method, inc


def _department_towns(names: list[str]) -> tuple[str | None, list]:
    """Town name and map points of the departments sent: an address heard on a
    Jackson Township call is looked up around Arcadia and Atlanta first."""
    town, anchors = None, []
    for name in names:
        d = config.PROFILE.department(name)
        for t in (d.towns if d else ()):
            c = config.PROFILE.city_centers.get(t)
            if c and c not in [a[:2] for a in anchors]:
                anchors.append((*c, d.reach_km * 1000 if d.reach_km else None))
                town = town or t.title()
    return town, anchors


def process(tx):
    c = db.conn()
    fd, tmp = tempfile.mkstemp(suffix=".m4a")
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(openmhz.download(tx["url"]))
        raw = transcribe.transcribe(tmp)
        transcript = departments.normalize(raw)
        fields, method, inc = handle_transcript(tx, transcript)
        audio_path = None
        if inc and settings.get("KEEP_AUDIO"):
            audio_path = _archive_path(tx)
            shutil.move(tmp, audio_path)
        c.execute("UPDATE transmissions SET status='done', transcript=?, transcript_raw=?, method=?, extracted=?, "
                  "is_dispatch=?, incident_id=?, audio_path=?, error=NULL WHERE id=?",
                  (transcript, raw, method, json.dumps(fields), int(bool(fields["is_dispatch"])), inc, audio_path,
                   tx["id"]))
        if inc:
            r = c.execute("SELECT nature, matched, address, place, precision, agencies FROM incidents WHERE id=?",
                          (inc,)).fetchone()
            log.info(f"[{', '.join(json.loads(r['agencies']))}] incident #{inc}: {r['nature']} @ "
                     f"{r['matched'] or r['address'] or r['place']} ({r['precision'] or 'not mapped'})")
        status["last_processed"] = time.time()
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def worker_loop(stop: threading.Event):
    c = db.conn()
    geocoder()    # build/load street index up front
    if settings.get("LLM_ENABLED"):
        _prepare_llm(wait_s=300)
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


# ─────────────────────────────────────────────
# Retention
# ─────────────────────────────────────────────
def housekeeping() -> dict:
    """Apply the retention settings: delete audio clips, then call details
    (transcripts, summaries, patient info, units), then whole incidents."""
    c = db.conn()
    now = time.time() * 1000
    audio_cut = now - settings.get("AUDIO_RETENTION_HOURS") * 3_600_000
    detail_cut = now - settings.get("DETAIL_RETENTION_DAYS") * 86_400_000
    incident_cut = now - settings.get("INCIDENT_RETENTION_DAYS") * 86_400_000
    done = {"audio": 0, "transmissions": 0, "details": 0, "incidents": 0}

    for tx_id, path in c.execute("SELECT id, audio_path FROM transmissions WHERE audio_path IS NOT NULL "
                                 "AND (time_ms < ? OR time_ms < ?)", (audio_cut, detail_cut)).fetchall():
        try:
            os.remove(path)
        except OSError:
            pass
        c.execute("UPDATE transmissions SET audio_path=NULL WHERE id=?", (tx_id,))
        done["audio"] += 1
    # Clips no row points at any more (e.g. from before this ran), by file age
    if config.AUDIO_DIR.exists():
        for f in config.AUDIO_DIR.rglob("*.m4a"):
            try:
                if f.stat().st_mtime * 1000 < audio_cut:
                    f.unlink()
                    done["audio"] += 1
            except OSError:
                pass
        for d in sorted((p for p in config.AUDIO_DIR.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
            try:
                d.rmdir()                       # only succeeds when empty
            except OSError:
                pass

    done["transmissions"] = c.execute("DELETE FROM transmissions WHERE time_ms < ?", (detail_cut,)).rowcount
    done["details"] = c.execute(
        "UPDATE incidents SET summary=NULL, patient_age=NULL, patient_sex=NULL, units='[]' WHERE last_ms < ? "
        "AND (summary IS NOT NULL OR patient_age IS NOT NULL OR patient_sex IS NOT NULL OR units <> '[]')",
        (detail_cut,)).rowcount
    done["incidents"] = c.execute("DELETE FROM incidents WHERE first_ms < ?", (incident_cut,)).rowcount
    if any(done.values()):
        log.info("Retention: removed {audio} audio clips, {transmissions} transmissions, details of {details} "
                 "incidents, {incidents} incidents".format(**done))
    return done


def housekeeping_loop(stop: threading.Event):
    while not stop.is_set():
        try:
            housekeeping()
        except Exception as e:
            log.warning(f"Retention cleanup failed: {e}")
        stop.wait(900)


def reprocess(use_llm: bool = True):
    """Rebuild incidents from stored transcripts (after prompt/taxonomy/geocoder changes).

    Transcripts are only kept for DETAIL_RETENTION_DAYS, so only incidents from
    that period are rebuilt; older incidents (type, location, departments) stay."""
    c = db.conn()
    oldest = c.execute("SELECT MIN(time_ms) FROM transmissions WHERE status='done' AND transcript IS NOT NULL"
                       ).fetchone()[0]
    if oldest is None:
        log.info("No stored transcripts to reprocess")
        return
    c.execute("DELETE FROM incidents WHERE last_ms >= ?", (oldest,))
    c.execute("DELETE FROM geocache")      # geocoder may have improved since these were cached
    c.execute("UPDATE transmissions SET incident_id=NULL")
    rows = c.execute("SELECT * FROM transmissions WHERE status='done' AND transcript IS NOT NULL "
                     "ORDER BY time_ms").fetchall()
    log.info(f"Re-extracting {len(rows)} transmissions")
    for i, tx in enumerate(rows, 1):
        raw = tx["transcript_raw"] or tx["transcript"]       # rows from before transcript_raw existed
        transcript = departments.normalize(raw)
        fields, method, inc = handle_transcript(tx, transcript, use_llm=use_llm)
        c.execute("UPDATE transmissions SET transcript=?, transcript_raw=?, method=?, extracted=?, is_dispatch=?, "
                  "incident_id=? WHERE id=?",
                  (transcript, raw, method, json.dumps(fields), int(bool(fields["is_dispatch"])), inc, tx["id"]))
        if i % 50 == 0:
            log.info(f"  {i}/{len(rows)}")
    log.info(f"Done: {c.execute('SELECT COUNT(*) FROM incidents').fetchone()[0]} incidents")

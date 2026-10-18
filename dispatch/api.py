"""HTTP API + dashboard.

Every list/stats endpoint takes the same filter parameters, so the map, the
feed, and every chart always describe the same set of incidents:

  days      look-back window in days (default 30; 0 = all time)
  start/end explicit window, YYYY-MM-DD local dates (overrides days)
  category  comma list      nature  comma list      agency  comma list
  hours     comma list of local hours 0-23          dows    comma list 0=Mon..6=Sun
  bbox      south,west,north,east (only incidents inside the map view)
  q         free text over address/place/summary/units
"""

from __future__ import annotations

import csv
import io
import json
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, openmhz, pipeline
from .taxonomy import CATEGORIES, TAXONOMY

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="Tippecanoe Dispatch Monitor", docs_url="/api/docs")
app.mount("/lib", StaticFiles(directory=WEB_DIR / "lib"), name="lib")


@app.get("/", include_in_schema=False)
@app.get("/dashboard.html", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "dashboard.html", headers={"Cache-Control": "no-cache"})


# ─────────────────────────────────────────────
# Filters
# ─────────────────────────────────────────────
def _csv(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def where(req: Request) -> tuple[str, list]:
    p = req.query_params
    clauses, args = [], []
    if p.get("start") or p.get("end"):
        if p.get("start"):
            clauses.append("local_date >= ?"); args.append(p["start"])
        if p.get("end"):
            clauses.append("local_date <= ?"); args.append(p["end"])
    else:
        days = float(p.get("days", 30))
        if days > 0:
            clauses.append("first_ms >= ?"); args.append(int((time.time() - days * 86400) * 1000))
    for key, col in (("category", "category"), ("nature", "nature")):
        vals = _csv(p.get(key))
        if vals:
            clauses.append(f"{col} IN ({','.join('?' * len(vals))})"); args += vals
    agencies = _csv(p.get("agency"))
    if agencies:
        clauses.append("(" + " OR ".join("agencies LIKE ?" for _ in agencies) + ")")
        args += [f'%"{a}"%' for a in agencies]
    for key, col in (("hours", "local_hour"), ("dows", "local_dow")):
        vals = [int(v) for v in _csv(p.get(key)) if v.isdigit()]
        if vals:
            clauses.append(f"{col} IN ({','.join('?' * len(vals))})"); args += vals
    bbox = _csv(p.get("bbox"))
    if len(bbox) == 4:
        s, w, n, e = map(float, bbox)
        clauses.append("lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?"); args += [s, n, w, e]
    q = (p.get("q") or "").strip()
    if q:
        for tok in q.split():
            clauses.append("(address LIKE ? OR place LIKE ? OR matched LIKE ? OR summary LIKE ? OR units LIKE ? "
                           "OR nature LIKE ?)")
            args += [f"%{tok}%"] * 6
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", args


def _incident(row) -> dict:
    d = dict(row)
    d["agencies"] = json.loads(d["agencies"])
    d["units"] = json.loads(d["units"])
    return d


# ─────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────
@app.get("/api/meta")
def meta():
    return {"categories": CATEGORIES, "taxonomy": TAXONOMY,
            "agencies": sorted({ch.agency for ch in config.CHANNELS.values()}),
            "channels": [{"talkgroup": ch.talkgroup, "agency": ch.agency} for ch in config.CHANNELS.values()],
            "carto_key": config.CARTO_API_KEY or None}


@app.get("/api/status")
def status():
    c = db.conn()
    q = lambda sql: c.execute(sql).fetchone()[0]
    s = pipeline.status
    return {
        "incidents": q("SELECT COUNT(*) FROM incidents"),
        "mapped": q("SELECT COUNT(*) FROM incidents WHERE lat IS NOT NULL"),
        "transmissions": q("SELECT COUNT(*) FROM transmissions"),
        "queue": q("SELECT COUNT(*) FROM transmissions WHERE status='pending' AND attempts < 4"),
        "errors": q("SELECT COUNT(*) FROM transmissions WHERE status='error'"),
        "oldest_ms": q("SELECT MIN(first_ms) FROM incidents"),
        "newest_ms": q("SELECT MAX(first_ms) FROM incidents"),
        "last_poll": s["last_poll"], "last_poll_error": s["last_poll_error"],
        "last_processed": s["last_processed"],
        "llm": {"enabled": config.LLM_ENABLED, "model": config.OLLAMA_MODEL, "ok": s["llm_ok"], "error": s["llm_error"]},
        "whisper_model": config.WHISPER_MODEL,
        "now": time.time(),
    }


@app.get("/api/incidents")
def incidents(req: Request, limit: int = Query(5000, le=50000)):
    w, a = where(req)
    rows = db.conn().execute(
        f"SELECT id, first_ms, local_date, local_hour, local_dow, address, place, matched, lat, lon, precision, zip, "
        f"category, nature, agencies, units, patient_age, patient_sex, summary, n_tx "
        f"FROM incidents{w} ORDER BY first_ms DESC LIMIT ?", (*a, limit)).fetchall()
    return [_incident(r) for r in rows]


@app.get("/api/incidents/{inc_id}")
def incident(inc_id: int):
    c = db.conn()
    row = c.execute("SELECT * FROM incidents WHERE id=?", (inc_id,)).fetchone()
    if not row:
        raise HTTPException(404)
    d = _incident(row)
    d["transmissions"] = [dict(t) for t in c.execute(
        "SELECT id, agency, talkgroup, time_ms, len_s, transcript FROM transmissions WHERE incident_id=? "
        "ORDER BY time_ms", (inc_id,)).fetchall()]
    if d["matched"]:
        d["history"] = [dict(r) for r in c.execute(
            "SELECT id, first_ms, nature, category FROM incidents WHERE matched=? AND id<>? "
            "ORDER BY first_ms DESC LIMIT 25", (d["matched"], inc_id)).fetchall()]
    return d


@app.get("/api/stats")
def stats(req: Request):
    w, a = where(req)
    c = db.conn()

    def rows(sql):
        return [dict(r) for r in c.execute(sql, a).fetchall()]

    total = c.execute(f"SELECT COUNT(*), SUM(lat IS NOT NULL), MIN(local_date), MAX(local_date) FROM incidents{w}", a).fetchone()
    and_w = (w + " AND") if w else " WHERE"
    return {
        "total": total[0], "mapped": total[1] or 0, "first_date": total[2], "last_date": total[3],
        "by_category": rows(f"SELECT category AS key, COUNT(*) AS n FROM incidents{w} GROUP BY 1 ORDER BY 2 DESC"),
        "by_nature": rows(f"SELECT nature AS key, category, COUNT(*) AS n FROM incidents{w} GROUP BY 1 ORDER BY 3 DESC LIMIT 20"),
        "by_zip": rows(f"SELECT zip AS key, COUNT(*) AS n FROM incidents{and_w} zip IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 15"),
        "by_hour_dow": rows(f"SELECT local_dow AS dow, local_hour AS hour, COUNT(*) AS n FROM incidents{w} GROUP BY 1, 2"),
        "by_hour_category": rows(f"SELECT local_hour AS hour, category, COUNT(*) AS n FROM incidents{w} GROUP BY 1, 2"),
        "daily": rows(f"SELECT local_date AS date, COUNT(*) AS n FROM incidents{w} GROUP BY 1 ORDER BY 1"),
        "top_locations": rows(
            f"SELECT matched AS key, COUNT(*) AS n, AVG(lat) AS lat, AVG(lon) AS lon, "
            f"MAX(place) AS place, GROUP_CONCAT(DISTINCT category) AS categories "
            f"FROM incidents{and_w} matched IS NOT NULL AND precision IN ('address','place','intersection') "
            f"GROUP BY matched HAVING n > 1 ORDER BY n DESC LIMIT 15"),
        "by_agency": [{"key": k, "n": n} for k, n in _agency_counts(c, w, a)],
    }


def _agency_counts(c, w, a):
    counts: dict[str, int] = {}
    for (ags,) in c.execute(f"SELECT agencies FROM incidents{w}", a):
        for ag in json.loads(ags):
            counts[ag] = counts.get(ag, 0) + 1
    return sorted(counts.items(), key=lambda x: -x[1])


@app.get("/api/export.csv")
def export_csv(req: Request):
    w, a = where(req)
    cur = db.conn().execute(f"SELECT * FROM incidents{w} ORDER BY first_ms", a)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()   # sqlite objects can't cross into the streaming thread

    def gen():
        buf = io.StringIO()
        wr = csv.writer(buf)
        wr.writerow(["time_local"] + cols)
        for r in rows:
            wr.writerow([pipeline._local(r["first_ms"]).isoformat()] + list(r))
            yield buf.getvalue(); buf.seek(0); buf.truncate()
    return StreamingResponse(gen(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=incidents.csv"})


@app.get("/api/audio/{tx_id}")
def audio(tx_id: str):
    row = db.conn().execute("SELECT url, audio_path FROM transmissions WHERE id=?", (tx_id,)).fetchone()
    if not row:
        raise HTTPException(404)
    if row["audio_path"] and Path(row["audio_path"]).exists():
        return FileResponse(row["audio_path"], media_type="audio/mp4")
    try:   # not archived: proxy from OpenMHz (browsers get the Cloudflare challenge)
        return Response(openmhz.download(row["url"]), media_type="audio/mp4")
    except Exception:
        raise HTTPException(502, "Audio no longer available from OpenMHz")

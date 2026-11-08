"""Calls pushed to us instead of pulled from OpenMHz.

POST /api/call-upload speaks the Rdio Scanner upload format, which trunk-recorder
(rdioscanner_uploader plugin) and SDRTrunk can send to. So a recorder on your own
SDR, or one run by whoever already feeds OpenMHz for the county, can feed this app
directly: no Cloudflare in the way.

An uploaded clip is saved under AUDIO_DIR/incoming and queued like an OpenMHz call;
its url is "local:<path>", which pipeline.fetch_audio reads from disk.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

from . import config, db, settings

log = logging.getLogger(__name__)

LOCAL = "local:"
EXTS = {"audio/mp4": ".m4a", "audio/m4a": ".m4a", "audio/aac": ".m4a", "audio/wav": ".wav",
        "audio/x-wav": ".wav", "audio/wave": ".wav", "audio/mpeg": ".mp3", "audio/mp3": ".mp3"}

status = {"last_upload": None, "uploads": 0, "ignored": 0}


def incoming_dir() -> Path:
    p = config.AUDIO_DIR / "incoming"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _ext(audio_type: str | None, name: str | None) -> str:
    if name and Path(name).suffix.lower() in (".m4a", ".wav", ".mp3", ".aac"):
        return ".m4a" if Path(name).suffix.lower() == ".aac" else Path(name).suffix.lower()
    return EXTS.get((audio_type or "").split(";")[0].strip().lower(), ".m4a")


def duration_s(path: str) -> float | None:
    try:
        import av
        with av.open(path) as f:
            if f.duration:
                return f.duration / 1_000_000
            s = f.streams.audio[0]
            return float(s.duration * s.time_base) if s.duration else None
    except Exception:
        return None


def parse_time_ms(value: str) -> int:
    """trunk-recorder sends unix seconds; Rdio Scanner clients may send ms or ISO 8601."""
    v = (value or "").strip()
    if not v:
        return int(time.time() * 1000)
    try:
        n = float(v)
        return int(n if n > 1e12 else n * 1000)
    except ValueError:
        from datetime import datetime
        return int(datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp() * 1000)


def accept(*, talkgroup: int, time_ms: int, audio: bytes, audio_type: str | None = None,
           audio_name: str | None = None, system: str = "", source: str = "upload") -> tuple[str, str | None]:
    """Queue one uploaded call. Returns (result, transmission id): result is
    'queued', 'short', 'duplicate' or 'ignored' (talkgroup not monitored)."""
    ch = settings.enabled_channels().get(talkgroup)
    if ch is None:
        status["ignored"] += 1
        return "ignored", None
    digest = hashlib.sha1(audio).hexdigest()[:10]
    tx_id = f"up-{talkgroup}-{time_ms}-{digest}"
    c = db.conn()
    if c.execute("SELECT 1 FROM transmissions WHERE id=?", (tx_id,)).fetchone():
        return "duplicate", tx_id
    path = incoming_dir() / f"{tx_id}{_ext(audio_type, audio_name)}"
    path.write_bytes(audio)
    length = duration_s(str(path))
    short = length is not None and length < settings.get("MIN_CALL_SECONDS")
    if short:
        path.unlink(missing_ok=True)                # nothing will ever read it
    c.execute("INSERT OR IGNORE INTO transmissions(id, talkgroup, agency, time_ms, len_s, url, status) "
              "VALUES (?,?,?,?,?,?,?)",
              (tx_id, talkgroup, ch.agency, time_ms, round(length) if length is not None else None,
               None if short else LOCAL + str(path), "short" if short else "pending"))
    status["last_upload"] = time.time()
    status["uploads"] += 1
    return ("short" if short else "queued"), tx_id


def is_local(url: str | None) -> bool:
    return bool(url) and url.startswith(LOCAL)


def local_path(url: str) -> Path:
    return Path(url[len(LOCAL):])


def cleanup(url: str | None):
    """Remove an uploaded clip once it has been processed (an archived copy is kept separately)."""
    if is_local(url):
        local_path(url).unlink(missing_ok=True)

"""OpenMHz API client.

api.openmhz.com and media2.openmhz.com sit behind Cloudflare, which rejects
python-requests and cloudscraper with a JS challenge. curl_cffi impersonates a
real browser's TLS fingerprint. If Cloudflare still challenges us, every request
pauses for a cool-down rather than retrying (see _request).

Endpoints (all accept filter-type=talkgroup&filter-code=1,2,3):
  /{system}/calls               newest 50 calls
  /{system}/calls/older?time=ms 50 calls older than time
  /{system}/calls/newer?time=ms calls newer than time
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from curl_cffi import requests as cr

from . import config, settings

log = logging.getLogger(__name__)

_local = threading.local()

# Browser identities to present to Cloudflare, newest first. When one gets
# challenged the client moves to the next, so a fingerprint Cloudflare starts
# rejecting heals itself without an update. Names that this curl_cffi version
# doesn't know are skipped.
IMPERSONATE = ("chrome", "chrome146", "chrome142", "safari184", "firefox147", "edge101", "chrome131", "safari180")
_identity = {"i": 0}
_identity_lock = threading.Lock()


class Challenged(RuntimeError):
    """Cloudflare answered with a challenge page instead of data."""


def _session() -> cr.Session:
    # curl_cffi sessions are not thread-safe; poller and worker each get one.
    s = getattr(_local, "s", None)
    if s is None or getattr(_local, "i", None) != _identity["i"]:
        for _ in range(len(IMPERSONATE)):
            try:
                s = cr.Session(impersonate=IMPERSONATE[_identity["i"]])
                break
            except Exception:
                _rotate(log_it=False)              # unknown to this curl_cffi version
        _local.s, _local.i = s, _identity["i"]
    return s


def _rotate(log_it: bool = True):
    with _identity_lock:
        _identity["i"] = (_identity["i"] + 1) % len(IMPERSONATE)
        if log_it:
            log.info(f"OpenMHz: switching browser identity to {IMPERSONATE[_identity['i']]}")
    _local.s = None


def identity() -> str:
    return IMPERSONATE[_identity["i"]]


def _challenged(r) -> bool:
    return r.status_code in (403, 503) and ("Just a moment" in r.text or "cf-chl" in r.text or
                                            "challenge" in r.text.lower()[:2000])


def _filter() -> dict:
    return {"filter-type": "talkgroup", "filter-code": ",".join(str(t) for t in settings.enabled_channels()) or "0"}


# After a Cloudflare challenge nobody (poller, downloads, backfill) contacts OpenMHz until
# a cool-down has passed: 1 minute, doubling per challenge in a row up to 15. Quick
# retries are what get an IP flagged in the first place.
COOL_START_S, COOL_MAX_S = 60, 900
_cool = {"until": 0.0, "n": 0}


def cooling_down() -> float:
    """Seconds left before OpenMHz may be contacted again (0 when it may)."""
    return max(0.0, _cool["until"] - time.time())


def _on_challenge():
    with _identity_lock:
        _cool["n"] += 1
        wait = min(COOL_START_S * 2 ** (_cool["n"] - 1), COOL_MAX_S)
        _cool["until"] = time.time() + wait
    log.warning(f"OpenMHz answered with a Cloudflare challenge; pausing all requests for {wait}s")
    _rotate()                                       # the next try uses another browser identity


def _request(url: str, timeout: int, **params):
    """One GET (plus one retry with a fresh session after a connection error). A Cloudflare
    challenge starts the shared cool-down and raises Challenged without retrying."""
    left = cooling_down()
    if left:
        raise Challenged(f"OpenMHz Cloudflare challenge; next try in {round(left)}s")
    last = None
    for attempt in range(2):
        try:
            r = _session().get(url, params=params or None, timeout=timeout)
        except Exception as e:                      # connection reset, TLS error: fresh session once
            last = e
            _local.s = None
            time.sleep(3)
            continue
        if _challenged(r):
            _on_challenge()
            raise Challenged("OpenMHz Cloudflare challenge")
        r.raise_for_status()
        _cool["n"] = 0
        return r
    raise RuntimeError(f"OpenMHz unreachable: {last}")


def _get(path: str, **params) -> dict:
    url = f"{config.OPENMHZ_API}/{config.OPENMHZ_SYSTEM}{path}"
    return _request(url, 20, **params, **_filter()).json()


def to_ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def latest() -> list[dict]:
    return _get("/calls").get("calls", [])


def older(time_ms: int) -> list[dict]:
    return _get("/calls/older", time=time_ms).get("calls", [])


def download(url: str) -> bytes:
    r = _request(url, 60)
    if r.headers.get("content-type", "").startswith("text/html"):
        raise RuntimeError(f"Got HTML instead of audio for {url}")
    return r.content


def talkgroups() -> dict:
    url = f"{config.OPENMHZ_API}/{config.OPENMHZ_SYSTEM}/talkgroups"
    return _request(url, 20).json().get("talkgroups", {})

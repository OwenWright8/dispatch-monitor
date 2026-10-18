"""OpenMHz API client.

api.openmhz.com and media2.openmhz.com sit behind Cloudflare, which rejects
python-requests and cloudscraper with a JS challenge. curl_cffi impersonates a
real browser's TLS fingerprint and gets straight through.

Endpoints (all accept filter-type=talkgroup&filter-code=1,2,3):
  /{system}/calls               newest 50 calls
  /{system}/calls/older?time=ms 50 calls older than time
  /{system}/calls/newer?time=ms calls newer than time
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from curl_cffi import requests as cr

from . import config

log = logging.getLogger(__name__)

_local = threading.local()


def _session() -> cr.Session:
    # curl_cffi sessions are not thread-safe; poller and worker each get one.
    s = getattr(_local, "s", None)
    if s is None:
        s = _local.s = cr.Session(impersonate="chrome")
    return s


def _filter() -> dict:
    return {"filter-type": "talkgroup", "filter-code": ",".join(str(t) for t in config.CHANNELS)}


def _get(path: str, **params) -> dict:
    url = f"{config.OPENMHZ_API}/{config.OPENMHZ_SYSTEM}{path}"
    r = _session().get(url, params={**params, **_filter()}, timeout=20)
    if r.status_code == 403 and "Just a moment" in r.text:
        raise RuntimeError("OpenMHz Cloudflare challenge not bypassed (curl_cffi impersonation may need updating)")
    r.raise_for_status()
    return r.json()


def to_ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def latest() -> list[dict]:
    return _get("/calls").get("calls", [])


def older(time_ms: int) -> list[dict]:
    return _get("/calls/older", time=time_ms).get("calls", [])


def download(url: str) -> bytes:
    r = _session().get(url, timeout=60)
    r.raise_for_status()
    if r.headers.get("content-type", "").startswith("text/html"):
        raise RuntimeError(f"Got HTML instead of audio for {url}")
    return r.content


def talkgroups() -> dict:
    url = f"{config.OPENMHZ_API}/{config.OPENMHZ_SYSTEM}/talkgroups"
    r = _session().get(url, timeout=20)
    r.raise_for_status()
    return r.json().get("talkgroups", {})

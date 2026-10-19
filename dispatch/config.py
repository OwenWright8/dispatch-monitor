"""Runtime configuration, all overridable through environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path


# Release version, set by the image build from the git tag (VERSION build arg).
VERSION = os.environ.get("APP_VERSION", "dev")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _bool(name: str, default: bool) -> bool:
    return _env(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# Which county this install monitors: a profile in dispatch/counties/.
from .counties import ChannelSpec, load as _load_profile

PROFILE = _load_profile(_env("COUNTY", "tippecanoe"))


@dataclass(frozen=True)
class Channel:
    talkgroup: int
    agency: str                      # channel label shown on transmissions
    # City tried first when geocoding addresses heard on this channel.
    city_hint: str
    default_department: str | None = None
    shared: bool = False


def _from_spec(c: ChannelSpec) -> Channel:
    return Channel(c.talkgroup, c.label, c.city_hint, c.default_department, c.shared)


def _parse_channels(spec: str) -> dict[int, Channel]:
    """CHANNELS=talkgroup:label:city hint,...  (overrides the profile's channels).
    A label naming one of the profile's departments credits that department."""
    channels = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        tg, _, rest = part.partition(":")
        label, _, city = rest.partition(":")
        known = PROFILE.channel(int(tg))
        if known and not label:
            channels[int(tg)] = _from_spec(known)
            continue
        label = label or f"TG {tg}"
        dept = known.default_department if known else (label if PROFILE.department(label) else None)
        channels[int(tg)] = Channel(int(tg), label, city or (known.city_hint if known else PROFILE.name),
                                    dept, known.shared if known else dept is None)
    return channels


DATA_DIR = Path(_env("DATA_DIR", "data"))
DB_PATH = Path(_env("DB_PATH", str(DATA_DIR / "dispatch.db")))
AUDIO_DIR = Path(_env("AUDIO_DIR", str(DATA_DIR / "audio")))
KEEP_AUDIO = _bool("KEEP_AUDIO", True)          # archive dispatch audio locally
# Retention: audio clips, then call details (transcripts, summary, patient,
# units), then the incident itself (type, location, departments, time).
AUDIO_RETENTION_HOURS = int(_env("AUDIO_RETENTION_HOURS", "24"))
DETAIL_RETENTION_DAYS = int(_env("DETAIL_RETENTION_DAYS", "7"))
INCIDENT_RETENTION_DAYS = int(_env("INCIDENT_RETENTION_DAYS", "365"))
# Read-only data baked into the Docker image at build time (Whisper model,
# street index, place names) so a fresh install needs no first-run downloads.
BUNDLE_DIR = Path(_env("BUNDLE_DIR", "/opt/bundle"))

OPENMHZ_SYSTEM = _env("OPENMHZ_SYSTEM", PROFILE.openmhz_system)
OPENMHZ_API = _env("OPENMHZ_API", "https://api.openmhz.com")
CHANNELS = (_parse_channels(os.environ["CHANNELS"]) if os.environ.get("CHANNELS", "").strip()
            else {c.talkgroup: _from_spec(c) for c in PROFILE.channels})
POLL_INTERVAL_S = int(_env("POLL_INTERVAL_S", "30"))   # gentle on OpenMHz; gaps are filled by paging back
BACKFILL_HOURS = float(_env("BACKFILL_HOURS", "24"))   # history to pull on first start
MIN_CALL_SECONDS = int(_env("MIN_CALL_SECONDS", "4"))  # shorter clips are acks/keyups

WHISPER_MODEL = _env("WHISPER_MODEL", "small.en")
# Optional speech-to-text server (whisper-asr-webservice, e.g. on a GPU); empty = built-in model
WHISPER_URL = _env("WHISPER_URL", "").strip()
WHISPER_COMPUTE = _env("WHISPER_COMPUTE", "int8")
WHISPER_THREADS = int(_env("WHISPER_THREADS", "0"))    # 0 = let CTranslate2 decide
WHISPER_BEAM = int(_env("WHISPER_BEAM", "5"))

# Set LLM_ENABLED=false to run on keyword rules alone (no Ollama needed).
LLM_ENABLED = _bool("LLM_ENABLED", True)
OLLAMA_URL = _env("OLLAMA_URL", "http://localhost:11434").rstrip("/")
# Older configs pointed straight at /api/generate; accept that too.
for _suffix in ("/api/generate", "/api/chat"):
    if OLLAMA_URL.endswith(_suffix):
        OLLAMA_URL = OLLAMA_URL[: -len(_suffix)]
OLLAMA_MODEL = _env("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_TIMEOUT_S = int(_env("OLLAMA_TIMEOUT_S", "180"))

TIMEZONE = _env("TZ_LOCAL", PROFILE.timezone)

COUNTY_FIPS = PROFILE.fips
COUNTY_BBOX = PROFILE.bbox                     # south, west, north, east (with a little margin)
STATE = PROFILE.state
TIGER_YEAR = _env("TIGER_YEAR", "2025")
NOMINATIM_URL = _env("NOMINATIM_URL", "https://nominatim.openstreetmap.org")
USER_AGENT = _env("USER_AGENT", f"{PROFILE.key}-dispatch-monitor/2.1 (self-hosted)")

# Transmissions about the same address within this window form one incident.
INCIDENT_WINDOW_MIN = int(_env("INCIDENT_WINDOW_MIN", "20"))

# CARTO basemap key for the dashboard (https://carto.com/basemaps). Without it
# the map uses standard OpenStreetMap tiles. It is sent to browsers by design.
CARTO_API_KEY = _env("CARTO_API_KEY", "").strip()

WEB_HOST = _env("WEB_HOST", "0.0.0.0")
WEB_PORT = int(_env("WEB_PORT_INTERNAL", "8080"))

"""Runtime configuration, all overridable through environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _bool(name: str, default: bool) -> bool:
    return _env(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Channel:
    talkgroup: int
    agency: str
    # City tried first when geocoding addresses heard on this channel.
    city_hint: str


# Dispatch talkgroups on the OpenMHz "tippco" system. Only dispatch channels are
# monitored by default: that is where addresses and call types are read out.
# Format: talkgroup:agency:city hint
DEFAULT_CHANNELS = ",".join([
    "1827:Tippecanoe Fire:Lafayette",       # TCFD DISPATCH  - county/township fire
    "1833:TEAS EMS:Lafayette",              # TEAS EMS DISP  - county ambulance service
    "1901:Lafayette FD:Lafayette",          # LFD DISPATCH
    "2021:West Lafayette FD:West Lafayette",  # WLFD DISPATCH
    "2105:Purdue FD:West Lafayette",        # PUFD DISPATCH
])


def _parse_channels(spec: str) -> dict[int, Channel]:
    channels = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        tg, _, rest = part.partition(":")
        agency, _, city = rest.partition(":")
        channels[int(tg)] = Channel(int(tg), agency or f"TG {tg}", city or "Lafayette")
    return channels


DATA_DIR = Path(_env("DATA_DIR", "data"))
DB_PATH = Path(_env("DB_PATH", str(DATA_DIR / "dispatch.db")))
AUDIO_DIR = Path(_env("AUDIO_DIR", str(DATA_DIR / "audio")))
KEEP_AUDIO = _bool("KEEP_AUDIO", True)          # archive dispatch audio locally

OPENMHZ_SYSTEM = _env("OPENMHZ_SYSTEM", "tippco")
OPENMHZ_API = _env("OPENMHZ_API", "https://api.openmhz.com")
CHANNELS = _parse_channels(_env("CHANNELS", DEFAULT_CHANNELS))
POLL_INTERVAL_S = int(_env("POLL_INTERVAL_S", "20"))
BACKFILL_HOURS = float(_env("BACKFILL_HOURS", "24"))   # history to pull on first start
MIN_CALL_SECONDS = int(_env("MIN_CALL_SECONDS", "4"))  # shorter clips are acks/keyups

WHISPER_MODEL = _env("WHISPER_MODEL", "small.en")
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

TIMEZONE = _env("TZ_LOCAL", "America/Indiana/Indianapolis")

# Tippecanoe County, IN (FIPS 18157) with a little margin.
COUNTY_FIPS = "18157"
COUNTY_BBOX = (40.20, -87.11, 40.58, -86.68)   # south, west, north, east
TIGER_YEAR = _env("TIGER_YEAR", "2025")
NOMINATIM_URL = _env("NOMINATIM_URL", "https://nominatim.openstreetmap.org")
USER_AGENT = _env("USER_AGENT", "tippecanoe-dispatch-monitor/2.0 (self-hosted)")

# Transmissions about the same address within this window form one incident.
INCIDENT_WINDOW_MIN = int(_env("INCIDENT_WINDOW_MIN", "20"))

# CARTO basemap key for the dashboard (https://carto.com/basemaps). Without it
# the map uses standard OpenStreetMap tiles. It is sent to browsers by design.
CARTO_API_KEY = _env("CARTO_API_KEY", "").strip()

WEB_HOST = _env("WEB_HOST", "0.0.0.0")
WEB_PORT = int(_env("WEB_PORT_INTERNAL", "8080"))

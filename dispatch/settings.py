"""Settings that can be changed from the dashboard.

Each setting starts from its environment variable (or built-in default, see
config.py); a value saved in the dashboard is stored in the database and wins
over the environment from then on. "Reset" deletes the stored value. Changes
apply without a restart: everything that uses a setting reads it through get().
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

from . import config, db


@dataclass(frozen=True)
class Spec:
    key: str
    label: str
    group: str
    kind: str                       # bool | int | str | choice | channels
    default: object
    help: str = ""
    choices: tuple = ()
    min: float | None = None
    max: float | None = None


SPECS: tuple[Spec, ...] = (
    Spec("LLM_ENABLED", "Use the AI model", "AI model", "bool", config.LLM_ENABLED,
         "Off = keyword rules only (fast, less accurate)."),
    Spec("OLLAMA_URL", "Ollama address", "AI model", "str", config.OLLAMA_URL,
         "e.g. http://host.docker.internal:11434 for an Ollama installed on this computer."),
    Spec("OLLAMA_MODEL", "Model", "AI model", "str", config.OLLAMA_MODEL,
         "Downloaded automatically if Ollama doesn't have it yet."),
    Spec("OLLAMA_TIMEOUT_S", "Timeout per call (s)", "AI model", "int", config.OLLAMA_TIMEOUT_S, min=10, max=900),
    Spec("WHISPER_MODEL", "Speech-to-text model", "Speech to text", "choice", config.WHISPER_MODEL,
         "Bigger hears names better but is slower. Models other than the built-in one download on first use.",
         choices=("base.en", "small.en", "medium.en", "large-v3")),
    Spec("WHISPER_BEAM", "Beam size", "Speech to text", "int", config.WHISPER_BEAM,
         "Higher = slightly more accurate, slower.", min=1, max=10),
    Spec("ENABLED_CHANNELS", "Channels to monitor", "Radio feed", "channels",
         ",".join(str(t) for t in config.CHANNELS), "Untick a channel to stop transcribing it."),
    Spec("POLL_INTERVAL_S", "Check for new calls every (s)", "Radio feed", "int", config.POLL_INTERVAL_S,
         min=10, max=600),
    Spec("MIN_CALL_SECONDS", "Skip clips shorter than (s)", "Radio feed", "int", config.MIN_CALL_SECONDS,
         "Very short clips are acknowledgements and key-ups.", min=0, max=30),
    Spec("INCIDENT_WINDOW_MIN", "Merge transmissions within (min)", "Incidents", "int",
         config.INCIDENT_WINDOW_MIN, "Calls about the same address within this window become one incident.",
         min=1, max=180),
    Spec("KEEP_AUDIO", "Keep dispatch audio", "Incidents", "bool", config.KEEP_AUDIO,
         "Saves each dispatch clip (~100 KB) so it can be replayed after OpenMHz drops it."),
    Spec("AUDIO_RETENTION_HOURS", "Keep audio clips for (hours)", "Data retention", "int",
         config.AUDIO_RETENTION_HOURS, "Older clips are deleted.", min=1, max=24 * 365),
    Spec("DETAIL_RETENTION_DAYS", "Keep call details for (days)", "Data retention", "int",
         config.DETAIL_RETENTION_DAYS,
         "Transcripts, summaries, patient age/sex and units. After this only the call type, location, "
         "departments and time remain.", min=1, max=3650),
    Spec("INCIDENT_RETENTION_DAYS", "Keep incidents for (days)", "Data retention", "int",
         config.INCIDENT_RETENTION_DAYS, "Call type, location and departments, used for the statistics.",
         min=7, max=3650),
    Spec("CARTO_API_KEY", "CARTO map key", "Map", "str", config.CARTO_API_KEY,
         "Optional key for the CARTO dark/light map style. Empty = OpenStreetMap tiles. Visible to viewers."),
)
BY_KEY = {s.key: s for s in SPECS}
PREFIX = "setting:"

# Set ADMIN_PASSWORD (environment only) to require it for saving settings.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()

_cache: dict[str, object] = {}
_cache_at = 0.0
_lock = threading.Lock()


def _parse(spec: Spec, raw) -> object:
    if spec.kind == "bool":
        return raw if isinstance(raw, bool) else str(raw).strip().lower() in ("1", "true", "yes", "on")
    if spec.kind == "int":
        v = int(float(raw))
        if spec.min is not None and v < spec.min or spec.max is not None and v > spec.max:
            raise ValueError(f"{spec.label} must be between {spec.min:g} and {spec.max:g}")
        return v
    if spec.kind == "choice":
        v = str(raw).strip()
        if v not in spec.choices:
            raise ValueError(f"{spec.label} must be one of {', '.join(spec.choices)}")
        return v
    if spec.kind == "channels":
        tgs = [int(t) for t in str(raw).replace(" ", "").split(",") if t]
        unknown = [t for t in tgs if t not in config.CHANNELS]
        if unknown:
            raise ValueError(f"Unknown talkgroup(s): {unknown}")
        return ",".join(str(t) for t in tgs)
    v = str(raw).strip()
    return v.rstrip("/") if spec.key == "OLLAMA_URL" else v


def _load() -> dict[str, object]:
    global _cache, _cache_at
    with _lock:
        if time.time() - _cache_at < 3:
            return _cache
        stored = {}
        for k, v in db.conn().execute("SELECT key, value FROM state WHERE key LIKE ?", (PREFIX + "%",)):
            spec = BY_KEY.get(k[len(PREFIX):])
            if spec:
                try:
                    stored[spec.key] = _parse(spec, v)
                except (ValueError, TypeError):
                    pass
        _cache, _cache_at = stored, time.time()
        return stored


def get(key: str):
    stored = _load()
    return stored[key] if key in stored else BY_KEY[key].default


def overridden() -> set[str]:
    return set(_load())


def enabled_channels() -> dict:
    """The configured channels minus any unticked in the dashboard."""
    on = {int(t) for t in str(get("ENABLED_CHANNELS")).split(",") if t}
    return {tg: ch for tg, ch in config.CHANNELS.items() if tg in on}


def update(values: dict) -> list[str]:
    """Validate and store; a value of None resets that setting to its default."""
    global _cache_at
    parsed = {}
    for k, raw in values.items():
        spec = BY_KEY.get(k)
        if not spec:
            raise ValueError(f"Unknown setting {k}")
        parsed[k] = None if raw is None else _parse(spec, raw)
    c = db.conn()
    for k, v in parsed.items():
        if v is None or v == BY_KEY[k].default:
            c.execute("DELETE FROM state WHERE key=?", (PREFIX + k,))
        else:
            c.execute("INSERT OR REPLACE INTO state(key, value) VALUES (?, ?)", (PREFIX + k, str(v)))
    with _lock:
        _cache_at = 0.0
    return list(parsed)


def describe() -> list[dict]:
    over = overridden()
    out = []
    for s in SPECS:
        d = {"key": s.key, "label": s.label, "group": s.group, "kind": s.kind, "help": s.help,
             "value": get(s.key), "default": s.default, "changed": s.key in over}
        if s.choices:
            d["choices"] = list(s.choices)
        if s.min is not None:
            d["min"], d["max"] = s.min, s.max
        if s.kind == "channels":
            d["channels"] = [{"talkgroup": ch.talkgroup, "label": ch.agency} for ch in config.CHANNELS.values()]
        out.append(d)
    return out

"""County profiles.

Everything that is specific to one county lives in a profile module next to
this file: which OpenMHz system and talkgroups to listen to, the departments
that get dispatched (with the ways Whisper mishears their names and the unit
numbers they use), local street and place vocabulary, and the map area.

Pick one with COUNTY=<module name> (default: tippecanoe). Adding a county is
one new file here; no other code changes.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Department:
    name: str                       # canonical display name: "Wea Township Fire"
    kind: str = "fire"              # fire | ems | rescue
    # Regexes (case-insensitive) for how the name is said or misheard. A match
    # means this department was dispatched. Matched text is rewritten to `spoken`.
    aliases: tuple[str, ...] = ()
    spoken: str | None = None       # canonical spoken form written into transcripts ("Wea Fire")
    # Regexes that show the department was dispatched without rewriting the
    # transcript ("Westside Medic" is a TEAS ambulance).
    detect: tuple[str, ...] = ()
    # Unit numbers this department uses on a shared county channel, with x for
    # any digit: ("4xx",) = 400-499, ("13xx",) = 1300-1399, ("34x",) = 340-349.
    unit_prefixes: tuple[str, ...] = ()
    area: str = ""                  # what it covers, shown in the dashboard
    # Towns it covers (keys of Profile.city_centers). Addresses on its calls are
    # looked up near these first: "101 W Main St" for Jackson Township is in
    # Arcadia, not Carmel.
    towns: tuple[str, ...] = ()
    # How far from those towns its calls can be (km); None = the default
    # township-sized district. Purdue Fire stays on campus.
    reach_km: float | None = None


@dataclass(frozen=True)
class Station:
    lat: float
    lon: float
    dept: str                       # department name (as in Profile.departments)
    label: str                      # "Station 41"
    address: str = ""


@dataclass(frozen=True)
class ChannelSpec:
    talkgroup: int
    label: str                      # what the channel is ("County fire dispatch")
    city_hint: str                  # town tried first when geocoding
    # Department credited when a dispatch names nobody specific. None = credit
    # nobody (a shared county channel where a guess would be wrong).
    default_department: str | None = None
    # Unit numbers on this channel map to departments via unit_prefixes.
    shared: bool = False


@dataclass(frozen=True)
class Profile:
    key: str
    name: str                       # "Tippecanoe County"
    state: str                      # "IN"
    title: str                      # dashboard title
    subtitle: str
    fips: str                       # 5-digit county FIPS (TIGER street data)
    bbox: tuple[float, float, float, float]   # south, west, north, east
    center: tuple[float, float]
    zoom: int
    timezone: str
    openmhz_system: str
    channels: tuple[ChannelSpec, ...]
    departments: tuple[Department, ...]
    city_centers: dict[str, tuple[float, float]]
    whisper_prompt: str             # biases speech-to-text toward local words (<~200 tokens)
    street_hints: str               # comma list of local streets for the LLM prompt
    place_examples: str             # named places for the LLM prompt
    place_aliases: dict[str, str] = field(default_factory=dict)
    # Extra (regex, replacement) transcript fixes beyond department names.
    transcript_fixes: tuple[tuple[str, str], ...] = ()
    unit_examples: str = '"Station 3", "Engine 1502", "Medic 5"'
    # Worked examples of this county's dispatch style for the LLM:
    # ((transcript, expected JSON fields), ...)
    examples: tuple = ()
    # Fire stations, keyed by station number. Shown on the map, and used for
    # grid codes: when dispatch reads one ("55 George") the number is the
    # first-due station. Stations not listed are taken from the map data
    # ("Fire Station 55").
    grid_codes: bool = False
    stations: dict = field(default_factory=dict)       # {"41": Station(...)}
    # Hospitals units transport to ("en route to Riverview with one" is not a call there).
    hospitals: tuple[str, ...] = ()

    # ── helpers ─────────────────────────────────────────────────────────
    def department(self, name: str | None) -> Department | None:
        for d in self.departments:
            if d.name == name:
                return d
        return None

    def channel(self, talkgroup: int) -> ChannelSpec | None:
        for c in self.channels:
            if c.talkgroup == talkgroup:
                return c
        return None

    @property
    def city_regex(self) -> str:
        """Alternation of town names, longest first, for stripping ', Lafayette, IN'."""
        names = sorted(self.city_centers, key=len, reverse=True)
        return "|".join(re.escape(n) for n in names)


def available() -> list[str]:
    return sorted(m.name for m in pkgutil.iter_modules(__path__) if not m.name.startswith("_"))


def load(key: str) -> Profile:
    key = (key or "tippecanoe").strip().lower()
    try:
        mod = importlib.import_module(f"{__name__}.{key}")
    except ModuleNotFoundError as e:
        raise SystemExit(f"Unknown COUNTY={key!r}. Available: {', '.join(available())}") from e
    return mod.PROFILE

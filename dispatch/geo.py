"""Geocoding for the configured county (see dispatch/counties/).

Primary: a local geocoder built from the Census TIGER/Line road network for the
county (street names incl. alternates, house-number ranges, ZIPs, and node ids).
It is offline, has no rate limit, fuzzy-matches the street names Whisper
mishears, interpolates house numbers, and finds intersections from shared
nodes — none of which Nominatim does well.

Fallbacks: the US Census geocoder API for numbered addresses, then Nominatim
(bounded to the county) for named places and anything else.
"""

from __future__ import annotations

import difflib
import io
import json
import logging
import math
import pickle
import re
import threading
import time
import zipfile
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path

import requests

from . import config

log = logging.getLogger(__name__)

S, W, N, E = config.COUNTY_BBOX

CITY_CENTERS = config.PROFILE.city_centers
STATE_NAMES = {"IN": "Indiana"}
STATE_NAME = STATE_NAMES.get(config.STATE, config.STATE)


def index_name() -> str:
    """Street index file for this county (several counties can share a bundle)."""
    return f"index-{config.COUNTY_FIPS}-{config.TIGER_YEAR}.pkl"


def places_name() -> str:
    return f"places-{config.COUNTY_FIPS}.json"

DIRS = {
    "north": "n", "n": "n", "south": "s", "s": "s", "east": "e", "e": "e", "west": "w", "w": "w",
    "northeast": "ne", "ne": "ne", "northwest": "nw", "nw": "nw",
    "southeast": "se", "se": "se", "southwest": "sw", "sw": "sw",
}

# spoken / written street type -> TIGER abbreviation (lowercase)
TYPES = {
    "street": "st", "st": "st", "avenue": "ave", "ave": "ave", "av": "ave", "road": "rd", "rd": "rd",
    "drive": "dr", "dr": "dr", "lane": "ln", "ln": "ln", "court": "ct", "ct": "ct",
    "boulevard": "blvd", "blvd": "blvd", "parkway": "pkwy", "pkwy": "pkwy", "trail": "trl", "trl": "trl",
    "place": "pl", "pl": "pl", "circle": "cir", "cir": "cir", "way": "way", "highway": "hwy", "hwy": "hwy",
    "terrace": "ter", "ter": "ter", "pike": "pike", "run": "run", "pass": "pass", "crossing": "xing",
    "xing": "xing", "loop": "loop", "row": "row", "square": "sq", "sq": "sq", "cove": "cv", "cv": "cv",
    "point": "pt", "pt": "pt", "path": "path", "walk": "walk", "mall": "mall", "ridge": "rdg", "rdg": "rdg",
    "bend": "bnd", "bnd": "bnd", "trace": "trce", "trce": "trce", "commons": "cmns", "cmns": "cmns",
    "plaza": "plz", "plz": "plz", "alley": "aly", "aly": "aly", "expressway": "expy", "expy": "expy",
    "heights": "hts", "hts": "hts", "hill": "hl", "hl": "hl", "knoll": "knl", "knl": "knl",
    "landing": "lndg", "lndg": "lndg", "meadows": "mdws", "mdws": "mdws", "park": "park",
    "view": "vw", "vw": "vw", "vista": "vis", "glen": "gln", "gln": "gln", "green": "grn",
    "grove": "grv", "grv": "grv", "estates": "ests", "ests": "ests",
}

ORDINAL_WORDS = {
    "first": "1st", "second": "2nd", "third": "3rd", "fourth": "4th", "fifth": "5th", "sixth": "6th",
    "seventh": "7th", "eighth": "8th", "ninth": "9th", "tenth": "10th", "eleventh": "11th",
    "twelfth": "12th", "thirteenth": "13th", "fourteenth": "14th", "fifteenth": "15th",
    "sixteenth": "16th", "seventeenth": "17th", "eighteenth": "18th", "nineteenth": "19th",
    "twentieth": "20th", "twenty-first": "21st", "twenty-second": "22nd", "twenty-third": "23rd",
    "twenty-fourth": "24th", "twenty-fifth": "25th", "twenty-sixth": "26th", "twenty-eighth": "28th",
}

_UNIT_RE = re.compile(
    r"[,\s]+(?:apartment|apt\.?|unit|suite|ste\.?|room|rm\.?|lot|building|bldg\.?|#)\s*[\w-]+.*$", re.I)


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }".replace(" ", "")


def norm_name(name: str) -> str:
    """Normalize a street *base name* ("Seventh" -> "7th", "Eagles  Way" -> "eagles way")."""
    t = name.lower().replace(".", " ").replace("'", "")
    t = re.sub(r"\s+", " ", t).strip()
    words = [ORDINAL_WORDS.get(w, w) for w in t.split()]
    return " ".join(words)


@dataclass
class StreetQuery:
    predir: str
    name: str
    suftype: str
    sufdir: str


def parse_street(text: str) -> StreetQuery:
    """Split a spoken street into components: 'North Salisbury Street' -> n/salisbury/st/''."""
    t = text.lower().replace(".", " ").replace(",", " ")
    t = re.sub(r"\b(?:county road|co rd|cr|county rd)\b", " ", t)
    t = re.sub(r"\bstate road\b|\bstate route\b|\bsr\b", "state rd", t)
    t = re.sub(r"\bus highway\b|\bus route\b|\bu s\b|\bus\b(?= \d)", "us hwy", t)
    t = re.sub(r"\binterstate\b|\bi-\s*", "i- ", t)
    toks = [ORDINAL_WORDS.get(w, w) for w in t.split()]
    pd = sd = st = ""
    # Peel from the outside in, never consuming the last token: "South Street"
    # is the street named South, "Sagamore Parkway South" is Sagamore Pkwy S.
    if len(toks) > 1 and toks[-1] in DIRS:
        sd = DIRS[toks.pop()]
    if len(toks) > 1 and toks[-1] in TYPES and not toks[-1].isdigit():
        st = TYPES[toks.pop()]
    if len(toks) > 1 and toks[0] in DIRS:
        pd = DIRS[toks.pop(0)]
    # A bare number followed by a direction is a county road ("450 East").
    # A bare number name otherwise is an ordinal ("North 7" -> "7th").
    if len(toks) == 1 and toks[0].isdigit() and not sd and int(toks[0]) < 40:
        toks[0] = _ordinal(int(toks[0]))
    return StreetQuery(pd, " ".join(toks), st, sd)


@dataclass
class GeoResult:
    lat: float
    lon: float
    precision: str      # address | intersection | street | place | city
    matched: str        # canonical name that was matched
    zip: str | None
    source: str         # tiger | census | nominatim

    def to_dict(self):
        return asdict(self)


def in_county(lat: float, lon: float) -> bool:
    return S <= lat <= N and W <= lon <= E


def _dist_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    dy = (a[0] - b[0]) * 111_000
    dx = (a[1] - b[1]) * 111_000 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


def _seed_from_bundle(rel: str, dest: Path):
    """Copy data baked into the image into the data volume on first start."""
    src = config.BUNDLE_DIR / rel
    if not dest.exists() and src.exists():
        import shutil
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        log.info(f"Using bundled {rel}")


# ─────────────────────────────────────────────
# TIGER index
# ─────────────────────────────────────────────
class TigerIndex:
    """Street network for one county, built from TIGER/Line EDGES + FEATNAMES."""

    def __init__(self, edges: dict, names: dict, display: dict):
        self.edges = edges          # tlid -> dict(pts, lf, lt, rf, rt, zl, zr, nf, nt)
        self.names = names          # norm base name -> list[(tlid, pd, st, sd)]
        self.display = display      # (pd, name, st, sd) -> canonical display name
        self.name_list = list(names)

    # ── construction ────────────────────────────────────────────────────
    @classmethod
    def load(cls, tiger_dir: Path) -> "TigerIndex | None":
        cache = tiger_dir / index_name()
        _seed_from_bundle(f"tiger/{cache.name}", cache)
        if cache.exists():
            try:
                with open(cache, "rb") as f:
                    return cls(*pickle.load(f))
            except Exception as e:  # corrupt cache: rebuild
                log.warning(f"TIGER cache unreadable ({e}); rebuilding")
        try:
            data = cls._build(tiger_dir)
        except Exception as e:
            log.error(f"Could not build TIGER street index ({e}); falling back to online geocoders only")
            return None
        with open(cache, "wb") as f:
            pickle.dump(data, f)
        return cls(*data)

    @staticmethod
    def _download(kind: str, tiger_dir: Path) -> zipfile.ZipFile:
        y, fips = config.TIGER_YEAR, config.COUNTY_FIPS
        path = tiger_dir / f"tl_{y}_{fips}_{kind.lower()}.zip"
        if not path.exists():
            url = f"https://www2.census.gov/geo/tiger/TIGER{y}/{kind}/tl_{y}_{fips}_{kind.lower()}.zip"
            log.info(f"Downloading TIGER {kind}: {url}")
            r = requests.get(url, timeout=120, headers={"User-Agent": config.USER_AGENT})
            r.raise_for_status()
            path.write_bytes(r.content)
        return zipfile.ZipFile(path)

    @classmethod
    def _build(cls, tiger_dir: Path):
        import shapefile  # pyshp

        tiger_dir.mkdir(parents=True, exist_ok=True)
        ez = cls._download("EDGES", tiger_dir)
        fz = cls._download("FEATNAMES", tiger_dir)

        def member(z, ext):
            return io.BytesIO(z.read(next(n for n in z.namelist() if n.endswith(ext))))

        er = shapefile.Reader(shp=member(ez, ".shp"), dbf=member(ez, ".dbf"), shx=member(ez, ".shx"))
        edges = {}
        for sr in er.iterShapeRecords():
            rec = sr.record
            if rec["ROADFLG"] != "Y":
                continue

            def num(v):
                return int(v) if str(v).strip().isdigit() else None

            edges[rec["TLID"]] = {
                "pts": [(round(y, 6), round(x, 6)) for x, y in sr.shape.points],  # (lat, lon)
                "lf": num(rec["LFROMADD"]), "lt": num(rec["LTOADD"]),
                "rf": num(rec["RFROMADD"]), "rt": num(rec["RTOADD"]),
                "zl": rec["ZIPL"] or None, "zr": rec["ZIPR"] or None,
                "nf": rec["TNIDF"], "nt": rec["TNIDT"],
            }

        fr = shapefile.Reader(dbf=member(fz, ".dbf"))
        names = defaultdict(list)
        display = {}
        for rec in fr.iterRecords():
            tlid = rec["TLID"]
            if tlid not in edges or not rec["NAME"]:
                continue
            pd = rec["PREDIRABRV"].lower()
            st = (rec["SUFTYPABRV"] or rec["PRETYPABRV"]).lower()
            sd = rec["SUFDIRABRV"].lower()
            base = norm_name(rec["NAME"])
            if rec["PRETYPABRV"]:          # "State Rd 26", "US Hwy 231"
                base = norm_name(f"{rec['PRETYPABRV']} {rec['NAME']}")
                st = ""
            # Some records carry the direction inside NAME ("N 7th", no PREDIR).
            toks = base.split()
            if not pd and len(toks) > 1 and toks[0] in DIRS:
                pd, base = DIRS[toks[0]], " ".join(toks[1:])
            elif not sd and len(toks) > 1 and toks[-1] in DIRS and not toks[-2].isdigit():
                sd, base = DIRS[toks[-1]], " ".join(toks[:-1])
            names[base].append((tlid, pd, st, sd))
            if rec["PAFLAG"] == "P":           # primary name wins over alternates ("Greenbush St Exn")
                display[(pd, base, st, sd)] = rec["FULLNAME"]
            else:
                display.setdefault((pd, base, st, sd), rec["FULLNAME"])
        log.info(f"TIGER index: {len(edges)} road segments, {len(names)} street names")
        return edges, dict(names), display

    # ── street matching ─────────────────────────────────────────────────
    def match(self, spoken: str) -> list[tuple[float, str, list[int]]]:
        """Return [(score, display_name, [tlid,...])] best first."""
        q = parse_street(spoken)
        if not q.name:
            return []
        candidates: dict[str, float] = {}
        if q.name in self.names:
            candidates[q.name] = 1.0
        # "North River Road" may be stored as name "north river"
        if q.predir:
            full = norm_name(f"{[k for k, v in DIRS.items() if v == q.predir][0]} {q.name}")
            if full in self.names:
                candidates[full] = 1.0
        if not candidates:
            for m in difflib.get_close_matches(q.name, self.name_list, n=4, cutoff=0.78):
                candidates[m] = difflib.SequenceMatcher(None, q.name, m).ratio()
            # Whisper often drops/merges a word: try without spaces
            squashed = q.name.replace(" ", "")
            for n in self.name_list:
                if n.replace(" ", "") == squashed:
                    candidates[n] = 0.95
        groups: dict[tuple, dict] = {}
        for base, ratio in candidates.items():
            for tlid, pd, st, sd in self.names[base]:
                score = ratio * 10
                if q.suftype:
                    score += 2 if st == q.suftype else -1.5
                if q.predir:
                    score += 1.5 if pd == q.predir else (-3 if pd else -0.5)
                elif pd:
                    score -= 0.3
                if q.sufdir:
                    score += 1.5 if sd == q.sufdir else (-3 if sd else -0.5)
                key = (pd, base, st, sd)
                g = groups.setdefault(key, {"score": score, "tlids": []})
                g["tlids"].append(tlid)
        out = [(g["score"], self.display.get(k, k[1].title()), g["tlids"]) for k, g in groups.items()]
        out.sort(key=lambda x: -x[0])
        return out

    # ── geometry helpers ────────────────────────────────────────────────
    @staticmethod
    def _interpolate(pts, frac):
        frac = min(max(frac, 0.0), 1.0)
        seg = [_dist_m(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
        total = sum(seg) or 1.0
        target = total * frac
        for i, d in enumerate(seg):
            if target <= d or i == len(seg) - 1:
                f = target / d if d else 0
                return (pts[i][0] + (pts[i + 1][0] - pts[i][0]) * f,
                        pts[i][1] + (pts[i + 1][1] - pts[i][1]) * f)
            target -= d
        return pts[0]

    def address(self, number: int, spoken_street: str, near=None) -> GeoResult | None:
        matches = self.match(spoken_street)
        if not matches:
            return None
        best_score = matches[0][0]
        hits = []          # (exactness, distance-to-hint, GeoResult)
        for score, disp, tlids in matches:
            if score < best_score - 2.5:
                break
            closest = None
            for tlid in tlids:
                e = self.edges[tlid]
                for f, t, z in ((e["lf"], e["lt"], e["zl"]), (e["rf"], e["rt"], e["zr"])):
                    if f is None or t is None:
                        continue
                    lo, hi = min(f, t), max(f, t)
                    if lo <= number <= hi:
                        frac = (number - f) / (t - f) if t != f else 0.5
                        lat, lon = self._interpolate(e["pts"], frac)
                        parity = 1 if (number % 2) == (f % 2) else 0
                        res = GeoResult(lat, lon, "address", f"{number} {disp}", z, "tiger")
                        hits.append((score + parity, -_dist_m((lat, lon), near) if near else 0, res))
                    else:
                        gap = min(abs(number - lo), abs(number - hi))
                        if closest is None or gap < closest[0]:
                            end = e["pts"][0] if abs(number - f) < abs(number - t) else e["pts"][-1]
                            closest = (gap, end, z)
            if not hits and closest and closest[0] <= 400:
                lat, lon = closest[1]
                hits.append((score - 5, 0, GeoResult(lat, lon, "street", f"{number} {disp}", closest[2], "tiger")))
        if hits:
            hits.sort(key=lambda h: (-h[0], -h[1]))
            return hits[0][2]
        return None

    def street(self, spoken_street: str, near=None) -> GeoResult | None:
        matches = self.match(spoken_street)
        if not matches:
            return None
        score, disp, tlids = matches[0]
        pts = [p for t in tlids for p in self.edges[t]["pts"]]
        if near:
            lat, lon = min(pts, key=lambda p: _dist_m(p, near))
        else:
            lat, lon = pts[len(pts) // 2]
        z = self.edges[tlids[0]]["zl"] or self.edges[tlids[0]]["zr"]
        return GeoResult(lat, lon, "street", disp, z, "tiger")

    def intersection(self, a: str, b: str, near=None) -> GeoResult | None:
        ma, mb = self.match(a), self.match(b)
        if not ma or not mb:
            return None
        for sa, da, ta in ma[:3]:
            nodes_a = {}
            for t in ta:
                e = self.edges[t]
                nodes_a[e["nf"]] = (e["pts"][0], e["zl"] or e["zr"])
                nodes_a[e["nt"]] = (e["pts"][-1], e["zl"] or e["zr"])
            for sb, db, tb in mb[:3]:
                shared = []
                for t in tb:
                    e = self.edges[t]
                    for n in (e["nf"], e["nt"]):
                        if n in nodes_a:
                            shared.append(nodes_a[n])
                if shared:
                    if near:
                        shared.sort(key=lambda s: _dist_m(s[0], near))
                    (lat, lon), z = shared[0]
                    return GeoResult(lat, lon, "intersection", f"{da} & {db}", z, "tiger")
        return None


# ─────────────────────────────────────────────
# Named places (OSM gazetteer)
# ─────────────────────────────────────────────
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# How dispatchers actually say some places -> the OSM name
PLACE_ALIASES = config.PROFILE.place_aliases

# Words that describe a kind of place rather than name it
_GENERIC = {"the", "of", "at", "and", "residence", "hall", "center", "centre", "building", "bldg", "recreational",
            "recreation", "department", "dept", "apartments", "apartment", "apts", "inc", "store", "facility",
            "complex", "house", "home", "a", "in", "on", "sports"}


def _place_tokens(name: str) -> list[str]:
    import unicodedata
    t = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9 ]", " ", t.replace("-", "").replace("'", ""))
    return [w for w in t.split() if w not in _GENERIC]


class Gazetteer:
    """Named buildings/businesses/campus halls in the county, fuzzy-matched."""

    REFRESH_DAYS = 30

    def __init__(self, path: Path):
        self.path = path
        _seed_from_bundle(path.name, path)
        self.places: list[tuple[str, float, float, list[str]]] = []   # name, lat, lon, tokens
        self.by_name: dict[str, tuple[float, float]] = {}
        self._last_try = 0.0
        self._load()

    def _load(self):
        stale = not self.path.exists() or time.time() - self.path.stat().st_mtime > self.REFRESH_DAYS * 86400
        if stale and time.time() - self._last_try > 3600:
            self._last_try = time.time()
            self._fetch()
        if not self.path.exists():
            return
        rows = json.loads(self.path.read_text())
        self.places = [(n, la, lo, _place_tokens(n)) for n, la, lo in rows]
        self.by_name = {n.lower(): (la, lo) for n, la, lo, _ in self.places}
        log.info(f"Place gazetteer: {len(self.places)} named places")

    def _fetch(self):
        s, w, n, e = config.COUNTY_BBOX
        bb = f"({s},{w},{n},{e})"
        q = "[out:json][timeout:120];(" + "".join(
            f'nwr["name"]["{k}"]{bb};' for k in ("building", "amenity", "shop", "leisure", "healthcare",
                                                  "office", "tourism", "craft")) + ");out center tags qt;"
        for url in OVERPASS_URLS:
            try:
                r = requests.post(url, data={"data": q}, timeout=180, headers={"User-Agent": config.USER_AGENT})
                r.raise_for_status()
                rows = []
                for el in r.json()["elements"]:
                    c = el.get("center") or el
                    if "lat" not in c:
                        continue
                    tags = el.get("tags", {})
                    for key in ("name", "alt_name", "short_name", "official_name", "old_name"):
                        for nm in (tags.get(key) or "").split(";"):
                            if nm.strip():
                                rows.append((nm.strip(), round(c["lat"], 6), round(c["lon"], 6)))
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(rows))
                log.info(f"Downloaded {len(rows)} place names from {url}")
                return
            except Exception as e:
                log.info(f"Overpass {url} unavailable ({e}); trying next")
        log.warning("Could not download place names; named places will use Nominatim only (retrying hourly)")

    def find(self, query: str, near=None) -> GeoResult | None:
        if not self.places:
            self._load()
            if not self.places:
                return None
        key = re.sub(r"[^a-z0-9 ]", " ", query.lower().replace("-", " ")).strip()
        key = re.sub(r"\s+", " ", key)
        alias = PLACE_ALIASES.get(key) or PLACE_ALIASES.get(key.replace(" ", ""))
        if not alias:   # misheard alias ("Cadova Recreational Center" -> cordova)
            close = difflib.get_close_matches(" ".join(_place_tokens(query)), list(PLACE_ALIASES), n=1, cutoff=0.75)
            alias = PLACE_ALIASES[close[0]] if close else None
        if alias and alias.lower() in self.by_name:
            lat, lon = self.by_name[alias.lower()]
            return GeoResult(lat, lon, "place", alias, None, "osm")
        if key in self.by_name:
            lat, lon = self.by_name[key]
            return GeoResult(lat, lon, "place", query, None, "osm")
        qt = _place_tokens(query)
        if not qt:
            return None
        best = None
        for name, lat, lon, nt in self.places:
            if not nt:
                continue
            ratios = [max(difflib.SequenceMatcher(None, a, b).ratio() for b in nt) for a in qt]
            q_frac = sum(r >= 0.8 for r in ratios) / len(qt)
            n_frac = sum(any(difflib.SequenceMatcher(None, a, b).ratio() >= 0.8 for a in qt) for b in nt) / len(nt)
            if len(qt) == 1 and (max(ratios) < 0.9 or n_frac < 0.5):
                continue
            if q_frac < 0.75 or n_frac < 0.34:
                continue
            score = q_frac * 0.6 + n_frac * 0.4 + sum(ratios) / len(ratios) * 0.2
            if near:
                score -= min(_dist_m((lat, lon), near), 20_000) / 200_000   # nudge toward the channel's town
            if best is None or score > best[0]:
                best = (score, name, lat, lon)
        if best:
            return GeoResult(best[2], best[3], "place", best[1], None, "osm")
        return None


# ─────────────────────────────────────────────
# Online fallbacks
# ─────────────────────────────────────────────
_session = requests.Session()
_session.headers["User-Agent"] = config.USER_AGENT
_nominatim_lock = threading.Lock()
_nominatim_last = 0.0


def census_geocode(oneline: str) -> GeoResult | None:
    try:
        r = _session.get(
            "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
            params={"address": oneline, "benchmark": "Public_AR_Current", "format": "json"}, timeout=15)
        r.raise_for_status()
        for m in r.json()["result"]["addressMatches"]:
            lat, lon = m["coordinates"]["y"], m["coordinates"]["x"]
            if in_county(lat, lon):
                # "3106 EAGLES WAY DR, LAFAYETTE, IN, 47909" -> "3106 Eagles Way Dr" (same form as TIGER)
                return GeoResult(lat, lon, "address", m["matchedAddress"].split(",")[0].title(),
                                 m["addressComponents"].get("zip"), "census")
    except Exception as e:
        log.debug(f"Census geocoder error for {oneline!r}: {e}")
    return None


def nominatim(query: str) -> GeoResult | None:
    """Bounded to the county; Nominatim's policy is max 1 request/second."""
    global _nominatim_last
    with _nominatim_lock:
        wait = 1.1 - (time.time() - _nominatim_last)
        if wait > 0:
            time.sleep(wait)
        _nominatim_last = time.time()
        try:
            r = _session.get(f"{config.NOMINATIM_URL}/search", params={
                "q": query, "format": "jsonv2", "limit": 1, "countrycodes": "us",
                "viewbox": f"{W},{N},{E},{S}", "bounded": 1, "addressdetails": 1}, timeout=15)
            r.raise_for_status()
            res = r.json()
        except Exception as e:
            log.debug(f"Nominatim error for {query!r}: {e}")
            return None
    if not res:
        return None
    hit = res[0]
    lat, lon = float(hit["lat"]), float(hit["lon"])
    if not in_county(lat, lon):
        return None
    prec = "address" if hit.get("addresstype") in ("building", "house") or hit.get("category") == "building" else "place"
    return GeoResult(lat, lon, prec, hit.get("name") or hit["display_name"].split(",")[0],
                     (hit.get("address") or {}).get("postcode"), "nominatim")


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────
_WORD_DIGITS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                "six": "6", "seven": "7", "eight": "8", "nine": "9"}


def clean_address(addr: str) -> str:
    """Strip unit numbers / city / state and turn spoken digits into numerals."""
    a = addr.strip().strip(".,")
    a = _UNIT_RE.sub("", a)
    a = re.sub(r"^(\d+)\s*,\s*", r"\1 ", a)          # "3106, Eagles Way Drive"
    if re.match(r"^\d+\s+\S", a):
        a = a.split(",")[0]                               # ", 1309" / ", LTHC" after the street
    # Trailing ", West Lafayette, IN 47906" (only after a comma, so "Lafayette Ave" survives)
    a = re.sub(rf",\s*(?:{config.PROFILE.city_regex}|{STATE_NAME}|{config.STATE})\b.*$", "", a,
               flags=re.I).strip(" ,")
    # "three six three three Regal Valley" -> "3633 Regal Valley"
    toks = a.split()
    digits = []
    while toks and toks[0].lower().strip(",") in _WORD_DIGITS:
        digits.append(_WORD_DIGITS[toks.pop(0).lower().strip(",")])
    if len(digits) >= 2:
        a = "".join(digits) + " " + " ".join(toks)
    a = re.sub(r"^(\d)-(\d)(?:-(\d))?(?:-(\d))?\b", lambda m: "".join(g for g in m.groups() if g), a)
    return re.sub(r"\s+", " ", a).strip()


class Geocoder:
    def __init__(self, conn_factory):
        self._conn = conn_factory
        self.tiger = TigerIndex.load(config.DATA_DIR / "tiger")
        self.places = Gazetteer(config.DATA_DIR / places_name())

    def _cached(self, key: str):
        row = self._conn().execute("SELECT result FROM geocache WHERE key=?", (key,)).fetchone()
        if row is None:
            return False, None
        return True, (GeoResult(**json.loads(row[0])) if row[0] else None)

    def _store(self, key: str, res: GeoResult | None):
        self._conn().execute("INSERT OR REPLACE INTO geocache(key, result) VALUES (?, ?)",
                             (key, json.dumps(res.to_dict()) if res else None))

    def geocode(self, address: str | None, place: str | None, city_hint: str = "Lafayette") -> GeoResult | None:
        near = CITY_CENTERS.get((city_hint or "").lower())
        key = f"{(address or '').lower()}|{(place or '').lower()}|{(city_hint or '').lower()}"
        hit, cached = self._cached(key)
        if hit:
            return cached
        res = self._geocode(address, place, city_hint, near)
        self._store(key, res)
        return res

    def _geocode(self, address, place, city_hint, near) -> GeoResult | None:
        if address:
            a = clean_address(address)
            m = re.match(r"^(\d{1,6})\s+(.+)$", a)
            inter = re.split(r"\s+(?:and|&|at|near)\s+", re.sub(r"^(?:the )?intersection of\s+", "", a, flags=re.I), maxsplit=1)
            if m:
                number, street = int(m.group(1)), m.group(2)
                if self.tiger:
                    r = self.tiger.address(number, street, near)
                    if r and r.precision == "address":
                        return r
                r2 = census_geocode(f"{number} {street}, {city_hint}, {config.STATE}")
                if r2:
                    return r2
                if r:      # right street, number off its known ranges
                    return r
            elif len(inter) == 2 and self.tiger:
                r = self.tiger.intersection(inter[0], inter[1], near)
                if r:
                    return r
            elif self.tiger and not place:
                r = self.tiger.street(a, near)
                if r:
                    return r
        if place and self.tiger and not address and re.search(
                r"\b(street|st|avenue|ave|road|rd|drive|dr|lane|ln|boulevard|blvd|parkway|pkwy|trail|court|ct)\b",
                place, re.I):
            r = self.tiger.street(place, near)    # "Warren Road" given as the place
            if r:
                return r
        if place:
            r = self.places.find(place, near)
            if r:
                return r
            for q in (f"{place}, {city_hint}, {STATE_NAME}", place):
                r = nominatim(q)
                if r:
                    return r
        if address:
            r = nominatim(f"{clean_address(address)}, {city_hint}, {STATE_NAME}")
            if r:
                return r
            if self.tiger:   # last resort: just the street
                street = re.sub(r"^\d+\s+", "", clean_address(address))
                street = re.split(r"\s+(?:and|&|at)\s+", street)[0]
                r = self.tiger.street(street, near)
                if r:
                    return r
        return None

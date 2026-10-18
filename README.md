# Tippecanoe Dispatch Monitor

Listens to Tippecanoe County fire/EMS dispatch radio (via [OpenMHz](https://openmhz.com/system/tippco)),
transcribes each transmission, works out the address and type of emergency, pins it on a map, and
keeps statistics on where calls happen, what kind, and when.

Monitored dispatch channels (configurable with `CHANNELS`):

| Talkgroup | Channel | Agency |
|---|---|---|
| 1827 | TCFD DISPATCH | Tippecanoe County / township fire |
| 1833 | TEAS EMS DISP | Tippecanoe EMS (ambulances) |
| 1901 | LFD DISPATCH | Lafayette Fire |
| 2021 | WLFD DISPATCH | West Lafayette Fire |
| 2105 | PUFD DISPATCH | Purdue Fire |

## How it works

```
OpenMHz API ──(curl_cffi, gets past Cloudflare)──► transmissions table (work queue)
                                                         │ newest first
                                    faster-whisper small.en (speech → text)
                                                         │
                         Ollama qwen2.5:7b, JSON-schema output (fallback: keyword rules)
                         → is it a dispatch? address, place, call type, units, patient
                                                         │
          local TIGER/Line street index for the county (offline, fuzzy street names,
          house-number interpolation, intersections) → Census geocoder → Nominatim
                                                         │
          incident grouping: the same address within 20 min on any channel = one incident
                                                         │
                         SQLite ──► FastAPI ──► dashboard (map + statistics)
```

Things worth knowing:

- **One incident, many transmissions.** A medical call is usually dispatched on TEAS EMS *and* the
  fire department channel, and the dispatcher repeats it. These merge into one incident, so the
  statistics aren't double-counted.
- **Fixed call-type taxonomy** (`dispatch/taxonomy.py`): about 45 natures ("Fall", "Chest Pain",
  "Fire Alarm", "Crash with Injuries"…) in 8 categories. The LLM must pick one, which keeps the
  statistics consistent.
- **Grounding.** Small models sometimes "correct" an address or invent a unit. The house number and
  street words must actually appear in the transcript, otherwise the regex-extracted address is used.
- **Location precision** is recorded (`address`, `intersection`, `place`, `street`). The map draws
  street-only matches as hollow markers.
- Live calls are processed first; history backfills behind them. Restarts lose nothing.

## Dashboard

`http://<host>:8086/`

- **Map**: heatmap or incident markers colored by category, plus a live feed. Click an incident for
  its transcripts, audio, and earlier incidents at the same address.
- **Statistics**: incidents per day, by category, top call types, a weekday × hour heatmap, hour of
  day by category, repeat locations, by ZIP, and by agency.
- Filters (time range, category, agency, search, weekday/hour, call type, "only map area") apply to
  everything at once. Click a chart bar or heatmap cell to filter by it. Pan the map with
  *Only map area* on to get statistics for any neighborhood.
- **Export CSV** downloads the filtered incidents.

API docs are at `/api/docs`.

## Running it

### M4 Mac mini (16 GB)

Run Ollama natively, since Docker on macOS can't use the GPU:

```bash
brew install ollama        # or the app from ollama.com
ollama serve &             # the app pulls qwen2.5:7b itself on first start

git clone https://github.com/OwenWright8/dispatch-monitor.git && cd dispatch-monitor
cp .env.example .env
docker compose up -d
```

Expected load: Whisper about 2 s per clip and the LLM about 2 s per dispatch. That's well under 5% of the machine for roughly 300
transmissions a day. Memory is about 1.5 GB for the app plus about 5 GB for the 7B model while it's loaded.

Without Docker: `pip install -r requirements.txt && OLLAMA_URL=http://localhost:11434 python -m dispatch`
(Python 3.10–3.13) and open http://localhost:8080.

### UGREEN DXP4800 Plus (32 GB), Docker / Dockge

The NAS has no GPU, so Ollama runs as a second container on the CPU. In `.env`:

```
COMPOSE_PROFILES=ollama
OLLAMA_URL=http://ollama:11434
```

then `docker compose up -d` (in Dockge, paste `docker-compose.yml` and put those two lines in its
`.env` box). The first start downloads the 7B model (about 5 GB), the Whisper model (about 500 MB) and the county street
data (about 3 MB), all into volumes.

Expected load: Whisper a few seconds per clip and the LLM about 30–60 s per dispatch on the Pentium, i.e. a
couple of CPU-hours a day, with live calls showing up within a minute or two. If it falls behind, set
`OLLAMA_MODEL=qwen2.5:3b` (about 2× faster, less accurate call types) or `WHISPER_MODEL=base.en`.

### Pre-built image

GitHub Actions publishes `ghcr.io/owenwright8/dispatch-monitor:latest` (amd64 + arm64) on every push
to `main`. To build locally instead, uncomment `build: .` in `docker-compose.yml`.

## Configuration

All settings are environment variables; see `.env.example`. The main ones:

| Variable | Default | |
|---|---|---|
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama server |
| `OLLAMA_MODEL` | `qwen2.5:7b` | pulled automatically if missing |
| `LLM_ENABLED` | `true` | `false` = keyword rules only, no Ollama |
| `WHISPER_MODEL` | `small.en` | `base.en` / `small.en` / `medium.en` |
| `BACKFILL_HOURS` | `24` | history to fetch; raise it later and the gap is filled on restart |
| `CHANNELS` | the five above | `talkgroup:agency:city-hint,…` |
| `KEEP_AUDIO` | `true` | keep dispatch clips (about 100 KB each) in `/data/audio` |
| `INCIDENT_WINDOW_MIN` | `20` | transmissions merge into one incident within this window |
| `WEB_PORT` | `8086` | host port |
| `CARTO_API_KEY` | empty | [CARTO basemaps](https://carto.com/basemaps) key for the dark/light map style; empty = OpenStreetMap tiles |

## Maintenance

```bash
# Re-run extraction + geocoding over every stored transcript, e.g. after changing the
# model, prompt, or taxonomy, or after an Ollama outage left calls on keyword rules:
docker compose exec app python -m dispatch reprocess

# Back up: everything lives in the dispatch-data volume (/data/dispatch.db + /data/audio)
```

## Caveats

- OpenMHz is a free community service behind Cloudflare. The app identifies as a browser via
  `curl_cffi`, polls every 20 s, and pages history gently. If Cloudflare changes and polling fails, the
  header shows **Feed error**. Updating `curl_cffi` usually fixes it.
- Radio transcription is imperfect. Expect a share of incidents with a misheard street or an
  "Other" call type. The detail view shows the transcript and audio so you can judge.
- Whisper occasionally misses the start of a clip or invents words on very noisy audio.

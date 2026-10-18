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

## Install

You need Docker (Docker Desktop on a Mac; it's built into the UGREEN NAS app center). Everything
else, including the AI model server, runs in the stack.

```bash
mkdir dispatch-monitor && cd dispatch-monitor
curl -fsSLO https://raw.githubusercontent.com/OwenWright8/dispatch-monitor/main/docker-compose.yml
docker compose up -d
```

Open **http://<that machine's IP>:8086**. That's it.

Both images are built by GitHub Actions with everything inside: the speech model, the AI model
(qwen2.5:7b) and the county street and place data. The first `up` pulls about 7 GB of images. After
that the stack starts in under a minute and needs no other downloads.

**Getting data in right away:** on first start the app loads the **last 24 hours** of calls in the
background while it listens live. You can load more at any time with **Load past calls** in the
dashboard header (24 h, 3 days or 7 days). The header shows how many clips are still queued. Live
calls always go first: the worker processes the newest clip next, so a call that comes in during a
backfill waits for at most the one clip already in progress. Set `BACKFILL_HOURS=0` in `.env` to start
live-only.

**Dockge / Portainer:** create a stack, paste in [`docker-compose.yml`](docker-compose.yml), and deploy.
Nothing else is needed.

**Optional:** to use the CARTO dark/light map style, create a `.env` file next to `docker-compose.yml` containing
`CARTO_API_KEY=your-key`. In Dockge, that goes in the stack's `.env` box. Then run `docker compose up -d` again.
Other settings are in [`.env.example`](.env.example).

**Update:** `docker compose pull && docker compose up -d`. Data is kept in Docker volumes.

### What to expect on each machine

| | M4 Mac mini, 16 GB | UGREEN DXP4800 Plus, 32 GB |
|---|---|---|
| Speech-to-text per clip | ~2 s | ~5 s |
| AI extraction per dispatch | ~10 s | ~30–60 s |
| Memory in use | ~7 GB | ~7 GB |
| Working through a 24 h backfill | ~1 h | ~2–3 h |

There are about 300 transmissions and 150 dispatches a day, so either machine keeps up easily. On the Mac,
Docker Desktop gives containers half the RAM by default (8 GB), which is enough. If the model ever gets
killed, raise it under *Settings → Resources*. If the NAS falls behind, set `OLLAMA_MODEL=qwen2.5:3b` in
`.env` (about 2× faster, less accurate call types).

### Building from source

```bash
git clone https://github.com/OwenWright8/dispatch-monitor.git && cd dispatch-monitor
# in docker-compose.yml, swap each "image:" line for the "build:" line under it
docker compose up -d --build
```

GitHub Actions publishes both images (amd64 + arm64) on pushes to `main`:
`ghcr.io/owenwright8/dispatch-monitor` whenever the app changes, and
`ghcr.io/owenwright8/dispatch-monitor-ollama` when `Dockerfile.ollama` changes. To pick up a newer
Ollama release, run that workflow manually. Pull requests build both images without publishing them.

## Configuration

All settings are optional and go in `.env` (see `.env.example`):

| Variable | Default | |
|---|---|---|
| `OLLAMA_MODEL` | `qwen2.5:7b` | pulled automatically if missing |
| `LLM_ENABLED` | `true` | `false` = keyword rules only, no Ollama |
| `WHISPER_MODEL` | `small.en` | `base.en` / `small.en` / `medium.en` |
| `BACKFILL_HOURS` | `24` | history to load in the background on first start; `0` = live only. More can be loaded from the dashboard. |
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

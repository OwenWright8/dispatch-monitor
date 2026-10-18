# Dispatch Monitor

Listens to a county's fire/EMS dispatch radio (via [OpenMHz](https://openmhz.com)), transcribes each
transmission, works out the address, the type of emergency and which departments were sent, pins it on a
map, and keeps statistics on where calls happen, what kind, and when.

## Counties

Each county is a profile in [`dispatch/counties/`](dispatch/counties/). Pick one with `COUNTY=` in `.env`:

| `COUNTY` | Area | OpenMHz system | Channels |
|---|---|---|---|
| `tippecanoe` (default) | Lafayette, West Lafayette, Purdue | [tippco](https://openmhz.com/system/tippco) | 1827 county/township fire, 1833 TEAS EMS, 1901 Lafayette FD, 2021 West Lafayette FD, 2105 Purdue FD |
| `hamilton` | Carmel, Fishers, Noblesville, Westfield | [hamiltonco](https://openmhz.com/system/hamiltonco) | 29010 county fire dispatch, 29006 county fire control, 29155 Noblesville FD |

A profile holds everything county-specific: the OpenMHz system and talkgroups; the departments with the
ways Whisper mishears their names ("Tees" → TEAS, "we all fire" → Wea Fire) and the unit numbers they use
(Tippecanoe 9xx = Buck Creek, Hamilton 34x = Carmel); local street and place names for the speech and AI
models; and the map area. **Adding a county is one new file** — copy `hamilton.py`, change the values,
and set `COUNTY=` to its file name. Run two counties side by side as two stacks with different
`COUNTY` and `WEB_PORT` (each stack keeps its own database volume).

## How it works

```
OpenMHz API ──(curl_cffi, gets past Cloudflare)──► transmissions table (work queue)
                                                         │ newest first
                                    faster-whisper small.en (speech → text)
                                                         │
                       name fixes from the county profile ("T.E.'s" → TEAS, "Otterbun" → Otterbein)
                                                         │
                         Ollama qwen2.5:7b, JSON-schema output (fallback: keyword rules)
                         → is it a dispatch? address, place, call type, units, patient
                         → rule checks: an alarm is not a fire; "Other Medical" only as a last resort
                         → departments: named in the tone-out, or by unit number on shared channels
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
- **Fixed call-type taxonomy** (`dispatch/taxonomy.py`): about 55 natures ("Fall", "Stroke",
  "Heart Problem / Blood Pressure", "Lift Assist", "Medical Alarm", "Fire Alarm", "Crash with Injuries"…)
  in 8 categories. The LLM must pick one, which keeps the statistics consistent. Confusable types come
  with definitions in the prompt, and rule checks then correct the common mistakes: an alarm or detector
  going off with no reported fire is a **Fire Alarm**, never a Structure Fire, and "Other Medical" is
  replaced by a specific type when the complaint names one (speech problems → Stroke, high blood
  pressure → Heart Problem / Blood Pressure).
- **Departments, not just channels.** The county fire channel tones out township departments by name
  ("TEAS, Wea Fire…"), so each incident lists the actual departments sent — Wea Township Fire, Buck
  Creek Fire, TEAS — instead of "county fire". Shared channels also map unit numbers to departments.
- **Transcripts keep both versions.** The detail view shows the corrected transcript, with what Whisper
  first heard one click away.
- **Grounding.** Small models sometimes "correct" an address or invent a unit. The house number and
  street words must actually appear in the transcript, otherwise the regex-extracted address is used.
- **Location precision** is recorded (`address`, `intersection`, `place`, `street`). The map draws
  street-only matches as hollow markers.
- Live calls are processed first; history backfills behind them. Restarts lose nothing.

## Dashboard

`http://<host>:8086/`

- **Map**: incident markers colored by category (calls from the last 15 minutes pulse and are marked
  NEW in the feed) or a heatmap, plus a live feed with the departments sent. Click an incident for its
  transcripts, audio, and earlier incidents at the same address.
- **Statistics**: incidents per day, by category, top call types, a weekday × hour heatmap, hour of
  day by category, repeat locations, by ZIP, and by department.
- The dashboard remembers your time range, map mode and department filter.
- Filters (time range, category, department, search, weekday/hour, call type, "only map area") apply to
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

## Settings

Most settings can be changed from the dashboard: **Settings** in the header. That covers:
- the AI model (on/off, Ollama address, model, timeout)
- the speech-to-text model and beam size
- which channels are monitored, how often to poll, and the minimum clip length
- the incident merge window
- whether audio is kept
- the CARTO map key

Values saved there are stored in the database, override `.env`, and apply within a few seconds without a
restart. **Reset** goes back to the `.env` / built-in value.

If the dashboard is reachable from the internet (e.g. through a reverse proxy), set `ADMIN_PASSWORD` in
`.env`. Saving settings then requires it, while viewing stays open.

**Data retention** (also in Settings): dispatch audio clips are kept for 24 hours; call details (transcripts,
summaries, patient age/sex, units) for 7 days; and the incident itself (call type, location, departments,
time) for 365 days, which is what the statistics use. `python -m dispatch reprocess` rebuilds only the
incidents that still have transcripts.

## Corrections (teach it)

Open any incident and click **Something wrong? Correct it** to fix:
- the call type
- the location (type the right address or **pick it on the map**)
- the units and departments sent
- misheard words, e.g. heard "Tablix", should be "Talblick"

The fix shows right away and is locked, so later transmissions, retries and `reprocess` won't undo it. The
app also learns from it:

- **Locations:** the corrected spot is remembered for the address and place name heard on that call, per
  department. The next call with the same (mis)heard address goes straight there.
- **Call types:** corrected calls become worked examples the AI sees for similar transcripts.
- **Misheard words:** applied to every new transcript before anything else reads it.
- **Units:** the departments follow the corrected units, and a unit number no department's pattern covers
  is learned for the call's department ("Battalion 304" → Carmel Fire).

Settings lists what it has learned, with **remove** for anything that turns out wrong. Saving needs
`ADMIN_PASSWORD` when one is set.

## Versions

Releases are tagged `vMAJOR.MINOR.PATCH` and listed on the GitHub *Releases* page with what changed. Each
release publishes the image as `:2.1.0`, `:2.1` and `:2` as well as `:latest`. To stay on a version
instead of following `:latest`, put the tag in `docker-compose.yml`, e.g.
`ghcr.io/owenwright8/dispatch-monitor:2.1`. The running version is shown in the Settings panel.

## Configuration

All settings are optional and go in `.env` (see `.env.example`). The ones marked ⚙ can also be changed
in the dashboard:

| Variable | Default | |
|---|---|---|
| `COUNTY` | `tippecanoe` | county profile in `dispatch/counties/` (`tippecanoe`, `hamilton`) |
| `OLLAMA_URL` ⚙ | `http://ollama:11434` | point at an Ollama installed on the host (e.g. `http://host.docker.internal:11434`) to use its GPU |
| `OLLAMA_MODEL` ⚙ | `qwen2.5:7b` | pulled automatically if missing |
| `LLM_ENABLED` ⚙ | `true` | `false` = keyword rules only, no Ollama |
| `WHISPER_URL` ⚙ | empty | a [whisper-asr-webservice](https://github.com/ahmetoner/whisper-asr-webservice) on a GPU (e.g. `large-v3`); the built-in model takes over if it's down |
| `WHISPER_MODEL` ⚙ | `small.en` | `base.en` / `small.en` / `medium.en` |
| `BACKFILL_HOURS` | `24` | history to load in the background on first start; `0` = live only. More can be loaded from the dashboard. |
| `CHANNELS` | the county profile's | `talkgroup:label:city-hint,…`; a label naming a department credits it |
| `AUDIO_RETENTION_HOURS` ⚙ | `24` | delete audio clips after this |
| `DETAIL_RETENTION_DAYS` ⚙ | `7` | delete transcripts, summaries, patient info and units after this |
| `INCIDENT_RETENTION_DAYS` ⚙ | `365` | delete incidents (type, location, departments) after this |
| `KEEP_AUDIO` ⚙ | `true` | keep dispatch clips (about 100 KB each) in `/data/audio` |
| `INCIDENT_WINDOW_MIN` ⚙ | `20` | transmissions merge into one incident within this window |
| `WEB_PORT` | `8086` | host port |
| `ADMIN_PASSWORD` | empty | required to save settings in the dashboard when set |
| `CARTO_API_KEY` ⚙ | empty | [CARTO basemaps](https://carto.com/basemaps) key for the dark/light map style; empty = OpenStreetMap tiles |

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

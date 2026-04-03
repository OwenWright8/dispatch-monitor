# Lafayette IN · Dispatch Monitor

Real-time fire/EMS dispatch monitoring for Tippecanoe County, Indiana.
Polls [OpenMHz](https://openmhz.com), transcribes audio with Whisper, extracts call details with Ollama, and plots everything on a live heatmap dashboard.

---

## Prerequisites

- **Ollama** running somewhere accessible — [install guide](https://ollama.com)
  ```bash
  ollama pull llama3.2
  ```
- **Docker** + Docker Compose v2

---

## Quick start (git clone → Docker)

```bash
git clone https://github.com/YOUR_USERNAME/dispatch-monitor.git
cd dispatch-monitor

cp .env.example .env
# Edit .env if Ollama isn't on the same machine

docker compose up -d --build
```

Open **http://localhost:8086/dashboard.html**

---

## Dockge / Portainer (pre-built image)

After the GitHub Actions workflow runs once, a pre-built image is published to GHCR.
You can paste this compose directly into Dockge — no source code needed.

```yaml
services:
  poller:
    image: ghcr.io/YOUR_GITHUB_USERNAME/dispatch-monitor:latest
    restart: unless-stopped
    environment:
      - OLLAMA_URL=http://host.docker.internal:11434/api/generate
      - WHISPER_MODEL=base.en
      - OLLAMA_MODEL=llama3.2
      - DB_PATH=/app/data/dispatch.db
      - CALLS_JSON=/app/data/calls.json
    volumes:
      - dispatch-data:/app/data
      - whisper-cache:/root/.cache/whisper
    extra_hosts:
      - "host.docker.internal:host-gateway"
    healthcheck:
      test: ["CMD", "python", "-c",
             "import sqlite3,os; con=sqlite3.connect(os.environ['DB_PATH']); con.execute('SELECT 1'); con.close()"]
      interval: 60s
      timeout: 10s
      start_period: 90s
      retries: 3

  web:
    # dashboard.html and nginx config are baked in — no bind mounts needed
    image: ghcr.io/YOUR_GITHUB_USERNAME/dispatch-monitor-web:latest
    restart: unless-stopped
    ports:
      - "8086:80"
    volumes:
      - dispatch-data:/usr/share/nginx/html/data:ro
    depends_on:
      poller:
        condition: service_healthy

volumes:
  dispatch-data:
  whisper-cache:
```

> **Note:** Replace `YOUR_GITHUB_USERNAME` with your actual GitHub username.
> To make the packages public: GitHub → your profile → Packages → select each package → Package settings → Change visibility → Public.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_URL` | `http://host.docker.internal:11434/api/generate` | Ollama API endpoint |
| `OLLAMA_MODEL` | `llama3.2` | Model for call extraction (`mistral`, `llama3.1`, etc.) |
| `WHISPER_MODEL` | `base.en` | Whisper model size (`tiny.en` / `base.en` / `small.en` / `medium.en`) |
| `WEB_PORT` | `8086` | Host port for the dashboard |
| `DB_PATH` | `/app/data/dispatch.db` | SQLite database path (inside container) |
| `CALLS_JSON` | `/app/data/calls.json` | JSON export path (served by nginx) |

All vars can be set in a `.env` file (copy `.env.example` to get started).

---

## Updating

```bash
# If running from source
git pull && docker compose up -d --build

# If using GHCR image (Dockge)
docker compose pull && docker compose up -d
```

---

## Architecture

```
OpenMHz API (Tippecanoe County radio)
    ↓ poll every 30s (cloudscraper — bypasses Cloudflare)
Download audio MP3
    ↓
Whisper (base.en) — speech-to-text
    ↓
Regex extraction (fast path — addresses, units, call type)
    ↓ fallback if regex incomplete
Ollama (llama3.2) — structured JSON extraction
    ↓
Nominatim geocoding (with in-memory cache)
    ↓
SQLite  →  calls.json (refreshed every 2 min)
    ↓
Nginx serves dashboard.html + calls.json
    ↓
Browser: Leaflet heatmap, live sidebar, call detail modal
```

---

## Talkgroup filter

By default the poller listens to these fire/EMS talkgroups only:

```python
TALKGROUP_FILTER = ["LFD DISP", "WLFD DISP", "TIPCO EMS", "LFD OPS", "WLFD OPS", "EMS OPS"]
```

Check [OpenMHz → tippco](https://openmhz.com/system/tippco) for the exact tag names and adjust `TALKGROUP_FILTER` in `poller.py` as needed.

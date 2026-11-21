# Lafayette IN Dispatch Monitor

Listens to Tippecanoe County fire/EMS radio via OpenMHz, transcribes with Whisper,
extracts call details with Ollama, and plots on a live heatmap dashboard.

## Quick start
```bash
brew services start ollama && ollama pull llama3.2
mkdir data
docker compose up -d
```
Then open http://localhost:8086/dashboard.html

## Updating
```bash
git pull && docker compose up -d --build
```

## Troubleshooting
- **Status pill says "RECORDED" / "FEED OFFLINE"**: the poller can't reach OpenMHz.
  Hover the pill for the reason, or check `data/status.json` / `data/poller.log`.
  Since late 2026 the OpenMHz API sits behind a Cloudflare bot challenge (HTTP 403),
  so polling needs access approved by OpenMHz. The dashboard keeps showing the last captured data.
- **Re-run extraction on stored calls** (e.g. after changing the prompt/model):
  `DB_PATH=data/dispatch.db CALLS_JSON=data/calls.json python poller.py --reextract`
- After editing `dashboard.html`, run `docker compose restart web` (single-file bind mount).

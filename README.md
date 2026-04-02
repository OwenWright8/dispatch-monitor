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

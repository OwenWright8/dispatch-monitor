# syntax=docker/dockerfile:1

# ── Stage 1: download models and county data (runs natively on the build host;
#    the results are architecture-independent) ──────────────────────────────
FROM --platform=$BUILDPLATFORM python:3.12-slim AS bundle
WORKDIR /src
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY dispatch/ dispatch/
ARG WHISPER_MODEL=small.en
RUN WHISPER_MODEL=${WHISPER_MODEL} python -m dispatch prefetch --out /bundle

# ── Stage 2: the app ─────────────────────────────────────────────────────────
# PyAV wheels bundle ffmpeg, so no system ffmpeg is needed.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=bundle /bundle /opt/bundle
COPY dispatch/ dispatch/
COPY web/ web/

ENV DATA_DIR=/data \
    BUNDLE_DIR=/opt/bundle \
    WHISPER_MODEL=small.en \
    OLLAMA_URL=http://ollama:11434 \
    OLLAMA_MODEL=qwen2.5:7b \
    HF_HUB_DISABLE_TELEMETRY=1 \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/status', timeout=5)" || exit 1

CMD ["python", "-m", "dispatch"]

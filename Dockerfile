FROM python:3.12-slim

# PyAV wheels bundle ffmpeg, so no system ffmpeg is needed.
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY dispatch/ dispatch/
COPY web/ web/

ENV DATA_DIR=/data \
    WHISPER_MODEL=small.en \
    OLLAMA_URL=http://host.docker.internal:11434 \
    OLLAMA_MODEL=qwen2.5:7b \
    HF_HUB_DISABLE_TELEMETRY=1 \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/status', timeout=5)" || exit 1

CMD ["python", "-m", "dispatch"]

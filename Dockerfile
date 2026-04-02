FROM python:3.12-slim

# ffmpeg is required by Whisper for audio decoding
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer-cached unless requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY poller.py .

# Data dir (mounted as named volume in production)
RUN mkdir -p /app/data

# Whisper model: tiny.en / base.en / small.en / medium.en
ENV WHISPER_MODEL=base.en
ENV OLLAMA_MODEL=llama3.2
ENV OLLAMA_URL=http://host.docker.internal:11434/api/generate
ENV DB_PATH=/app/data/dispatch.db
ENV CALLS_JSON=/app/data/calls.json

HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
  CMD python -c "import sqlite3,os; con=sqlite3.connect(os.environ['DB_PATH']); con.execute('SELECT 1'); con.close()" \
      || exit 1

CMD ["python", "-u", "poller.py"]

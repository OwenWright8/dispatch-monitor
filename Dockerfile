FROM python:3.12-slim

# Install ffmpeg (required by Whisper)
RUN apt-get update && apt-get install -y ffmpeg curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app files
COPY poller.py .

# Data directory (mounted as volume)
RUN mkdir -p /app/data

CMD ["python", "poller.py"]

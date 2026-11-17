FROM python:3.12-slim

# ffmpeg (montage) + DejaVu fonts (intro/outro cards, thumbnail)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV TRENDPULSE_FAST_RENDER=1

# Autonomous loop: one production cycle every TRENDPULSE_INTERVAL_SECONDS
CMD ["sh", "docker/entrypoint.sh"]

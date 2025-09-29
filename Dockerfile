# --- Layer 1: Python bot ---
FROM python:3.11-slim AS bot

WORKDIR /app

# Cài đặt ffmpeg và Java để chạy Lavalink
RUN apt-get update && apt-get install -y ffmpeg openjdk-17-jre && rm -rf /var/lib/apt/lists/*

# Copy bot code và requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# --- Run bot + Lavalink ---
CMD java -jar Lavalink.jar & python bot.py

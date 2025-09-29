FROM python:3.10-slim

WORKDIR /app

# Cài ffmpeg + Java 21 để chạy Lavalink
RUN apt-get update && apt-get install -y ffmpeg openjdk-21-jre-headless && rm -rf /var/lib/apt/lists/*

# Copy requirements và cài bot
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Chạy Lavalink + Bot
CMD java -jar Lavalink.jar & python bot.py

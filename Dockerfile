# Base image Python
FROM python:3.11-slim

# Cài đặt ffmpeg + Java
RUN apt-get update && apt-get install -y ffmpeg openjdk-21-jre && rm -rf /var/lib/apt/lists/*

# Copy code bot
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy tất cả file
COPY . .

# Mở port Lavalink
EXPOSE 2333

# Chạy Lavalink song song với bot
CMD java -jar Lavalink.jar & python bot.py

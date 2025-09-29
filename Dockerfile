FROM python:3.11-slim

# Cài ffmpeg và opus
RUN apt-get update && apt-get install -y ffmpeg libopus0 && rm -rf /var/lib/apt/lists/*

# Cài ffmpeg và libopus
RUN apt-get update && apt-get install -y ffmpeg libopus0 && rm -rf /var/lib/apt/lists/*

# Thư mục làm việc
WORKDIR /app

# Copy code
COPY . .

# Cài dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Chạy bot
CMD ["python", "bot.py"]

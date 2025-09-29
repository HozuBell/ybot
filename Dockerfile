FROM python:3.11-slim

# Cài ffmpeg
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Copy code vào container
WORKDIR /app
COPY . /app

# Cài thư viện Python
RUN pip install --no-cache-dir -r requirements.txt

# Lệnh chạy bot
CMD ["python", "bot.py"]

FROM python:3.11-slim

# Install ffmpeg and essential tools
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code (only what's needed)
COPY bot.py downloader.py .env.example ./

# Create downloads directory
RUN mkdir -p bot_downloads/saved_full_quality

# Expose health check port (Render assigns $PORT dynamically)
EXPOSE 8080

# Run bot
CMD ["python", "bot.py"]

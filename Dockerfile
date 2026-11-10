# Aio: web app + API (default) or Telegram bot (command: aio-bot) in one image.
FROM python:3.13-slim-trixie

# ffmpeg merges and converts streams; Node.js solves YouTube's JavaScript challenges for yt-dlp.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg nodejs ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY core ./core
COPY server ./server
RUN pip install --no-cache-dir ./core ./server

RUN useradd --create-home --uid 1000 aio && mkdir /data && chown aio /data
USER aio

ENV AIO_HOST=0.0.0.0 \
    AIO_PORT=8000 \
    AIO_STORAGE_DIR=/data \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request, os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"AIO_PORT\"]}/manifest.json')"

CMD ["aio-server"]

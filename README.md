<div align="center">

<img src="server/aio_server/static/icons/icon-192.png" width="96" alt="Aio logo">

# Aio

**Paste a link, keep the file.** Video, audio and images from YouTube and 1,000+ other sites,<br>
as a Python library, a command-line tool, and a self-hosted app for your browser, phone and Telegram.

[![CI](https://github.com/fonneyx/Aio/actions/workflows/ci.yml/badge.svg)](https://github.com/fonneyx/Aio/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/aio-dl)](https://pypi.org/project/aio-dl/)
[![Python](https://img.shields.io/pypi/pyversions/aio-dl)](https://pypi.org/project/aio-dl/)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)](LICENSE)

</div>

---

Downloaders usually make you pick: a powerful command line that needs a manual, or a website full of ads that
breaks every other week. Aio puts the power of [yt-dlp](https://github.com/yt-dlp/yt-dlp),
[gallery-dl](https://github.com/mikf/gallery-dl) and ffmpeg behind one simple interface, and runs on your own
machine, so it keeps working and nobody else sees what you save.

## What you get

| | |
|---|---|
| 🎬 **Video up to 4K** | AV1/VP9 for the best picture, or H.264 that plays on every device. HDR, MP4 or MKV, embedded subtitles. |
| 🎵 **Audio** | MP3 (128–320 kbps), M4A, Opus, FLAC, WAV or the untouched original, with cover art and tags. |
| 🖼 **Images** | Photo posts, carousels and albums through gallery-dl, when there's no video to take. |
| ✂️ **Trim and clean up** | Keep just a clip (`--start 1:30 --end 2:00`), or cut sponsor segments with SponsorBlock. |
| 📏 **See before you save** | Every quality with its codec and exact file size, before anything downloads. |
| 📱 **Share from your phone** | Install the web app, then tap **Share → Aio** inside TikTok, Instagram or YouTube. |
| 🤖 **Telegram bot** | Send a link, tap a quality, the file arrives in the chat. Albums for multi-item posts. |
| 🔒 **Yours** | Self-hosted, no accounts, no tracking. Files are deleted after 24 hours. Optional password. |

## Pick your way in

### 1. Self-hosted app (Docker)

```bash
git clone https://github.com/fonneyx/Aio.git && cd Aio
docker compose up -d
```

Open http://localhost:8000. Everything is included: ffmpeg, a JavaScript runtime for YouTube, the web app and the API.

- **On your phone:** open the page and choose *Add to Home Screen* / *Install app*. Aio then appears in the
  phone's Share menu. Your phone has to reach the server, so set a password first (see [Configuration](#configuration)).
- **Telegram bot:** put `AIO_TELEGRAM_TOKEN` (from [@BotFather](https://t.me/BotFather)) in `.env`, then
  `docker compose --profile bot up -d`.

### 2. Command line

```bash
pip install aio-dl        # or: uv tool install aio-dl / pipx install aio-dl
```

```bash
aio-dl https://youtu.be/dQw4w9WgXcQ                       # best video, up to 4K
aio-dl -q 1080 --compatible URL                           # 1080p H.264, plays everywhere
aio-dl -x URL                                             # MP3, 320 kbps
aio-dl -x -f flac URL                                     # lossless audio
aio-dl --start 1:30 --end 2:00 URL                        # just that clip
aio-dl -i URL                                             # list qualities and sizes, no download
aio-dl -a links.txt -o ~/Downloads                        # many links from a file
```

ffmpeg is used from your PATH; if there is none, a static build is downloaded on first use.
YouTube also needs a JavaScript runtime on PATH: [Node](https://nodejs.org), [Deno](https://deno.com) or [Bun](https://bun.sh).

### 3. Python library

```python
import aio_dl

info = aio_dl.probe("https://youtu.be/dQw4w9WgXcQ")
print(info["title"], [row["label"] for row in info["video"]])   # ... ['4K', '1440p', '1080p', ...]

files = aio_dl.download("https://youtu.be/dQw4w9WgXcQ", "downloads", kind="audio", audio_format="mp3")
```

Need cookies, a proxy or progress updates? Use an `Engine`. It is thread-safe, so one instance can serve a whole app:

```python
from aio_dl import Engine, Request

engine = Engine(cookies_file="cookies.txt", proxy="http://proxy:8080")
request = Request("https://youtu.be/dQw4w9WgXcQ", quality="720", codec="compatible", start="0:30", end="1:00")
files = engine.download(request, "downloads", on_progress=lambda **p: print(p.get("stage"), p.get("progress")))
```

See the [library README](core/README.md) for every option.

## Configuration

The app reads `AIO_*` environment variables, from `.env` with Docker or `server/.env` when run from source.
[`.env.example`](.env.example) lists them all.

| Variable | Purpose | Default |
|---|---|---|
| `AIO_AUTH` | `user:password` to require a login. **Set it before exposing the server beyond localhost.** | none |
| `AIO_TELEGRAM_TOKEN` | Telegram bot token | none |
| `AIO_TELEGRAM_USERS` | Comma-separated Telegram user IDs allowed to use the bot | everyone |
| `AIO_COOKIES_FILE` | Netscape `cookies.txt` for sites that need a login (Instagram, some Vimeo and Reddit content) | none |
| `AIO_PROXY` | Outbound proxy for extraction | none |
| `AIO_WORKERS` | Downloads processed in parallel | `2` |
| `AIO_FILE_TTL_HOURS` | How long finished files are kept | `24` |
| `AIO_HOST`, `AIO_PORT` | Listen address | `127.0.0.1`, `8000` (Docker: `0.0.0.0`) |
| `AIO_STORAGE_DIR` | Where finished files are staged | `server/storage` (Docker: `/data`) |
| `AIO_FFMPEG` | ffmpeg binary or folder | PATH, else a static build |

## HTTP API

The web app is a thin client over a small JSON API you can script against too. Interactive docs live at `/docs`.

| | |
|---|---|
| `POST /api/probe` `{"url": ...}` | What a link offers: title, thumbnail, every resolution with codec and size, audio, subtitles. `kind` is `media`, `post` (several items) or `images`. |
| `POST /api/jobs` | Start a download. Same options as the library's `Request`: `kind`, `quality`, `codec`, `hdr`, `container`, `subtitles`, `audio_format`, `audio_bitrate`, `sponsorblock`, `start`, `end`, `precise_trim`. Returns `202` and a job ID. |
| `GET /api/jobs/{id}` | `status` (`queued`, `downloading`, `processing`, `completed`, `failed`), `stage`, `progress`, `speed`, `eta`, `download_url`, `error`. |
| `GET /api/jobs/{id}/file` | The finished file. Posts and galleries come as one ZIP. |
| `GET /api/health` | Versions of Aio and yt-dlp, and the JavaScript runtimes found. |

## Run from source

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-packages
uv run aio-server          # web app on http://127.0.0.1:8000
uv run aio-bot             # Telegram bot
uv run aio-dl --help       # CLI
uv run pytest              # tests
```

```
core/     aio-dl: the library and CLI (published to PyPI)
  src/aio_dl/
    engine.py    yt-dlp + ffmpeg + gallery-dl: probing, format choice, trimming, progress, friendly errors
    request.py   Request options and validation
    summary.py   probe results: qualities, sizes, codecs
    gallery.py   image galleries through gallery-dl
    cli.py       the aio-dl command
server/   aio-server: web app, HTTP API and Telegram bot
  aio_server/
    api.py       FastAPI app, request validation, serves the web app
    bot.py       Telegram bot
    worker.py    background job pool
    jobs.py      job store (in memory)
    storage.py   staging folder and expiry
    static/      the web app: one HTML file, no build step, installable (PWA)
```

## Good to know

- **Single process.** Jobs live in memory, so a restart forgets them and clears their files.
- **Posts and short playlists** of up to 20 items are downloaded together. Whole channels and long playlists aren't supported yet.
- **Some sites need a login** for some content: export your browser's cookies to a `cookies.txt` and set `AIO_COOKIES_FILE`.
- **When a site breaks**, it's almost always fixed by a newer yt-dlp: `pip install -U aio-dl` or `docker compose pull`.

## Responsible use

Aio is for saving content you have the right to keep: your own uploads, Creative Commons and public-domain
media, or copies for personal offline use where the law allows it. Respect copyright and each platform's terms.
Run it for yourself; don't operate it as a public service.

## Contributing

Bug reports with a public example link are the most helpful thing you can send. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[GPL-3.0](LICENSE). Aio builds on yt-dlp ([Unlicense](https://github.com/yt-dlp/yt-dlp/blob/master/LICENSE)),
gallery-dl ([GPL-2.0](https://github.com/mikf/gallery-dl/blob/master/LICENSE), run as a separate process)
and ffmpeg (LGPL/GPL, a separate program). The Geist fonts in the web app are under the
[SIL Open Font License](server/aio_server/static/fonts/OFL.txt).

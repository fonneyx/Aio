# aio-grab

Download video, audio and images from YouTube and 1,000+ other sites with one call.
A friendly, typed layer over [yt-dlp](https://github.com/yt-dlp/yt-dlp), [gallery-dl](https://github.com/mikf/gallery-dl)
and ffmpeg: sensible defaults, sizes before you download, clear error messages, and one progress figure.

Part of [Aio](https://github.com/fonneyx/Aio), which also ships a self-hosted web app and Telegram bot built on this library.

```bash
pip install aio-grab
```

ffmpeg is taken from your PATH, or a static build is downloaded on first use. YouTube needs a JavaScript runtime on
PATH ([Node](https://nodejs.org), [Deno](https://deno.com) or [Bun](https://bun.sh)).

## Command line

```bash
aio-grab https://youtu.be/dQw4w9WgXcQ                   # best video, up to 4K
aio-grab -q 1080 --compatible URL                       # 1080p H.264/AAC, plays everywhere
aio-grab -x URL                                         # MP3, 320 kbps
aio-grab -x -f opus URL                                 # smallest file at the same quality
aio-grab --start 1:30 --end 2:00 URL                    # just that clip
aio-grab --sponsorblock URL                             # without sponsor segments (YouTube)
aio-grab -i URL                                         # qualities, codecs and sizes; no download
aio-grab -i --json URL                                  # the same, machine-readable
aio-grab -a links.txt -o ~/Downloads                    # one link per line
```

`aio-grab --help` lists every option. `--cookies`, `--proxy` and `--ffmpeg` also read `AIO_COOKIES_FILE`,
`AIO_PROXY` and `AIO_FFMPEG`.

## Library

```python
import aio_grab

info = aio_grab.probe("https://youtu.be/dQw4w9WgXcQ")
for row in info["video"]:
    print(row["label"], row["codecs"], row["size_sdr"])   # 4K ['AV1', 'VP9'] 244057213 ...

files = aio_grab.download("https://youtu.be/dQw4w9WgXcQ", "downloads", kind="audio", audio_format="mp3")
```

### Engine

`aio_grab.probe` and `aio_grab.download` use a shared default `Engine`. Create your own for cookies, a proxy or a
specific ffmpeg. An Engine is thread-safe: one instance can run many downloads in parallel.

```python
from aio_grab import Engine, EngineError, Request

engine = Engine(
    cookies_file="cookies.txt",     # Netscape format, for sites that need a login
    proxy="http://proxy:8080",
    ffmpeg="",                      # binary or folder; empty = PATH, else a static build
    fetch_ffmpeg=True,              # False: never download ffmpeg, fail instead
)

def on_progress(**update):
    # Any of: status, stage, progress (0-100 or None), speed (bytes/s), eta (s), title, platform, thumbnail, duration
    print(update)

try:
    files = engine.download(Request("https://youtu.be/dQw4w9WgXcQ", quality="720"), "downloads", on_progress)
except EngineError as e:
    print(e)    # a message meant for people, e.g. "This video is private."
```

`engine.download(..., zip_posts=True)` returns one ZIP for a multi-item post instead of one file per item.

### Request

Invalid combinations raise `ValueError` when the `Request` is created.

| Field | Values | Default |
|---|---|---|
| `url` | link to a video, track, post, short playlist or image gallery | required |
| `kind` | `video`, `audio` | `video` |
| `quality` | `best` (up to 4K) or a maximum resolution, e.g. `1080` | `best` |
| `codec` | `best` (AV1/VP9) or `compatible` (H.264/AAC) | `best` |
| `hdr` | prefer HDR streams | `False` |
| `container` | `mp4`, `mkv` | `mp4` |
| `subtitles` | language code to embed, e.g. `en`, or `en-orig` for auto-generated | none |
| `audio_format` | `original`, `mp3`, `m4a`, `opus`, `flac`, `wav` | `mp3` |
| `audio_bitrate` | mp3: `320` `256` `192` `128`; m4a: `256` `192` `128` | highest |
| `sponsorblock` | cut sponsor segments (YouTube) | `False` |
| `start`, `end` | trim range: seconds or `"[hh:]mm:ss"` | none |
| `precise_trim` | frame-exact cuts (re-encodes; slow above 1080p) | `False` |

### Probe results

`probe()` returns a dict whose `kind` is:

- `media`: one video or track. `video` has one row per resolution with `codecs`, `fps`, `hdr`, and the size and
  codec a download would get in each mode (`size`/`codec` for HDR, `size_sdr`/`codec_sdr`, `size_compatible`/`codec_compatible`).
  `audio` describes the best source audio; `subtitles` lists the tracks.
- `post`: several items (a carousel, a multi-video tweet, a short playlist), with `items` and `entries`.
- `images`: a photo or gallery, with `items`. Downloading it saves every image.

## License

GPL-3.0-or-later. yt-dlp is Unlicense; gallery-dl (GPL-2.0) runs as a separate process.

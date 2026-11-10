"""aio-grab: download video, audio and images from YouTube and 1,000+ other sites with one call.

    import aio_grab

    info = aio_grab.probe("https://youtu.be/dQw4w9WgXcQ")      # qualities and sizes, no download
    files = aio_grab.download("https://youtu.be/dQw4w9WgXcQ", "downloads", kind="audio", audio_format="mp3")

For cookies, a proxy or a custom ffmpeg, create an Engine and use its probe() and download().
"""

import threading
from pathlib import Path

from .engine import Engine, EngineError, ProgressCallback
from .request import AUDIO_FORMATS, CONTAINERS, MAX_RES, VIDEO_CODECS, Request, Status, parse_time

__version__ = "0.1.0"
__all__ = [
    "AUDIO_FORMATS", "CONTAINERS", "MAX_RES", "VIDEO_CODECS",
    "Engine", "EngineError", "ProgressCallback", "Request", "Status",
    "download", "parse_time", "probe",
]

_default: Engine | None = None
_default_lock = threading.Lock()


def _engine() -> Engine:
    global _default
    with _default_lock:
        if _default is None:
            _default = Engine()
        return _default


def probe(url: str) -> dict:
    """What a link offers (title, qualities with sizes, audio, subtitles), without downloading it."""
    return _engine().probe(url)


def download(url: str, out_dir: Path | str = ".", on_progress: ProgressCallback | None = None,
             **options) -> list[Path]:
    """Download url into out_dir and return the files. options are Request fields, e.g. kind="audio"."""
    return _engine().download(Request(url, **options), out_dir, on_progress)

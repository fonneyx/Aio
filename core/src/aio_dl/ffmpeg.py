import os
import shutil
from pathlib import Path


def ensure_ffmpeg_on_path(configured: str = "", fetch: bool = True) -> str | None:
    """Put ffmpeg on PATH (configured location, else system, else a static build). Returns its directory.

    yt-dlp's ffmpeg_location option isn't honored everywhere (trimmed downloads only look on PATH),
    so PATH is the one reliable way to point it at a binary. With fetch=False and no ffmpeg anywhere,
    returns None instead of downloading the static build (~190 MB unpacked).
    """
    if configured:
        directory = Path(configured)
        directory = directory if directory.is_dir() else directory.parent
    elif shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return str(Path(shutil.which("ffmpeg")).parent)
    elif not fetch:
        return None
    else:
        from static_ffmpeg.run import get_or_fetch_platform_executables_else_raise

        ffmpeg, _ffprobe = get_or_fetch_platform_executables_else_raise()
        directory = Path(ffmpeg).parent
    os.environ["PATH"] = os.pathsep.join([str(directory), os.environ.get("PATH", "")])
    return str(directory)

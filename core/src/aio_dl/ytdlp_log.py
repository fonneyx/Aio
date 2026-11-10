import logging

log = logging.getLogger("aio_dl.yt_dlp")


class YtdlpLogger:
    """Routes yt-dlp's output into logging instead of stdout."""

    def debug(self, msg: str) -> None:
        log.debug(msg)

    def info(self, msg: str) -> None:
        log.debug(msg)

    def warning(self, msg: str) -> None:
        # Probes run before ffmpeg is located; downloads always have it.
        if "ffmpeg not found" in msg:
            log.debug(msg)
        else:
            log.warning(msg)

    def error(self, msg: str) -> None:
        # Errors are raised as DownloadError and turned into EngineError by the engine.
        log.debug(msg)

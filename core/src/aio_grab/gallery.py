"""Image galleries (photo posts, carousels, albums) through gallery-dl, for links yt-dlp has no media for.

gallery-dl runs as a separate process: its configuration is global to the interpreter, so a subprocess
keeps parallel downloads apart.
"""

import logging
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

ERROR_LINE = re.compile(r"^\[[^\]]+\]\[error\] (.+)$", re.MULTILINE)
TIMEOUT_SECONDS = 600


class GalleryError(Exception):
    pass


class Gallery:
    def __init__(self, max_items: int, cookies_file: str = "", proxy: str = "") -> None:
        self.max_items = max_items
        self.cookies_file = cookies_file
        self.proxy = proxy

    def list_files(self, url: str) -> list[str]:
        """URLs of the files at url, at most max_items + 1 of them. Raises GalleryError if there are none."""
        result = self._run(["--get-urls", "--range", f"1-{self.max_items + 1}", url], timeout=120)
        files = [line.strip() for line in result.stdout.splitlines() if line.startswith(("http://", "https://"))]
        if not files:
            raise GalleryError(_error(result.stderr) or "There's nothing to download here.")
        return files

    def download(self, url: str, out_dir: Path, items: int, on_file: Callable[[int], None]) -> list[Path]:
        """Download every file at url into out_dir, calling on_file(count) after each one."""
        command = self._command(["--directory", str(out_dir), "--range", f"1-{self.max_items}", url])
        log.debug("Running %s", command)
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              encoding="utf-8", errors="replace") as process:
            count = 0
            for line in process.stdout:  # gallery-dl prints each finished file's path
                if line.strip():
                    count += 1
                    on_file(min(count, items))
            stderr = process.stderr.read()
            process.wait(timeout=TIMEOUT_SECONDS)
        files = sorted(p for p in out_dir.iterdir() if p.is_file() and not p.name.endswith(".part"))
        if not files:
            raise GalleryError(_error(stderr) or "The download finished but produced no file.")
        return files

    def _run(self, args: list[str], timeout: float) -> subprocess.CompletedProcess:
        command = self._command(args)
        log.debug("Running %s", command)
        try:
            return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            raise GalleryError("The site took too long to answer.") from None

    def _command(self, args: list[str]) -> list[str]:
        command = [sys.executable, "-m", "gallery_dl", "--quiet" if "--get-urls" in args else "--no-colors"]
        if self.cookies_file:
            command += ["--cookies", self.cookies_file]
        if self.proxy:
            command += ["--proxy", self.proxy]
        return command + args


def platform_name(url: str) -> str:
    host = urlsplit(url).hostname or ""
    parts = host.removeprefix("www.").split(".")
    return parts[-2].capitalize() if len(parts) >= 2 else host


def _error(stderr: str) -> str | None:
    errors = ERROR_LINE.findall(stderr or "")
    if not errors:
        return None
    message = errors[-1].strip()
    if message.lower().startswith("unsupported url"):
        return "This site isn't supported yet."
    return message

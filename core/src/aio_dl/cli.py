"""Command line: aio-dl URL [URL ...]"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from . import __version__
from .engine import Engine, EngineError
from .request import AUDIO_FORMATS, CONTAINERS, Request

EPILOG = """examples:
  aio-dl https://youtu.be/dQw4w9WgXcQ                 best video, up to 4K
  aio-dl -q 1080 --compatible URL                     1080p H.264, plays everywhere
  aio-dl -x URL                                       MP3, 320 kbps
  aio-dl -x -f flac URL                               lossless audio
  aio-dl --start 1:30 --end 2:00 URL                  just that clip
  aio-dl --info URL                                   list qualities and sizes, no download
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aio-dl", description="Download video, audio and images from YouTube and 1,000+ other sites.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("urls", nargs="*", metavar="URL", help="links to download")
    p.add_argument("-V", "--version", action="version", version=f"aio-dl {__version__}")
    p.add_argument("-o", "--output", type=Path, default=Path("."), metavar="DIR", help="folder to save into (default: here)")
    p.add_argument("-a", "--batch-file", type=Path, metavar="FILE", help="read links from a file, one per line")
    p.add_argument("-i", "--info", action="store_true", help="show what a link offers instead of downloading")
    p.add_argument("--json", action="store_true", help="print results as JSON")

    video = p.add_argument_group("video")
    video.add_argument("-q", "--quality", default="best", metavar="RES", help="best (up to 4K) or a max resolution, e.g. 1080")
    video.add_argument("--compatible", action="store_true", help="H.264/AAC instead of AV1/VP9: plays everywhere")
    video.add_argument("--hdr", action="store_true", help="prefer HDR streams")
    video.add_argument("--container", choices=CONTAINERS, default="mp4")
    video.add_argument("--subs", metavar="LANG", help="embed subtitles, e.g. en (en-orig for auto-generated)")

    audio = p.add_argument_group("audio")
    audio.add_argument("-x", "--audio", action="store_true", help="download audio only")
    audio.add_argument("-f", "--format", dest="audio_format", choices=list(AUDIO_FORMATS), default="mp3",
                       help="audio format (default: mp3)")
    audio.add_argument("-b", "--bitrate", dest="audio_bitrate", metavar="KBPS", help="mp3/m4a bitrate (default: highest)")

    edit = p.add_argument_group("editing")
    edit.add_argument("--start", metavar="TIME", help="trim start, seconds or [hh:]mm:ss")
    edit.add_argument("--end", metavar="TIME", help="trim end, seconds or [hh:]mm:ss")
    edit.add_argument("--precise", action="store_true", help="frame-exact trim (re-encodes, slow)")
    edit.add_argument("--sponsorblock", action="store_true", help="cut sponsor segments (YouTube)")

    net = p.add_argument_group("network")
    net.add_argument("--cookies", default=os.environ.get("AIO_COOKIES_FILE", ""), metavar="FILE",
                     help="Netscape cookies.txt for sites that need a login")
    net.add_argument("--proxy", default=os.environ.get("AIO_PROXY", ""), metavar="URL", help="outbound proxy")
    net.add_argument("--ffmpeg", default=os.environ.get("AIO_FFMPEG", ""), metavar="PATH", help="ffmpeg binary or folder")
    p.add_argument("-v", "--verbose", action="store_true", help="show yt-dlp's log")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        # Titles can hold any character; a piped Windows console would otherwise crash on them.
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8" if not stream.isatty() else stream.encoding, errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.ERROR, format="%(levelname)s %(name)s: %(message)s")

    urls = list(args.urls)
    if args.batch_file:
        lines = args.batch_file.read_text(encoding="utf-8").splitlines()
        urls += [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    if not urls:
        parser.print_usage(sys.stderr)
        print("aio-dl: error: give at least one URL", file=sys.stderr)
        return 2

    try:
        requests = [_request(url, args) for url in urls]
    except ValueError as e:
        print(f"aio-dl: error: {e}", file=sys.stderr)
        return 2

    engine = Engine(ffmpeg=args.ffmpeg, cookies_file=args.cookies, proxy=args.proxy)
    failures = 0
    results = []
    for request in requests:
        try:
            if args.info:
                summary = engine.probe(request.url)
                results.append(summary)
                if not args.json:
                    print(format_summary(summary))
            else:
                progress = None if args.json else _Progress()
                try:
                    files = engine.download(request, args.output, progress)
                finally:
                    if progress:
                        progress.clear()
                results.append({"url": request.url, "files": [str(f) for f in files]})
                if not args.json:
                    for f in files:
                        print(f"Saved {f}")
        except EngineError as e:
            failures += 1
            results.append({"url": request.url, "error": str(e)})
            print(f"aio-dl: {request.url}: {e}", file=sys.stderr)
        except KeyboardInterrupt:
            print("\nCancelled", file=sys.stderr)
            return 130
    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], indent=2, ensure_ascii=False))
    return 1 if failures else 0


def _request(url: str, args: argparse.Namespace) -> Request:
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"not a link: {url}")
    return Request(
        url,
        kind="audio" if args.audio else "video",
        quality=args.quality,
        codec="compatible" if args.compatible else "best",
        hdr=args.hdr,
        container=args.container,
        subtitles=args.subs,
        audio_format=args.audio_format,
        audio_bitrate=args.audio_bitrate,
        sponsorblock=args.sponsorblock,
        start=args.start,
        end=args.end,
        precise_trim=args.precise,
    )


def format_summary(s: dict) -> str:
    lines = [s.get("title") or s.get("url") or "Untitled"]
    details = [s.get("platform"), s.get("uploader"), f"{s['items']} items" if (s.get("items") or 0) > 1 else None,
               _clock(s.get("duration"))]
    lines.append("  " + " · ".join(d for d in details if d))
    if s.get("kind") == "images":
        lines.append(f"  {s['items']} images" if s["items"] > 1 else "  1 image")
        return "\n".join(lines)
    if s["video"]:
        lines.append("  video")
        for row in s["video"]:
            sizes = [f"{name} {_bytes(row.get(key))}" for name, key in
                     (("best", "size_sdr"), ("compatible", "size_compatible")) if row.get(key)]
            codecs = "/".join(row.get("codecs") or [])
            hdr = " HDR" if row.get("hdr") else ""
            lines.append(f"    {row['label']:>6}  {codecs}{hdr}  {'  '.join(sizes)}".rstrip())
    if s.get("audio"):
        a = s["audio"]
        lines.append(f"  audio   {a['codec']} {a['bitrate'] or '?'} kbps  {_bytes(a.get('size'))}".rstrip())
    if s.get("subtitles"):
        lines.append("  subs    " + ", ".join(t["code"] for t in s["subtitles"][:12]))
    return "\n".join(lines)


class _Progress:
    """One status line on stderr, redrawn in place."""

    def __init__(self) -> None:
        self.last = 0.0
        self.state: dict = {}
        self.tty = sys.stderr.isatty()

    def __call__(self, **changes) -> None:
        self.state.update(changes)
        now = time.monotonic()
        if now - self.last < 0.2 and "status" not in changes:
            return
        self.last = now
        s = self.state
        parts = [s.get("stage") or "Working"]
        if s.get("progress") is not None:
            parts.append(f"{s['progress']:5.1f}%")
        if s.get("speed"):
            parts.append(f"{s['speed'] / 1e6:.1f} MB/s")
        if s.get("eta"):
            parts.append(f"ETA {_clock(s['eta'])}")
        line = "  ".join(parts)
        if self.tty:
            sys.stderr.write(f"\r\033[K{line}")
        elif "stage" in changes:
            sys.stderr.write(line + "\n")
        sys.stderr.flush()

    def clear(self) -> None:
        if self.tty and self.state:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()


def _bytes(size: int | None) -> str:
    if not size:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1000:
            return f"{size:.0f} {unit}"
        size /= 1000
    return f"{size:.1f} TB"


def _clock(seconds: float | None) -> str | None:
    if not seconds:
        return None
    seconds = int(seconds)
    h, m, s = seconds // 3600, seconds // 60 % 60, seconds % 60
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


if __name__ == "__main__":
    raise SystemExit(main())

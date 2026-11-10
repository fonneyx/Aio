"""The extraction engine: turns a URL into finished files using yt-dlp, ffmpeg and gallery-dl."""

import copy
import itertools
import logging
import math
import re
import shutil
import threading
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import yt_dlp
from yt_dlp.utils import DownloadError, download_range_func

from .ffmpeg import ensure_ffmpeg_on_path
from .gallery import Gallery, GalleryError, platform_name
from .request import AUDIO_FORMATS, COVER_ART_AUDIO, Request, Status
from .summary import post_title, summarize, summarize_gallery, summarize_image, summarize_post, thumbnail
from .ytdlp_log import YtdlpLogger

log = logging.getLogger(__name__)

# Posts with several items (carousels, slideshows, multi-video tweets) and short playlists are downloaded
# whole. Anything longer is a big playlist or a channel.
MAX_POST_ITEMS = 20

SPONSORBLOCK_CATEGORIES = ["sponsor", "selfpromo", "interaction"]

# Keyed by yt-dlp's postprocessor key, which drops the "FFmpeg" prefix and "PP" suffix.
POSTPROCESSOR_STAGES = {
    "Merger": "Merging video and audio",
    "ExtractAudio": "Converting audio",
    "VideoRemuxer": "Remuxing",
    "EmbedSubtitle": "Embedding subtitles",
    "SponsorBlock": "Fetching SponsorBlock segments",
    "ModifyChapters": "Cutting sponsor segments",
    "Metadata": "Writing tags",
    "EmbedThumbnail": "Adding cover art",
}

# Some yt-dlp extractors (Wikimedia, Flickr photos, ...) return a still image as if it were media.
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "avif", "bmp", "tif", "tiff", "svg", "heic"}

UNSUPPORTED = "This site isn't supported yet."
# Lowercase substrings of yt-dlp errors -> clearer message. First match wins.
FRIENDLY_ERRORS = (
    (("unsupported url", "no suitable extractor"), UNSUPPORTED),
    (
        ("confirm you're not a bot", "confirm you’re not a bot"),
        "The platform is blocking this machine with a bot check. Use a cookies file or a proxy.",
    ),
    (
        ("--cookies", "login required", "logged-in", "authentication is required"),
        "This platform requires a logged-in session for this content. Use a cookies file.",
    ),
    (("private video",), "This video is private."),
    (
        ("timed out", "sslerror", "[ssl:", "connection reset", "connection refused", "getaddrinfo failed"),
        "Couldn't reach the site. It may be blocked on this network. Try again or use a proxy.",
    ),
)

# Lowercase substrings of yt-dlp errors that mean "no video here", e.g. a photo-only Instagram post.
GALLERY_HINTS = ("no video", "no media", "no formats", "nothing to download")

# Called with any of: status, stage, progress (0-100 or None), speed (bytes/s), eta (s),
# title, platform, thumbnail, duration.
ProgressCallback = Callable[..., None]


class EngineError(Exception):
    """A failure whose message is safe to show to the user."""


class Engine:
    """Downloads media from YouTube and 1,000+ other sites, and images from galleries.

    Thread-safe: one Engine can serve many downloads in parallel.
    """

    def __init__(self, *, ffmpeg: str = "", cookies_file: str = "", proxy: str = "", fetch_ffmpeg: bool = True,
                 max_post_items: int = MAX_POST_ITEMS) -> None:
        """
        ffmpeg: ffmpeg binary or folder. Empty: PATH, else a static build downloaded on first use.
        cookies_file: Netscape cookies.txt for sites that need a login.
        proxy: outbound proxy, e.g. http://user:pass@host:port
        fetch_ffmpeg: download a static ffmpeg when none is found. If False, a missing ffmpeg is an error.
        """
        self.ffmpeg = ffmpeg
        self.fetch_ffmpeg = fetch_ffmpeg
        self.max_post_items = max_post_items
        self.js_runtimes = {name: {} for name in ("deno", "node", "bun") if shutil.which(name)}
        if not self.js_runtimes:
            log.warning("No JavaScript runtime (deno, node or bun) found; YouTube extraction will fail")
        self.cookies_file = cookies_file
        self.proxy = proxy
        self.gallery = Gallery(max_post_items, cookies_file, proxy)
        self._cache = _InfoCache(ttl_seconds=600)
        self._ffmpeg_dir: str | None = None
        self._ffmpeg_lock = threading.Lock()

    # ---- public API ----

    def ensure_ffmpeg(self) -> str:
        """Locate (or fetch) ffmpeg and put it on PATH. Called automatically before the first download."""
        with self._ffmpeg_lock:
            if self._ffmpeg_dir is None:
                directory = ensure_ffmpeg_on_path(self.ffmpeg, fetch=self.fetch_ffmpeg)
                if directory is None:
                    raise EngineError("ffmpeg isn't installed. Install it or allow fetching a static build.")
                self._ffmpeg_dir = directory
            return self._ffmpeg_dir

    def probe(self, url: str) -> dict:
        """Look up a link without downloading: metadata plus the qualities actually available.

        The result's "kind" is "media" (one video or track), "post" (several items, downloaded together)
        or "images" (a photo gallery).
        """
        try:
            with yt_dlp.YoutubeDL(self._base_options()) as ydl:
                raw = self._extract(ydl, url)
                if _is_post(raw):
                    self._cache.put(url, raw)
                    return summarize_post(raw)
                if _is_image(raw):
                    self._cache.put(url, raw)
                    return summarize_image(raw)
                try:
                    # Processing sorts the formats (worst -> best), which summarize() relies on.
                    info = ydl.process_ie_result(copy.deepcopy(raw), download=False)
                except DownloadError as e:
                    raise EngineError(friendly_error(str(e))) from e
        except EngineError as error:
            if not _may_be_gallery(error):
                raise
            return self._probe_gallery(url, error)
        if _is_post(info):  # a redirect that only turned out to be a playlist once followed
            raise EngineError("This link points to a playlist. Paste the playlist's own link instead.")
        self._cache.put(url, raw)
        return summarize(info)

    def download(self, request: Request, out_dir: Path | str, on_progress: ProgressCallback | None = None,
                 *, zip_posts: bool = False) -> list[Path]:
        """Download the media at request.url into out_dir and return the files.

        A multi-item post or gallery gives one file per item, or a single ZIP with zip_posts=True.
        """
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        report = on_progress or _ignore
        files, title = self._download(request, out_dir, report)
        if zip_posts and len(files) > 1:
            return [bundle(files, out_dir, title)]
        return files

    # ---- yt-dlp ----

    def _download(self, request: Request, out_dir: Path, report: ProgressCallback) -> tuple[list[Path], str | None]:
        progress = _ProgressTracker(report)
        try:
            # A probe just before the download already has the info; reuse it to skip a second lookup.
            info = self._cache.get(request.url)
            if info is None:
                report(status=Status.DOWNLOADING, stage="Analyzing link")
                try:
                    with yt_dlp.YoutubeDL(self._base_options()) as ydl:
                        info = self._extract(ydl, request.url)
                except EngineError as error:
                    if not _may_be_gallery(error):
                        raise
                    return self._download_gallery(request.url, out_dir, report, error)
            if info.get("_aio_gallery"):
                return self._download_gallery(request.url, out_dir, report, None, info["_aio_gallery"])
            if not _is_post(info) and _is_image(info):
                return self._download_image(info, out_dir, progress, report)
            self.ensure_ffmpeg()
            post = _is_post(info)
            entries = info["entries"] if post else [info]
            title = post_title(info) if post else info.get("title")
            report(
                title=title,
                platform=info.get("extractor_key"),
                thumbnail=thumbnail(entries[0]) or thumbnail(info),
                duration=sum(e.get("duration") or 0 for e in entries) or None,
            )
            options = self._options(request, out_dir, progress, post=post)
            if post:
                progress.items = len(entries)
            else:
                progress.streams = _stream_count(request, info)
            report(status=Status.DOWNLOADING, stage="Downloading clip" if request.trimmed else "Downloading")
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.process_ie_result(info, download=True)
        except DownloadError as e:
            raise EngineError(friendly_error(str(e))) from e

        # A failed item in a post is skipped (None entry) rather than failing the whole post.
        done = (info.get("entries") or []) if post else [info]
        files = [Path(e["requested_downloads"][0]["filepath"]) for e in done if e and e.get("requested_downloads")]
        files = [f for f in files if f.is_file()]
        if not files:
            raise EngineError("The download finished but produced no file.")
        return files, title

    def _download_image(self, info: dict, out_dir: Path, progress: "_ProgressTracker",
                        report: ProgressCallback) -> tuple[list[Path], str | None]:
        """A still image: saved as is, with none of the video remuxing or tagging."""
        report(title=info.get("title"), platform=info.get("extractor_key"), thumbnail=thumbnail(info) or _image_url(info),
               status=Status.DOWNLOADING, stage="Downloading image")
        options = self._base_options()
        options.update(outtmpl=str(out_dir / "%(title).90B [%(id)s].%(ext)s"), format="best",
                       progress_hooks=[progress.on_download])
        with yt_dlp.YoutubeDL(options) as ydl:
            done = ydl.process_ie_result(info, download=True)
        files = [Path(d["filepath"]) for d in done.get("requested_downloads") or () if Path(d["filepath"]).is_file()]
        if not files:
            raise EngineError("The download finished but produced no file.")
        return files, info.get("title")

    def _extract(self, ydl: yt_dlp.YoutubeDL, url: str) -> dict:
        """The extractor's raw result, before any format is chosen, so every download can make its own choice.

        A processed info dict would carry the previous selection (requested_formats and friends) into the
        next process_ie_result call, and an audio download would fetch the video stream too.
        """
        try:
            raw = ydl.extract_info(url, download=False, process=False)
            self._check_supported(raw)
        except DownloadError as e:
            raise EngineError(friendly_error(str(e))) from e
        return raw

    def _check_supported(self, info: dict) -> None:
        """Reject what can't be captured. Materializes a post's entries (at most max_post_items + 1)."""
        if info.get("is_live"):
            raise EngineError("Live streams can't be captured.")
        if _is_post(info):
            entries = list(itertools.islice(info.get("entries") or (), self.max_post_items + 1))
            if len(entries) > self.max_post_items:
                raise EngineError(
                    f"This playlist or channel has more than {self.max_post_items} items. "
                    f"Posts and playlists of up to {self.max_post_items} items are supported."
                )
            if not entries:
                raise EngineError("There's nothing to download in this post.")
            info["entries"] = entries

    def _base_options(self) -> dict:
        options = {
            "windowsfilenames": True,
            "noplaylist": True,
            # Resolve playlists without fetching every entry; long ones are rejected anyway.
            "extract_flat": "in_playlist",
            # Known sites only. The generic extractor would fetch arbitrary pages.
            "allowed_extractors": ["default", "-generic"],
            "quiet": True,
            "noprogress": True,
            "color": {"stdout": "no_color", "stderr": "no_color"},
            "logger": YtdlpLogger(),
            "retries": 3,
            "socket_timeout": 30,
            "concurrent_fragment_downloads": 4,
            # Chunked requests avoid per-connection throttling on long downloads.
            "http_chunk_size": 10 * 1024 * 1024,
        }
        if self.js_runtimes:
            options["js_runtimes"] = self.js_runtimes
        if self.cookies_file:
            options["cookiefile"] = self.cookies_file
        if self.proxy:
            options["proxy"] = self.proxy
        return options

    def _options(self, request: Request, out_dir: Path, progress: "_ProgressTracker", post: bool = False) -> dict:
        options = self._base_options()
        options["outtmpl"] = str(out_dir / "%(title).90B [%(id)s].%(ext)s")
        if post:
            # Download every entry (not just list it), number the files, and keep going past a failed item.
            options["extract_flat"] = False
            options["outtmpl"] = str(out_dir / "%(playlist_index)02d %(title).80B [%(id)s].%(ext)s")
            options["ignoreerrors"] = "only_download"
        options["progress_hooks"] = [progress.on_download]
        options["postprocessor_hooks"] = [progress.on_postprocess]

        # Order follows yt-dlp's own CLI: cut detection, conversion, subtitles, chapter edits, tags, cover.
        postprocessors = []
        if request.sponsorblock:
            postprocessors.append({"key": "SponsorBlock", "categories": SPONSORBLOCK_CATEGORIES, "when": "after_filter"})

        if request.kind == "audio":
            audio = AUDIO_FORMATS[request.audio_format]
            options["format"] = audio.selector
            postprocessors.append(
                {"key": "FFmpegExtractAudio", "preferredcodec": audio.codec, "preferredquality": request.audio_bitrate}
            )
            cover_art = request.audio_format in COVER_ART_AUDIO
        else:
            options["format"] = "bv*+ba/b"
            options["format_sort"] = request.format_sort()
            options["merge_output_format"] = request.container
            postprocessors.append({"key": "FFmpegVideoRemuxer", "preferedformat": request.container})
            # yt-dlp doesn't trim subtitle tracks, so a full-length track would stretch a trimmed clip.
            if request.subtitles and not request.trimmed:
                options["writesubtitles"] = True
                options["writeautomaticsub"] = True
                options["subtitleslangs"] = [request.subtitles]
                options["subtitlesformat"] = "vtt/srt/ass/best"
                postprocessors.append({"key": "FFmpegEmbedSubtitle"})
            cover_art = True

        if request.sponsorblock:
            postprocessors.append({"key": "ModifyChapters", "remove_sponsor_segments": SPONSORBLOCK_CATEGORIES})
        postprocessors.append({"key": "FFmpegMetadata"})  # title/artist tags and chapters
        if cover_art:
            options["writethumbnail"] = True
            postprocessors.append({"key": "EmbedThumbnail"})
        options["postprocessors"] = postprocessors

        if request.trimmed:
            section = (request.start or 0, request.end if request.end is not None else math.inf)
            options["download_ranges"] = download_range_func(None, [section])
            options["force_keyframes_at_cuts"] = request.precise_trim
        return options

    # ---- gallery-dl fallback ----

    def _probe_gallery(self, url: str, error: EngineError) -> dict:
        """yt-dlp found no media at url. If gallery-dl finds images there, describe those instead."""
        files = self._list_gallery(url, error)
        self._cache.put(url, {"_aio_gallery": len(files)})
        return summarize_gallery(url, platform_name(url), files)

    def _list_gallery(self, url: str, error: EngineError) -> list[str]:
        try:
            files = self.gallery.list_files(url)
        except GalleryError as gallery_error:
            # yt-dlp's message is the more useful one unless it just didn't know the site.
            raise (EngineError(str(gallery_error)) if str(error) == UNSUPPORTED else error) from None
        if len(files) > self.max_post_items:
            raise EngineError(f"This gallery has more than {self.max_post_items} items.")
        return files

    def _download_gallery(self, url: str, out_dir: Path, report: ProgressCallback, error: EngineError | None,
                          items: int | None = None) -> tuple[list[Path], str | None]:
        if items is None:
            items = len(self._list_gallery(url, error))
        report(platform=platform_name(url), status=Status.DOWNLOADING, stage="Downloading images", progress=0.0)

        def on_file(count: int) -> None:
            report(status=Status.DOWNLOADING, stage=f"Downloading {count} of {items}",
                   progress=round(count / items * 100, 1))

        try:
            files = self.gallery.download(url, out_dir, items, on_file)
        except GalleryError as e:
            raise EngineError(str(e)) from None
        return files, None


def bundle(files: list[Path], out_dir: Path, title: str | None) -> Path:
    """One ZIP for a multi-item post. Media is already compressed, so files are stored, not deflated."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", title or "").strip()[:80] or "Aio download"
    archive = out_dir / f"{name} ({len(files)} items).zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as zf:
        for f in files:
            zf.write(f, f.name)
    for f in files:
        f.unlink()
    return archive


def friendly_error(message: str) -> str:
    message = message.removeprefix("ERROR: ").strip()
    lowered = message.lower()
    for needles, friendly in FRIENDLY_ERRORS:
        if any(needle in lowered for needle in needles):
            return friendly
    message = re.sub(r"^\[[^\]]+\] [^:]+: ", "", message)  # "[youtube] dQw4w9WgXcQ: ..."
    return message.split("; please report this issue")[0]


def _may_be_gallery(error: EngineError) -> bool:
    """Whether yt-dlp failed because there was no video at all, so gallery-dl may find images instead."""
    message = str(error).lower()
    return str(error) == UNSUPPORTED or any(s in message for s in GALLERY_HINTS)


def _is_image(info: dict) -> bool:
    formats = info.get("formats") or ([info] if info.get("url") else [])
    return bool(formats) and all(
        _extension(f) in IMAGE_EXTENSIONS and f.get("vcodec") in (None, "none") for f in formats
    )


def _extension(f: dict) -> str:
    return (f.get("ext") or Path(urlsplit(f.get("url") or "").path).suffix.lstrip(".")).lower()


def _image_url(info: dict) -> str | None:
    formats = info.get("formats") or [info]
    return formats[-1].get("url")


def _is_post(info: dict) -> bool:
    return info.get("_type") in ("playlist", "multi_video")


def _stream_count(request: Request, info: dict) -> int:
    """How many separate downloads yt-dlp will make, so progress can span all of them."""
    if request.kind == "audio" or request.trimmed:  # a trimmed download is one ffmpeg pass
        return 1
    has_audio_only = any(f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")
                         for f in info.get("formats") or ())
    return 2 if has_audio_only else 1


def _ignore(**_changes) -> None:
    pass


class _InfoCache:
    """Recently extracted info dicts, so a download right after a probe skips a second extraction."""

    def __init__(self, ttl_seconds: float, max_items: int = 64) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self._items: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def put(self, url: str, info: dict) -> None:
        with self._lock:
            self._items[url] = (time.monotonic(), info)
            while len(self._items) > self.max_items:
                self._items.pop(next(iter(self._items)))

    def get(self, url: str) -> dict | None:
        with self._lock:
            entry = self._items.get(url)
            if entry is None or time.monotonic() - entry[0] > self.ttl_seconds:
                return None
            # yt-dlp mutates info while processing; every download gets its own copy.
            return copy.deepcopy(entry[1])


class _ProgressTracker:
    """Folds yt-dlp's per-stream progress events into one 0-100 figure."""

    def __init__(self, report: ProgressCallback) -> None:
        self.report = report
        self.streams = 1
        self.streams_done = 0
        self.items = 1  # entries in a multi-item post

    def on_download(self, d: dict) -> None:
        if d["status"] == "finished":
            self.streams_done += 1
            return
        if d["status"] != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        fraction = min(d.get("downloaded_bytes", 0) / total, 1.0) if total else None
        progress = None
        if self.items > 1:
            index = d.get("info_dict", {}).get("playlist_index") or 1
            stage = f"Downloading {index} of {self.items}"
            if fraction is not None:
                progress = round((index - 1 + fraction) / self.items * 100, 1)
        else:
            if fraction is not None:
                progress = round((min(self.streams_done, self.streams - 1) + fraction) / self.streams * 100, 1)
            stage = "Downloading"
            if self.streams > 1:
                stage += " audio" if d.get("info_dict", {}).get("vcodec") == "none" else " video"
        eta = d.get("eta")
        self.report(
            status=Status.DOWNLOADING,
            stage=stage,
            progress=progress,
            speed=d.get("speed"),
            eta=int(eta) if eta is not None else None,
        )

    def on_postprocess(self, d: dict) -> None:
        if d["status"] == "started":
            stage = POSTPROCESSOR_STAGES.get(d["postprocessor"], "Finishing up")
            self.report(status=Status.PROCESSING, stage=stage, progress=None, speed=None, eta=None)

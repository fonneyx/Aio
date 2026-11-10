"""Probe results: what a link offers (qualities, sizes, audio, subtitles) without downloading it."""

import yt_dlp

from .request import MAX_RES, Request
from .ytdlp_log import YtdlpLogger

# Video modes the probe estimates sizes for, as the request each one corresponds to.
SIZE_MODES = {
    "size": {"hdr": True},
    "size_sdr": {"hdr": False},
    "size_compatible": {"codec": "compatible"},
}

CODEC_NAMES = (
    ("avc", "H.264"), ("h264", "H.264"), ("hev", "HEVC"), ("hvc", "HEVC"), ("h265", "HEVC"),
    ("av01", "AV1"), ("vp09", "VP9"), ("vp9", "VP9"), ("vp8", "VP8"),
    ("mp4a", "AAC"), ("aac", "AAC"), ("opus", "Opus"), ("vorbis", "Vorbis"), ("mp3", "MP3"), ("flac", "FLAC"),
)


def summarize(info: dict) -> dict:
    """Public description of an extracted link: metadata, video rows per resolution, audio, subtitles."""
    formats = info.get("formats") or []  # processed: sorted worst -> best by yt-dlp's default order
    audio_only = [f for f in formats if is_audio_only(f)]
    best_audio = audio_only[-1] if audio_only else None
    video = [f for f in formats if f.get("vcodec") not in (None, "none") and _res(f)]

    # For each mode, the format a download would pick at each resolution, ranked exactly as it ranks them.
    picks: dict[str, dict[int, tuple[dict, dict | None]]] = {}
    for key, overrides in SIZE_MODES.items():
        request = Request(url="", **overrides)
        ranked = _rank(info, request.format_sort())
        audio = next((f for f in reversed(ranked) if is_audio_only(f)), None)
        picks[key] = {}
        for f in ranked:  # worst -> best, so the last one per resolution wins
            if f.get("vcodec") in (None, "none") or not _res(f):
                continue
            if request.codec == "compatible" and codec_name(f.get("vcodec")) != "H.264":
                continue
            picks[key][_res(f)] = (f, audio)

    rows = []
    for res in sorted({_res(f) for f in video}, reverse=True):
        if not 144 <= res <= MAX_RES:
            continue
        at_res = [f for f in video if _res(f) == res]
        row = {
            "res": res,
            "label": quality_label(res),
            "fps": max((f.get("fps") or 0) for f in at_res) or None,
            "hdr": any(_is_hdr(f) for f in at_res),
            "sdr": any(not _is_hdr(f) for f in at_res),
            "codecs": sorted({codec_name(f.get("vcodec")) for f in at_res}),
        }
        for key in SIZE_MODES:
            pick = picks[key].get(res)
            row[key] = _size(*pick) if pick else None
            # "size_sdr" -> "codec_sdr": the video codec that mode ends up with at this resolution.
            row[key.replace("size", "codec")] = codec_name(pick[0].get("vcodec")) if pick else None
        rows.append(row)

    subtitles = [
        {"code": code, "name": tracks[0].get("name") or code, "auto": False}
        for code, tracks in (info.get("subtitles") or {}).items()
        if tracks and code != "live_chat"
    ]
    subtitles += [
        {
            "code": code,
            "name": f"{(tracks[0].get('name') or code).removesuffix(' (Original)')} (auto-generated)",
            "auto": True,
        }
        for code, tracks in (info.get("automatic_captions") or {}).items()
        if tracks and code.endswith("-orig")
    ]

    return {
        "kind": "media",
        "url": info.get("webpage_url") or info.get("original_url"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "platform": info.get("extractor_key"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "video": rows,
        "audio": {
            "codec": codec_name(best_audio.get("acodec")),
            "bitrate": round(best_audio.get("abr") or best_audio.get("tbr") or 0) or None,
            "size": _size(best_audio),
        } if best_audio else None,
        "subtitles": subtitles,
        "chapters": len(info.get("chapters") or []),
        "sponsorblock": info.get("extractor_key") == "Youtube",
    }


def summarize_post(info: dict) -> dict:
    """Probe summary for a multi-item post or short playlist. Sizes are unknown until each item is processed."""
    entries = info["entries"]
    resolutions = sorted({
        res for e in entries for f in e.get("formats") or ()
        if f.get("vcodec") not in (None, "none") and (res := _res(f)) and 360 <= res <= MAX_RES
    }, reverse=True)
    blank = {"fps": None, "hdr": False, "sdr": True, "codecs": [], "size": None, "size_sdr": None,
             "size_compatible": None, "codec": None, "codec_sdr": None, "codec_compatible": None}
    first = entries[0]
    return {
        "kind": "post",
        "url": info.get("webpage_url") or info.get("original_url"),
        "title": post_title(info),
        "uploader": info.get("uploader") or info.get("channel") or first.get("uploader") or first.get("channel"),
        "platform": info.get("extractor_key"),
        "thumbnail": thumbnail(first) or thumbnail(info),
        "duration": sum(e.get("duration") or 0 for e in entries) or None,
        "items": len(entries),
        "entries": [{"title": e.get("title"), "thumbnail": thumbnail(e), "duration": e.get("duration")} for e in entries],
        "video": [{"res": res, "label": quality_label(res), **blank} for res in resolutions],
        "audio": None,
        "subtitles": [],
        "chapters": 0,
        "sponsorblock": False,
    }


def summarize_gallery(url: str, platform: str | None, files: list[str]) -> dict:
    """Probe summary for an image gallery (photos, carousels, albums) handled by gallery-dl."""
    return {
        "kind": "images",
        "url": url,
        "title": None,
        "uploader": None,
        "platform": platform,
        "thumbnail": next((f for f in files if not f.split("?")[0].lower().endswith((".mp4", ".webm", ".mov"))), None),
        "duration": None,
        "items": len(files),
        "entries": [],
        "video": [],
        "audio": None,
        "subtitles": [],
        "chapters": 0,
        "sponsorblock": False,
    }


def summarize_image(info: dict) -> dict:
    """Probe summary for a single still image that yt-dlp found (e.g. a Wikimedia Commons file page)."""
    formats = info.get("formats") or [info]
    return {
        **summarize_gallery(info.get("webpage_url") or info.get("original_url"), info.get("extractor_key"),
                            [formats[-1].get("url") or ""]),
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "thumbnail": thumbnail(info) or formats[-1].get("url"),
    }


def post_title(info: dict) -> str | None:
    entries = info.get("entries") or []
    return info.get("title") or (entries[0].get("title") if entries else None)


def thumbnail(info: dict) -> str | None:
    return info.get("thumbnail") or ((info.get("thumbnails") or [{}])[-1]).get("url")


def is_audio_only(f: dict) -> bool:
    return f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")


def quality_label(res: int) -> str:
    return "4K" if res >= 2160 else f"{res}p"


def codec_name(codec: str | None) -> str:
    codec = (codec or "").lower()
    for prefix, name in CODEC_NAMES:
        if codec.startswith(prefix):
            return name
    return codec.split(".")[0].upper() or "Unknown"


def _rank(info: dict, format_sort: list[str]) -> list[dict]:
    """info's formats sorted worst -> best the way a download with this format_sort would sort them."""
    ranking = {"formats": list(info.get("formats") or []), "_format_sort_fields": info.get("_format_sort_fields")}
    with yt_dlp.YoutubeDL({"format_sort": format_sort, "quiet": True, "logger": YtdlpLogger()}) as ydl:
        ydl.sort_formats(ranking)
    return ranking["formats"]


def _res(f: dict) -> int | None:
    # Matches yt-dlp's "res" sort field: the smaller dimension, so vertical 1080x1920 counts as 1080.
    width, height = f.get("width"), f.get("height")
    return min(width, height) if width and height else height


def _is_hdr(f: dict) -> bool:
    return f.get("dynamic_range") not in (None, "SDR")


def _size(*formats: dict | None) -> int | None:
    """Combined size of a video format and the audio it would be merged with. None if unknown."""
    if formats[0] is None:
        return None
    if len(formats) > 1 and formats[0].get("acodec") not in (None, "none"):
        formats = formats[:1]  # already has audio, nothing gets merged
    total = 0
    for f in formats:
        size = f.get("filesize") or f.get("filesize_approx") if f else None
        if not size:
            return None
        total += size
    return int(total)

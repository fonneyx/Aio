"""What to download: the Request options, their allowed values, and how they map to yt-dlp format sorting."""

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple

KINDS = ("video", "audio")
VIDEO_CODECS = ("best", "compatible")  # best: AV1/VP9 up to 4K; compatible: H.264/AAC, plays everywhere
# Highest resolution offered. 8K files run to tens of GB and are too heavy to play or edit on most machines.
MAX_RES = 2160
CONTAINERS = ("mp4", "mkv")
SUBTITLE_CODE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class AudioFormat(NamedTuple):
    codec: str  # yt-dlp FFmpegExtractAudio codec ("best" keeps the source codec)
    selector: str  # which source stream to fetch
    bitrates: tuple[str, ...]  # kbps choices, highest first; empty when not applicable


AUDIO_FORMATS = {
    "mp3": AudioFormat("mp3", "ba/b", ("320", "256", "192", "128")),
    "m4a": AudioFormat("m4a", "ba[acodec^=mp4a]/ba/b", ("256", "192", "128")),
    "opus": AudioFormat("opus", "ba[acodec=opus]/ba/b", ()),
    "original": AudioFormat("best", "ba/b", ()),
    "flac": AudioFormat("flac", "ba/b", ()),
    "wav": AudioFormat("wav", "ba/b", ()),
}
# Containers yt-dlp can embed cover art into. "original" is excluded because its extension isn't known upfront.
COVER_ART_AUDIO = {"mp3", "m4a", "opus", "flac"}


class Status(StrEnum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class Request:
    """One download. Invalid combinations raise ValueError on construction."""

    url: str
    kind: str = "video"  # "video" or "audio"
    # Video
    quality: str = "best"  # "best" or a maximum resolution such as "1080" (the smaller dimension)
    codec: str = "best"  # "best" (AV1/VP9, up to 4K) or "compatible" (H.264/AAC)
    hdr: bool = False
    container: str = "mp4"
    subtitles: str | None = None  # language code to embed, e.g. "en", or "en-orig" for auto-generated
    # Audio
    audio_format: str = "mp3"
    audio_bitrate: str | None = None  # kbps, for mp3 and m4a; defaults to the highest
    # Both
    sponsorblock: bool = False  # cut sponsor, self-promo and interaction-reminder segments (YouTube)
    start: float | None = None  # trim start, seconds
    end: float | None = None  # trim end, seconds
    # Re-encode around the cut points for frame-exact trims. Very CPU-heavy above 1080p; off by default,
    # in which case cuts snap to the nearest keyframe (a second or two early) with no re-encoding.
    precise_trim: bool = False

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"Kind must be one of: {', '.join(KINDS)}")
        quality = str(self.quality)
        if quality != "best" and not (quality.isdigit() and 144 <= int(quality) <= MAX_RES):
            raise ValueError(f"Quality must be 'best' or a resolution from 144 to {MAX_RES}")
        object.__setattr__(self, "quality", quality)
        if self.codec not in VIDEO_CODECS:
            raise ValueError(f"Codec must be one of: {', '.join(VIDEO_CODECS)}")
        if self.container not in CONTAINERS:
            raise ValueError(f"Container must be one of: {', '.join(CONTAINERS)}")
        if self.subtitles is not None and not SUBTITLE_CODE.match(self.subtitles):
            raise ValueError("Subtitles must be a language code such as 'en'")
        audio = AUDIO_FORMATS.get(self.audio_format)
        if audio is None:
            raise ValueError(f"Audio format must be one of: {', '.join(AUDIO_FORMATS)}")
        if self.kind != "audio" or not audio.bitrates:
            bitrate = None
        else:
            bitrate = str(self.audio_bitrate or audio.bitrates[0])
            if bitrate not in audio.bitrates:
                raise ValueError(f"Bitrate for {self.audio_format} must be one of: {', '.join(audio.bitrates)}")
        object.__setattr__(self, "audio_bitrate", bitrate)
        for name in ("start", "end"):
            object.__setattr__(self, name, parse_time(getattr(self, name)))
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("Trim end must be after trim start")

    @property
    def trimmed(self) -> bool:
        return self.start is not None or self.end is not None

    def format_sort(self) -> list[str]:
        """yt-dlp format_sort for a video request. Earlier fields win.

        "proto" prefers direct downloads over HLS: HLS manifests advertise peak bitrate, so they look
        bigger without being better, and their many fragments download slower.
        """
        res = f"res:{MAX_RES if self.quality == 'best' else self.quality}"
        if self.codec == "compatible":
            # H.264 first, even if that means a lower resolution than AV1/VP9 would give.
            return ["vcodec:h264", res, "fps", "proto", "acodec:aac", "ext:mp4:m4a"]
        # HDR looks washed out on SDR screens, so SDR wins unless HDR is asked for, even over a higher frame rate.
        dynamic_range = "hdr" if self.hdr else "hdr:sdr"
        return [res, dynamic_range, "fps", "vcodec", "proto", "ext:mp4:m4a"]


def parse_time(value: float | int | str | None) -> float | None:
    """Seconds from a number or a "[hh:]mm:ss" string. None and "" mean no limit."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        parts = value.strip().split(":")
        if len(parts) > 3:
            raise ValueError("Use seconds or [hh:]mm:ss")
        try:
            seconds = 0.0
            for part in parts:
                seconds = seconds * 60 + float(part)
        except ValueError:
            raise ValueError("Use seconds or [hh:]mm:ss") from None
        value = seconds
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("Use seconds or [hh:]mm:ss")
    if not math.isfinite(value) or value < 0:
        raise ValueError("Must be a positive time")
    return float(value)

from pathlib import Path

import pytest

from aio_grab import Engine, Request
from aio_grab.engine import _ProgressTracker, friendly_error
from aio_grab.summary import summarize

# Real yt-dlp error messages captured from live runs.
REPORT_BOILERPLATE = (
    "; please report this issue on  https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the appropriate"
    " issue template. Confirm you are on the latest version using  yt-dlp -U"
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ERROR: No suitable extractor found for URL https://example.com/some/page", "This site isn't supported yet."),
        (
            "ERROR: [vimeo] 76979871: The web client only works when logged-in. Use --cookies, --cookies-from-browser",
            "This platform requires a logged-in session for this content. Use a cookies file.",
        ),
        (
            "ERROR: [Reddit] 6rrwyj: Account authentication is required. Use --cookies-from-browser or --cookies",
            "This platform requires a logged-in session for this content. Use a cookies file.",
        ),
        (
            "ERROR: [TikTok] 6718335390845095173: Unable to download webpage: HTTPSConnectionPool(host='www.tiktok.com',"
            " port=443): Read timed out. (read timeout=30.0)",
            "Couldn't reach the site. It may be blocked on this network. Try again or use a proxy.",
        ),
        (
            "ERROR: [dailymotion] x5kesuj: Unable to download JSON metadata: [SSL: UNEXPECTED_EOF_WHILE_READING] EOF"
            + REPORT_BOILERPLATE,
            "Couldn't reach the site. It may be blocked on this network. Try again or use a proxy.",
        ),
        ("ERROR: [youtube] jNQXAC9IVRQ: This video is unavailable", "This video is unavailable"),
        ("ERROR: [example] abc: Something odd happened" + REPORT_BOILERPLATE, "Something odd happened"),
    ],
)
def test_friendly_error(raw, expected):
    assert friendly_error(raw) == expected


# A trimmed-down copy of a real YouTube format list (8K nature video), sizes in bytes.
def fmt(format_id, vcodec, acodec, height, size, *, fps=60, dynamic_range="SDR", ext="mp4", protocol="https", abr=None):
    width = height * 16 // 9 if height else None
    return {
        "format_id": format_id, "vcodec": vcodec, "acodec": acodec, "width": width, "height": height,
        "fps": fps if height else None, "dynamic_range": dynamic_range if height else None, "ext": ext,
        "protocol": protocol, "filesize": size, "abr": abr, "url": f"https://example.com/{format_id}",
    }


GB = 1_000_000_000
FORMATS = [
    fmt("140", "none", "mp4a.40.2", None, 82_000_000, ext="m4a", abr=129),
    fmt("251", "none", "opus", None, 89_000_000, ext="webm", abr=140),
    fmt("299", "avc1.64002A", "none", 1080, int(2.65 * GB)),
    fmt("303", "vp9", "none", 1080, int(2.02 * GB), ext="webm"),
    fmt("135", "avc1.4D401F", "none", 480, int(0.5 * GB), fps=30),
    fmt("697", "av01.0.05M.10", "none", 480, int(1.1 * GB), dynamic_range="HDR10"),
    fmt("315", "vp9", "none", 2160, int(11.6 * GB), ext="webm"),
    fmt("628", "vp09.00.51.08", "none", 2160, int(49 * GB), protocol="m3u8_native"),
    fmt("701", "av01.0.13M.10", "none", 2160, int(17.8 * GB), dynamic_range="HDR10"),
    fmt("702", "av01.0.17M.10", "none", 4320, int(35.6 * GB), dynamic_range="HDR10"),
]


def summary():
    return summarize({"title": "Nature", "extractor_key": "Youtube", "formats": list(FORMATS)})


def test_summary_rows_cover_every_resolution_with_codecs():
    rows = {row["label"]: row for row in summary()["video"]}
    # 8K is never offered, even when the platform has it.
    assert list(rows) == ["4K", "1080p", "480p"]
    assert rows["4K"]["codecs"] == ["AV1", "VP9"] and rows["4K"]["hdr"] and rows["4K"]["sdr"]


def test_summary_sizes_follow_job_selection():
    rows = {row["label"]: row for row in summary()["video"]}
    # SDR prefers the direct VP9 stream over the HLS one and over AV1 HDR; audio is the M4A stream.
    assert rows["4K"]["size_sdr"] == int(11.6 * GB) + 82_000_000
    assert rows["4K"]["size"] == int(17.8 * GB) + 82_000_000
    # SDR beats a higher frame rate.
    assert rows["480p"]["size_sdr"] == int(0.5 * GB) + 82_000_000
    assert (rows["4K"]["codec"], rows["4K"]["codec_sdr"], rows["1080p"]["codec_compatible"]) == ("AV1", "VP9", "H.264")
    # Compatible mode is H.264 only: nothing at 4K.
    assert rows["4K"]["size_compatible"] is None
    assert rows["1080p"]["size_compatible"] == int(2.65 * GB) + 82_000_000


def test_summary_audio_and_platform():
    s = summary()
    assert s["audio"] == {"codec": "Opus", "bitrate": 140, "size": 89_000_000}
    assert s["sponsorblock"] is True


@pytest.fixture
def engine():
    return Engine(fetch_ffmpeg=False)


def pp_keys(engine, request):
    options = engine._options(request, Path("."), _ProgressTracker(lambda **kw: None))
    return [pp["key"] for pp in options["postprocessors"]], options


def test_video_postprocessor_chain(engine):
    keys, options = pp_keys(engine, Request("u", subtitles="en", sponsorblock=True, container="mkv"))
    assert keys == [
        "SponsorBlock", "FFmpegVideoRemuxer", "FFmpegEmbedSubtitle", "ModifyChapters", "FFmpegMetadata", "EmbedThumbnail",
    ]
    assert options["merge_output_format"] == "mkv"
    assert options["subtitleslangs"] == ["en"]


def test_best_quality_stops_at_4k(engine):
    _, options = pp_keys(engine, Request("u"))
    assert options["format_sort"][0] == "res:2160"


def test_trims_skip_reencoding_unless_precise(engine):
    _, fast = pp_keys(engine, Request("u", quality="2160", start=30, end=36))
    _, precise = pp_keys(engine, Request("u", start=30, end=36, precise_trim=True))
    assert fast["force_keyframes_at_cuts"] is False
    assert precise["force_keyframes_at_cuts"] is True
    _, untrimmed = pp_keys(engine, Request("u"))
    assert "download_ranges" not in untrimmed


def test_trimmed_clips_skip_subtitles(engine):
    keys, options = pp_keys(engine, Request("u", subtitles="en", start=0, end=20))
    assert "FFmpegEmbedSubtitle" not in keys and "writesubtitles" not in options


@pytest.mark.parametrize("audio_format, cover", [("mp3", True), ("flac", True), ("wav", False), ("original", False)])
def test_audio_postprocessor_chain(engine, audio_format, cover):
    keys, options = pp_keys(engine, Request("u", "audio", audio_format=audio_format))
    assert keys[0] == "FFmpegExtractAudio"
    assert ("EmbedThumbnail" in keys) is cover
    assert "format_sort" not in options

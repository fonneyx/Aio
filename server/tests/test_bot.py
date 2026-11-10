from aio_server.bot import UPLOAD_LIMIT, _jpeg_thumbnail, build_choices

MB = 1024 * 1024


def row(res, label, size_compatible, codecs=("AV1", "H.264", "VP9")):
    return {"res": res, "label": label, "codecs": list(codecs), "size_compatible": size_compatible}


def summary(video, duration=212, platform="Youtube", audio=True):
    return {"video": video, "duration": duration, "platform": platform, "audio": {"codec": "Opus"} if audio else None}


def test_only_offers_what_fits_telegrams_limit():
    s = summary([
        row(2160, "4K", None, codecs=("AV1", "VP9")),  # no H.264 at 4K on YouTube: skipped
        row(1080, "1080p", 120 * MB),  # too big: skipped
        row(720, "720p", 21 * MB),
        row(480, "480p", 9 * MB),
    ])
    labels = [label for label, _ in build_choices(s)]
    assert labels == ["🎬 720p · 21 MB", "🎬 480p · 9 MB", "🎵 MP3 320 kbps · 8 MB", "🎵 MP3 128 kbps · 3 MB"]


def test_video_choices_are_h264_at_the_rows_resolution():
    choices = dict(build_choices(summary([row(720, "720p", 21 * MB)])))
    assert choices["🎬 720p · 21 MB"] == {"kind": "video", "quality": "720", "codec": "compatible"}


def test_long_audio_drops_mp3_options_over_the_limit():
    s = summary([], duration=3 * 3600)  # 3 hours: 320 kbps is ~430 MB, 128 kbps ~170 MB
    assert build_choices(s) == []
    s = summary([], duration=40 * 60)  # 40 minutes: 128 kbps fits, 320 doesn't
    assert [label for label, _ in build_choices(s)] == ["🎵 MP3 128 kbps · 37 MB"]
    assert UPLOAD_LIMIT > 40 * 60 * 128_000 / 8


def test_youtube_webp_thumbnails_become_jpeg():
    assert _jpeg_thumbnail("https://i.ytimg.com/vi_webp/abc/maxresdefault.webp") == "https://i.ytimg.com/vi/abc/maxresdefault.jpg"
    assert _jpeg_thumbnail("https://example.com/t.jpg") == "https://example.com/t.jpg"
    assert _jpeg_thumbnail(None) is None

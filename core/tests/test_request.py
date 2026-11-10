import pytest

from aio_dl import Request, parse_time


def test_defaults():
    r = Request("https://example.com/v")
    assert (r.kind, r.quality, r.codec, r.container, r.audio_bitrate) == ("video", "best", "best", "mp4", None)
    assert not r.trimmed


def test_audio_bitrate_defaults_to_highest_and_is_dropped_where_meaningless():
    assert Request("u", "audio").audio_bitrate == "320"
    assert Request("u", "audio", audio_format="m4a").audio_bitrate == "256"
    assert Request("u", "audio", audio_format="opus", audio_bitrate="320").audio_bitrate is None
    assert Request("u", "video", audio_bitrate="128").audio_bitrate is None


def test_times_are_parsed():
    r = Request("u", start="1:30", end="1:02:03")
    assert (r.start, r.end, r.trimmed) == (90.0, 3723.0, True)
    assert parse_time("") is None and parse_time(5) == 5.0


def test_quality_accepts_ints():
    assert Request("u", quality=1080).quality == "1080"


@pytest.mark.parametrize("fields", [
    {"kind": "gif"},
    {"quality": "4k"},
    {"quality": "100"},
    {"quality": "4320"},
    {"codec": "h265"},
    {"container": "avi"},
    {"subtitles": "../etc"},
    {"kind": "audio", "audio_format": "aiff"},
    {"kind": "audio", "audio_bitrate": "999"},
    {"start": "0:30", "end": "0:10"},
    {"start": "abc"},
    {"start": -5},
    {"start": "1:2:3:4"},
])
def test_invalid(fields):
    with pytest.raises(ValueError):
        Request("u", **fields)


def test_format_sort():
    assert Request("u", quality="720").format_sort()[0] == "res:720"
    assert Request("u", codec="compatible").format_sort()[0] == "vcodec:h264"
    assert "hdr" in Request("u", hdr=True).format_sort()
    assert "hdr:sdr" in Request("u").format_sort()

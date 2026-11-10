import json

import pytest

from aio_dl import cli


class FakeEngine:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.requests = []

    def probe(self, url):
        return {"kind": "media", "url": url, "title": "Clip", "platform": "Youtube", "uploader": "Someone",
                "duration": 75, "video": [{"label": "1080p", "codecs": ["VP9"], "hdr": False, "size_sdr": 2_000_000,
                                           "size_compatible": None}],
                "audio": {"codec": "Opus", "bitrate": 140, "size": 900_000}, "subtitles": [{"code": "en"}]}

    def download(self, request, out_dir, on_progress=None):
        self.requests.append(request)
        if on_progress:
            on_progress(stage="Downloading", progress=50.0)
        return [out_dir / "Clip.mp3"]


@pytest.fixture
def fake(monkeypatch):
    holder = {}

    def make(**kwargs):
        holder["engine"] = FakeEngine(**kwargs)
        return holder["engine"]

    monkeypatch.setattr(cli, "Engine", make)
    return holder


def test_audio_download(fake, tmp_path, capsys):
    assert cli.main(["-x", "-b", "192", "--start", "0:10", "-o", str(tmp_path), "https://youtu.be/x"]) == 0
    [request] = fake["engine"].requests
    assert (request.kind, request.audio_format, request.audio_bitrate, request.start) == ("audio", "mp3", "192", 10.0)
    assert "Saved" in capsys.readouterr().out


def test_video_flags(fake, tmp_path):
    cli.main(["-q", "720", "--compatible", "--container", "mkv", "--subs", "en", "-o", str(tmp_path), "https://x.com/v"])
    [request] = fake["engine"].requests
    assert (request.quality, request.codec, request.container, request.subtitles) == ("720", "compatible", "mkv", "en")


def test_info_json(fake, capsys):
    assert cli.main(["--info", "--json", "https://youtu.be/x"]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "Clip"


def test_info_text(fake, capsys):
    cli.main(["-i", "https://youtu.be/x"])
    out = capsys.readouterr().out
    assert "1080p" in out and "Opus 140 kbps" in out and "1:15" in out


def test_batch_file(fake, tmp_path):
    batch = tmp_path / "links.txt"
    batch.write_text("# comment\nhttps://a.com/1\n\nhttps://b.com/2\n", encoding="utf-8")
    cli.main(["-a", str(batch), "-o", str(tmp_path)])
    assert [r.url for r in fake["engine"].requests] == ["https://a.com/1", "https://b.com/2"]


@pytest.mark.parametrize("argv", [[], ["not-a-link"], ["-q", "4k", "https://x.com"]])
def test_usage_errors(fake, argv, capsys):
    assert cli.main(argv) == 2
    assert "error" in capsys.readouterr().err

"""The gallery-dl fallback, with yt-dlp and gallery-dl both stubbed out."""

from pathlib import Path

import pytest

from aio_grab import Engine, EngineError, Request
from aio_grab.engine import UNSUPPORTED
from aio_grab.gallery import _error, platform_name


class FakeGallery:
    def __init__(self, files=None, error=None):
        self.files = files or []
        self.error = error
        self.downloads = 0

    def list_files(self, url):
        from aio_grab.gallery import GalleryError

        if self.error:
            raise GalleryError(self.error)
        return self.files

    def download(self, url, out_dir: Path, items, on_file):
        self.downloads += 1
        paths = []
        for i, _ in enumerate(self.files, 1):
            path = out_dir / f"{i:02d}.jpg"
            path.write_bytes(b"jpeg")
            on_file(i)
            paths.append(path)
        return paths


@pytest.fixture
def engine(monkeypatch):
    engine = Engine(fetch_ffmpeg=False)

    def no_media(ydl, url):
        raise EngineError(no_media.message)

    no_media.message = UNSUPPORTED
    monkeypatch.setattr(engine, "_extract", no_media)
    engine.no_media = no_media
    return engine


def test_probe_falls_back_to_images(engine):
    engine.gallery = FakeGallery(["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"])
    summary = engine.probe("https://www.instagram.com/p/abc/")
    assert summary["kind"] == "images"
    assert summary["items"] == 2
    assert summary["platform"] == "Instagram"
    assert summary["thumbnail"] == "https://cdn.example.com/1.jpg"


def test_download_uses_gallery_and_zips_on_request(engine, tmp_path):
    engine.gallery = FakeGallery(["a", "b", "c"])
    events = []
    files = engine.download(Request("https://example.com/album"), tmp_path / "out", lambda **kw: events.append(kw))
    assert [f.name for f in files] == ["01.jpg", "02.jpg", "03.jpg"]
    assert events[-1]["progress"] == 100.0

    [archive] = engine.download(Request("https://example.com/album"), tmp_path / "zip", zip_posts=True)
    assert archive.suffix == ".zip"


def test_unrelated_errors_skip_the_fallback(engine):
    engine.no_media.message = "This video is private."
    engine.gallery = FakeGallery(error="should not be asked")
    with pytest.raises(EngineError, match="private"):
        engine.probe("https://example.com/v")


def test_ytdlp_message_wins_unless_site_unknown(engine):
    engine.no_media.message = "There is no video in this post"
    engine.gallery = FakeGallery(error="Login required")
    with pytest.raises(EngineError, match="no video"):
        engine.probe("https://example.com/p")

    engine.no_media.message = UNSUPPORTED
    with pytest.raises(EngineError, match="Login required"):
        engine.probe("https://example.com/p")


def test_too_many_images(engine):
    engine.gallery = FakeGallery([f"https://x/{i}.jpg" for i in range(engine.max_post_items + 1)])
    with pytest.raises(EngineError, match="more than"):
        engine.probe("https://example.com/huge")


def test_gallery_helpers():
    assert platform_name("https://www.flickr.com/photos/x") == "Flickr"
    assert _error("[gallery-dl][error] Unsupported URL 'https://x'") == UNSUPPORTED
    assert _error("[instagram][warning] meh\n[instagram][error] HttpError: 401 Unauthorized") == "HttpError: 401 Unauthorized"
    assert _error("") is None

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aio_grab import EngineError, Request, Status
from aio_server.api import create_app
from aio_server.config import Settings
from aio_server.jobs import JobStore
from aio_server.storage import Storage


class FakeEngine:
    """Stands in for yt-dlp: reports progress and writes a small file."""

    def __init__(self, error: str | None = None) -> None:
        self.error = error
        self.requests: list[Request] = []

    def probe(self, url: str) -> dict:
        if self.error:
            raise EngineError(self.error)
        return {"url": url, "title": "Test clip", "video": [{"res": 2160, "label": "4K"}]}

    def download(self, request: Request, out_dir: Path, report, zip_posts: bool = False) -> list[Path]:
        self.requests.append(request)
        report(title="Test clip", platform="Fake", duration=12.0)
        report(status=Status.DOWNLOADING, stage="Downloading", progress=50.0)
        if self.error:
            raise EngineError(self.error)
        path = out_dir / "Test clip.mp4"
        path.write_bytes(b"fake media")
        return [path]


def make_client(tmp_path: Path, engine: FakeEngine) -> TestClient:
    settings = Settings(storage_dir=tmp_path / "storage", workers=1)
    return TestClient(create_app(settings, engine=engine))


def wait_for(client: TestClient, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish")


def test_job_completes_and_file_downloads(tmp_path):
    engine = FakeEngine()
    with make_client(tmp_path, engine) as client:
        res = client.post("/api/jobs", json={"url": "https://example.com/watch?v=1"})
        assert res.status_code == 202
        assert res.json()["status"] == "queued"

        job = wait_for(client, res.json()["id"])
        assert job["status"] == "completed"
        assert job["title"] == "Test clip"
        assert job["filename"] == "Test clip.mp4"
        assert job["file_size"] == len(b"fake media")
        assert job["expires_at"]

        file = client.get(job["download_url"])
        assert file.status_code == 200
        assert file.content == b"fake media"
        assert "attachment" in file.headers["content-disposition"]
    assert engine.requests[0] == Request("https://example.com/watch?v=1")


def test_failed_job_reports_error_and_cleans_up(tmp_path):
    with make_client(tmp_path, FakeEngine(error="This site isn't supported yet.")) as client:
        job_id = client.post("/api/jobs", json={"url": "https://example.com/x"}).json()["id"]
        job = wait_for(client, job_id)
        assert job["status"] == "failed"
        assert job["error"] == "This site isn't supported yet."
        assert job["download_url"] is None
        assert client.get(f"/api/jobs/{job_id}/file").status_code == 404
    assert not (tmp_path / "storage" / job_id).exists()


def test_audio_defaults_and_trim_parsing(tmp_path):
    engine = FakeEngine()
    with make_client(tmp_path, engine) as client:
        res = client.post(
            "/api/jobs",
            json={"url": "https://example.com/a", "kind": "audio", "start": "1:30", "end": "1:02:03"},
        )
        assert res.status_code == 202
        wait_for(client, res.json()["id"])
    assert engine.requests[0] == Request(
        "https://example.com/a", "audio", audio_format="mp3", audio_bitrate="320", start=90.0, end=3723.0
    )


def test_options_reach_the_engine(tmp_path):
    engine = FakeEngine()
    video = {
        "url": "https://example.com/v", "quality": "2160", "codec": "best", "hdr": True,
        "container": "mkv", "subtitles": "en-orig", "sponsorblock": True,
    }
    audio = {"url": "https://example.com/a", "kind": "audio", "audio_format": "opus", "audio_bitrate": "320"}
    with make_client(tmp_path, engine) as client:
        for body in (video, audio):
            res = client.post("/api/jobs", json=body)
            assert res.status_code == 202
            wait_for(client, res.json()["id"])
            assert res.json()["container"] == body.get("container", "mp4")
    assert engine.requests[0] == Request(**video)
    # Opus has no bitrate choice: the value is dropped rather than rejected.
    assert engine.requests[1] == Request("https://example.com/a", "audio", audio_format="opus")


def test_probe(tmp_path):
    with make_client(tmp_path, FakeEngine()) as client:
        res = client.post("/api/probe", json={"url": " https://example.com/v "})
        assert res.status_code == 200
        assert res.json()["url"] == "https://example.com/v"
        assert client.post("/api/probe", json={"url": "nope"}).status_code == 422
    with make_client(tmp_path, FakeEngine(error="This site isn't supported yet.")) as client:
        res = client.post("/api/probe", json={"url": "https://example.com/v"})
        assert res.status_code == 422
        assert res.json()["detail"] == "This site isn't supported yet."


@pytest.mark.parametrize(
    "body",
    [
        {"url": "ftp://example.com/file"},
        {"url": "not a url"},
        {"url": "https://example.com", "quality": "4k"},
        {"url": "https://example.com", "quality": "100"},
        {"url": "https://example.com", "quality": "4320"},
        {"url": "https://example.com", "codec": "h265"},
        {"url": "https://example.com", "container": "avi"},
        {"url": "https://example.com", "subtitles": "../etc"},
        {"url": "https://example.com", "kind": "audio", "audio_format": "aiff"},
        {"url": "https://example.com", "kind": "audio", "audio_format": "mp3", "audio_bitrate": "999"},
        {"url": "https://example.com", "kind": "gif"},
        {"url": "https://example.com", "start": "0:30", "end": "0:10"},
        {"url": "https://example.com", "start": "abc"},
        {"url": "https://example.com", "start": -5},
        {"url": "https://example.com", "start": [1]},
    ],
)
def test_rejects_invalid_requests(tmp_path, body):
    with make_client(tmp_path, FakeEngine()) as client:
        assert client.post("/api/jobs", json=body).status_code == 422


def test_unknown_job_is_404(tmp_path):
    with make_client(tmp_path, FakeEngine()) as client:
        assert client.get("/api/jobs/doesnotexist0000").status_code == 404
        assert client.get("/api/jobs/doesnotexist0000/file").status_code == 404


def test_web_ui_is_served(tmp_path):
    with make_client(tmp_path, FakeEngine()) as client:
        res = client.get("/")
        assert res.status_code == 200
        assert "Paste a link" in res.text


def test_sweep_expires_old_jobs_and_orphans(tmp_path):
    store = JobStore()
    storage = Storage(tmp_path, ttl_seconds=60)

    old = store.create(Request("https://example.com/old"))
    fresh = store.create(Request("https://example.com/new"))
    running = store.create(Request("https://example.com/run"))
    for job in (old, fresh, running):
        (storage.job_dir(job.id) / "file.mp4").write_bytes(b"x")
    now = time.time()
    store.update(old.id, status=Status.COMPLETED, finished_at=now - 120)
    store.update(fresh.id, status=Status.COMPLETED, finished_at=now - 10)

    orphan = tmp_path / "AAAAAAAAAAAAAAAA"  # job-shaped name, no job: left over from a previous run
    orphan.mkdir()
    unrelated = tmp_path / "keep-me"  # never touched, whatever its age
    unrelated.mkdir()

    assert storage.sweep(store, now=now) == 1
    assert store.get(old.id) is None and not (tmp_path / old.id).exists()
    assert store.get(fresh.id) and (tmp_path / fresh.id).exists()
    assert store.get(running.id) and (tmp_path / running.id).exists()
    assert not orphan.exists()
    assert unrelated.exists()


def test_basic_auth(tmp_path):
    import base64

    settings = Settings(storage_dir=tmp_path / "storage", workers=1, auth="me:secret")
    with TestClient(create_app(settings, engine=FakeEngine())) as client:
        assert client.get("/api/health").status_code == 401
        assert client.get("/manifest.json").status_code == 200  # browsers fetch it without credentials
        wrong = base64.b64encode(b"me:nope").decode()
        assert client.get("/api/health", headers={"Authorization": f"Basic {wrong}"}).status_code == 401
        good = base64.b64encode(b"me:secret").decode()
        assert client.get("/api/health", headers={"Authorization": f"Basic {good}"}).status_code == 200

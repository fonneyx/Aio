"""HTTP API and web UI."""

import asyncio
import contextlib
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

import yt_dlp.version
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AfterValidator, BaseModel, Field, field_validator, model_validator

import aio_dl
from aio_dl import Engine, EngineError, Request, Status, parse_time

from . import __version__
from .auth import add_basic_auth
from .config import Settings
from .jobs import Job, JobStore
from .storage import Storage
from .worker import WorkerPool

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def _check_url(value: str) -> str:
    value = value.strip()
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("Enter a full link starting with http:// or https://")
    return value


Url = Annotated[str, Field(max_length=2048), AfterValidator(_check_url)]


class ProbeCreate(BaseModel):
    url: Url


class JobCreate(BaseModel):
    url: Url
    kind: Literal["video", "audio"] = "video"
    # Video: "best" or a maximum resolution (the smaller dimension), e.g. "2160" for 4K.
    quality: str = "best"
    codec: str = "best"
    hdr: bool = False
    container: str = "mp4"
    subtitles: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,32}$")
    # Audio
    audio_format: str = "mp3"
    audio_bitrate: str | None = None
    sponsorblock: bool = False
    # Trim range, as seconds or [hh:]mm:ss.
    start: float | None = None
    end: float | None = None
    precise_trim: bool = False

    @field_validator("start", "end", mode="before")
    @classmethod
    def parse_timestamp(cls, value):
        return parse_time(value)

    @model_validator(mode="after")
    def check_options(self) -> "JobCreate":
        # The library owns the rules; this model only adds the HTTP schema.
        self.audio_bitrate = Request(**self.model_dump()).audio_bitrate
        return self


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = JobStore()
    storage = Storage(settings.storage_dir, settings.file_ttl_seconds)
    engine = engine or engine_from(settings)
    workers = WorkerPool(store, storage, engine, settings.workers)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        sweeper = asyncio.create_task(_sweep_forever(storage, store))
        try:
            yield
        finally:
            sweeper.cancel()
            workers.shutdown()

    app = FastAPI(title="Aio", version=__version__, lifespan=lifespan)
    if settings.auth:
        add_basic_auth(app, settings.auth)

    def view(job: Job) -> dict:
        completed = job.status is Status.COMPLETED
        return {
            "id": job.id,
            "status": job.status,
            "stage": job.stage,
            "progress": job.progress,
            "speed": job.speed,
            "eta": job.eta,
            **asdict(job.request),
            "title": job.title,
            "platform": job.platform,
            "thumbnail": job.thumbnail,
            "duration": job.duration,
            "filename": job.file_path.name if completed else None,
            "file_size": job.file_size,
            "download_url": f"/api/jobs/{job.id}/file" if completed else None,
            "expires_at": _iso(job.finished_at + storage.ttl_seconds) if completed else None,
            "error": job.error,
            "created_at": _iso(job.created_at),
        }

    def find(job_id: str) -> Job:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found or expired")
        return job

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "aio_dl": aio_dl.__version__,
            "yt_dlp": yt_dlp.version.__version__,
            "js_runtimes": list(getattr(engine, "js_runtimes", {})),
        }

    @app.post("/api/probe")
    def probe(body: ProbeCreate) -> dict:
        try:
            return engine.probe(body.url)
        except EngineError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/api/jobs", status_code=202)
    def create_job(body: JobCreate) -> dict:
        job = store.create(Request(**body.model_dump()))
        workers.submit(job.id)
        log.info("Job %s queued: %s", job.id, body.model_dump_json(exclude_none=True))
        return view(job)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        return view(find(job_id))

    @app.get("/api/jobs/{job_id}/file")
    def download(job_id: str) -> FileResponse:
        job = find(job_id)
        if job.status is not Status.COMPLETED or not job.file_path.is_file():
            raise HTTPException(404, "File not ready")
        return FileResponse(job.file_path, filename=job.file_path.name)

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="web")
    return app


def engine_from(settings: Settings) -> Engine:
    return Engine(ffmpeg=settings.ffmpeg, cookies_file=settings.cookies_file, proxy=settings.proxy)


async def _sweep_forever(storage: Storage, store: JobStore) -> None:
    interval = max(5.0, min(600.0, storage.ttl_seconds / 10))
    while True:
        try:
            await asyncio.to_thread(storage.sweep, store)
        except Exception:
            log.exception("Storage sweep failed")
        await asyncio.sleep(interval)


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="seconds")

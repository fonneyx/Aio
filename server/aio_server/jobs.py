"""Job model and the in-memory job store.

The store is process-local for the prototype, so the server must run as a
single process. It is the piece to swap for Redis when workers move out of
the API process.
"""

import copy
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from aio_dl import Request, Status

__all__ = ["Job", "JobStore", "Request", "Status", "new_job_id"]


@dataclass
class Job:
    id: str
    request: Request
    status: Status = Status.QUEUED
    stage: str = "Queued"
    progress: float | None = None  # 0-100, None when unknown
    speed: float | None = None  # bytes per second while downloading
    eta: int | None = None  # seconds left while downloading
    title: str | None = None
    platform: str | None = None
    thumbnail: str | None = None
    duration: float | None = None
    file_path: Path | None = None
    file_size: int | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    @property
    def finished(self) -> bool:
        return self.status in (Status.COMPLETED, Status.FAILED)


def new_job_id() -> str:
    # 16 URL-safe characters. Unguessable, so a job's download link works like a signed URL.
    return secrets.token_urlsafe(12)


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, request: Request) -> Job:
        job = Job(id=new_job_id(), request=request)
        with self._lock:
            self._jobs[job.id] = job
            return copy.copy(job)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return copy.copy(job) if job else None

    def all(self) -> list[Job]:
        with self._lock:
            return [copy.copy(job) for job in self._jobs.values()]

    def update(self, job_id: str, **changes) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for name, value in changes.items():
                setattr(job, name, value)

    def remove(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)

"""Ephemeral staging area for finished files.

Every job gets its own directory. Files are deleted once they are older than
the TTL, so the staging area never grows without bound. In production this is
an object storage bucket with a lifecycle rule; locally it is a folder.
"""

import logging
import re
import shutil
import time
from pathlib import Path

from .jobs import JobStore

log = logging.getLogger(__name__)

# Only directories named like a job ID are ever deleted, so a misconfigured
# storage path can't wipe unrelated files.
JOB_DIR_NAME = re.compile(r"^[A-Za-z0-9_-]{16}$")


class Storage:
    def __init__(self, root: Path, ttl_seconds: float) -> None:
        self.root = root
        self.ttl_seconds = ttl_seconds
        root.mkdir(parents=True, exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        path = self.root / job_id
        path.mkdir(exist_ok=True)
        return path

    def delete(self, job_id: str) -> None:
        shutil.rmtree(self.root / job_id, ignore_errors=True)

    def sweep(self, store: JobStore, now: float | None = None) -> int:
        """Delete expired jobs and their files, plus directories no job owns. Returns jobs removed."""
        now = time.time() if now is None else now
        removed = 0
        for job in store.all():
            if job.finished and now - job.finished_at > self.ttl_seconds:
                store.remove(job.id)
                self.delete(job.id)
                removed += 1

        # Leftovers from a previous run: the store is in memory, so nothing can reach them.
        for child in self.root.iterdir():
            if child.is_dir() and JOB_DIR_NAME.match(child.name) and store.get(child.name) is None:
                shutil.rmtree(child, ignore_errors=True)

        if removed:
            log.info("Expired %d job(s)", removed)
        return removed

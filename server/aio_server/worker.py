"""Worker pool: runs jobs in the background so the API can answer immediately."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from aio_grab import Engine, EngineError

from .jobs import JobStore, Status
from .storage import Storage

log = logging.getLogger(__name__)


class WorkerPool:
    def __init__(self, store: JobStore, storage: Storage, engine: Engine, workers: int) -> None:
        self.store = store
        self.storage = storage
        self.engine = engine
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="aio-worker")

    def submit(self, job_id: str) -> None:
        self._pool.submit(self._run, job_id)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _run(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if job is None:
            return
        out_dir = self.storage.job_dir(job_id)
        report = partial(self.store.update, job_id)
        try:
            [path] = self.engine.download(job.request, out_dir, report, zip_posts=True)
        except EngineError as e:
            self._fail(job_id, str(e))
        except Exception:
            log.exception("Job %s crashed", job_id)
            self._fail(job_id, "Something went wrong while processing this link.")
        else:
            self.store.update(
                job_id,
                status=Status.COMPLETED,
                stage="Ready",
                progress=100.0,
                file_path=path,
                file_size=path.stat().st_size,
                finished_at=time.time(),
            )
            log.info("Job %s completed: %s", job_id, path.name)

    def _fail(self, job_id: str, message: str) -> None:
        self.store.update(job_id, status=Status.FAILED, stage="Failed", error=message, finished_at=time.time())
        self.storage.delete(job_id)
        log.info("Job %s failed: %s", job_id, message)

"""The queue the build runs on. A commit puts the approved brief here and returns at once; a small pool of workers builds the plans, so a long build holds no request open.

Bounded on purpose: when the workers and the waiting places are all taken, ``submit`` says so and the caller tells the user to try again, instead of the queue growing without limit.
``inline=True`` runs the job in the caller's thread (tests use it to get a deterministic order).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor


class CommitQueue:
    def __init__(self, workers: int = 2, waiting: int = 16, inline: bool = False):
        self.inline = inline
        self._pool = None if inline else ThreadPoolExecutor(max_workers=workers, thread_name_prefix="commit")
        self._places = threading.BoundedSemaphore(workers + waiting)

    def has_room(self) -> bool:
        if not self._places.acquire(blocking=False):
            return False
        self._places.release()
        return True

    def submit(self, job: Callable[[], None]) -> bool:
        """True when the job was accepted. Its own errors are the job's to record; the queue only keeps a worker from dying on one."""
        if not self._places.acquire(blocking=False):
            return False

        def run() -> None:
            try:
                job()
            except Exception:  # noqa: BLE001 - the job records its own failure on the brief; nothing here may take the worker down
                pass
            finally:
                self._places.release()

        if self._pool is None:
            run()
        else:
            self._pool.submit(run)
        return True

"""Reusable re-entrant thread and advisory process lock for JSON files."""

import fcntl
import threading
from pathlib import Path


class JsonFileLock:
    """Serialize one file across threads and cooperating processes."""

    def __init__(self, path_getter) -> None:
        self._path_getter = path_getter
        self._thread_lock = threading.RLock()
        self._local = threading.local()

    def __enter__(self):
        self._thread_lock.acquire()
        depth = getattr(self._local, "depth", 0)
        try:
            if depth == 0:
                data_path = Path(self._path_getter())
                data_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path = data_path.with_name(data_path.name + ".lock")
                handle = open(lock_path, "a+", encoding="utf-8")
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                except Exception:
                    handle.close()
                    raise
                self._local.handle = handle
                self._local.path = data_path
            self._local.depth = depth + 1
            return self
        except Exception:
            self._thread_lock.release()
            raise

    def __exit__(self, exc_type, exc, tb):
        depth = self._local.depth - 1
        self._local.depth = depth
        try:
            if depth == 0:
                handle = self._local.handle
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    try:
                        handle.close()
                    finally:
                        del self._local.handle
                        del self._local.path
        finally:
            self._thread_lock.release()
        return False

    @property
    def active_path(self) -> Path | None:
        return getattr(self._local, "path", None)

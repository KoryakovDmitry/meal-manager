"""Reusable re-entrant thread and advisory process locks for JSON files."""

import fcntl
import os
import stat
import threading
from pathlib import Path


class LockIdentityError(OSError):
    """Raised when a pinned lock pathname no longer names its locked inode."""


class JsonFileLock:
    """Serialize one file across threads and cooperating processes.

    Ordinary repository locks resolve a data path on every outer acquisition.
    The audit transaction lock uses :meth:`pinned` instead: its parent directory
    and lock inode stay open for the manager lifetime, and every acquisition
    verifies that the directory entry still names that inode.
    """

    def __init__(
        self, path_getter, *, _parent_fd=None, _name=None, _domain_fd=None
    ) -> None:
        self._path_getter = path_getter
        self._thread_lock = threading.RLock()
        self._local = threading.local()
        self._parent_fd = None
        self._name = None
        self._pinned_fd = None
        self._domain_fd = None
        self._compromised_error = None
        self._owner_pid = os.getpid()
        if _parent_fd is not None:
            if not isinstance(_name, str) or not _name or "/" in _name:
                raise ValueError("pinned lock name must be one path component")
            directory_flags = (
                os.O_RDONLY
                | os.O_DIRECTORY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            # Reopen instead of dup: flock is tied to an open-file description.
            # Independent descriptions let close() definitively release a lock
            # after a simulated or real LOCK_UN failure without closing manager FDs.
            self._parent_fd = os.open(".", directory_flags, dir_fd=_parent_fd)
            self._domain_fd = (
                os.open(".", directory_flags, dir_fd=_domain_fd)
                if _domain_fd is not None
                else None
            )
            self._name = _name
            flags = (
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            try:
                self._pinned_fd = os.open(
                    _name, flags, 0o600, dir_fd=self._parent_fd
                )
                os.fchmod(self._pinned_fd, 0o600)
                os.fsync(self._parent_fd)
                self.assert_identity()
            except Exception:
                if self._pinned_fd is not None:
                    os.close(self._pinned_fd)
                    self._pinned_fd = None
                os.close(self._parent_fd)
                self._parent_fd = None
                if self._domain_fd is not None:
                    os.close(self._domain_fd)
                    self._domain_fd = None
                raise

    def _refresh_after_fork(self):
        """Give a child process independent thread state and flock OFDs."""
        current_pid = os.getpid()
        if current_pid == self._owner_pid:
            return

        # A lock may have been held by another thread when fork happened.
        # Inherited Python lock ownership is unusable in the child regardless
        # of the kernel flock state.
        self._thread_lock = threading.RLock()
        self._local = threading.local()
        self._owner_pid = current_pid
        if self._pinned_fd is None or self._compromised_error is not None:
            return

        directory_flags = (
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        lock_flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        old_parent = self._parent_fd
        old_domain = self._domain_fd
        old_pinned = self._pinned_fd
        new_parent = new_domain = new_pinned = None
        try:
            if old_parent is None or self._name is None:
                raise LockIdentityError("pinned lock descriptors are unavailable")
            new_parent = os.open(".", directory_flags, dir_fd=old_parent)
            if old_domain is not None:
                new_domain = os.open(".", directory_flags, dir_fd=old_domain)
            new_pinned = os.open(
                self._name, lock_flags, 0o600, dir_fd=new_parent
            )
            self._parent_fd = new_parent
            self._domain_fd = new_domain
            self._pinned_fd = new_pinned
            self.assert_identity()
        except Exception as exc:
            for descriptor in (new_pinned, new_parent, new_domain):
                if descriptor is not None:
                    os.close(descriptor)
            self._parent_fd = None
            self._domain_fd = None
            self._pinned_fd = None
            self._compromised_error = exc
            raise
        finally:
            for descriptor in (old_pinned, old_parent, old_domain):
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass

    def _close_pinned_descriptors(self):
        """Close lock-owned open-file descriptions, releasing any held flocks."""
        for attribute in ("_pinned_fd", "_parent_fd", "_domain_fd"):
            descriptor = getattr(self, attribute)
            if descriptor is None:
                continue
            try:
                os.close(descriptor)
            finally:
                setattr(self, attribute, None)

    def _poison(self, error):
        self._compromised_error = error
        self._close_pinned_descriptors()

    @classmethod
    def pinned(cls, parent_fd, name, *, domain_fd=None):
        """Create a lifetime-pinned lock below an already pinned directory."""
        return cls(
            None, _parent_fd=parent_fd, _name=name, _domain_fd=domain_fd
        )

    @property
    def pinned_identity(self):
        if self._pinned_fd is None:
            return None
        info = os.fstat(self._pinned_fd)
        return info.st_dev, info.st_ino

    def assert_identity(self):
        """Fail closed if a pinned lock entry was unlinked or substituted."""
        if self._pinned_fd is None:
            return
        if self._parent_fd is None or self._name is None:
            raise LockIdentityError("pinned lock descriptors are unavailable")
        parent_fd = self._parent_fd
        name = self._name
        descriptor_info = os.fstat(self._pinned_fd)
        try:
            entry_info = os.stat(
                name, dir_fd=parent_fd, follow_symlinks=False
            )
        except FileNotFoundError as exc:
            raise LockIdentityError("audit lock pathname was removed") from exc
        if (
            not stat.S_ISREG(descriptor_info.st_mode)
            or not stat.S_ISREG(entry_info.st_mode)
            or descriptor_info.st_nlink != 1
            or entry_info.st_nlink != 1
            or (descriptor_info.st_dev, descriptor_info.st_ino)
            != (entry_info.st_dev, entry_info.st_ino)
        ):
            raise LockIdentityError("audit lock inode identity changed")

    def __enter__(self):
        self._refresh_after_fork()
        self._thread_lock.acquire()
        depth = getattr(self._local, "depth", 0)
        try:
            if depth == 0:
                if self._compromised_error is not None:
                    raise LockIdentityError(
                        "pinned audit lock is poisoned; restart is required"
                    ) from self._compromised_error
                if self._pinned_fd is not None:
                    if self._parent_fd is None or self._name is None:
                        raise LockIdentityError("pinned lock descriptors are unavailable")
                    try:
                        # The data-root parent survives replacement of the root;
                        # the audit directory survives replacement of the file.
                        if self._domain_fd is not None:
                            fcntl.flock(self._domain_fd, fcntl.LOCK_EX)
                        fcntl.flock(self._parent_fd, fcntl.LOCK_EX)
                        self.assert_identity()
                        fcntl.flock(self._pinned_fd, fcntl.LOCK_EX)
                        self.assert_identity()
                    except Exception:
                        try:
                            fcntl.flock(self._pinned_fd, fcntl.LOCK_UN)
                        except OSError:
                            pass
                        try:
                            fcntl.flock(self._parent_fd, fcntl.LOCK_UN)
                        except OSError:
                            pass
                        if self._domain_fd is not None:
                            try:
                                fcntl.flock(self._domain_fd, fcntl.LOCK_UN)
                            except OSError:
                                pass
                        raise
                    self._local.handle = None
                    self._local.path = Path(
                        f"/proc/self/fd/{self._parent_fd}/{self._name}"
                    )
                else:
                    data_path = Path(self._path_getter())
                    data_path.parent.mkdir(parents=True, exist_ok=True)
                    lock_path = data_path.with_name(data_path.name + ".lock")
                    flags = (
                        os.O_RDWR
                        | os.O_CREAT
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_CLOEXEC", 0)
                    )
                    descriptor = os.open(lock_path, flags, 0o600)
                    handle = os.fdopen(descriptor, "a+", encoding="utf-8")
                    try:
                        info = os.fstat(handle.fileno())
                        if not stat.S_ISREG(info.st_mode):
                            raise LockIdentityError("lock must be a regular file")
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    except Exception:
                        handle.close()
                        raise
                    self._local.handle = handle
                    self._local.path = data_path
            self._local.depth = depth + 1
            return self
        except Exception as caught:
            if self._pinned_fd is not None:
                # Any pinned identity failure is a process-lifetime compromise.
                # Poison before releasing the local waiter lock so restoring a
                # pathname cannot revive this manager.
                self._poison(caught)
            self._thread_lock.release()
            raise

    def __exit__(self, exc_type, exc, tb):
        depth = self._local.depth - 1
        self._local.depth = depth
        cleanup_error = None
        pinned = self._pinned_fd is not None
        try:
            if depth == 0:
                handle = self._local.handle
                if pinned:
                    try:
                        self.assert_identity()
                    except Exception as caught:
                        cleanup_error = caught
                    for descriptor in (
                        self._pinned_fd,
                        self._parent_fd,
                        self._domain_fd,
                    ):
                        if descriptor is None:
                            continue
                        try:
                            fcntl.flock(descriptor, fcntl.LOCK_UN)
                        except Exception as caught:
                            cleanup_error = cleanup_error or caught
                else:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except Exception as caught:
                        cleanup_error = caught
                    try:
                        handle.close()
                    except Exception as caught:
                        cleanup_error = cleanup_error or caught
                del self._local.handle
                del self._local.path
        finally:
            if cleanup_error is not None and pinned:
                # Poison while the process-local thread lock is still held so
                # no waiter can observe live-looking descriptors in between.
                self._poison(cleanup_error)
            self._thread_lock.release()
        if cleanup_error is not None:
            if pinned:
                # The body may already have durably committed. Preserve that
                # outcome, but close independent lock-owned descriptions so no
                # failed LOCK_UN can strand a cross-process flock. The object
                # remains poisoned and cannot be reused.
                return False
            raise cleanup_error
        return False

    def close(self):
        """Release pinned descriptors when their owning manager is reconfigured."""
        self._refresh_after_fork()
        with self._thread_lock:
            if self.active_path is not None:
                raise RuntimeError("cannot close an active file lock")
            self._close_pinned_descriptors()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    @property
    def active_path(self) -> Path | None:
        return getattr(self._local, "path", None)

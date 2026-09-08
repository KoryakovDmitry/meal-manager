"""File-backed transactional audit journal with deterministic recovery."""

import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from ..repositories.file_lock import JsonFileLock


_ALLOWED_ROOTS = {
    "dishes.json",
    "fridge.json",
    "history.json",
    "receipts.json",
    "prep_items.json",
    "shopping_requests.json",
    "tuning.json",
}
_LEGACY_RECOVERY_ROOTS = {"receipts.json"}
_LEGACY_RECOVERY_CONTRACTS = {
    ("record_purchase_receipt", "purchase_receipt.recorded.v1"),
    ("correct_purchase_receipt", "purchase_receipt.corrected.v1"),
    ("link_purchase_receipt_line", "purchase_receipt.line_linked.v1"),
    ("link_purchase_receipt_line", "purchase_receipt.line_unlinked.v1"),
    ("retract_purchase_receipt", "purchase_receipt.retracted.v1"),
}
_ALLOWED_DIRECTORIES = {"plans", "sessions"}
_TERMINAL_MARKERS = ("commit.json", "abort.json", "conflict.json")
_AUDIT_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)
_RAW_EVENT_FIELDS = {"event_type", "entity"}
_CANONICAL_EVENT_FIELDS = {
    "schema_version",
    "event_id",
    "transaction_id",
    "operation_id",
    "sequence",
    "operation",
    "occurred_at",
    "actor",
    "surface",
    "correlation_id",
    "causation_id",
    "redaction_policy",
}


def _is_exact_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


class AuditConflictError(RuntimeError):
    """Raised when recovery sees state matching neither before nor after."""


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _month(value):
    if not isinstance(value, str) or _AUDIT_TIMESTAMP_RE.fullmatch(value) is None:
        raise ValueError("audit timestamp is not canonical UTC")
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value[:7]


def _sha256(data):
    if data is None:
        return None
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value):
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _open_directory_chain(path):
    """Pin every component of an absolute directory without following symlinks."""
    path = Path(os.path.abspath(path))
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path.anchor, flags)
    descriptors = [descriptor]
    component_names = []
    try:
        for component in path.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            descriptors.append(child)
            component_names.append(component)
            descriptor = child
        return descriptors, component_names
    except Exception:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise


def _fsync_directory(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_bytes(path, data, *, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except Exception:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _atomic_write_bytes_at(directory_fd, name, data, *, mode=0o600):
    """Atomically publish a regular file relative to one pinned directory."""

    if not isinstance(name, str) or not name or PurePosixPath(name).name != name:
        raise ValueError("descriptor-relative file name is invalid")
    temporary = "." + name + "." + uuid.uuid4().hex + ".tmp"
    descriptor = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            mode,
            dir_fd=directory_fd,
        )
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(
            temporary,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def _exclusive_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = _json_bytes(value)
    temporary = path.parent / ("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class AuditTransactionManager:
    """Apply whitelisted data-file changes with durable audit proof."""

    def __init__(self, data_dir, *, fault_injector=None):
        self._fault_injector = fault_injector
        self._attempt_local = threading.local()
        self.last_transaction_id = None
        self.configure(data_dir)

    @property
    def last_transaction_id(self):
        return getattr(self._attempt_local, "transaction_id", None)

    @last_transaction_id.setter
    def last_transaction_id(self, value):
        self._attempt_local.transaction_id = value

    def configure(self, data_dir):
        resolved = Path(os.path.abspath(data_dir))
        existing_lock = getattr(self, "lock", None)
        if getattr(self, "data_dir", None) == resolved and existing_lock is not None:
            self._assert_data_root_current()
            return
        if existing_lock is not None and existing_lock.active_path is not None:
            raise RuntimeError("cannot reconfigure active audit transaction manager")
        root_fd = None
        parent_fd = None
        ancestor_fds = []
        ancestor_names = []
        audit_fd = None
        data_fd = None
        child_fds = {}
        domain_fds = {}
        try:
            ancestor_fds, ancestor_names = _open_directory_chain(resolved.parent)
            parent_fd = os.dup(ancestor_fds[-1])
            try:
                os.mkdir(resolved.name, mode=0o700, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except FileExistsError:
                pass
            root_fd = os.open(
                resolved.name,
                os.O_RDONLY
                | os.O_DIRECTORY
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            try:
                os.mkdir("audit", mode=0o700, dir_fd=root_fd)
                os.fsync(root_fd)
            except FileExistsError:
                pass
            audit_fd = os.open(
                "audit",
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=root_fd,
            )
            for child_name in ("transactions", "events"):
                try:
                    os.mkdir(child_name, mode=0o700, dir_fd=audit_fd)
                    os.fsync(audit_fd)
                except FileExistsError:
                    pass
                child_fds[child_name] = os.open(
                    child_name,
                    os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=audit_fd,
                )
            for child_name in sorted(_ALLOWED_DIRECTORIES):
                try:
                    os.mkdir(child_name, mode=0o700, dir_fd=root_fd)
                    os.fsync(root_fd)
                except FileExistsError:
                    pass
                domain_fds[child_name] = os.open(
                    child_name,
                    os.O_RDONLY
                    | os.O_DIRECTORY
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=root_fd,
                )
            data_fd = os.dup(root_fd)
        except Exception:
            for descriptor in child_fds.values():
                os.close(descriptor)
            for descriptor in domain_fds.values():
                os.close(descriptor)
            if audit_fd is not None:
                os.close(audit_fd)
            if parent_fd is not None:
                os.close(parent_fd)
            for descriptor in reversed(ancestor_fds):
                os.close(descriptor)
            if data_fd is not None:
                os.close(data_fd)
            raise
        finally:
            if root_fd is not None:
                os.close(root_fd)
        self.data_dir = resolved
        previous_lock = getattr(self, "lock", None)
        if previous_lock is not None:
            previous_lock.close()
        previous_parent_fd = getattr(self, "_parent_fd", None)
        if previous_parent_fd is not None:
            os.close(previous_parent_fd)
        self._parent_fd = parent_fd
        for descriptor in reversed(getattr(self, "_ancestor_fds", [])):
            os.close(descriptor)
        self._ancestor_fds = ancestor_fds
        self._ancestor_names = ancestor_names
        previous_data_fd = getattr(self, "_data_fd", None)
        if previous_data_fd is not None:
            os.close(previous_data_fd)
        self._data_fd = data_fd
        previous_audit_fd = getattr(self, "_audit_fd", None)
        if previous_audit_fd is not None:
            os.close(previous_audit_fd)
        for attribute in ("_transactions_fd", "_events_fd"):
            previous = getattr(self, attribute, None)
            if previous is not None:
                os.close(previous)
        for descriptor in getattr(self, "_domain_directory_fds", {}).values():
            os.close(descriptor)
        self._audit_fd = audit_fd
        self._transactions_fd = child_fds["transactions"]
        self._events_fd = child_fds["events"]
        self._domain_directory_fds = domain_fds
        self.audit_dir = Path(f"/proc/self/fd/{audit_fd}")
        self.transactions_dir = Path(f"/proc/self/fd/{self._transactions_fd}")
        for descriptor in getattr(self, "_pending_transaction_fds", set()):
            try:
                os.close(descriptor)
            except OSError:
                pass
        self._pending_transaction_fds = set()
        self.events_dir = Path(f"/proc/self/fd/{self._events_fd}")
        self.lock = JsonFileLock.pinned(
            self._audit_fd,
            ".txn.lock",
            domain_fd=self._parent_fd,
        )
        self._last_committed_transaction_id = None

    def close(self):
        """Release all descriptors pinned for this manager instance."""
        lock = getattr(self, "lock", None)
        if lock is not None:
            lock.close()
            del self.lock
        self._close_pending_transaction_fds()
        for attribute in (
            "_events_fd",
            "_transactions_fd",
            "_audit_fd",
            "_data_fd",
            "_parent_fd",
        ):
            descriptor = getattr(self, attribute, None)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, attribute, None)
        for descriptor in getattr(self, "_domain_directory_fds", {}).values():
            os.close(descriptor)
        self._domain_directory_fds = {}
        for descriptor in reversed(getattr(self, "_ancestor_fds", [])):
            os.close(descriptor)
        self._ancestor_fds = []
        self._ancestor_names = []

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _assert_root_identity(self):
        """Fail closed if configured root/audit pathnames changed identity."""
        try:
            anchor = os.stat(
                self.data_dir.anchor, follow_symlinks=False
            )
            pinned_anchor = os.fstat(self._ancestor_fds[0])
            if (
                not stat.S_ISDIR(anchor.st_mode)
                or not stat.S_ISDIR(pinned_anchor.st_mode)
                or (anchor.st_dev, anchor.st_ino)
                != (pinned_anchor.st_dev, pinned_anchor.st_ino)
            ):
                raise AuditConflictError("audit ancestor root identity changed")
            for index, name in enumerate(self._ancestor_names):
                current = os.stat(
                    name,
                    dir_fd=self._ancestor_fds[index],
                    follow_symlinks=False,
                )
                pinned = os.fstat(self._ancestor_fds[index + 1])
                if (
                    not stat.S_ISDIR(current.st_mode)
                    or not stat.S_ISDIR(pinned.st_mode)
                    or (current.st_dev, current.st_ino)
                    != (pinned.st_dev, pinned.st_ino)
                ):
                    raise AuditConflictError(
                        "audit data-root ancestor identity changed"
                    )
        except AuditConflictError:
            raise
        except OSError as exc:
            raise AuditConflictError(
                "audit data-root ancestor identity is unavailable"
            ) from exc
        try:
            root = os.stat(
                self.data_dir.name,
                dir_fd=self._parent_fd,
                follow_symlinks=False,
            )
            pinned_root = os.fstat(self._data_fd)
            audit = os.stat("audit", dir_fd=self._data_fd, follow_symlinks=False)
            pinned_audit = os.fstat(self._audit_fd)
        except OSError as exc:
            raise AuditConflictError("audit data-root identity is unavailable") from exc
        if (
            not stat.S_ISDIR(root.st_mode)
            or not stat.S_ISDIR(audit.st_mode)
            or (root.st_dev, root.st_ino)
            != (pinned_root.st_dev, pinned_root.st_ino)
            or (audit.st_dev, audit.st_ino)
            != (pinned_audit.st_dev, pinned_audit.st_ino)
        ):
            raise AuditConflictError("audit data-root identity changed")
        for name, descriptor in self._domain_directory_fds.items():
            try:
                current = os.stat(
                    name, dir_fd=self._data_fd, follow_symlinks=False
                )
                pinned = os.fstat(descriptor)
            except OSError as exc:
                raise AuditConflictError(
                    f"audit domain directory '{name}' is unavailable"
                ) from exc
            if (
                not stat.S_ISDIR(current.st_mode)
                or not stat.S_ISDIR(pinned.st_mode)
                or (current.st_dev, current.st_ino)
                != (pinned.st_dev, pinned.st_ino)
            ):
                raise AuditConflictError(
                    f"audit domain directory '{name}' identity changed"
                )
        for name, descriptor in (
            ("transactions", self._transactions_fd),
            ("events", self._events_fd),
        ):
            try:
                current = os.stat(
                    name, dir_fd=self._audit_fd, follow_symlinks=False
                )
                pinned = os.fstat(descriptor)
            except OSError as exc:
                raise AuditConflictError(
                    f"audit '{name}' directory is unavailable"
                ) from exc
            if (
                not stat.S_ISDIR(current.st_mode)
                or not stat.S_ISDIR(pinned.st_mode)
                or (current.st_dev, current.st_ino)
                != (pinned.st_dev, pinned.st_ino)
            ):
                raise AuditConflictError(
                    f"audit '{name}' directory identity changed"
                )
        self.lock.assert_identity()

    def assert_repository_path(self, path, relative, *, directory=False):
        """Require an injected repository to use this manager's pinned root."""
        pure = PurePosixPath(relative)
        if directory:
            if (
                pure.is_absolute()
                or len(pure.parts) != 1
                or pure.name not in _ALLOWED_DIRECTORIES
            ):
                raise ValueError("unsupported audited repository directory")
        else:
            relative = self._relative_target(relative)
            pure = PurePosixPath(relative)
        expected = (self.data_dir / pure.as_posix()).absolute()
        logical = Path(os.path.abspath(path))
        if logical != expected:
            raise ValueError("injected repository escaped the audit data root")
        self._assert_root_identity()
        if directory:
            parent_fd = os.dup(self._domain_directory_fds[pure.name])
            try:
                self._assert_target_parent_identity(
                    f"{pure.name}/placeholder.json", parent_fd
                )
            finally:
                os.close(parent_fd)
        else:
            parent_fd, _name = self._open_target_parent(relative)
            try:
                self._assert_target_parent_identity(relative, parent_fd)
            finally:
                os.close(parent_fd)

    def list_json_targets(self, directory):
        """List JSON target names from one pinned domain directory."""
        if directory not in _ALLOWED_DIRECTORIES:
            raise ValueError("unsupported audited repository directory")
        self._assert_root_identity()
        descriptor = self._domain_directory_fds[directory]
        try:
            names = os.listdir(descriptor)
        except OSError as exc:
            raise AuditConflictError(
                f"audit domain directory '{directory}' is unreadable"
            ) from exc
        self._assert_root_identity()
        return sorted(
            name for name in names
            if isinstance(name, str) and name.endswith(".json")
        )

    @contextmanager
    def consistent_read(self):
        """Recover pending transactions and hold one coherent domain view."""
        from .context import audit_read_scope

        with self.lock:
            self._assert_root_identity()
            self._close_pending_transaction_fds()
            self._recover_unlocked()
            self._assert_root_identity()
            try:
                with audit_read_scope(self):
                    yield
            finally:
                self._assert_root_identity()

    def _assert_data_root_current(self):
        """Fail when the configured logical root no longer names the pinned inode."""

        pairs = [
            (self.data_dir, self._data_fd, "data root"),
            (self.data_dir / "audit", self._audit_fd, "audit directory"),
            (
                self.data_dir / "audit" / "transactions",
                self._transactions_fd,
                "audit transactions directory",
            ),
            (
                self.data_dir / "audit" / "events",
                self._events_fd,
                "audit events directory",
            ),
        ]
        try:
            identities = [
                (path, label, os.stat(path, follow_symlinks=False), os.fstat(descriptor))
                for path, descriptor, label in pairs
            ]
        except OSError as exc:
            raise ValueError("configured audit data root is unavailable") from exc
        for _path, label, logical, pinned in identities:
            if (
                not stat.S_ISDIR(logical.st_mode)
                or logical.st_dev != pinned.st_dev
                or logical.st_ino != pinned.st_ino
            ):
                raise ValueError(f"configured {label} identity changed")

    def _open_or_create_directory(self, parent_fd, name):
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            pass
        return os.open(name, flags, dir_fd=parent_fd)

    def _new_transaction_directory(self, occurred_at, transaction_id):
        for (
            _month_name,
            existing_transaction_id,
            _month_fd,
            _transaction_fd,
            _transaction_dir,
        ) in self._iter_transaction_directories():
            if existing_transaction_id == transaction_id:
                raise AuditConflictError("audit transaction ID already exists")
        month = _month(occurred_at)
        month_fd = self._open_or_create_directory(self._transactions_fd, month)
        try:
            try:
                os.mkdir(transaction_id, mode=0o700, dir_fd=month_fd)
                os.fsync(month_fd)
            except FileExistsError as exc:
                raise AuditConflictError(
                    "audit transaction ID already exists"
                ) from exc
            transaction_fd = os.open(
                transaction_id,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=month_fd,
            )
        except Exception:
            os.close(month_fd)
            raise
        self._pending_transaction_fds.update((month_fd, transaction_fd))
        return month_fd, transaction_fd, Path(f"/proc/self/fd/{transaction_fd}")

    def _assert_transaction_directory_identity(
        self, month, transaction_id, month_fd, transaction_fd
    ):
        if re.fullmatch(r"\d{4}-\d{2}", month) is None:
            raise AuditConflictError("audit month directory name is invalid")
        self._assert_root_identity()
        try:
            current_month = os.stat(
                month,
                dir_fd=self._transactions_fd,
                follow_symlinks=False,
            )
            pinned_month = os.fstat(month_fd)
            current = os.stat(
                transaction_id,
                dir_fd=month_fd,
                follow_symlinks=False,
            )
            pinned = os.fstat(transaction_fd)
        except OSError as exc:
            raise AuditConflictError(
                "audit transaction directory identity is unavailable"
            ) from exc
        if (
            not stat.S_ISDIR(current_month.st_mode)
            or not stat.S_ISDIR(pinned_month.st_mode)
            or (current_month.st_dev, current_month.st_ino)
            != (pinned_month.st_dev, pinned_month.st_ino)
            or not stat.S_ISDIR(current.st_mode)
            or not stat.S_ISDIR(pinned.st_mode)
            or (current.st_dev, current.st_ino)
            != (pinned.st_dev, pinned.st_ino)
        ):
            raise AuditConflictError(
                "audit transaction directory identity changed"
            )

    def _close_pending_transaction_fds(self):
        for descriptor in tuple(self._pending_transaction_fds):
            try:
                os.close(descriptor)
            except OSError:
                pass
            self._pending_transaction_fds.discard(descriptor)

    def _iter_transaction_directories(self):
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        self._assert_root_identity()
        with os.scandir(self.transactions_dir) as month_scan:
            month_names = sorted(entry.name for entry in month_scan)
        for month_name in month_names:
            if (
                re.fullmatch(r"\d{4}-\d{2}", month_name) is None
                or not 1 <= int(month_name[5:]) <= 12
            ):
                raise AuditConflictError("audit month directory name is invalid")
            try:
                month_fd = os.open(month_name, flags, dir_fd=self._transactions_fd)
            except OSError as exc:
                raise AuditConflictError("audit month directory is unsafe") from exc
            try:
                month_path = Path(f"/proc/self/fd/{month_fd}")
                with os.scandir(month_path) as transaction_scan:
                    transaction_names = sorted(entry.name for entry in transaction_scan)
                for transaction_name in transaction_names:
                    if re.fullmatch(r"tx_[0-9a-f]{32}", transaction_name) is None:
                        raise AuditConflictError(
                            "audit transaction directory name is invalid"
                        )
                    try:
                        transaction_fd = os.open(
                            transaction_name, flags, dir_fd=month_fd
                        )
                    except OSError as exc:
                        raise AuditConflictError(
                            "audit transaction directory is unsafe"
                        ) from exc
                    try:
                        self._assert_transaction_directory_identity(
                            month_name, transaction_name, month_fd, transaction_fd
                        )
                        yield (
                            month_name,
                            transaction_name,
                            month_fd,
                            transaction_fd,
                            Path(f"/proc/self/fd/{transaction_fd}"),
                        )
                    finally:
                        os.close(transaction_fd)
            finally:
                os.close(month_fd)

    def _fault(self, stage):
        if self._fault_injector is not None:
            self._fault_injector(stage)

    def _relative_target(self, raw_path, *, allow_legacy_recovery=False):
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("audit target path must be a non-empty relative string")
        pure = PurePosixPath(raw_path)
        if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
            raise ValueError("audit target path is outside the data root")
        if len(pure.parts) == 1:
            allowed_roots = _ALLOWED_ROOTS | (
                _LEGACY_RECOVERY_ROOTS if allow_legacy_recovery else set()
            )
            if pure.name not in allowed_roots:
                raise ValueError(f"unsupported audit target '{raw_path}'")
        elif pure.parts[0] not in _ALLOWED_DIRECTORIES or len(pure.parts) != 2:
            raise ValueError(f"unsupported audit target '{raw_path}'")
        if pure.suffix != ".json":
            raise ValueError("audit targets must be JSON files")
        return pure.as_posix()

    def _open_target_parent(self, relative):
        pure = PurePosixPath(relative)
        if len(pure.parts) == 1:
            descriptor = os.dup(self._data_fd)
        else:
            descriptor = os.dup(self._domain_directory_fds[pure.parts[0]])
        return descriptor, pure.name

    def _assert_target_parent_identity(self, relative, parent_fd):
        pure = PurePosixPath(relative)
        pinned = os.fstat(parent_fd)
        if len(pure.parts) == 1:
            canonical = os.fstat(self._data_fd)
        else:
            canonical = os.fstat(
                self._domain_directory_fds[pure.parts[0]]
            )
        if (
            not stat.S_ISDIR(pinned.st_mode)
            or not stat.S_ISDIR(canonical.st_mode)
            or (pinned.st_dev, pinned.st_ino)
            != (canonical.st_dev, canonical.st_ino)
        ):
            raise AuditConflictError("audit target parent identity changed")
        self._assert_root_identity()

    @contextmanager
    def _pin_target_parents(self, relatives):
        pinned = {}
        try:
            for relative in sorted(set(relatives)):
                parent_fd, name = self._open_target_parent(relative)
                self._assert_target_parent_identity(relative, parent_fd)
                pinned[relative] = (parent_fd, name)
            yield pinned
        finally:
            for parent_fd, _name in pinned.values():
                os.close(parent_fd)

    def _read_target(self, relative, *, pinned=None):
        owns_parent = pinned is None
        if pinned is None:
            parent, name = self._open_target_parent(relative)
        else:
            parent, name = pinned
        try:
            self._assert_target_parent_identity(relative, parent)
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return None
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise AuditConflictError("audit target must be a regular file")
            descriptor = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=parent,
            )
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
            ):
                os.close(descriptor)
                raise AuditConflictError("audit target identity changed")
            with os.fdopen(descriptor, "rb") as handle:
                return handle.read()
        finally:
            if owns_parent:
                os.close(parent)

    def read_target(self, raw_path):
        """Read one allowlisted target through the pinned data-root descriptor."""

        relative = self._relative_target(raw_path)
        self._assert_root_identity()
        return self._read_target(relative)

    def _write_target(self, relative, payload, *, pinned=None):
        owns_parent = pinned is None
        if pinned is None:
            parent, name = self._open_target_parent(relative)
        else:
            parent, name = pinned
        temporary = ".audit-" + uuid.uuid4().hex + ".tmp"
        try:
            self._assert_target_parent_identity(relative, parent)
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                    raise AuditConflictError("audit target must be a regular file")
            except FileNotFoundError:
                pass
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent,
            )
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
        finally:
            if owns_parent:
                os.close(parent)

    def _delete_target(self, relative, *, pinned=None):
        owns_parent = pinned is None
        if pinned is None:
            parent, name = self._open_target_parent(relative)
        else:
            parent, name = pinned
        try:
            self._assert_target_parent_identity(relative, parent)
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return False
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise AuditConflictError("audit target must be a regular file")
            os.unlink(name, dir_fd=parent)
            os.fsync(parent)
            return True
        finally:
            if owns_parent:
                os.close(parent)

    def _assert_manifest_parents(self, targets, target_parents):
        """Require recovery target parents to match prepare-time identity."""
        for target in targets:
            manifest_parent = target.get("parent_dir")
            if manifest_parent is None:
                continue
            parent_fd, _name = target_parents[target["relative_path"]]
            current = os.fstat(parent_fd)
            if (
                not stat.S_ISDIR(current.st_mode)
                or current.st_dev != manifest_parent["dev"]
                or current.st_ino != manifest_parent["ino"]
            ):
                raise AuditConflictError(
                    "audit target parent identity changed since prepare"
                )

    @staticmethod
    def _validate_context(context):
        if not isinstance(context, dict):
            raise ValueError("audit context must be an object")
        required = {"actor", "surface"}
        allowed = required | {"correlation_id", "causation_id"}
        actor = context.get("actor")
        surface = context.get("surface")
        if (
            not required.issubset(context)
            or not set(context).issubset(allowed)
            or not isinstance(actor, dict)
            or set(actor) != {"type"}
            or not isinstance(actor.get("type"), str)
            or not actor["type"]
            or not isinstance(surface, dict)
            or set(surface) not in ({"kind"}, {"kind", "operation"})
            or not isinstance(surface.get("kind"), str)
            or not surface["kind"]
            or (
                "operation" in surface
                and (
                    not isinstance(surface["operation"], str)
                    or not surface["operation"]
                )
            )
            or (
                "correlation_id" in context
                and (
                    not isinstance(context["correlation_id"], str)
                    or not context["correlation_id"]
                )
            )
            or (
                context.get("causation_id") is not None
                and not isinstance(context.get("causation_id"), str)
            )
        ):
            raise ValueError("audit context fields are invalid")

    def _validate_event(self, event, *, canonical=False):
        if not isinstance(event, dict):
            raise ValueError("audit events must be objects")
        content_fields = {key for key in ("payload", "change") if key in event}
        expected = _RAW_EVENT_FIELDS | content_fields
        if canonical:
            expected |= _CANONICAL_EVENT_FIELDS
        if len(content_fields) != 1 or set(event) != expected:
            raise ValueError("audit event fields are invalid")
        if not isinstance(event.get("event_type"), str) or not event["event_type"]:
            raise ValueError("audit event_type is required")
        entity = event.get("entity")
        if (
            not isinstance(entity, dict)
            or set(entity) != {"type", "id"}
            or not isinstance(entity.get("type"), str)
            or not entity.get("type")
            or not isinstance(entity.get("id"), str)
            or not entity.get("id")
        ):
            raise ValueError("audit event entity requires type and id")
        content = event[next(iter(content_fields))]
        if not isinstance(content, dict):
            raise ValueError("audit event payload/change must be an object")
        if canonical:
            self._validate_context({
                "actor": event.get("actor"),
                "surface": event.get("surface"),
                "correlation_id": event.get("correlation_id"),
                "causation_id": event.get("causation_id"),
            })

    def _validate_prepared_event(
        self, event, *, transaction_id, sequence, operation
    ):
        try:
            self._validate_event(event, canonical=True)
            _month(event.get("occurred_at"))
        except (KeyError, TypeError, ValueError) as exc:
            raise AuditConflictError("audit event metadata is corrupt") from exc
        actor = event.get("actor")
        surface = event.get("surface")
        event_id = event.get("event_id")
        correlation_id = event.get("correlation_id")
        causation_id = event.get("causation_id")
        if (
            not _is_exact_int(event.get("schema_version"))
            or event.get("schema_version") != 1
            or not isinstance(event_id, str)
            or re.fullmatch(r"evt_[0-9a-f]{32}", event_id) is None
            or event.get("transaction_id") != transaction_id
            or event.get("operation_id") != transaction_id
            or not _is_exact_int(event.get("sequence"))
            or event.get("sequence") != sequence
            or event.get("operation") != operation
            or not isinstance(actor, dict)
            or not isinstance(actor.get("type"), str)
            or not actor["type"]
            or not isinstance(surface, dict)
            or not isinstance(surface.get("kind"), str)
            or not surface["kind"]
            or not isinstance(correlation_id, str)
            or not correlation_id
            or (causation_id is not None and not isinstance(causation_id, str))
            or event.get("redaction_policy") != "meal-audit-v1"
        ):
            raise AuditConflictError("audit event metadata is corrupt")

    def _prepare_events(self, events, transaction_id, operation, context, occurred_at):
        self._validate_context(context)
        actor = context["actor"]
        surface = context["surface"]
        prepared = []
        for sequence, raw in enumerate(events, 1):
            self._validate_event(raw)
            event = dict(raw)
            event.update({
                "schema_version": 1,
                "event_id": "evt_" + uuid.uuid4().hex,
                "transaction_id": transaction_id,
                "operation_id": transaction_id,
                "sequence": sequence,
                "operation": operation,
                "occurred_at": occurred_at,
                "actor": dict(actor),
                "surface": dict(surface),
                "correlation_id": context.get("correlation_id", transaction_id),
                "causation_id": context.get("causation_id"),
                "redaction_policy": "meal-audit-v1",
            })
            prepared.append(event)
        return prepared

    def commit(self, *, operation, targets, events, context):
        self.last_transaction_id = None
        if not isinstance(operation, str) or not operation:
            raise ValueError("audit operation is required")
        if not isinstance(targets, dict) or not targets:
            raise ValueError("audit transaction requires at least one target")
        if not isinstance(events, list) or not events:
            raise ValueError("audit transaction requires at least one event")
        normalized_targets = {}
        for raw_path, after in targets.items():
            relative = self._relative_target(raw_path)
            if after is not None and not isinstance(after, bytes):
                raise ValueError("audit target after-images must be bytes or null")
            normalized_targets[relative] = after

        with self.lock:
            self._assert_root_identity()
            self._close_pending_transaction_fds()
            self._recover_unlocked()
            self._assert_root_identity()
            with self._pin_target_parents(normalized_targets) as target_parents:
                return self._commit_unlocked(
                    operation=operation,
                    normalized_targets=normalized_targets,
                    events=events,
                    context=context,
                    target_parents=target_parents,
                )

    def _commit_unlocked(
        self, *, operation, normalized_targets, events, context, target_parents
    ):
        transaction_id = "tx_" + uuid.uuid4().hex
        self.last_transaction_id = transaction_id
        occurred_at = _utc_now()
        events_prepared = self._prepare_events(
            events, transaction_id, operation, context, occurred_at
        )
        canonical_transaction_dir = (
            self.data_dir / "audit" / "transactions"
            / _month(occurred_at) / transaction_id
        )
        month_fd, transaction_fd, transaction_dir = (
            self._new_transaction_directory(occurred_at, transaction_id)
        )
        os.chmod(transaction_dir, 0o700)
        targets_fd = self._open_or_create_directory(transaction_fd, "targets")
        try:
            self._fault("after_targets_open")
            manifest_targets = []
            for index, (relative, after) in enumerate(
                sorted(normalized_targets.items())
            ):
                before = self._read_target(
                    relative, pinned=target_parents[relative]
                )
                parent_stat = os.fstat(target_parents[relative][0])
                before_name = f"targets/{index:03d}.before"
                after_name = f"targets/{index:03d}.after"
                if before is not None:
                    _atomic_write_bytes_at(
                        targets_fd, f"{index:03d}.before", before
                    )
                if after is not None:
                    _atomic_write_bytes_at(
                        targets_fd, f"{index:03d}.after", after
                    )
                manifest_targets.append({
                    "relative_path": relative,
                    "before_exists": before is not None,
                    "before_sha256": _sha256(before),
                    "before_blob": before_name if before is not None else None,
                    "after_exists": after is not None,
                    "after_sha256": _sha256(after),
                    "after_blob": after_name if after is not None else None,
                    "parent_dir": {
                        "dev": parent_stat.st_dev,
                        "ino": parent_stat.st_ino,
                    },
                })
        finally:
            os.close(targets_fd)

        prepare = {
            "schema_version": 2,
            "transaction_id": transaction_id,
            "predecessor_transaction_id": self._last_committed_transaction_id,
            "state": "prepared",
            "prepared_at": _utc_now(),
            "operation": operation,
            "context": context,
            "targets": manifest_targets,
            "events": events_prepared,
        }
        _atomic_write_bytes(transaction_dir / "prepare.json", _json_bytes(prepare))
        self._fault("after_prepare")
        self._assert_root_identity()
        self._assert_transaction_directory_identity(
            _month(occurred_at), transaction_id, month_fd, transaction_fd
        )

        for index, target in enumerate(manifest_targets):
            relative = target["relative_path"]
            pinned = target_parents[relative]
            if target["after_exists"]:
                after = self._read_transaction_file(
                    transaction_dir, target["after_blob"]
                )
                if _sha256(after) != target["after_sha256"]:
                    raise AuditConflictError("audit after-image hash mismatch")
                self._write_target(relative, after, pinned=pinned)
            else:
                self._delete_target(relative, pinned=pinned)
            self._fault(f"after_target:{index}")
        self._verify_targets(
            manifest_targets,
            transaction_dir,
            state="after",
            target_parents=target_parents,
        )
        self._fault("after_all_targets")
        self._assert_root_identity()
        self._assert_transaction_directory_identity(
            _month(occurred_at), transaction_id, month_fd, transaction_fd
        )
        self._verify_targets(
            manifest_targets,
            transaction_dir,
            state="after",
            target_parents=target_parents,
        )

        committed_at = _utc_now()
        _exclusive_json(transaction_dir / "commit.json", {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "state": "committed",
            "committed_at": committed_at,
        })
        self._fault("after_commit")
        self._assert_root_identity()
        self._assert_transaction_directory_identity(
            _month(occurred_at), transaction_id, month_fd, transaction_fd
        )
        self._verify_targets(
            manifest_targets,
            transaction_dir,
            state="after",
            target_parents=target_parents,
        )
        self._export_events(events_prepared, committed_at)
        self._fault("after_export")
        self._assert_root_identity()
        self._assert_transaction_directory_identity(
            _month(occurred_at), transaction_id, month_fd, transaction_fd
        )
        self._verify_targets(
            manifest_targets,
            transaction_dir,
            state="after",
            target_parents=target_parents,
        )
        self._pending_transaction_fds.discard(transaction_fd)
        os.close(transaction_fd)
        self._pending_transaction_fds.discard(month_fd)
        os.close(month_fd)
        return {
            "status": "committed",
            "transaction_id": transaction_id,
            "transaction_dir": str(canonical_transaction_dir),
            "event_ids": [event["event_id"] for event in events_prepared],
        }


    def recover(self):
        with self.lock:
            self._assert_root_identity()
            self._close_pending_transaction_fds()
            recovered = self._recover_unlocked()
            self._assert_root_identity()
            return recovered

    def resolve_last_transaction(self):
        """Recover and return a committed result when the last transaction won."""
        with self.lock:
            self._assert_root_identity()
            transaction_id = self.last_transaction_id
            self._close_pending_transaction_fds()
            self._recover_unlocked()
            if not transaction_id:
                return None
            matches = 0
            for (
                _month_name,
                candidate_id,
                _month_fd,
                transaction_fd,
                _transaction_dir,
            ) in self._iter_transaction_directories():
                if candidate_id != transaction_id:
                    continue
                if self._regular_entry_exists(
                    transaction_fd,
                    "commit.json",
                    label="audit terminal marker",
                ):
                    matches += 1
            if matches != 1:
                return None
            return {"status": "committed", "transaction_id": transaction_id}

    @staticmethod
    def _regular_entry_exists(directory_fd, name, *, label):
        try:
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise AuditConflictError(f"{label} is unreadable") from exc
        if not stat.S_ISREG(info.st_mode):
            raise AuditConflictError(f"{label} is not a regular file")
        return True

    @staticmethod
    def _read_regular_entry(directory_fd, name, *, label):
        descriptor = None
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=directory_fd,
            )
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise AuditConflictError(f"{label} is not a regular file")
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = None
                return handle.read()
        except AuditConflictError:
            raise
        except OSError as exc:
            raise AuditConflictError(f"{label} is unreadable") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _read_transaction_file(self, transaction_dir, relative):
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            raise AuditConflictError("audit transaction file path is unsafe")
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        transaction_dir = Path(transaction_dir)
        if transaction_dir.parent == Path("/proc/self/fd"):
            descriptor = os.dup(int(transaction_dir.name))
        else:
            descriptor = os.open(transaction_dir, directory_flags)
        try:
            for component in pure.parts[:-1]:
                child = os.open(component, directory_flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            file_fd = os.open(
                pure.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=descriptor,
            )
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode):
                os.close(file_fd)
                raise AuditConflictError("audit transaction file is not regular")
            with os.fdopen(file_fd, "rb") as handle:
                return handle.read()
        except OSError as exc:
            raise AuditConflictError("audit transaction file is unreadable") from exc
        finally:
            os.close(descriptor)

    def _validate_prepare(
        self,
        prepare,
        transaction_dir,
        expected_transaction_id=None,
        expected_month=None,
    ):
        if not isinstance(prepare, dict):
            raise AuditConflictError("audit prepare record must be an object")
        expected_transaction_id = expected_transaction_id or transaction_dir.name
        required_prepare_fields = {
            "schema_version", "transaction_id", "state", "prepared_at",
            "operation", "context", "targets", "events",
        }
        allowed_prepare_fields = required_prepare_fields | {
            "predecessor_transaction_id"
        }
        try:
            _month(prepare.get("prepared_at"))
        except (TypeError, ValueError) as exc:
            raise AuditConflictError("audit prepare timestamp is corrupt") from exc
        operation = prepare.get("operation")
        context = prepare.get("context")
        version = prepare.get("schema_version")
        try:
            self._validate_context(context)
        except ValueError as exc:
            raise AuditConflictError("audit prepare context is corrupt") from exc
        if (
            not required_prepare_fields.issubset(prepare)
            or not set(prepare).issubset(allowed_prepare_fields)
            or not _is_exact_int(version)
            or version not in {1, 2}
            or (version == 1 and set(prepare) != required_prepare_fields)
            or (version == 2 and set(prepare) != allowed_prepare_fields)
            or prepare.get("transaction_id") != expected_transaction_id
            or prepare.get("state") != "prepared"
            or not isinstance(operation, str)
            or not operation
        ):
            raise AuditConflictError("audit prepare metadata is corrupt")
        predecessor = prepare.get("predecessor_transaction_id")
        if (
            version == 2
            and predecessor is not None
            and (
                not isinstance(predecessor, str)
                or re.fullmatch(r"tx_[0-9a-f]{32}", predecessor) is None
                or predecessor == expected_transaction_id
            )
        ):
            raise AuditConflictError("audit predecessor transaction is corrupt")
        targets = prepare.get("targets")
        events = prepare.get("events")
        if (
            not isinstance(targets, list) or not targets
            or not isinstance(events, list) or not events
        ):
            raise AuditConflictError("audit prepare targets/events are corrupt")
        event_ids = set()
        occurred_at = None
        for sequence, event in enumerate(events, 1):
            self._validate_prepared_event(
                event,
                transaction_id=expected_transaction_id,
                sequence=sequence,
                operation=operation,
            )
            if event["event_id"] in event_ids:
                raise AuditConflictError("audit event IDs are not unique")
            event_ids.add(event["event_id"])
            if occurred_at is None:
                occurred_at = event["occurred_at"]
            elif event["occurred_at"] != occurred_at:
                raise AuditConflictError("audit event timestamps disagree")
        if expected_month is not None and _month(occurred_at) != expected_month:
            raise AuditConflictError("audit transaction is in the wrong month directory")
        relative_paths = []
        target_fields_v1 = {
            "relative_path",
            "before_exists",
            "before_sha256",
            "before_blob",
            "after_exists",
            "after_sha256",
            "after_blob",
        }
        target_fields_v2 = target_fields_v1 | {"parent_dir"}
        for index, target in enumerate(targets):
            expected_fields = (
                target_fields_v2 if version == 2 else target_fields_v1
            )
            if not isinstance(target, dict) or set(target) != expected_fields:
                raise AuditConflictError("audit target manifest is corrupt")
            manifest_parent = target.get("parent_dir")
            if manifest_parent is not None and (
                not isinstance(manifest_parent, dict)
                or set(manifest_parent) != {"dev", "ino"}
                or not _is_exact_int(manifest_parent.get("dev"))
                or not _is_exact_int(manifest_parent.get("ino"))
            ):
                raise AuditConflictError(
                    "audit target parent identity is corrupt"
                )
            try:
                relative = self._relative_target(
                    target["relative_path"], allow_legacy_recovery=True
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise AuditConflictError("audit target path is corrupt") from exc
            if relative != target["relative_path"]:
                raise AuditConflictError("audit target path is not canonical")
            if relative in relative_paths:
                raise AuditConflictError("audit target paths are not unique")
            relative_paths.append(relative)
            for state in ("before", "after"):
                exists = target.get(f"{state}_exists")
                digest = target.get(f"{state}_sha256")
                blob = target.get(f"{state}_blob")
                expected_blob = f"targets/{index:03d}.{state}" if exists else None
                if not isinstance(exists, bool) or blob != expected_blob:
                    raise AuditConflictError("audit target blob reference is corrupt")
                if exists:
                    try:
                        payload = self._read_transaction_file(
                            transaction_dir, expected_blob
                        )
                    except AuditConflictError as exc:
                        raise AuditConflictError("audit target blob is unreadable") from exc
                    if _sha256(payload) != digest:
                        raise AuditConflictError("audit target blob hash mismatch")
                elif digest is not None:
                    raise AuditConflictError("absent audit target has a digest")
        if version == 1 and any(
            path in _LEGACY_RECOVERY_ROOTS for path in relative_paths
        ):
            event = events[0] if len(events) == 1 else None
            entity = event.get("entity") if isinstance(event, dict) else None
            if (
                relative_paths != ["receipts.json"]
                or event is None
                or (operation, event.get("event_type"))
                not in _LEGACY_RECOVERY_CONTRACTS
                or not isinstance(entity, dict)
                or entity.get("type") != "purchase_receipt"
                or not isinstance(entity.get("id"), str)
                or re.fullmatch(r"receipt_[0-9a-f]{32}", entity["id"]) is None
            ):
                raise AuditConflictError(
                    "legacy receipt audit proof is outside the recovery contract"
                )
        return targets

    def _read_terminal(
        self, path, expected_state, transaction_id, *, expected_target_count=None
    ):
        try:
            record = json.loads(
                self._read_transaction_file(path.parent, path.name).decode("utf-8")
            )
        except (AuditConflictError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AuditConflictError("audit terminal marker is corrupt") from exc
        if not isinstance(record, dict):
            raise AuditConflictError("audit terminal marker must be an object")
        timestamp_field = {
            "committed": "committed_at",
            "aborted": "aborted_at",
            "conflict": "detected_at",
        }[expected_state]
        try:
            _month(record.get(timestamp_field))
        except (TypeError, ValueError) as exc:
            raise AuditConflictError("audit terminal marker timestamp is corrupt") from exc
        common_fields = {"schema_version", "transaction_id", "state", timestamp_field}
        optional_fields = {
            "committed": {"recovered"},
            "aborted": {"recovered", "rolled_back_mixed_state"},
            "conflict": set(),
        }[expected_state]
        required_fields = (
            common_fields | ({"target_states"} if expected_state == "conflict" else set())
        )
        target_states = record.get("target_states")
        if (
            not isinstance(record, dict)
            or not _is_exact_int(record.get("schema_version"))
            or record.get("schema_version") != 1
            or record.get("transaction_id") != transaction_id
            or record.get("state") != expected_state
            or not required_fields.issubset(record)
            or not set(record).issubset(required_fields | optional_fields)
            or any(
                key in record and record[key] is not True
                for key in ("recovered", "rolled_back_mixed_state")
            )
            or (
                expected_state == "conflict"
                and (
                    not isinstance(target_states, list)
                    or expected_target_count is None
                    or len(target_states) != expected_target_count
                    or any(
                        state not in {"before", "after", "unknown"}
                        for state in target_states
                    )
                )
            )
        ):
            raise AuditConflictError("audit terminal marker does not match transaction")
        return record

    def _verify_committed_target_chains(self, committed_records):
        """Validate total-order lineage, target continuity, and latest images."""
        transaction_ids = [
            record["transaction_id"] for record in committed_records
        ]
        if len(transaction_ids) != len(set(transaction_ids)):
            raise AuditConflictError(
                "audit transaction IDs are not globally unique"
            )
        legacy = sorted(
            (
                record for record in committed_records
                if not record["predecessor_present"]
            ),
            key=lambda record: (
                record["occurred_at"], record["transaction_id"]
            ),
        )
        linked = {
            record["transaction_id"]: record
            for record in committed_records
            if record["predecessor_present"]
        }
        ordered = list(legacy)
        tip = legacy[-1]["transaction_id"] if legacy else None
        while linked:
            candidates = [
                record for record in linked.values()
                if record["predecessor_transaction_id"] == tip
            ]
            if len(candidates) != 1:
                raise AuditConflictError(
                    "audit committed transaction lineage is branched or disconnected"
                )
            record = candidates[0]
            ordered.append(record)
            tip = record["transaction_id"]
            del linked[record["transaction_id"]]

        latest = {}
        for record in ordered:
            transaction_id = record["transaction_id"]
            for target in record["targets"]:
                relative = target["relative_path"]
                previous = latest.get(relative)
                if previous is not None and (
                    target["before_exists"] != previous["after_exists"]
                    or target["before_sha256"] != previous["after_sha256"]
                ):
                    raise AuditConflictError(
                        f"audit committed target chain is broken for {relative}"
                    )
                latest[relative] = target | {"transaction_id": transaction_id}
        with self._pin_target_parents(latest) as target_parents:
            for relative, target in latest.items():
                current = self._read_target(
                    relative, pinned=target_parents[relative]
                )
                if (
                    (current is not None) != target["after_exists"]
                    or _sha256(current) != target["after_sha256"]
                ):
                    raise AuditConflictError(
                        f"audit latest committed target {relative} does not match storage"
                    )
        return ordered

    def _recover_unlocked(self):
        self._assert_data_root_current()
        recovered = []
        committed_records = []
        seen_transaction_ids = set()
        for (
            month_name,
            transaction_name,
            month_fd,
            transaction_fd,
            transaction_dir,
        ) in self._iter_transaction_directories():
            if transaction_name in seen_transaction_ids:
                raise AuditConflictError(
                    "audit transaction IDs are not globally unique"
                )
            seen_transaction_ids.add(transaction_name)
            prepare_path = transaction_dir / "prepare.json"
            if not self._regular_entry_exists(
                transaction_fd, "prepare.json", label="audit prepare record"
            ):
                terminal_without_prepare = any(
                    self._regular_entry_exists(
                        transaction_fd,
                        marker,
                        label="audit terminal marker",
                    )
                    for marker in _TERMINAL_MARKERS
                )
                self._assert_transaction_directory_identity(
                    month_name, transaction_name, month_fd, transaction_fd
                )
                if terminal_without_prepare:
                    raise AuditConflictError(
                        "audit terminal marker has no prepare record"
                    )
                continue
            try:
                prepare = json.loads(
                    self._read_transaction_file(
                        transaction_dir, "prepare.json"
                    ).decode("utf-8")
                )
            except (AuditConflictError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AuditConflictError("audit prepare record is corrupt") from exc
            targets = self._validate_prepare(
                prepare,
                transaction_dir,
                transaction_name,
                expected_month=month_name,
            )
            self._assert_transaction_directory_identity(
                month_name, transaction_name, month_fd, transaction_fd
            )
            transaction_id = prepare["transaction_id"]
            terminals = []
            for marker in _TERMINAL_MARKERS:
                if self._regular_entry_exists(
                    transaction_fd,
                    marker,
                    label="audit terminal marker",
                ):
                    terminals.append(transaction_dir / marker)
            if len(terminals) > 1:
                raise AuditConflictError("audit transaction has contradictory terminal markers")
            if terminals:
                marker = terminals[0]
                expected_state = {
                    "commit.json": "committed",
                    "abort.json": "aborted",
                    "conflict.json": "conflict",
                }[marker.name]
                self._read_terminal(
                    marker,
                    expected_state,
                    transaction_id,
                    expected_target_count=(
                        len(targets) if expected_state == "conflict" else None
                    ),
                )
                self._assert_transaction_directory_identity(
                    month_name, transaction_name, month_fd, transaction_fd
                )
                if marker.name == "commit.json":
                    committed_records.append({
                        "occurred_at": prepare["events"][0]["occurred_at"],
                        "transaction_id": transaction_id,
                        "predecessor_present": (
                            "predecessor_transaction_id" in prepare
                        ),
                        "predecessor_transaction_id": prepare.get(
                            "predecessor_transaction_id"
                        ),
                        "targets": targets,
                    })
                elif marker.name == "conflict.json":
                    raise AuditConflictError(
                        f"audit transaction {transaction_id} has unresolved conflict"
                    )
                continue
            with self._pin_target_parents(
                target["relative_path"] for target in targets
            ) as target_parents:
                self._assert_manifest_parents(targets, target_parents)
                states = self._target_states(targets, target_parents)
                self._assert_transaction_directory_identity(
                    month_name, transaction_name, month_fd, transaction_fd
                )
                if all(state == "after" for state in states):
                    self._require_unchanged_target_states(
                        states, self._target_states(targets, target_parents)
                    )
                    committed_at = _utc_now()
                    _exclusive_json(transaction_dir / "commit.json", {
                        "schema_version": 1,
                        "transaction_id": transaction_id,
                        "state": "committed",
                        "committed_at": committed_at,
                        "recovered": True,
                    })
                    self._verify_targets(
                        targets,
                        transaction_dir,
                        state="after",
                        target_parents=target_parents,
                    )
                    self._assert_transaction_directory_identity(
                        month_name, transaction_name, month_fd, transaction_fd
                    )
                    committed_records.append({
                        "occurred_at": prepare["events"][0]["occurred_at"],
                        "transaction_id": transaction_id,
                        "predecessor_present": (
                            "predecessor_transaction_id" in prepare
                        ),
                        "predecessor_transaction_id": prepare.get(
                            "predecessor_transaction_id"
                        ),
                        "targets": targets,
                    })
                    recovered.append((transaction_id, "committed"))
                    continue
                if all(state == "before" for state in states):
                    self._require_unchanged_target_states(
                        states, self._target_states(targets, target_parents)
                    )
                    _exclusive_json(transaction_dir / "abort.json", {
                        "schema_version": 1,
                        "transaction_id": transaction_id,
                        "state": "aborted",
                        "aborted_at": _utc_now(),
                        "recovered": True,
                    })
                    self._verify_targets(
                        targets,
                        transaction_dir,
                        state="before",
                        target_parents=target_parents,
                    )
                    self._assert_transaction_directory_identity(
                        month_name, transaction_name, month_fd, transaction_fd
                    )
                    recovered.append((transaction_id, "aborted"))
                    continue
                if all(state in {"before", "after"} for state in states):
                    self._require_unchanged_target_states(
                        states, self._target_states(targets, target_parents)
                    )
                    self._restore_before(
                        targets,
                        transaction_dir,
                        target_parents=target_parents,
                    )
                    self._assert_transaction_directory_identity(
                        month_name, transaction_name, month_fd, transaction_fd
                    )
                    _exclusive_json(transaction_dir / "abort.json", {
                        "schema_version": 1,
                        "transaction_id": transaction_id,
                        "state": "aborted",
                        "aborted_at": _utc_now(),
                        "recovered": True,
                        "rolled_back_mixed_state": True,
                    })
                    self._assert_transaction_directory_identity(
                        month_name, transaction_name, month_fd, transaction_fd
                    )
                    recovered.append((transaction_id, "rolled_back"))
                    continue
                self._require_unchanged_target_states(
                    states, self._target_states(targets, target_parents)
                )
                _exclusive_json(transaction_dir / "conflict.json", {
                    "schema_version": 1,
                    "transaction_id": transaction_id,
                    "state": "conflict",
                    "detected_at": _utc_now(),
                    "target_states": states,
                })
                self._assert_transaction_directory_identity(
                    month_name, transaction_name, month_fd, transaction_fd
                )
                raise AuditConflictError(
                    f"audit transaction {transaction_id} has unknown target state"
                )
        ordered_commits = (
            self._verify_committed_target_chains(committed_records)
            if committed_records else []
        )
        self._last_committed_transaction_id = (
            ordered_commits[-1]["transaction_id"] if ordered_commits else None
        )
        self._assert_root_identity()
        self._export_events([], None)
        self._assert_root_identity()
        return recovered

    def _target_states(self, targets, target_parents):
        return [
            self._current_target_state(
                target,
                pinned=target_parents[target["relative_path"]],
            )
            for target in targets
        ]

    @staticmethod
    def _require_unchanged_target_states(observed, current):
        if current != observed:
            raise AuditConflictError(
                "audit target state changed during recovery decision"
            )

    def _current_target_state(self, target, *, pinned=None):
        current = self._read_target(
            target["relative_path"], pinned=pinned
        )
        digest = _sha256(current)
        exists = current is not None
        before_match = (
            exists == target["before_exists"] and digest == target["before_sha256"]
        )
        after_match = (
            exists == target["after_exists"] and digest == target["after_sha256"]
        )
        if before_match and after_match:
            return "after"
        if after_match:
            return "after"
        if before_match:
            return "before"
        return "unknown"

    def _restore_before(
        self, targets, transaction_dir, *, target_parents=None
    ):
        for target in targets:
            relative = target["relative_path"]
            pinned = (
                target_parents[relative]
                if target_parents is not None else None
            )
            if target["before_exists"]:
                before = self._read_transaction_file(
                    transaction_dir, target["before_blob"]
                )
                if _sha256(before) != target["before_sha256"]:
                    raise AuditConflictError("audit before-image hash mismatch")
                self._write_target(relative, before, pinned=pinned)
            else:
                self._delete_target(relative, pinned=pinned)
        self._verify_targets(
            targets,
            transaction_dir,
            state="before",
            target_parents=target_parents,
        )

    def _verify_targets(
        self, targets, transaction_dir, *, state, target_parents=None
    ):
        for target in targets:
            expected_exists = target[f"{state}_exists"]
            expected_digest = target[f"{state}_sha256"]
            relative = target["relative_path"]
            current = self._read_target(
                relative,
                pinned=(
                    target_parents[relative]
                    if target_parents is not None else None
                ),
            )
            if (current is not None) != expected_exists or _sha256(current) != expected_digest:
                raise AuditConflictError(
                    f"audit target {target['relative_path']} failed {state} verification"
                )

    def _committed_events(self):
        """Return canonical committed events grouped by month.

        Transaction proofs are read only through pinned directory descriptors.
        Event identity is global across the corpus: a duplicate is corruption,
        never a projection overwrite.
        """
        committed = {}
        seen_event_ids = set()
        for (
            month_name,
            transaction_name,
            month_fd,
            transaction_fd,
            transaction_dir,
        ) in self._iter_transaction_directories():
            has_prepare = self._regular_entry_exists(
                transaction_fd, "prepare.json", label="audit prepare record"
            )
            has_commit = self._regular_entry_exists(
                transaction_fd, "commit.json", label="audit terminal marker"
            )
            if not has_prepare:
                if has_commit:
                    raise AuditConflictError(
                        "committed audit transaction has no prepare record"
                    )
                continue
            if not has_commit:
                continue
            try:
                prepare = json.loads(
                    self._read_transaction_file(
                        transaction_dir, "prepare.json"
                    ).decode("utf-8")
                )
            except (
                AuditConflictError, UnicodeDecodeError, json.JSONDecodeError
            ) as exc:
                raise AuditConflictError(
                    "canonical audit transaction is corrupt"
                ) from exc
            self._validate_prepare(
                prepare,
                transaction_dir,
                transaction_name,
                expected_month=month_name,
            )
            commit = self._read_terminal(
                transaction_dir / "commit.json",
                "committed",
                prepare.get("transaction_id"),
            )
            self._assert_transaction_directory_identity(
                month_name, transaction_name, month_fd, transaction_fd
            )
            for event in prepare["events"]:
                event_id = event["event_id"]
                if event_id in seen_event_ids:
                    raise AuditConflictError(
                        "audit event IDs are not globally unique"
                    )
                seen_event_ids.add(event_id)
                exported = dict(event)
                exported["committed_at"] = commit["committed_at"]
                event_month = _month(exported["occurred_at"])
                committed.setdefault(event_month, {})[event_id] = exported
        return committed

    def list_events(
        self,
        *,
        entity_type=None,
        entity_id=None,
        event_type=None,
        since=None,
        until=None,
        actor_type=None,
        surface_kind=None,
        operation=None,
        operation_id=None,
        limit=100,
    ):
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError("audit event limit must be an integer from 1 to 1000")
        for value, label in (
            (entity_type, "entity_type"),
            (entity_id, "entity_id"),
            (event_type, "event_type"),
            (actor_type, "actor_type"),
            (surface_kind, "surface_kind"),
            (operation, "operation"),
            (operation_id, "operation_id"),
        ):
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"audit {label} must be a non-empty string")
        if since is not None:
            if not isinstance(since, str):
                raise ValueError("audit since must be an RFC3339 string")
            parsed_since = datetime.fromisoformat(since.replace("Z", "+00:00"))
            if parsed_since.tzinfo is None:
                raise ValueError("audit since must be timezone-aware")
        else:
            parsed_since = None
        if until is not None:
            if not isinstance(until, str):
                raise ValueError("audit until must be an RFC3339 string")
            parsed_until = datetime.fromisoformat(until.replace("Z", "+00:00"))
            if parsed_until.tzinfo is None:
                raise ValueError("audit until must be timezone-aware")
        else:
            parsed_until = None

        with self.consistent_read():
            events = []
            canonical = self._committed_events()
            ordered = sorted(
                (
                    event
                    for month_events in canonical.values()
                    for event in month_events.values()
                ),
                key=lambda item: (
                    item["occurred_at"],
                    item["transaction_id"],
                    item["sequence"],
                ),
                reverse=True,
            )
            for event in ordered:
                    entity = event.get("entity")
                    if not isinstance(entity, dict):
                        raise AuditConflictError("audit event entity is corrupt")
                    if entity_type is not None and entity.get("type") != entity_type:
                        continue
                    if entity_id is not None and entity.get("id") != entity_id:
                        continue
                    if event_type is not None and event.get("event_type") != event_type:
                        continue
                    if actor_type is not None and event.get("actor", {}).get("type") != actor_type:
                        continue
                    if surface_kind is not None and event.get("surface", {}).get("kind") != surface_kind:
                        continue
                    if operation is not None and event.get("operation") != operation:
                        continue
                    if operation_id is not None and event.get("operation_id") != operation_id:
                        continue
                    if parsed_since is not None:
                        try:
                            occurred = datetime.fromisoformat(
                                event["occurred_at"].replace("Z", "+00:00")
                            )
                        except (KeyError, TypeError, ValueError) as exc:
                            raise AuditConflictError("audit event timestamp is corrupt") from exc
                        if occurred < parsed_since:
                            continue
                    if parsed_until is not None:
                        try:
                            occurred = datetime.fromisoformat(
                                event["occurred_at"].replace("Z", "+00:00")
                            )
                        except (KeyError, TypeError, ValueError) as exc:
                            raise AuditConflictError("audit event timestamp is corrupt") from exc
                        if occurred > parsed_until:
                            continue
                    events.append(event)
                    if len(events) >= limit:
                        return events
            return events

    def _export_events(self, events, committed_at):
        """Atomically rebuild the derived JSONL projection from canonical commits."""
        del events, committed_at
        committed = self._committed_events()
        try:
            projection_names = sorted(
                name for name in os.listdir(self._events_fd)
                if isinstance(name, str) and name.endswith(".jsonl")
            )
        except OSError as exc:
            raise AuditConflictError("audit JSONL projection is unreadable") from exc
        for name in projection_names:
            self._regular_entry_exists(
                self._events_fd, name, label="audit JSONL projection"
            )

        expected_names = set()
        for month, event_map in committed.items():
            name = f"{month}.jsonl"
            expected_names.add(name)
            ordered = sorted(
                event_map.values(),
                key=lambda item: (item["occurred_at"], item["transaction_id"], item["sequence"]),
            )
            payload = b"".join(
                json.dumps(
                    event, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                ).encode("utf-8") + b"\n"
                for event in ordered
            )
            current = (
                self._read_regular_entry(
                    self._events_fd, name, label="audit JSONL projection"
                )
                if name in projection_names else None
            )
            if current != payload:
                _atomic_write_bytes(self.events_dir / name, payload)
        for name in projection_names:
            if name not in expected_names:
                os.unlink(name, dir_fd=self._events_fd)
        os.fsync(self._events_fd)

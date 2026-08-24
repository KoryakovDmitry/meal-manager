"""File-backed transactional audit journal with deterministic recovery."""

import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from ..repositories.file_lock import JsonFileLock


_ALLOWED_ROOTS = {
    "dishes.json",
    "fridge.json",
    "history.json",
    "prep_items.json",
    "shopping_requests.json",
    "tuning.json",
}
_ALLOWED_DIRECTORIES = {"plans", "sessions"}
_TERMINAL_MARKERS = ("commit.json", "abort.json", "conflict.json")
_AUDIT_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)


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
    """Pin an absolute directory without following symlinks in any component."""
    path = Path(os.path.abspath(path))
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
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
            return
        if existing_lock is not None and existing_lock.active_path is not None:
            raise RuntimeError("cannot reconfigure active audit transaction manager")
        root_fd = None
        audit_fd = None
        data_fd = None
        child_fds = {}
        try:
            root_fd = _open_directory_chain(resolved)
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
            data_fd = os.dup(root_fd)
        except Exception:
            for descriptor in child_fds.values():
                os.close(descriptor)
            if audit_fd is not None:
                os.close(audit_fd)
            if data_fd is not None:
                os.close(data_fd)
            raise
        finally:
            if root_fd is not None:
                os.close(root_fd)
        self.data_dir = resolved
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
        self._audit_fd = audit_fd
        self._transactions_fd = child_fds["transactions"]
        self._events_fd = child_fds["events"]
        self.audit_dir = Path(f"/proc/self/fd/{audit_fd}")
        self.transactions_dir = Path(f"/proc/self/fd/{self._transactions_fd}")
        for descriptor in getattr(self, "_pending_transaction_fds", set()):
            try:
                os.close(descriptor)
            except OSError:
                pass
        self._pending_transaction_fds = set()
        self.events_dir = Path(f"/proc/self/fd/{self._events_fd}")
        self.lock = JsonFileLock(lambda: self.audit_dir / ".txn")

    def _open_or_create_directory(self, parent_fd, name):
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            pass
        return os.open(name, flags, dir_fd=parent_fd)

    def _new_transaction_directory(self, occurred_at, transaction_id):
        month = _month(occurred_at)
        month_fd = self._open_or_create_directory(self._transactions_fd, month)
        try:
            transaction_fd = self._open_or_create_directory(month_fd, transaction_id)
        finally:
            os.close(month_fd)
        self._pending_transaction_fds.add(transaction_fd)
        return transaction_fd, Path(f"/proc/self/fd/{transaction_fd}")

    def _close_pending_transaction_fds(self):
        for descriptor in tuple(self._pending_transaction_fds):
            try:
                os.close(descriptor)
            except OSError:
                pass
            self._pending_transaction_fds.discard(descriptor)

    def _iter_transaction_directories(self):
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        with os.scandir(self.transactions_dir) as month_scan:
            month_names = sorted(entry.name for entry in month_scan)
        for month_name in month_names:
            try:
                month_fd = os.open(month_name, flags, dir_fd=self._transactions_fd)
            except OSError as exc:
                raise AuditConflictError("audit month directory is unsafe") from exc
            try:
                month_path = Path(f"/proc/self/fd/{month_fd}")
                with os.scandir(month_path) as transaction_scan:
                    transaction_names = sorted(entry.name for entry in transaction_scan)
                for transaction_name in transaction_names:
                    try:
                        transaction_fd = os.open(
                            transaction_name, flags, dir_fd=month_fd
                        )
                    except OSError as exc:
                        raise AuditConflictError(
                            "audit transaction directory is unsafe"
                        ) from exc
                    try:
                        yield transaction_name, Path(f"/proc/self/fd/{transaction_fd}")
                    finally:
                        os.close(transaction_fd)
            finally:
                os.close(month_fd)

    def _fault(self, stage):
        if self._fault_injector is not None:
            self._fault_injector(stage)

    def _relative_target(self, raw_path):
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("audit target path must be a non-empty relative string")
        pure = PurePosixPath(raw_path)
        if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
            raise ValueError("audit target path is outside the data root")
        if len(pure.parts) == 1:
            if pure.name not in _ALLOWED_ROOTS:
                raise ValueError(f"unsupported audit target '{raw_path}'")
        elif pure.parts[0] not in _ALLOWED_DIRECTORIES or len(pure.parts) != 2:
            raise ValueError(f"unsupported audit target '{raw_path}'")
        if pure.suffix != ".json":
            raise ValueError("audit targets must be JSON files")
        return pure.as_posix()

    def _target_path(self, relative):
        logical = self.data_dir / relative
        cursor = self.data_dir
        for part in PurePosixPath(relative).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError("audit targets cannot be symbolic links")
        target = logical.resolve()
        try:
            target.relative_to(self.data_dir)
        except ValueError as exc:
            raise ValueError("audit target escaped the data root") from exc
        if target != logical.absolute():
            raise ValueError("audit target resolution changed its logical path")
        return target

    def _open_target_parent(self, relative):
        pure = PurePosixPath(relative)
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.dup(self._data_fd)
        try:
            for component in pure.parts[:-1]:
                try:
                    child = os.open(component, flags, dir_fd=descriptor)
                except FileNotFoundError:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                    child = os.open(component, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            return descriptor, pure.name
        except Exception:
            os.close(descriptor)
            raise

    def _read_target(self, relative):
        parent, name = self._open_target_parent(relative)
        try:
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return None
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise AuditConflictError("audit target must be a regular file")
            descriptor = os.open(
                name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent
            )
            with os.fdopen(descriptor, "rb") as handle:
                return handle.read()
        finally:
            os.close(parent)

    def _write_target(self, relative, payload):
        parent, name = self._open_target_parent(relative)
        temporary = ".audit-" + uuid.uuid4().hex + ".tmp"
        try:
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
            os.close(parent)

    def _delete_target(self, relative):
        parent, name = self._open_target_parent(relative)
        try:
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
            os.close(parent)

    def _validate_event(self, event):
        if not isinstance(event, dict):
            raise ValueError("audit events must be objects")
        if not isinstance(event.get("event_type"), str) or not event["event_type"]:
            raise ValueError("audit event_type is required")
        entity = event.get("entity")
        if (
            not isinstance(entity, dict)
            or not isinstance(entity.get("type"), str)
            or not entity.get("type")
            or not isinstance(entity.get("id"), str)
            or not entity.get("id")
        ):
            raise ValueError("audit event entity requires type and id")
        if ("payload" in event) == ("change" in event):
            raise ValueError("audit event requires exactly one of payload or change")

    def _validate_prepared_event(
        self, event, *, transaction_id, sequence, operation
    ):
        try:
            self._validate_event(event)
            _month(event.get("occurred_at"))
        except (KeyError, TypeError, ValueError) as exc:
            raise AuditConflictError("audit event metadata is corrupt") from exc
        actor = event.get("actor")
        surface = event.get("surface")
        event_id = event.get("event_id")
        correlation_id = event.get("correlation_id")
        causation_id = event.get("causation_id")
        if (
            event.get("schema_version") != 1
            or not isinstance(event_id, str)
            or re.fullmatch(r"evt_[0-9a-f]{32}", event_id) is None
            or event.get("transaction_id") != transaction_id
            or event.get("operation_id") != transaction_id
            or event.get("sequence") != sequence
            or isinstance(event.get("sequence"), bool)
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
        actor = context.get("actor") if isinstance(context, dict) else None
        surface = context.get("surface") if isinstance(context, dict) else None
        if not isinstance(actor, dict) or not isinstance(actor.get("type"), str):
            raise ValueError("audit context actor.type is required")
        if not isinstance(surface, dict) or not isinstance(surface.get("kind"), str):
            raise ValueError("audit context surface.kind is required")
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
            self._close_pending_transaction_fds()
            self._recover_unlocked()
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
            transaction_fd, transaction_dir = self._new_transaction_directory(
                occurred_at, transaction_id
            )
            os.chmod(transaction_dir, 0o700)
            targets_fd = self._open_or_create_directory(transaction_fd, "targets")
            os.close(targets_fd)

            manifest_targets = []
            for index, (relative, after) in enumerate(sorted(normalized_targets.items())):
                self._target_path(relative)
                before = self._read_target(relative)
                before_name = f"targets/{index:03d}.before"
                after_name = f"targets/{index:03d}.after"
                if before is not None:
                    _atomic_write_bytes(transaction_dir / before_name, before)
                if after is not None:
                    _atomic_write_bytes(transaction_dir / after_name, after)
                manifest_targets.append({
                    "relative_path": relative,
                    "before_exists": before is not None,
                    "before_sha256": _sha256(before),
                    "before_blob": before_name if before is not None else None,
                    "after_exists": after is not None,
                    "after_sha256": _sha256(after),
                    "after_blob": after_name if after is not None else None,
                })

            prepare = {
                "schema_version": 1,
                "transaction_id": transaction_id,
                "state": "prepared",
                "prepared_at": _utc_now(),
                "operation": operation,
                "context": context,
                "targets": manifest_targets,
                "events": events_prepared,
            }
            _atomic_write_bytes(transaction_dir / "prepare.json", _json_bytes(prepare))
            self._fault("after_prepare")

            for index, target in enumerate(manifest_targets):
                relative = target["relative_path"]
                if target["after_exists"]:
                    after = self._read_transaction_file(
                        transaction_dir, target["after_blob"]
                    )
                    if _sha256(after) != target["after_sha256"]:
                        raise AuditConflictError("audit after-image hash mismatch")
                    self._write_target(relative, after)
                else:
                    self._delete_target(relative)
                self._fault(f"after_target:{index}")
            self._verify_targets(manifest_targets, transaction_dir, state="after")
            self._fault("after_all_targets")

            committed_at = _utc_now()
            _exclusive_json(transaction_dir / "commit.json", {
                "schema_version": 1,
                "transaction_id": transaction_id,
                "state": "committed",
                "committed_at": committed_at,
            })
            self._fault("after_commit")
            self._export_events(events_prepared, committed_at)
            self._fault("after_export")
            self._pending_transaction_fds.discard(transaction_fd)
            os.close(transaction_fd)
            return {
                "status": "committed",
                "transaction_id": transaction_id,
                "transaction_dir": str(canonical_transaction_dir),
                "event_ids": [event["event_id"] for event in events_prepared],
            }

    def recover(self):
        with self.lock:
            self._close_pending_transaction_fds()
            return self._recover_unlocked()

    def resolve_last_transaction(self):
        """Recover and return a committed result when the last transaction won."""
        with self.lock:
            transaction_id = self.last_transaction_id
            self._close_pending_transaction_fds()
            self._recover_unlocked()
            if not transaction_id:
                return None
            commits = list(
                self.transactions_dir.glob(f"*/{transaction_id}/commit.json")
            )
            if len(commits) != 1:
                return None
            return {"status": "committed", "transaction_id": transaction_id}

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
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
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

    def _validate_prepare(self, prepare, transaction_dir, expected_transaction_id=None):
        if not isinstance(prepare, dict):
            raise AuditConflictError("audit prepare record must be an object")
        expected_transaction_id = expected_transaction_id or transaction_dir.name
        expected_prepare_fields = {
            "schema_version", "transaction_id", "state", "prepared_at",
            "operation", "context", "targets", "events",
        }
        try:
            _month(prepare.get("prepared_at"))
        except (TypeError, ValueError) as exc:
            raise AuditConflictError("audit prepare timestamp is corrupt") from exc
        operation = prepare.get("operation")
        context = prepare.get("context")
        if (
            set(prepare) != expected_prepare_fields
            or prepare.get("schema_version") != 1
            or prepare.get("transaction_id") != expected_transaction_id
            or prepare.get("state") != "prepared"
            or not isinstance(operation, str)
            or not operation
            or not isinstance(context, dict)
        ):
            raise AuditConflictError("audit prepare metadata is corrupt")
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
        for index, target in enumerate(targets):
            if not isinstance(target, dict):
                raise AuditConflictError("audit target manifest is corrupt")
            try:
                relative = self._relative_target(target["relative_path"])
            except (KeyError, TypeError, ValueError) as exc:
                raise AuditConflictError("audit target path is corrupt") from exc
            if relative != target["relative_path"]:
                raise AuditConflictError("audit target path is not canonical")
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
        return targets

    def _read_terminal(self, path, expected_state, transaction_id):
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
            "conflict": {"target_states"},
        }[expected_state]
        if (
            not isinstance(record, dict)
            or record.get("schema_version") != 1
            or record.get("transaction_id") != transaction_id
            or record.get("state") != expected_state
            or not common_fields.issubset(record)
            or not set(record).issubset(common_fields | optional_fields)
            or any(
                key in record and not isinstance(record[key], bool)
                for key in ("recovered", "rolled_back_mixed_state")
            )
            or (
                "target_states" in record
                and not isinstance(record["target_states"], list)
            )
        ):
            raise AuditConflictError("audit terminal marker does not match transaction")
        return record

    def _recover_unlocked(self):
        recovered = []
        projection_needed = False
        root = self.transactions_dir
        if not root.exists():
            return recovered
        for transaction_name, transaction_dir in self._iter_transaction_directories():
            prepare_path = transaction_dir / "prepare.json"
            if not prepare_path.exists():
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
                prepare, transaction_dir, transaction_name
            )
            transaction_id = prepare["transaction_id"]
            terminals = [
                transaction_dir / marker
                for marker in _TERMINAL_MARKERS
                if (transaction_dir / marker).exists()
            ]
            if len(terminals) > 1:
                raise AuditConflictError("audit transaction has contradictory terminal markers")
            if terminals:
                marker = terminals[0]
                expected_state = {
                    "commit.json": "committed",
                    "abort.json": "aborted",
                    "conflict.json": "conflict",
                }[marker.name]
                self._read_terminal(marker, expected_state, transaction_id)
                if marker.name == "commit.json":
                    projection_needed = True
                continue
            states = [self._current_target_state(target) for target in targets]
            if all(state == "after" for state in states):
                committed_at = _utc_now()
                _exclusive_json(transaction_dir / "commit.json", {
                    "schema_version": 1,
                    "transaction_id": transaction_id,
                    "state": "committed",
                    "committed_at": committed_at,
                    "recovered": True,
                })
                projection_needed = True
                recovered.append((transaction_id, "committed"))
                continue
            if all(state == "before" for state in states):
                _exclusive_json(transaction_dir / "abort.json", {
                    "schema_version": 1,
                    "transaction_id": transaction_id,
                    "state": "aborted",
                    "aborted_at": _utc_now(),
                    "recovered": True,
                })
                recovered.append((transaction_id, "aborted"))
                continue
            if all(state in {"before", "after"} for state in states):
                self._restore_before(targets, transaction_dir)
                _exclusive_json(transaction_dir / "abort.json", {
                    "schema_version": 1,
                    "transaction_id": transaction_id,
                    "state": "aborted",
                    "aborted_at": _utc_now(),
                    "recovered": True,
                    "rolled_back_mixed_state": True,
                })
                recovered.append((transaction_id, "rolled_back"))
                continue
            _exclusive_json(transaction_dir / "conflict.json", {
                "schema_version": 1,
                "transaction_id": transaction_id,
                "state": "conflict",
                "detected_at": _utc_now(),
                "target_states": states,
            })
            raise AuditConflictError(
                f"audit transaction {transaction_id} has unknown target state"
            )
        if projection_needed:
            self._export_events([], None)
        return recovered

    def _current_target_state(self, target):
        current = self._read_target(target["relative_path"])
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

    def _restore_before(self, targets, transaction_dir):
        for target in targets:
            relative = target["relative_path"]
            if target["before_exists"]:
                before = self._read_transaction_file(
                    transaction_dir, target["before_blob"]
                )
                if _sha256(before) != target["before_sha256"]:
                    raise AuditConflictError("audit before-image hash mismatch")
                self._write_target(relative, before)
            else:
                self._delete_target(relative)
        self._verify_targets(targets, transaction_dir, state="before")

    def _verify_targets(self, targets, transaction_dir, *, state):
        for target in targets:
            expected_exists = target[f"{state}_exists"]
            expected_digest = target[f"{state}_sha256"]
            current = self._read_target(target["relative_path"])
            if (current is not None) != expected_exists or _sha256(current) != expected_digest:
                raise AuditConflictError(
                    f"audit target {target['relative_path']} failed {state} verification"
                )

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

        with self.lock:
            self._recover_unlocked()
            events = []
            events_dir = self.events_dir
            if not events_dir.exists():
                return []
            for path in sorted(events_dir.glob("*.jsonl"), reverse=True):
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except (OSError, UnicodeDecodeError) as exc:
                    raise AuditConflictError("audit JSONL projection is unreadable") from exc
                for line in reversed(lines):
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise AuditConflictError("audit JSONL projection is corrupt") from exc
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
        committed = {}
        root = self.transactions_dir
        if root.exists():
            for transaction_name, transaction_dir in self._iter_transaction_directories():
                prepare_path = transaction_dir / "prepare.json"
                commit_path = transaction_dir / "commit.json"
                if not prepare_path.exists() or not commit_path.exists():
                    continue
                try:
                    prepare = json.loads(
                        self._read_transaction_file(
                            transaction_dir, "prepare.json"
                        ).decode("utf-8")
                    )
                except (AuditConflictError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise AuditConflictError("canonical audit transaction is corrupt") from exc
                self._validate_prepare(
                    prepare, transaction_dir, transaction_name
                )
                commit = self._read_terminal(
                    commit_path, "committed", prepare.get("transaction_id")
                )
                for event in prepare["events"]:
                    self._validate_event(event)
                    exported = dict(event)
                    exported["committed_at"] = commit["committed_at"]
                    month = _month(exported["occurred_at"])
                    committed.setdefault(month, {})[exported["event_id"]] = exported

        events_dir = self.events_dir
        events_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.fsync(self._audit_fd)
        expected_paths = set()
        for month, event_map in committed.items():
            path = events_dir / f"{month}.jsonl"
            expected_paths.add(path)
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
            _atomic_write_bytes(path, payload)
        for path in events_dir.glob("*.jsonl"):
            if path not in expected_paths:
                path.unlink()
                _fsync_directory(events_dir)

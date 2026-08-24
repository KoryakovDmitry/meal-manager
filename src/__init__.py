"""meal_manager.src -- domain modules package."""

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, cast


def _open_directory_no_follow(path: Path):
    """Open and pin an absolute directory chain without following symlinks."""
    absolute = Path(os.path.abspath(path))
    flags = (
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(absolute.anchor, flags)
    try:
        for component in absolute.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_regular_bytes_no_follow(path: Path, *, missing):
    """Read one stable regular-file inode, rejecting links and special files."""
    path = Path(os.path.abspath(path))
    try:
        parent_fd = _open_directory_no_follow(path.parent)
    except FileNotFoundError:
        return missing
    descriptor = None
    try:
        try:
            entry = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return missing
        if not stat.S_ISREG(entry.st_mode):
            raise ValueError("JSON repository target must be a regular file")
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (entry.st_dev, entry.st_ino)
        ):
            raise ValueError("JSON repository target identity changed")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            return handle.read()
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_fd)


def _audited_commit(manager, **kwargs):
    try:
        return manager.commit(**kwargs)
    except Exception:
        resolved = manager.resolve_last_transaction()
        if resolved is None:
            raise
        return resolved


def read_json_file(path: Path, *, missing=None):
    """Read JSON through the pinned audit root when a coherent scope is active."""
    from .audit.context import current_audit_manager

    path = Path(path)
    manager = cast(Any, current_audit_manager())
    if manager is not None:
        logical = path.absolute()
        try:
            relative = logical.relative_to(manager.data_dir).as_posix()
        except ValueError as exc:
            raise ValueError("audited read escaped the configured data root") from exc
        relative = manager._relative_target(relative)
        payload = manager._read_target(relative)
        if payload is None:
            return missing
        return json.loads(payload.decode("utf-8"))
    payload = _read_regular_bytes_no_follow(path, missing=missing)
    if payload is missing:
        return missing
    return json.loads(cast(bytes, payload).decode("utf-8"))


def list_json_files(directory: Path):
    """List canonical JSON files through a pinned domain descriptor when active."""
    from .audit.context import current_audit_manager

    directory = Path(directory)
    manager = cast(Any, current_audit_manager())
    if manager is not None:
        logical = directory.absolute()
        try:
            relative = logical.relative_to(manager.data_dir).as_posix()
        except ValueError as exc:
            raise ValueError("audited directory read escaped the data root") from exc
        if "/" in relative or not relative:
            raise ValueError("audited JSON listing requires one domain directory")
        return [directory / name for name in manager.list_json_targets(relative)]
    try:
        descriptor = _open_directory_no_follow(directory)
    except FileNotFoundError:
        return []
    try:
        names = sorted(
            name for name in os.listdir(descriptor)
            if isinstance(name, str) and name.endswith(".json")
        )
        for name in names:
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("JSON repository entry must be a regular file")
        return [directory / name for name in names]
    finally:
        os.close(descriptor)


def atomic_delete_json(path: Path, *, fsync_dir: bool = True) -> bool:
    """Delete one JSON document, journaling it inside an active audit scope."""
    from .audit.context import current_audit_context

    path = Path(path)
    audit_context = current_audit_context()
    if audit_context is not None:
        manager = audit_context["manager"]
        logical = path.absolute()
        try:
            relative = logical.relative_to(manager.data_dir).as_posix()
        except ValueError as exc:
            raise ValueError("audited delete escaped the configured data root") from exc
        before = manager._read_target(relative)
        if before is None:
            return False
        _audited_commit(
            manager,
            operation=audit_context["operation"],
            targets={relative: None},
            events=[{
                "event_type": "storage.document_deleted.v1",
                "entity": {"type": "domain_document", "id": relative},
                "payload": {
                    "document": relative,
                    "before_sha256": hashlib.sha256(before).hexdigest(),
                },
            }],
            context={
                "actor": audit_context["actor"],
                "surface": audit_context["surface"],
                "correlation_id": audit_context["correlation_id"],
            },
        )
        return True

    if not path.exists():
        return False
    path.unlink()
    if fsync_dir:
        try:
            directory = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            pass
    return True


def atomic_write_json(path: Path, data, *, indent: int | None = 2,
                      fsync_dir: bool = True) -> None:
    """Write JSON atomically via temp file + os.replace.

    ``fsync_dir`` also fsyncs the parent directory so the rename is crash-durable
    for the canonical data files. Callers writing ephemeral, reconstructable
    files (e.g. DII session backups, which are held under a lock during the
    write) may pass ``fsync_dir=False`` to keep the critical section short.
    """
    from .audit.context import current_audit_context

    path = Path(path)
    audit_context = current_audit_context()
    if audit_context is not None:
        serialized = json.dumps(
            data, ensure_ascii=False, indent=indent
        ).encode("utf-8")
        manager = audit_context["manager"]
        logical = path.absolute()
        try:
            relative = logical.relative_to(manager.data_dir).as_posix()
        except ValueError as exc:
            raise ValueError("audited write escaped the configured data root") from exc
        before = manager._read_target(relative)
        if before == serialized:
            return
        _audited_commit(
            manager,
            operation=audit_context["operation"],
            targets={relative: serialized},
            events=[{
                "event_type": "storage.document_replaced.v1",
                "entity": {"type": "domain_document", "id": relative},
                "payload": {
                    "document": relative,
                    "after_sha256": hashlib.sha256(serialized).hexdigest(),
                    "before_sha256": (
                        hashlib.sha256(before).hexdigest() if before is not None else None
                    ),
                },
            }],
            context={
                "actor": audit_context["actor"],
                "surface": audit_context["surface"],
                "correlation_id": audit_context["correlation_id"],
            },
        )
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
        # Also fsync the parent directory so the rename itself is durable:
        # on many filesystems the directory entry is not persisted until the
        # directory is synced, so a crash right after os.replace could
        # otherwise revert to the pre-write file. Best-effort — some platforms
        # (notably Windows) do not support directory fsync.
        if fsync_dir:
            try:
                dir_fd = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

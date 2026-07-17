"""meal_manager.src -- domain modules package."""

import hashlib
import json
import os
import tempfile
from pathlib import Path


def _audited_commit(manager, **kwargs):
    try:
        return manager.commit(**kwargs)
    except Exception:
        resolved = manager.resolve_last_transaction()
        if resolved is None:
            raise
        return resolved


def atomic_delete_json(path: Path, *, fsync_dir: bool = True) -> bool:
    """Delete one JSON document, journaling it inside an active audit scope."""
    from .audit.context import current_audit_context

    path = Path(path)
    if not path.exists():
        return False
    audit_context = current_audit_context()
    if audit_context is not None:
        manager = audit_context["manager"]
        logical = path.absolute()
        try:
            relative = logical.relative_to(manager.data_dir).as_posix()
        except ValueError as exc:
            raise ValueError("audited delete escaped the configured data root") from exc
        target = manager._target_path(relative)
        before = target.read_bytes()
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
        target = manager._target_path(relative)
        before = target.read_bytes() if target.exists() else None
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

"""Crash-safe audit transaction composition root."""

from pathlib import Path

from .transaction import AuditConflictError, AuditTransactionManager

_DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
audit_manager = AuditTransactionManager(_DEFAULT_DATA_DIR)


def configure(data_dir):
    """Redirect the shared audit manager to an injectable data root."""
    audit_manager.configure(data_dir)


__all__ = [
    "AuditConflictError",
    "AuditTransactionManager",
    "audit_manager",
    "configure",
]

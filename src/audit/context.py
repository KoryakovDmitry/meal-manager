"""Cross-surface audit context for repository writes."""

from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

_ACTIVE_AUDIT: ContextVar[dict | None] = ContextVar("meal_manager_active_audit", default=None)


def current_audit_context():
    return _ACTIVE_AUDIT.get()


@contextmanager
def audit_scope(
    *,
    operation,
    manager,
    actor_type,
    surface_kind,
    correlation_id=None,
):
    """Hold the global journal lock and annotate writes in one command."""
    current = _ACTIVE_AUDIT.get()
    if current is not None:
        yield current
        return
    context = {
        "operation": operation,
        "manager": manager,
        "actor": {"type": actor_type},
        "surface": {"kind": surface_kind},
        "correlation_id": correlation_id or f"op_{uuid4().hex}",
    }
    with manager.lock:
        manager.recover()
        token = _ACTIVE_AUDIT.set(context)
        try:
            yield context
        finally:
            _ACTIVE_AUDIT.reset(token)

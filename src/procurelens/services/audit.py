"""Audit trail helper. Callers pass the active session so the audit row commits atomically
with the change it describes."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from procurelens.adapters.persistence.models import AuditEvent


def record_audit(
    session: Session,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str | int | None = None,
    dataset: str | None = None,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> None:
    session.add(
        AuditEvent(
            actor_name=actor,
            action=action,
            entity_type=entity_type,
            entity_id=None if entity_id is None else str(entity_id),
            dataset=dataset,
            reason=reason,
            details=details or {},
            run_id=run_id,
        )
    )

"""Exception detection persistence, querying and the human review lifecycle."""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from procurelens.adapters.erpnext.demo import DEMO_AS_OF
from procurelens.adapters.persistence.db import Database
from procurelens.adapters.persistence.models import (
    ExceptionEvent,
    ProcurementException,
    SourceDocument,
)
from procurelens.adapters.persistence.repositories import SourceRepository
from procurelens.domain import lifecycle
from procurelens.domain.models import ExceptionStatus, Finding, ReconciliationPolicy
from procurelens.domain.priority import score_finding
from procurelens.domain.reconciliation import fingerprint, reconcile
from procurelens.security.permissions import Permission, Principal

from .app_config import load_business_settings
from .audit import record_audit

log = logging.getLogger(__name__)
SYSTEM = "system"


def evaluation_date(dataset: str) -> date:
    return DEMO_AS_OF if dataset == "demo" else datetime.now(UTC).date()


def policy_for(session: Session, dataset: str, base_currency: str) -> ReconciliationPolicy:
    bs = load_business_settings(session)
    return ReconciliationPolicy(
        base_currency=base_currency,
        price_tolerance_pct=bs.price_tolerance_pct,
        overdue_grace_days=bs.overdue_grace_days,
        as_of=evaluation_date(dataset),
        critical_items=bs.critical_items,
    )


def _snapshot(exc: ProcurementException, extra: dict | None = None) -> dict:
    return {
        "facts": exc.facts,
        "calculation": exc.calculation,
        "related": exc.related,
        "exposure": str(exc.exposure),
        "priority_score": exc.priority_score,
        "fingerprint": exc.fingerprint,
        "captured_at": datetime.now(UTC).isoformat(),
        **(extra or {}),
    }


def refresh_exceptions(
    session: Session, dataset: str, findings: list[Finding], run_id: str | None
) -> dict[str, int]:
    """Upsert findings as exceptions. Runs inside the caller's (sync) transaction."""
    now = datetime.now(UTC)
    existing = {
        e.exception_key: e
        for e in session.scalars(
            select(ProcurementException).where(ProcurementException.dataset == dataset)
        )
    }
    counts = {"new": 0, "changed": 0, "unchanged": 0, "cleared": 0, "reopened": 0}
    seen: set[str] = set()
    for f in findings:
        seen.add(f.key)
        score = score_finding(f)
        fp = fingerprint({**f.facts, "_related": ",".join(r.label() for r in f.related)})
        values = dict(
            exception_type=f.exception_type.value,
            supplier=f.supplier,
            po_id=f.po_id,
            po_line_id=f.po_line_id,
            item_code=f.item_code,
            title=f.title[:400],
            facts=f.facts,
            calculation=list(f.calculation),
            related=[asdict(r) for r in f.related],
            notes=list(f.notes),
            fingerprint=fp,
            exposure=f.exposure,
            days_overdue=f.days_overdue,
            critical_item=f.critical_item,
            priority_score=score.score,
            priority_reasons=list(score.reasons),
        )
        exc = existing.get(f.key)
        if exc is None:
            exc = ProcurementException(
                dataset=dataset,
                exception_key=f.key,
                status="open",
                first_detected_at=now,
                last_detected_at=now,
                **values,
            )
            session.add(exc)
            session.flush()
            session.add(
                ExceptionEvent(
                    exception_id=exc.id,
                    event_type="detected",
                    to_status="open",
                    actor_name=SYSTEM,
                    reason=f"Detected by sync run {run_id}",
                    evidence_snapshot=_snapshot(exc),
                )
            )
            counts["new"] += 1
            continue
        exc.last_detected_at = now
        if exc.fingerprint == fp:
            if not exc.condition_active:
                exc.condition_active = True
                session.add(
                    ExceptionEvent(
                        exception_id=exc.id,
                        event_type="condition_returned",
                        actor_name=SYSTEM,
                        reason="Discrepancy detected again",
                        evidence_snapshot=_snapshot(exc),
                    )
                )
            # Priority may change daily (overdue days) without a material fact change.
            exc.priority_score, exc.priority_reasons = score.score, list(score.reasons)
            counts["unchanged"] += 1
            continue
        before = _snapshot(exc)
        for k, v in values.items():
            setattr(exc, k, v)
        exc.source_changed = True
        exc.condition_active = True
        exc.updated_at = now
        session.add(
            ExceptionEvent(
                exception_id=exc.id,
                event_type="facts_changed",
                actor_name=SYSTEM,
                reason="Source data changed materially",
                evidence_snapshot={"before": before, "after": _snapshot(exc)},
            )
        )
        counts["changed"] += 1
        if exc.status == ExceptionStatus.RESOLVED.value:
            exc.status = ExceptionStatus.OPEN.value
            session.add(
                ExceptionEvent(
                    exception_id=exc.id,
                    event_type="reopen",
                    from_status="resolved",
                    to_status="open",
                    actor_name=SYSTEM,
                    reason="Automatically reopened: source data changed after resolution.",
                    evidence_snapshot=_snapshot(exc),
                )
            )
            counts["reopened"] += 1
    for key, exc in existing.items():
        if key not in seen and exc.condition_active:
            exc.condition_active = False
            exc.updated_at = now
            session.add(
                ExceptionEvent(
                    exception_id=exc.id,
                    event_type="condition_cleared",
                    actor_name=SYSTEM,
                    reason="Discrepancy no longer detected in source data; a reviewer decides "
                    "whether to resolve.",
                    evidence_snapshot=_snapshot(exc),
                )
            )
            counts["cleared"] += 1
    return counts


def reevaluate(
    session: Session, dataset: str, base_currency: str, run_id: str | None
) -> dict[str, int]:
    repo = SourceRepository(session)
    orders, receipts, invoices = repo.load_domain(dataset)
    findings = reconcile(orders, receipts, invoices, policy_for(session, dataset, base_currency))
    return refresh_exceptions(session, dataset, findings, run_id)


class ExceptionService:
    def __init__(self, db: Database, base_currency: str) -> None:
        self.db = db
        self.base_currency = base_currency

    # -- queries ----------------------------------------------------------------------------
    def list(
        self,
        principal: Principal,
        dataset: str,
        *,
        statuses: list[str] | None = None,
        types: list[str] | None = None,
        supplier: str | None = None,
        active_only: bool = False,
    ) -> list[ProcurementException]:
        principal.require(Permission.VIEW)
        principal.require_dataset(dataset)
        q = select(ProcurementException).where(ProcurementException.dataset == dataset)
        if statuses:
            q = q.where(ProcurementException.status.in_(statuses))
        if types:
            q = q.where(ProcurementException.exception_type.in_(types))
        if supplier:
            q = q.where(ProcurementException.supplier == supplier)
        if active_only:
            q = q.where(ProcurementException.condition_active.is_(True))
        q = q.order_by(
            ProcurementException.priority_score.desc(),
            ProcurementException.exposure.desc(),
            ProcurementException.id,
        )
        with self.db.read() as s:
            return list(s.scalars(q))

    def get(self, principal: Principal, exception_id: int) -> ProcurementException:
        principal.require(Permission.VIEW)
        with self.db.read() as s:
            exc = s.scalar(
                select(ProcurementException)
                .where(ProcurementException.id == exception_id)
                .options(selectinload(ProcurementException.events))
            )
            if exc is None:
                raise LookupError("Exception not found.")
            principal.require_dataset(exc.dataset)  # no cross-dataset access by id
            return exc

    def source_documents(
        self, principal: Principal, exc: ProcurementException
    ) -> list[SourceDocument]:
        principal.require_dataset(exc.dataset)
        refs = sorted({(r["doctype"], r["source_id"]) for r in exc.related})
        with self.db.read() as s:
            return SourceRepository(s).get_documents(exc.dataset, refs)

    def supplier_names(self, principal: Principal, dataset: str) -> dict[str, str]:
        principal.require(Permission.VIEW)
        principal.require_dataset(dataset)
        with self.db.read() as s:
            return SourceRepository(s).supplier_names(dataset)

    def overview(self, principal: Principal, dataset: str) -> dict:
        principal.require(Permission.VIEW)
        principal.require_dataset(dataset)
        E = ProcurementException
        with self.db.read() as s:
            rows = s.execute(
                select(
                    E.status, E.exception_type, func.count(), func.coalesce(func.sum(E.exposure), 0)
                )
                .where(E.dataset == dataset)
                .group_by(E.status, E.exception_type)
            ).all()
            overdue_open = (
                s.scalar(
                    select(func.count()).where(
                        E.dataset == dataset,
                        E.exception_type == "overdue_delivery",
                        E.status != "resolved",
                        E.condition_active.is_(True),
                    )
                )
                or 0
            )
            changed = (
                s.scalar(
                    select(func.count()).where(
                        E.dataset == dataset, E.source_changed.is_(True), E.status != "resolved"
                    )
                )
                or 0
            )
        by_status: dict[str, int] = {}
        by_type: dict[str, int] = {}
        open_exposure = Decimal(0)
        for status, etype, n, exp in rows:
            by_status[status] = by_status.get(status, 0) + n
            if status != "resolved":
                by_type[etype] = by_type.get(etype, 0) + n
                open_exposure += Decimal(exp)
        return {
            "by_status": by_status,
            "open_by_type": by_type,
            "open_exposure": open_exposure,
            "overdue_open": overdue_open,
            "source_changed_open": changed,
        }

    # -- lifecycle --------------------------------------------------------------------------
    def transition(
        self,
        principal: Principal,
        exception_id: int,
        action: lifecycle.Action,
        reason: str | None,
        *,
        evidence: dict | None = None,
        expected_version: int | None = None,
    ) -> ProcurementException:
        """Apply a reviewer action; decision event and audit row commit together."""
        principal.require(Permission.INVESTIGATE)
        with self.db.transaction() as s:
            exc = s.scalar(
                select(ProcurementException)
                .where(ProcurementException.id == exception_id)
                .with_for_update()
            )
            if exc is None:
                raise LookupError("Exception not found.")
            principal.require_dataset(exc.dataset)
            if expected_version is not None and exc.version != expected_version:
                raise lifecycle.LifecycleError(
                    "This exception changed since you opened it. Reload and review again."
                )
            current = ExceptionStatus(exc.status)
            target = lifecycle.apply(action, current, reason)
            text = (reason or "").strip() or None
            exc.status = target.value
            exc.updated_at = datetime.now(UTC)
            if action == lifecycle.Action.RESOLVE:
                exc.source_changed = False  # reviewer has seen the current facts
            s.add(
                ExceptionEvent(
                    exception_id=exc.id,
                    event_type=action.value,
                    from_status=current.value,
                    to_status=target.value,
                    actor_name=principal.username,
                    reason=text,
                    evidence_snapshot=_snapshot(exc, {"evidence": evidence or {}}),
                )
            )
            record_audit(
                s,
                actor=principal.username,
                action=f"exception_{action.value}",
                entity_type="exception",
                entity_id=exc.id,
                dataset=exc.dataset,
                reason=text,
                details={
                    "from": current.value,
                    "to": target.value,
                    "exception_key": exc.exception_key,
                },
            )
            s.flush()
            log.info(
                "exception transition",
                extra={
                    "event": "exception_transition",
                    "exception_id": exc.id,
                    "action": action.value,
                },
            )
            return exc

    def reevaluate_all(self, principal: Principal, reason: str) -> dict[str, dict[str, int]]:
        principal.require(Permission.CONFIGURE)
        out = {}
        with self.db.transaction() as s:
            for ds in principal.datasets:
                out[ds] = reevaluate(s, ds, self.base_currency, None)
            record_audit(
                s,
                actor=principal.username,
                action="exceptions_reevaluated",
                entity_type="exceptions",
                reason=reason,
                details=out,
            )
        return out

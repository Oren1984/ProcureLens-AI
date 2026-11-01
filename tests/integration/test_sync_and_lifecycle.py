import copy

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from procurelens.adapters.erpnext.base import FetchResult, SourceError
from procurelens.adapters.erpnext.demo import DemoSource
from procurelens.adapters.persistence.models import (
    AuditEvent,
    ExceptionEvent,
    ProcurementException,
    SourceDocument,
    SyncRun,
)
from procurelens.domain.lifecycle import Action, LifecycleError

pytestmark = pytest.mark.db


def count(c, model, *where):
    with c.db.read() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


def test_demo_sync_detects_expected_exceptions(container, admin):
    run = container.sync.run(admin, DemoSource())
    assert run.status == "succeeded"
    assert run.counts["document_created"] == 29
    assert run.counts["exceptions_new"] == 10
    rows = container.exceptions.list(admin, "demo")
    by_key = {e.exception_key: e for e in rows}
    q = by_key["qty:PO-2026-0001:PO-2026-0001-L1"]
    assert q.facts["unmatched_qty"] == "20" and str(q.exposure) == "250.00"
    assert rows[0].priority_score >= rows[-1].priority_score  # prioritised


def test_repeated_sync_is_idempotent(container, admin):
    container.sync.run(admin, DemoSource())
    before = (
        count(container, SourceDocument),
        count(container, ProcurementException),
        count(container, ExceptionEvent),
    )
    run2 = container.sync.run(admin, DemoSource())
    assert run2.counts.get("document_unchanged") == 29
    assert run2.counts.get("exceptions_new", 0) == 0
    after = (
        count(container, SourceDocument),
        count(container, ProcurementException),
        count(container, ExceptionEvent),
    )
    assert before == after


def test_duplicate_source_rows_blocked_by_constraint(container, admin):
    container.sync.run(admin, DemoSource())
    with pytest.raises(IntegrityError), container.db.transaction() as s:
        s.add(
            SourceDocument(
                dataset="demo",
                doctype="Purchase Order",
                source_id="PO-2026-0001",
                supplier="X",
                docstatus=1,
                currency="EUR",
                raw_json={},
                content_hash="x",
            )
        )


def _changed_invoice(qty):
    src = DemoSource()
    inv = copy.deepcopy(
        next(r for r in src.fetch("Purchase Invoice").records if r["name"] == "PI-2026-0001")
    )
    inv["items"][0]["qty"] = qty
    inv["modified"] = "2026-10-20 10:00:00.000000"
    return DemoSource({"Purchase Invoice": [inv]})


def test_material_change_after_resolution_reopens_and_keeps_history(container, admin):
    container.sync.run(admin, DemoSource())
    exc = next(
        e
        for e in container.exceptions.list(admin, "demo")
        if e.exception_key == "qty:PO-2026-0001:PO-2026-0001-L1"
    )
    container.exceptions.transition(
        admin, exc.id, Action.RESOLVE, "Supplier promised credit note", evidence={"refs": ["x"]}
    )
    container.sync.run(admin, _changed_invoice(110))  # invoice now 110 → unmatched 30
    exc2 = container.exceptions.get(admin, exc.id)
    assert exc2.status == "open" and exc2.source_changed
    assert exc2.facts["unmatched_qty"] == "30"
    kinds = [e.event_type for e in exc2.events]
    assert kinds == ["detected", "resolve", "facts_changed", "reopen"]
    resolve_ev = exc2.events[1]
    assert resolve_ev.evidence_snapshot["facts"]["unmatched_qty"] == "20"  # decision evidence kept
    assert resolve_ev.evidence_snapshot["evidence"] == {"refs": ["x"]}
    assert exc2.events[2].evidence_snapshot["before"]["facts"]["unmatched_qty"] == "20"


def test_cleared_condition_is_flagged_not_auto_resolved(container, admin):
    container.sync.run(admin, DemoSource())
    container.sync.run(admin, _changed_invoice(80))  # now matches accepted qty
    exc = next(
        e
        for e in container.exceptions.list(admin, "demo")
        if e.exception_key == "qty:PO-2026-0001:PO-2026-0001-L1"
    )
    assert exc.status == "open" and not exc.condition_active


def test_source_failure_records_failed_run_and_keeps_data(container, admin):
    container.sync.run(admin, DemoSource())
    docs_before = count(container, SourceDocument)

    class Broken(DemoSource):
        def fetch(self, doctype):
            if doctype == "Purchase Invoice":
                raise SourceError("rate_limited", "ERPNext returned HTTP 429.")
            return super().fetch(doctype)

    run = container.sync.run(admin, Broken())
    assert run.status == "failed" and "rate_limited" in run.error
    assert count(container, SourceDocument) == docs_before
    fresh = container.sync.freshness(admin, "demo")
    assert fresh["failures_since_success"] == 1 and fresh["last_success"] is not None


def test_malformed_records_become_warnings(container, admin):
    class Partial(DemoSource):
        def fetch(self, doctype):
            r = super().fetch(doctype)
            if doctype == "Purchase Receipt":
                r = FetchResult(r.records + [{"name": "PR-BAD", "docstatus": 9}], warnings=[])
            return r

    run = container.sync.run(admin, Partial())
    assert run.status == "succeeded_with_warnings"
    assert any("PR-BAD" in w for w in run.warnings) and run.counts["skipped"] == 1


def test_lifecycle_records_event_and_audit_atomically(container, admin):
    container.sync.run(admin, DemoSource())
    exc = container.exceptions.list(admin, "demo")[0]
    container.exceptions.transition(admin, exc.id, Action.START_REVIEW, None)
    with pytest.raises(LifecycleError):
        container.exceptions.transition(admin, exc.id, Action.RESOLVE, "")
    audits_before = count(container, AuditEvent, AuditEvent.action.like("exception_%"))
    container.exceptions.transition(admin, exc.id, Action.RESOLVE, "Credit note CN-1 received")
    container.exceptions.transition(admin, exc.id, Action.REOPEN, "Credit note was reversed")
    assert count(container, AuditEvent, AuditEvent.action.like("exception_%")) == audits_before + 2
    with container.db.read() as s:
        a = s.scalar(select(AuditEvent).where(AuditEvent.action == "exception_reopen"))
    assert (a.actor_name, a.entity_id, a.reason) == (
        "admin",
        str(exc.id),
        "Credit note was reversed",
    )


def test_stale_version_is_rejected(container, admin):
    container.sync.run(admin, DemoSource())
    exc = container.exceptions.list(admin, "demo")[0]
    container.exceptions.transition(admin, exc.id, Action.START_REVIEW, None)
    with pytest.raises(LifecycleError, match="changed since"):
        container.exceptions.transition(
            admin, exc.id, Action.RESOLVE, "valid reason", expected_version=exc.version
        )


def test_sync_run_audited(container, admin):
    run = container.sync.run(admin, DemoSource())
    with container.db.read() as s:
        actions = set(s.scalars(select(AuditEvent.action).where(AuditEvent.run_id == run.id)))
    assert {"sync_started", "sync_completed"} <= actions
    assert count(container, SyncRun) == 1

"""User-triggered, bounded synchronisation from a procurement source.

Fetch everything first (no DB writes), then apply documents + reconciliation + run status in
ONE transaction, so readers never see receipts updated without the matching invoices.
A fetch failure leaves the previous data untouched and records a failed run.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select, text

from procurelens.adapters.erpnext.base import DOCTYPES, ProcurementSource, SourceError
from procurelens.adapters.erpnext.mapping import (
    MappingError,
    normalize_document,
    normalize_supplier,
)
from procurelens.adapters.persistence.db import Database
from procurelens.adapters.persistence.models import SyncRun
from procurelens.adapters.persistence.repositories import SourceRepository
from procurelens.security.permissions import Permission, Principal

from .audit import record_audit
from .exceptions import reevaluate

log = logging.getLogger(__name__)
Progress = Callable[[str, float], None]


class SyncService:
    def __init__(self, db: Database, base_currency: str) -> None:
        self.db = db
        self.base_currency = base_currency

    def run(
        self, principal: Principal, source: ProcurementSource, progress: Progress | None = None
    ) -> SyncRun:
        principal.require(Permission.SYNC)
        principal.require_dataset(source.dataset)
        progress = progress or (lambda _m, _p: None)
        run_id = str(uuid.uuid4())
        ctx = {"run_id": run_id, "dataset": source.dataset, "adapter": source.adapter_name}
        with self.db.transaction() as s:
            s.add(
                SyncRun(
                    id=run_id,
                    dataset=source.dataset,
                    adapter=source.adapter_name,
                    status="running",
                    triggered_by=principal.username,
                    counts={},
                    warnings=[],
                )
            )
            record_audit(
                s,
                actor=principal.username,
                action="sync_started",
                entity_type="sync_run",
                entity_id=run_id,
                dataset=source.dataset,
                run_id=run_id,
            )
        log.info("sync started", extra={"event": "sync_started", **ctx})

        fetched: dict[str, list[dict]] = {}
        warnings: list[str] = []
        try:
            for i, doctype in enumerate(DOCTYPES):
                progress(f"Fetching {doctype} records…", i / (len(DOCTYPES) + 1))
                result = source.fetch(doctype)
                fetched[doctype] = result.records
                warnings.extend(result.warnings)
        except SourceError as exc:
            return self._fail(principal, run_id, f"{exc.kind}: {exc}", ctx)
        except Exception as exc:  # unexpected adapter failure: never leave a 'running' run
            log.exception("sync fetch crashed", extra={"event": "sync_failed", **ctx})
            return self._fail(principal, run_id, f"unexpected: {type(exc).__name__}", ctx)

        progress("Applying records and re-evaluating exceptions…", 0.85)
        try:
            with self.db.transaction() as s:
                # Serialise syncs per dataset; released at commit/rollback.
                s.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                    {"k": f"procurelens-sync-{source.dataset}"},
                )
                repo = SourceRepository(s)
                counts: dict[str, int] = {}

                def bump(key: str) -> None:
                    counts[key] = counts.get(key, 0) + 1

                for raw in fetched.get("Supplier", []):
                    try:
                        sid, name = normalize_supplier(raw)
                    except MappingError as exc:
                        warnings.append(f"Supplier skipped: {exc}")
                        bump("skipped")
                        continue
                    bump(f"supplier_{repo.upsert_supplier(source.dataset, sid, name, raw, run_id)}")
                for doctype in DOCTYPES[1:]:
                    for raw in fetched.get(doctype, []):
                        try:
                            doc = normalize_document(doctype, raw)
                        except MappingError as exc:
                            warnings.append(f"{doctype} skipped: {exc}")
                            bump("skipped")
                            continue
                        bump(f"document_{repo.upsert_document(source.dataset, doc, run_id)}")
                s.flush()
                exc_counts = reevaluate(s, source.dataset, self.base_currency, run_id)
                counts.update({f"exceptions_{k}": v for k, v in exc_counts.items()})
                run = s.get(SyncRun, run_id)
                run.status = "succeeded_with_warnings" if warnings else "succeeded"
                run.finished_at = datetime.now(UTC)
                run.counts = counts
                run.warnings = warnings[:200]
                record_audit(
                    s,
                    actor=principal.username,
                    action="sync_completed",
                    entity_type="sync_run",
                    entity_id=run_id,
                    dataset=source.dataset,
                    run_id=run_id,
                    details={
                        "status": run.status,
                        "counts": counts,
                        "warning_count": len(warnings),
                    },
                )
        except Exception as exc:
            log.exception("sync apply failed", extra={"event": "sync_failed", **ctx})
            return self._fail(principal, run_id, f"apply failed: {type(exc).__name__}", ctx)
        progress("Done.", 1.0)
        log.info("sync finished", extra={"event": "sync_finished", "status": run.status, **ctx})
        return run

    def _fail(self, principal: Principal, run_id: str, error: str, ctx: dict) -> SyncRun:
        with self.db.transaction() as s:
            run = s.get(SyncRun, run_id)
            run.status, run.finished_at, run.error = "failed", datetime.now(UTC), error[:1000]
            record_audit(
                s,
                actor=principal.username,
                action="sync_failed",
                entity_type="sync_run",
                entity_id=run_id,
                dataset=run.dataset,
                run_id=run_id,
                details={"error": run.error},
            )
        log.warning("sync failed", extra={"event": "sync_failed", "error": error, **ctx})
        return run

    def history(self, principal: Principal, dataset: str, limit: int = 20) -> list[SyncRun]:
        principal.require(Permission.VIEW)
        principal.require_dataset(dataset)
        with self.db.read() as s:
            return list(
                s.scalars(
                    select(SyncRun)
                    .where(SyncRun.dataset == dataset)
                    .order_by(SyncRun.started_at.desc())
                    .limit(limit)
                )
            )

    def freshness(self, principal: Principal, dataset: str) -> dict:
        runs = self.history(principal, dataset, limit=50)
        last_ok = next((r for r in runs if r.status.startswith("succeeded")), None)
        recent_failures = 0
        for r in runs:
            if r.status.startswith("succeeded"):
                break
            if r.status == "failed":
                recent_failures += 1
        return {
            "last_success": last_ok,
            "last_run": runs[0] if runs else None,
            "failures_since_success": recent_failures,
        }

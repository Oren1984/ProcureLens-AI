"""Repositories: translate between ORM rows and domain objects. No business rules here."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from procurelens.adapters.erpnext.mapping import NormalizedDocument, content_hash
from procurelens.domain.models import (
    InvoiceLine,
    OrderLine,
    PurchaseInvoice,
    PurchaseOrder,
    PurchaseReceipt,
    ReceiptLine,
)

from .models import SourceDocument, SourceLine, Supplier


class SourceRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def upsert_supplier(
        self, dataset: str, source_id: str, name: str, raw: dict, run_id: str
    ) -> str:
        h = content_hash(raw)
        row = self.s.scalar(
            select(Supplier).where(Supplier.dataset == dataset, Supplier.source_id == source_id)
        )
        if row is None:
            self.s.add(
                Supplier(
                    dataset=dataset,
                    source_id=source_id,
                    name=name,
                    raw_json=raw,
                    content_hash=h,
                    last_sync_run_id=run_id,
                )
            )
            return "created"
        row.last_sync_run_id = run_id
        if row.content_hash == h:
            return "unchanged"
        row.name, row.raw_json, row.content_hash = name, raw, h
        row.updated_at = datetime.now(UTC)
        return "updated"

    def upsert_document(self, dataset: str, doc: NormalizedDocument, run_id: str) -> str:
        """Idempotent: unchanged content is a no-op; changed content replaces all lines."""
        row = self.s.scalar(
            select(SourceDocument)
            .where(
                SourceDocument.dataset == dataset,
                SourceDocument.doctype == doc.doctype,
                SourceDocument.source_id == doc.source_id,
            )
            .options(selectinload(SourceDocument.lines))
        )
        outcome = "created"
        if row is None:
            row = SourceDocument(dataset=dataset, doctype=doc.doctype, source_id=doc.source_id)
            self.s.add(row)
        else:
            row.last_sync_run_id = run_id
            if row.content_hash == doc.content_hash:
                return "unchanged"
            outcome = "updated"
            row.lines.clear()
            self.s.flush()
        row.supplier, row.docstatus, row.status = doc.supplier, doc.docstatus, doc.status
        row.currency, row.doc_date, row.is_return = doc.currency, doc.doc_date, doc.is_return
        row.source_modified, row.raw_json = doc.source_modified, doc.raw
        row.content_hash, row.last_sync_run_id = doc.content_hash, run_id
        row.updated_at = datetime.now(UTC)
        for ln in doc.lines:
            row.lines.append(
                SourceLine(
                    line_id=ln.line_id,
                    idx=ln.idx,
                    item_code=ln.item_code,
                    item_name=ln.item_name,
                    qty=ln.qty,
                    received_qty=ln.received_qty,
                    rejected_qty=ln.rejected_qty,
                    rate=ln.rate,
                    uom=ln.uom,
                    schedule_date=ln.schedule_date,
                    po_id=ln.po_id,
                    po_line_id=ln.po_line_id,
                )
            )
        return outcome

    def documents(self, dataset: str, doctype: str | None = None) -> list[SourceDocument]:
        q = (
            select(SourceDocument)
            .where(SourceDocument.dataset == dataset)
            .options(selectinload(SourceDocument.lines))
            .order_by(SourceDocument.source_id)
        )
        if doctype:
            q = q.where(SourceDocument.doctype == doctype)
        return list(self.s.scalars(q))

    def get_documents(self, dataset: str, refs: list[tuple[str, str]]) -> list[SourceDocument]:
        if not refs:
            return []
        ids = {r[1] for r in refs}
        rows = self.s.scalars(
            select(SourceDocument)
            .where(SourceDocument.dataset == dataset, SourceDocument.source_id.in_(ids))
            .options(selectinload(SourceDocument.lines))
        )
        wanted = set(refs)
        return [r for r in rows if (r.doctype, r.source_id) in wanted]

    def supplier_names(self, dataset: str) -> dict[str, str]:
        return dict(
            self.s.execute(
                select(Supplier.source_id, Supplier.name).where(Supplier.dataset == dataset)
            ).all()
        )

    def load_domain(self, dataset: str):
        orders, receipts, invoices = [], [], []
        for d in self.documents(dataset):
            if d.doctype == "Purchase Order":
                orders.append(
                    PurchaseOrder(
                        d.source_id,
                        d.supplier,
                        d.currency,
                        d.docstatus,
                        d.status,
                        d.doc_date,
                        tuple(
                            OrderLine(
                                ln.line_id,
                                ln.item_code,
                                ln.item_name,
                                ln.qty,
                                ln.rate,
                                ln.uom,
                                ln.schedule_date,
                            )
                            for ln in d.lines
                        ),
                    )
                )
            elif d.doctype == "Purchase Receipt":
                receipts.append(
                    PurchaseReceipt(
                        d.source_id,
                        d.supplier,
                        d.currency,
                        d.docstatus,
                        d.doc_date,
                        d.is_return,
                        tuple(
                            ReceiptLine(
                                ln.line_id,
                                ln.item_code,
                                ln.qty,
                                ln.received_qty,
                                ln.rejected_qty,
                                ln.uom,
                                ln.po_id,
                                ln.po_line_id,
                            )
                            for ln in d.lines
                        ),
                    )
                )
            else:
                invoices.append(
                    PurchaseInvoice(
                        d.source_id,
                        d.supplier,
                        d.currency,
                        d.docstatus,
                        d.doc_date,
                        d.is_return,
                        tuple(
                            InvoiceLine(
                                ln.line_id,
                                ln.item_code,
                                ln.qty,
                                ln.rate,
                                ln.uom,
                                ln.po_id,
                                ln.po_line_id,
                            )
                            for ln in d.lines
                        ),
                    )
                )
        return orders, receipts, invoices

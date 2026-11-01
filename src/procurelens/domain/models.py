"""Typed business entities used by the reconciliation rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

# ERPNext docstatus values.
DRAFT, SUBMITTED, CANCELLED = 0, 1, 2


class ExceptionType(StrEnum):
    QTY_EXCEEDS_RECEIPT = "qty_exceeds_receipt"
    PRICE_VARIANCE = "price_variance"
    OVERDUE_DELIVERY = "overdue_delivery"
    MANUAL_REVIEW = "manual_review"

    @property
    def label(self) -> str:
        return {
            "qty_exceeds_receipt": "Invoiced qty exceeds accepted receipt",
            "price_variance": "Invoice price outside tolerance",
            "overdue_delivery": "Overdue undelivered quantity",
            "manual_review": "Unsupported record — manual review",
        }[self.value]


class ExceptionStatus(StrEnum):
    OPEN = "open"
    IN_REVIEW = "in_review"
    RESOLVED = "resolved"

    @property
    def label(self) -> str:
        return {"open": "Open", "in_review": "In review", "resolved": "Resolved"}[self.value]


@dataclass(frozen=True)
class OrderLine:
    line_id: str
    item_code: str
    item_name: str
    qty: Decimal
    rate: Decimal
    uom: str
    schedule_date: date | None


@dataclass(frozen=True)
class PurchaseOrder:
    source_id: str
    supplier: str
    currency: str
    docstatus: int
    status: str
    transaction_date: date | None
    lines: tuple[OrderLine, ...]


@dataclass(frozen=True)
class ReceiptLine:
    line_id: str
    item_code: str
    accepted_qty: Decimal
    received_qty: Decimal
    rejected_qty: Decimal
    uom: str
    po_id: str | None
    po_line_id: str | None


@dataclass(frozen=True)
class PurchaseReceipt:
    source_id: str
    supplier: str
    currency: str
    docstatus: int
    posting_date: date | None
    is_return: bool
    lines: tuple[ReceiptLine, ...]


@dataclass(frozen=True)
class InvoiceLine:
    line_id: str
    item_code: str
    qty: Decimal
    rate: Decimal
    uom: str
    po_id: str | None
    po_line_id: str | None


@dataclass(frozen=True)
class PurchaseInvoice:
    source_id: str
    supplier: str
    currency: str
    docstatus: int
    posting_date: date | None
    is_return: bool
    lines: tuple[InvoiceLine, ...]


@dataclass(frozen=True)
class ReconciliationPolicy:
    base_currency: str
    price_tolerance_pct: Decimal
    overdue_grace_days: int
    as_of: date
    critical_items: frozenset[str] = frozenset()


@dataclass(frozen=True)
class DocRef:
    doctype: str
    source_id: str
    line_id: str | None = None

    def label(self) -> str:
        return f"{self.doctype} {self.source_id}" + (
            f" / line {self.line_id}" if self.line_id else ""
        )


@dataclass(frozen=True)
class Finding:
    """A discrepancy detected by deterministic rules. ``key`` is stable across syncs."""

    key: str
    exception_type: ExceptionType
    supplier: str
    po_id: str | None
    po_line_id: str | None
    item_code: str | None
    title: str
    facts: dict[str, str]
    calculation: tuple[str, ...]
    exposure: Decimal
    days_overdue: int
    critical_item: bool
    related: tuple[DocRef, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)

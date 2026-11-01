"""Deterministic three-way matching: purchase order line ↔ receipts ↔ invoices.

Matching uses only explicit ERP line references (``purchase_order`` + ``purchase_order_item``
on receipts, ``purchase_order`` + ``po_detail`` on invoices). No text similarity is used.

Scope of V1 (anything outside it becomes a MANUAL_REVIEW finding, never a silent calculation):
* single currency equal to the configured base currency;
* receipt/invoice UOM identical to the PO line UOM (no conversions);
* no returns / debit notes;
* every receipt/invoice line references an existing, matching PO line.

Draft (docstatus 0) and cancelled (docstatus 2) documents are excluded from all totals and
listed as excluded evidence.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from .models import (
    CANCELLED,
    SUBMITTED,
    DocRef,
    ExceptionType,
    Finding,
    OrderLine,
    PurchaseInvoice,
    PurchaseOrder,
    PurchaseReceipt,
    ReconciliationPolicy,
)
from .numbers import ZERO, exceeds_tolerance, money, pct, price_deviation_pct, qty

CLOSED_PO_STATUSES = {"Closed", "Completed"}


def fingerprint(facts: dict[str, str]) -> str:
    """Hash of the material facts; a change means the discrepancy changed materially."""
    payload = json.dumps(facts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class _LineTotals:
    accepted: Decimal = ZERO
    received: Decimal = ZERO
    rejected: Decimal = ZERO
    invoiced: Decimal = ZERO
    invoiced_amount: Decimal = ZERO
    receipt_refs: list[DocRef] = field(default_factory=list)
    invoice_refs: list[DocRef] = field(default_factory=list)
    invoice_lines: list[tuple[PurchaseInvoice, object]] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    blocked: bool = False


def _fmt(d: Decimal) -> str:
    """Money/percent values (exactly 2 dp after rounding) keep cents; quantities are trimmed."""
    if d.as_tuple().exponent == -2:
        return format(d, "f")
    return format(d.normalize(), "f")


def reconcile(
    orders: list[PurchaseOrder],
    receipts: list[PurchaseReceipt],
    invoices: list[PurchaseInvoice],
    policy: ReconciliationPolicy,
) -> list[Finding]:
    findings: list[Finding] = []
    po_by_id = {po.source_id: po for po in orders}
    line_index: dict[tuple[str, str], tuple[PurchaseOrder, OrderLine]] = {}
    totals: dict[tuple[str, str], _LineTotals] = defaultdict(_LineTotals)

    def manual(
        key: str,
        supplier: str,
        title: str,
        reason: str,
        refs: list[DocRef],
        po: PurchaseOrder | None = None,
        line: OrderLine | None = None,
    ) -> None:
        findings.append(
            Finding(
                key=f"manual:{key}",
                exception_type=ExceptionType.MANUAL_REVIEW,
                supplier=supplier,
                po_id=po.source_id if po else None,
                po_line_id=line.line_id if line else None,
                item_code=line.item_code if line else None,
                title=title,
                facts={"reason": reason},
                calculation=(
                    reason,
                    "Not calculated automatically: outside the V1 supported scope.",
                ),
                exposure=ZERO,
                days_overdue=0,
                critical_item=bool(line and line.item_code in policy.critical_items),
                related=tuple(refs),
            )
        )

    # 1. Purchase orders: only submitted, base-currency orders with valid lines are matched.
    for po in orders:
        if po.docstatus != SUBMITTED:
            continue
        if po.currency.upper() != policy.base_currency:
            manual(
                f"PO:{po.source_id}:currency",
                po.supplier,
                f"Purchase order {po.source_id} in unsupported currency {po.currency}",
                f"Currency {po.currency} differs from base currency {policy.base_currency}.",
                [DocRef("Purchase Order", po.source_id)],
                po,
            )
            continue
        for line in po.lines:
            if line.qty <= ZERO or line.rate <= ZERO:
                manual(
                    f"POL:{po.source_id}:{line.line_id}:values",
                    po.supplier,
                    f"Inconsistent quantity or rate on {po.source_id}",
                    f"Ordered qty {_fmt(line.qty)} / rate {_fmt(line.rate)} must be positive.",
                    [DocRef("Purchase Order", po.source_id, line.line_id)],
                    po,
                    line,
                )
                continue
            line_index[(po.source_id, line.line_id)] = (po, line)

    def resolve_line(doctype: str, doc_id: str, ln, supplier: str):
        """Return the PO line a receipt/invoice line refers to, or flag it."""
        ref = DocRef(doctype, doc_id, ln.line_id)
        if not ln.po_id or not ln.po_line_id:
            manual(
                f"{doctype}:{doc_id}:{ln.line_id}:unlinked",
                supplier,
                f"{doctype} {doc_id} line without purchase-order reference",
                "Line has no explicit purchase order line reference; it cannot be matched.",
                [ref],
            )
            return None
        target_po = po_by_id.get(ln.po_id)
        if target_po is not None and target_po.docstatus == CANCELLED:
            manual(
                f"{doctype}:{doc_id}:{ln.line_id}:cancelled-po",
                supplier,
                f"{doctype} {doc_id} references cancelled order {ln.po_id}",
                f"Referenced purchase order {ln.po_id} is cancelled.",
                [ref, DocRef("Purchase Order", ln.po_id)],
            )
            return None
        hit = line_index.get((ln.po_id, ln.po_line_id))
        if hit is None:
            if (
                target_po is not None
                and target_po.docstatus == SUBMITTED
                and (target_po.currency.upper() != policy.base_currency)
            ):
                return None  # already flagged at PO level
            manual(
                f"{doctype}:{doc_id}:{ln.line_id}:orphan",
                supplier,
                f"{doctype} {doc_id} references an unknown order line",
                f"Referenced line {ln.po_id} / {ln.po_line_id} was not found among synced, "
                "submitted purchase orders.",
                [ref, DocRef("Purchase Order", ln.po_id, ln.po_line_id)],
            )
            return None
        po, line = hit
        key = (po.source_id, line.line_id)
        if ln.item_code != line.item_code:
            manual(
                f"{doctype}:{doc_id}:{ln.line_id}:item",
                supplier,
                f"Item mismatch between {doc_id} and {po.source_id}",
                f"Line item {ln.item_code} differs from order line item {line.item_code}.",
                [ref, DocRef("Purchase Order", po.source_id, line.line_id)],
                po,
                line,
            )
            totals[key].blocked = True
            return None
        if ln.uom != line.uom:
            manual(
                f"{doctype}:{doc_id}:{ln.line_id}:uom",
                supplier,
                f"Unit-of-measure conversion required on {doc_id}",
                f"Line UOM {ln.uom} differs from order UOM {line.uom}; conversions are outside V1.",
                [ref, DocRef("Purchase Order", po.source_id, line.line_id)],
                po,
                line,
            )
            totals[key].blocked = True
            return None
        return key

    def doc_excluded(doctype: str, doc) -> bool:
        if doc.docstatus != SUBMITTED:
            state = "cancelled" if doc.docstatus == CANCELLED else "draft"
            for ln in doc.lines:
                if ln.po_id and ln.po_line_id:
                    totals[(ln.po_id, ln.po_line_id)].excluded.append(
                        f"{doctype} {doc.source_id} ({state}) excluded from totals"
                    )
            return True
        if doc.is_return:
            manual(
                f"{doctype}:{doc.source_id}:return",
                doc.supplier,
                f"Return document {doc.source_id}",
                "Returns and debit notes are outside V1 and are excluded from totals.",
                [DocRef(doctype, doc.source_id)],
            )
            return True
        if doc.currency.upper() != policy.base_currency:
            manual(
                f"{doctype}:{doc.source_id}:currency",
                doc.supplier,
                f"{doctype} {doc.source_id} in unsupported currency {doc.currency}",
                f"Currency {doc.currency} differs from base currency {policy.base_currency}.",
                [DocRef(doctype, doc.source_id)],
            )
            return True
        return False

    # 2. Receipts.
    for pr in receipts:
        if doc_excluded("Purchase Receipt", pr):
            continue
        for ln in pr.lines:
            key = resolve_line("Purchase Receipt", pr.source_id, ln, pr.supplier)
            if key is None:
                continue
            t = totals[key]
            t.accepted += ln.accepted_qty
            t.received += ln.received_qty
            t.rejected += ln.rejected_qty
            t.receipt_refs.append(DocRef("Purchase Receipt", pr.source_id, ln.line_id))

    # 3. Invoices.
    for pi in invoices:
        if doc_excluded("Purchase Invoice", pi):
            continue
        for ln in pi.lines:
            key = resolve_line("Purchase Invoice", pi.source_id, ln, pi.supplier)
            if key is None:
                continue
            t = totals[key]
            t.invoiced += ln.qty
            t.invoiced_amount += ln.qty * ln.rate
            t.invoice_refs.append(DocRef("Purchase Invoice", pi.source_id, ln.line_id))
            t.invoice_lines.append((pi, ln))

    # 4. Rules per matched PO line.
    for (po_id, line_id), (po, line) in sorted(line_index.items()):
        t = totals[(po_id, line_id)]
        if t.blocked:
            continue  # ambiguous relationship already flagged for manual review
        base_refs = [DocRef("Purchase Order", po_id, line_id), *t.receipt_refs, *t.invoice_refs]
        critical = line.item_code in policy.critical_items
        notes = tuple(t.excluded)
        common_facts = {
            "ordered_qty": _fmt(qty(line.qty)),
            "accepted_qty": _fmt(qty(t.accepted)),
            "rejected_qty": _fmt(qty(t.rejected)),
            "invoiced_qty": _fmt(qty(t.invoiced)),
            "po_rate": _fmt(money(line.rate)),
            "uom": line.uom,
        }

        # 4a. Invoiced quantity exceeds accepted quantity.
        if t.invoiced > t.accepted:
            unmatched = t.invoiced - t.accepted
            avg_rate = t.invoiced_amount / t.invoiced
            exposure = money(unmatched * avg_rate)
            facts = {
                **common_facts,
                "unmatched_qty": _fmt(qty(unmatched)),
                "avg_invoice_rate": _fmt(money(avg_rate)),
                "exposure": _fmt(exposure),
            }
            calc = [
                f"Ordered: {_fmt(qty(line.qty))} {line.uom} at {_fmt(money(line.rate))}",
                f"Accepted into stock across {len(t.receipt_refs)} receipt line(s): "
                f"{_fmt(qty(t.accepted))} {line.uom}"
                + (f" (rejected: {_fmt(qty(t.rejected))})" if t.rejected else ""),
                f"Invoiced across {len(t.invoice_refs)} invoice line(s): "
                f"{_fmt(qty(t.invoiced))} {line.uom}",
                f"Unmatched quantity = {_fmt(qty(t.invoiced))} − {_fmt(qty(t.accepted))} "
                f"= {_fmt(qty(unmatched))} {line.uom}",
                f"Exposure = {_fmt(qty(unmatched))} × average invoiced rate "
                f"{_fmt(money(avg_rate))} = {_fmt(exposure)} {policy.base_currency}",
            ]
            findings.append(
                Finding(
                    key=f"qty:{po_id}:{line_id}",
                    exception_type=ExceptionType.QTY_EXCEEDS_RECEIPT,
                    supplier=po.supplier,
                    po_id=po_id,
                    po_line_id=line_id,
                    item_code=line.item_code,
                    title=f"{line.item_code}: invoiced {_fmt(qty(t.invoiced))}, accepted "
                    f"{_fmt(qty(t.accepted))} on {po_id}",
                    facts=facts,
                    calculation=tuple(calc),
                    exposure=exposure,
                    days_overdue=0,
                    critical_item=critical,
                    related=tuple(base_refs),
                    notes=notes,
                )
            )

        # 4b. Price variance, per invoice line.
        for pi, ln in t.invoice_lines:
            if not exceeds_tolerance(line.rate, ln.rate, policy.price_tolerance_pct):
                continue
            dev = price_deviation_pct(line.rate, ln.rate)
            exposure = money(abs(ln.rate - line.rate) * ln.qty)
            direction = "above" if dev > 0 else "below"
            facts = {
                "po_rate": _fmt(money(line.rate)),
                "invoice_rate": _fmt(money(ln.rate)),
                "invoice_qty": _fmt(qty(ln.qty)),
                "deviation_pct": _fmt(pct(dev)),
                "tolerance_pct": _fmt(policy.price_tolerance_pct),
                "exposure": _fmt(exposure),
            }
            calc = [
                f"PO rate: {_fmt(money(line.rate))}; invoice rate: {_fmt(money(ln.rate))}",
                f"Deviation = ({_fmt(money(ln.rate))} − {_fmt(money(line.rate))}) / "
                f"{_fmt(money(line.rate))} = {_fmt(pct(dev))}% ({direction} PO)",
                f"Tolerance: ±{_fmt(policy.price_tolerance_pct)}% → outside tolerance",
                f"Exposure = |rate difference| × invoiced qty {_fmt(qty(ln.qty))} = "
                f"{_fmt(exposure)} {policy.base_currency}",
            ]
            findings.append(
                Finding(
                    key=f"price:{po_id}:{line_id}:{pi.source_id}:{ln.line_id}",
                    exception_type=ExceptionType.PRICE_VARIANCE,
                    supplier=po.supplier,
                    po_id=po_id,
                    po_line_id=line_id,
                    item_code=line.item_code,
                    title=f"{line.item_code}: invoice {pi.source_id} rate "
                    f"{_fmt(pct(dev))}% {direction} PO",
                    facts=facts,
                    calculation=tuple(calc),
                    exposure=exposure,
                    days_overdue=0,
                    critical_item=critical,
                    related=(
                        DocRef("Purchase Order", po_id, line_id),
                        DocRef("Purchase Invoice", pi.source_id, ln.line_id),
                    ),
                    notes=notes,
                )
            )

        # 4c. Overdue undelivered quantity.
        if po.status in CLOSED_PO_STATUSES or line.schedule_date is None:
            continue
        outstanding = line.qty - t.accepted
        days_late = (policy.as_of - line.schedule_date).days
        if outstanding > ZERO and days_late > policy.overdue_grace_days:
            exposure = money(outstanding * line.rate)
            facts = {
                **common_facts,
                "outstanding_qty": _fmt(qty(outstanding)),
                "schedule_date": line.schedule_date.isoformat(),
                "days_overdue": str(days_late),
                "undelivered_value": _fmt(exposure),
            }
            calc = [
                f"Scheduled delivery: {line.schedule_date.isoformat()} "
                f"({days_late} days ago; grace {policy.overdue_grace_days} days)",
                f"Outstanding = ordered {_fmt(qty(line.qty))} − accepted "
                f"{_fmt(qty(t.accepted))} = {_fmt(qty(outstanding))} {line.uom}"
                + (
                    f" (rejected goods not counted as delivered: {_fmt(qty(t.rejected))})"
                    if t.rejected
                    else ""
                ),
                f"Undelivered value = {_fmt(qty(outstanding))} × {_fmt(money(line.rate))} = "
                f"{_fmt(exposure)} {policy.base_currency}",
            ]
            findings.append(
                Finding(
                    key=f"overdue:{po_id}:{line_id}",
                    exception_type=ExceptionType.OVERDUE_DELIVERY,
                    supplier=po.supplier,
                    po_id=po_id,
                    po_line_id=line_id,
                    item_code=line.item_code,
                    title=f"{line.item_code}: {_fmt(qty(outstanding))} {line.uom} overdue "
                    f"on {po_id}",
                    facts=facts,
                    calculation=tuple(calc),
                    exposure=exposure,
                    days_overdue=days_late,
                    critical_item=critical,
                    related=tuple(base_refs),
                    notes=notes,
                )
            )

    # Deduplicate manual findings produced more than once for the same key.
    unique: dict[str, Finding] = {}
    for f in findings:
        unique.setdefault(f.key, f)
    return list(unique.values())

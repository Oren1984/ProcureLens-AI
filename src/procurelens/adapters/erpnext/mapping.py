"""Map ERPNext REST JSON documents to validated, normalised rows.

Malformed or unsupported records raise ``MappingError``; the sync records them as warnings
instead of silently guessing values.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from procurelens.domain.numbers import to_decimal

MAX_ID = 140


class MappingError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedLine:
    line_id: str
    idx: int
    item_code: str
    item_name: str
    qty: Decimal
    received_qty: Decimal
    rejected_qty: Decimal
    rate: Decimal
    uom: str
    schedule_date: date | None
    po_id: str | None
    po_line_id: str | None


@dataclass(frozen=True)
class NormalizedDocument:
    doctype: str
    source_id: str
    supplier: str
    docstatus: int
    status: str
    currency: str
    doc_date: date | None
    is_return: bool
    source_modified: str | None
    lines: tuple[NormalizedLine, ...]
    raw: dict
    content_hash: str


def content_hash(raw: dict) -> str:
    return hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _id(value: object, field: str, *, required: bool = True) -> str | None:
    if value is None or value == "":
        if required:
            raise MappingError(f"missing {field}")
        return None
    if not isinstance(value, str | int):
        raise MappingError(f"invalid {field}")
    text = str(value).strip()
    if not text or len(text) > MAX_ID:
        raise MappingError(f"invalid {field}")
    return text


def _date(value: object, field: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise MappingError(f"invalid date in {field}") from exc


def _num(value: object, field: str, *, allow_negative: bool = False) -> Decimal:
    try:
        d = to_decimal(value)
    except ValueError as exc:
        raise MappingError(f"invalid number in {field}") from exc
    if not d.is_finite():
        raise MappingError(f"invalid number in {field}")
    if d < 0 and not allow_negative:
        raise MappingError(f"negative {field}")
    return d


def normalize_supplier(raw: dict) -> tuple[str, str]:
    source_id = _id(raw.get("name"), "name")
    name = str(raw.get("supplier_name") or source_id)[:255]
    return source_id, name


def normalize_document(doctype: str, raw: dict) -> NormalizedDocument:
    if not isinstance(raw, dict):
        raise MappingError("record is not an object")
    source_id = _id(raw.get("name"), "name")
    docstatus = raw.get("docstatus")
    if docstatus not in (0, 1, 2):
        raise MappingError(f"{source_id}: invalid docstatus")
    currency = str(raw.get("currency") or "").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise MappingError(f"{source_id}: invalid currency")
    is_return = bool(raw.get("is_return"))
    items = raw.get("items")
    if not isinstance(items, list) or not items:
        raise MappingError(f"{source_id}: document has no items")
    lines: list[NormalizedLine] = []
    seen: set[str] = set()
    for pos, it in enumerate(items, start=1):
        if not isinstance(it, dict):
            raise MappingError(f"{source_id}: invalid item row")
        line_id = _id(it.get("name"), f"{source_id} item name")
        if line_id in seen:
            raise MappingError(f"{source_id}: duplicate item row {line_id}")
        seen.add(line_id)
        uom = str(it.get("uom") or "").strip()
        if not uom:
            raise MappingError(f"{source_id}: item row {line_id} has no UOM")
        # Returns carry negative quantities in ERPNext; they are flagged later, not computed.
        neg = is_return
        if doctype == "Purchase Order":
            po_id, po_line_id = source_id, line_id
            qty = _num(it.get("qty"), "qty")
            received = rejected = Decimal(0)
        elif doctype == "Purchase Receipt":
            po_id = _id(it.get("purchase_order"), "purchase_order", required=False)
            po_line_id = _id(it.get("purchase_order_item"), "purchase_order_item", required=False)
            qty = _num(it.get("qty"), "qty", allow_negative=neg)  # accepted qty
            received = _num(
                it.get("received_qty", it.get("qty")), "received_qty", allow_negative=neg
            )
            rejected = _num(it.get("rejected_qty"), "rejected_qty", allow_negative=neg)
        elif doctype == "Purchase Invoice":
            po_id = _id(it.get("purchase_order"), "purchase_order", required=False)
            po_line_id = _id(it.get("po_detail"), "po_detail", required=False)
            qty = _num(it.get("qty"), "qty", allow_negative=neg)
            received = rejected = Decimal(0)
        else:
            raise MappingError(f"unsupported doctype {doctype}")
        lines.append(
            NormalizedLine(
                line_id=line_id,
                idx=int(it.get("idx") or pos),
                item_code=_id(it.get("item_code"), f"{source_id} item_code"),
                item_name=str(it.get("item_name") or "")[:255],
                qty=qty,
                received_qty=received,
                rejected_qty=rejected,
                rate=_num(it.get("rate"), "rate"),
                uom=uom[:40],
                schedule_date=_date(it.get("schedule_date"), "schedule_date"),
                po_id=po_id,
                po_line_id=po_line_id,
            )
        )
    doc_date_field = "transaction_date" if doctype == "Purchase Order" else "posting_date"
    return NormalizedDocument(
        doctype=doctype,
        source_id=source_id,
        supplier=_id(raw.get("supplier"), "supplier"),
        docstatus=docstatus,
        status=str(raw.get("status") or "")[:40],
        currency=currency,
        doc_date=_date(raw.get(doc_date_field), doc_date_field),
        is_return=is_return,
        source_modified=str(raw.get("modified") or "")[:40] or None,
        lines=tuple(lines),
        raw=raw,
        content_hash=content_hash(raw),
    )

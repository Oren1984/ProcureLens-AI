"""Deterministic synthetic procurement data in ERPNext REST JSON shape. No network access.

All companies, people and documents are fictional. Dates are fixed and evaluated against a
fixed demo evaluation date so results are identical on every run.
"""

from __future__ import annotations

import copy
from datetime import date

from .base import ConnectionStatus, FetchResult

DEMO_AS_OF = date(2026, 10, 15)
DEMO_CRITICAL_ITEMS = ("MTR-750", "BRG-6204")

_SUPPLIERS = [
    {"name": "SUP-0001", "supplier_name": "Nordwind Components GmbH", "supplier_group": "Hardware"},
    {
        "name": "SUP-0002",
        "supplier_name": "Alba Industrial Supplies SL",
        "supplier_group": "Hardware",
    },
    {"name": "SUP-0003", "supplier_name": "Kestrel Packaging BV", "supplier_group": "Packaging"},
    {"name": "SUP-0004", "supplier_name": "Meridian Lab Instruments Inc.", "supplier_group": "Lab"},
]


def _po(name, supplier, tdate, lines, *, currency="EUR", docstatus=1, status="To Receive and Bill"):
    return {
        "doctype": "Purchase Order",
        "name": name,
        "supplier": supplier,
        "currency": currency,
        "docstatus": docstatus,
        "status": status,
        "transaction_date": tdate,
        "modified": f"{tdate} 09:00:00.000000",
        "items": [
            {
                "name": f"{name}-L{i}",
                "idx": i,
                "item_code": code,
                "item_name": iname,
                "qty": qty,
                "rate": rate,
                "uom": uom,
                "schedule_date": sched,
            }
            for i, (code, iname, qty, rate, uom, sched) in enumerate(lines, start=1)
        ],
    }


def _pr(name, supplier, pdate, lines, *, currency="EUR", docstatus=1, is_return=0):
    return {
        "doctype": "Purchase Receipt",
        "name": name,
        "supplier": supplier,
        "currency": currency,
        "docstatus": docstatus,
        "is_return": is_return,
        "posting_date": pdate,
        "modified": f"{pdate} 10:00:00.000000",
        "items": [
            {
                "name": f"{name}-L{i}",
                "idx": i,
                "item_code": code,
                "qty": acc,
                "received_qty": rec,
                "rejected_qty": rej,
                "uom": uom,
                "purchase_order": po,
                "purchase_order_item": pol,
            }
            for i, (code, rec, acc, rej, uom, po, pol) in enumerate(lines, start=1)
        ],
    }


def _pi(name, supplier, pdate, lines, *, currency="EUR", docstatus=1, is_return=0):
    return {
        "doctype": "Purchase Invoice",
        "name": name,
        "supplier": supplier,
        "currency": currency,
        "docstatus": docstatus,
        "is_return": is_return,
        "posting_date": pdate,
        "bill_no": f"INV-{name[-4:]}",
        "modified": f"{pdate} 11:00:00.000000",
        "items": [
            {
                "name": f"{name}-L{i}",
                "idx": i,
                "item_code": code,
                "qty": qty,
                "rate": rate,
                "uom": uom,
                "purchase_order": po,
                "po_detail": pol,
            }
            for i, (code, qty, rate, uom, po, pol) in enumerate(lines, start=1)
        ],
    }


_ORDERS = [
    # 1. Classic: 100 ordered, 80 accepted, 100 invoiced → qty exception + overdue remainder.
    _po(
        "PO-2026-0001",
        "SUP-0001",
        "2026-09-01",
        [("BRG-6204", "Deep groove ball bearing 6204", 100, 12.50, "Nos", "2026-09-20")],
    ),
    # 2. Two partial receipts (one with rejected goods) and two invoices that fully match.
    _po(
        "PO-2026-0002",
        "SUP-0001",
        "2026-09-03",
        [("SEAL-40", "Shaft seal 40 mm", 200, 3.20, "Nos", "2026-09-25")],
    ),
    # 3. Price above tolerance on a critical motor.
    _po(
        "PO-2026-0003",
        "SUP-0002",
        "2026-09-05",
        [("MTR-750", "Induction motor 0.75 kW", 50, 410.00, "Nos", "2026-09-30")],
    ),
    # 4. Price deviation within tolerance (1.67% < 2%) → no exception.
    _po(
        "PO-2026-0004",
        "SUP-0002",
        "2026-09-06",
        [("CBL-10", "Control cable 10 x 0.75", 500, 1.80, "Meter", "2026-09-28")],
    ),
    # 5. Partial delivery long overdue; a cancelled invoice is excluded.
    _po(
        "PO-2026-0005",
        "SUP-0003",
        "2026-08-15",
        [("BOX-A4", "Corrugated shipping box A4", 1000, 0.45, "Nos", "2026-09-01")],
    ),
    # 6. Rejected goods invoiced in full → qty exception (within delivery grace).
    _po(
        "PO-2026-0006",
        "SUP-0003",
        "2026-09-20",
        [("PAL-EU", "EUR pallet, heat treated", 300, 9.00, "Nos", "2026-10-14")],
    ),
    # 7. Foreign currency → manual review.
    _po(
        "PO-2026-0007",
        "SUP-0004",
        "2026-09-10",
        [("PH-MTR", "Benchtop pH meter", 4, 380.00, "Nos", "2026-10-30")],
        currency="USD",
    ),
    # 8. Unit-of-measure conversion required → manual review.
    _po(
        "PO-2026-0008",
        "SUP-0001",
        "2026-09-12",
        [("GLV-NIT", "Nitrile gloves, box of 100", 10, 8.40, "Box", "2026-10-20")],
    ),
    # 9. Cancelled order: ignored.
    _po(
        "PO-2026-0009",
        "SUP-0002",
        "2026-09-14",
        [("MTR-750", "Induction motor 0.75 kW", 5, 410.00, "Nos", "2026-10-05")],
        docstatus=2,
        status="Cancelled",
    ),
]

_RECEIPTS = [
    _pr(
        "PR-2026-0001",
        "SUP-0001",
        "2026-09-19",
        [("BRG-6204", 80, 80, 0, "Nos", "PO-2026-0001", "PO-2026-0001-L1")],
    ),
    _pr(
        "PR-2026-0002",
        "SUP-0001",
        "2026-09-22",
        [("SEAL-40", 120, 120, 0, "Nos", "PO-2026-0002", "PO-2026-0002-L1")],
    ),
    _pr(
        "PR-2026-0003",
        "SUP-0001",
        "2026-09-26",
        [("SEAL-40", 90, 80, 10, "Nos", "PO-2026-0002", "PO-2026-0002-L1")],
    ),
    _pr(
        "PR-2026-0004",
        "SUP-0002",
        "2026-09-29",
        [("MTR-750", 50, 50, 0, "Nos", "PO-2026-0003", "PO-2026-0003-L1")],
    ),
    _pr(
        "PR-2026-0005",
        "SUP-0002",
        "2026-09-27",
        [("CBL-10", 500, 500, 0, "Meter", "PO-2026-0004", "PO-2026-0004-L1")],
    ),
    _pr(
        "PR-2026-0006",
        "SUP-0003",
        "2026-08-30",
        [("BOX-A4", 400, 400, 0, "Nos", "PO-2026-0005", "PO-2026-0005-L1")],
    ),
    _pr(
        "PR-2026-0007",
        "SUP-0003",
        "2026-10-13",
        [("PAL-EU", 300, 250, 50, "Nos", "PO-2026-0006", "PO-2026-0006-L1")],
    ),
    _pr(
        "PR-2026-0008",
        "SUP-0001",
        "2026-10-18",
        [("GLV-NIT", 10, 10, 0, "Box", "PO-2026-0008", "PO-2026-0008-L1")],
    ),
    # Return of 5 seals → returns are outside V1 → manual review.
    _pr(
        "PR-2026-0009",
        "SUP-0001",
        "2026-10-02",
        [("SEAL-40", -5, -5, 0, "Nos", "PO-2026-0002", "PO-2026-0002-L1")],
        is_return=1,
    ),
]

_INVOICES = [
    _pi(
        "PI-2026-0001",
        "SUP-0001",
        "2026-09-24",
        [("BRG-6204", 100, 12.50, "Nos", "PO-2026-0001", "PO-2026-0001-L1")],
    ),
    _pi(
        "PI-2026-0002",
        "SUP-0001",
        "2026-09-24",
        [("SEAL-40", 120, 3.20, "Nos", "PO-2026-0002", "PO-2026-0002-L1")],
    ),
    _pi(
        "PI-2026-0003",
        "SUP-0001",
        "2026-09-30",
        [("SEAL-40", 80, 3.20, "Nos", "PO-2026-0002", "PO-2026-0002-L1")],
    ),
    _pi(
        "PI-2026-0004",
        "SUP-0002",
        "2026-10-02",
        [("MTR-750", 50, 428.00, "Nos", "PO-2026-0003", "PO-2026-0003-L1")],
    ),
    _pi(
        "PI-2026-0005",
        "SUP-0002",
        "2026-09-30",
        [("CBL-10", 500, 1.83, "Meter", "PO-2026-0004", "PO-2026-0004-L1")],
    ),
    _pi(
        "PI-2026-0006",
        "SUP-0003",
        "2026-09-05",
        [("BOX-A4", 1000, 0.45, "Nos", "PO-2026-0005", "PO-2026-0005-L1")],
        docstatus=2,
    ),
    _pi(
        "PI-2026-0007",
        "SUP-0003",
        "2026-09-06",
        [("BOX-A4", 400, 0.45, "Nos", "PO-2026-0005", "PO-2026-0005-L1")],
    ),
    _pi(
        "PI-2026-0008",
        "SUP-0003",
        "2026-10-14",
        [("PAL-EU", 300, 9.00, "Nos", "PO-2026-0006", "PO-2026-0006-L1")],
    ),
    _pi(
        "PI-2026-0009",
        "SUP-0004",
        "2026-10-12",
        [("PH-MTR", 4, 380.00, "Nos", "PO-2026-0007", "PO-2026-0007-L1")],
        currency="USD",
    ),
    _pi(
        "PI-2026-0010",
        "SUP-0001",
        "2026-10-19",
        [("GLV-NIT", 1000, 0.084, "Nos", "PO-2026-0008", "PO-2026-0008-L1")],
    ),
    # Freight line without a purchase-order reference → ambiguous → manual review.
    _pi("PI-2026-0011", "SUP-0002", "2026-10-03", [("FRT-SVC", 1, 145.00, "Nos", None, None)]),
]

_DATA = {
    "Supplier": _SUPPLIERS,
    "Purchase Order": _ORDERS,
    "Purchase Receipt": _RECEIPTS,
    "Purchase Invoice": _INVOICES,
}


class DemoSource:
    """Synthetic source. ``overrides`` lets tests simulate changed or malformed records."""

    adapter_name = "demo"
    dataset = "demo"

    def __init__(self, overrides: dict[str, list[dict]] | None = None) -> None:
        self._data = copy.deepcopy(_DATA)
        for doctype, records in (overrides or {}).items():
            by_name = {r.get("name"): r for r in self._data[doctype]}
            for r in records:
                by_name[r.get("name")] = r
            self._data[doctype] = list(by_name.values())

    def check_connection(self) -> ConnectionStatus:
        return ConnectionStatus(True, "Demo source ready (synthetic data, no network).", "demo")

    def fetch(self, doctype: str) -> FetchResult:
        return FetchResult(records=copy.deepcopy(self._data[doctype]))

from datetime import date
from decimal import Decimal as D

import pytest

from procurelens.domain.models import (
    ExceptionType,
    InvoiceLine,
    OrderLine,
    PurchaseInvoice,
    PurchaseOrder,
    PurchaseReceipt,
    ReceiptLine,
    ReconciliationPolicy,
)
from procurelens.domain.numbers import exceeds_tolerance, money, price_deviation_pct, qty
from procurelens.domain.reconciliation import fingerprint, reconcile

POLICY = ReconciliationPolicy("EUR", D("2.0"), 3, date(2026, 10, 15), frozenset({"CRIT"}))


def po(
    qty_="100",
    rate="12.50",
    *,
    docstatus=1,
    status="To Receive and Bill",
    currency="EUR",
    uom="Nos",
    sched=date(2026, 10, 30),
    item="ITEM",
    rate_po=None,
):
    return PurchaseOrder(
        "PO1",
        "S1",
        currency,
        docstatus,
        status,
        date(2026, 9, 1),
        (OrderLine("L1", item, "Item", D(qty_), D(rate_po or rate), uom, sched),),
    )


def pr(
    name,
    acc,
    rec=None,
    rej="0",
    *,
    docstatus=1,
    is_return=False,
    uom="Nos",
    item="ITEM",
    po_id="PO1",
    po_line="L1",
    currency="EUR",
):
    return PurchaseReceipt(
        name,
        "S1",
        currency,
        docstatus,
        date(2026, 9, 10),
        is_return,
        (ReceiptLine(f"{name}-1", item, D(acc), D(rec or acc), D(rej), uom, po_id, po_line),),
    )


def pi(
    name,
    qty_,
    rate="12.50",
    *,
    docstatus=1,
    is_return=False,
    uom="Nos",
    item="ITEM",
    po_id="PO1",
    po_line="L1",
    currency="EUR",
):
    return PurchaseInvoice(
        name,
        "S1",
        currency,
        docstatus,
        date(2026, 9, 12),
        is_return,
        (InvoiceLine(f"{name}-1", item, D(qty_), D(rate), uom, po_id, po_line),),
    )


def by_type(findings, t):
    return [f for f in findings if f.exception_type == t]


# -- numbers ---------------------------------------------------------------------------------
def test_rounding_half_up_and_decimal_exactness():
    assert money(D("2.345")) == D("2.35")
    assert money(D("0.1") + D("0.2")) == D("0.30")
    assert qty(D("1.0005")) == D("1.001")


def test_tolerance_boundary_is_inclusive():
    assert price_deviation_pct(D("100"), D("102")) == D("2")
    assert not exceeds_tolerance(D("100"), D("102"), D("2"))
    assert exceeds_tolerance(D("100"), D("102.01"), D("2"))
    assert exceeds_tolerance(D("100"), D("97.99"), D("2"))
    with pytest.raises(ValueError):
        price_deviation_pct(D("0"), D("1"))


# -- quantity ------------------------------------------------------------------------------------
def test_classic_case_100_ordered_80_accepted_100_invoiced():
    f = reconcile([po()], [pr("PR1", "80")], [pi("PI1", "100")], POLICY)
    (q,) = by_type(f, ExceptionType.QTY_EXCEEDS_RECEIPT)
    assert q.facts["unmatched_qty"] == "20"
    assert q.exposure == D("250.00")
    assert q.key == "qty:PO1:L1"


def test_partial_deliveries_and_multiple_invoices_sum_per_line():
    f = reconcile(
        [po()], [pr("PR1", "30"), pr("PR2", "50")], [pi("PI1", "30"), pi("PI2", "60")], POLICY
    )
    (q,) = by_type(f, ExceptionType.QTY_EXCEEDS_RECEIPT)
    assert q.facts["accepted_qty"] == "80" and q.facts["invoiced_qty"] == "90"
    assert q.facts["unmatched_qty"] == "10"
    assert q.exposure == D("125.00")


def test_fully_matched_partial_deliveries_produce_no_exception():
    f = reconcile(
        [po()], [pr("PR1", "40"), pr("PR2", "60")], [pi("PI1", "40"), pi("PI2", "60")], POLICY
    )
    assert f == []


def test_rejected_goods_are_not_accepted_quantity():
    f = reconcile([po()], [pr("PR1", "90", rec="100", rej="10")], [pi("PI1", "100")], POLICY)
    (q,) = by_type(f, ExceptionType.QTY_EXCEEDS_RECEIPT)
    assert q.facts["rejected_qty"] == "10"
    assert q.facts["unmatched_qty"] == "10"


def test_weighted_invoice_rate_for_exposure():
    f = reconcile(
        [po()], [pr("PR1", "50")], [pi("PI1", "50", "12.50"), pi("PI2", "10", "12.50")], POLICY
    )
    assert by_type(f, ExceptionType.QTY_EXCEEDS_RECEIPT)[0].exposure == D("125.00")


def test_fractional_quantities_are_exact():
    f = reconcile(
        [po("10.5", "3.333", uom="Kg")],
        [pr("PR1", "10.25", uom="Kg")],
        [pi("PI1", "10.5", "3.333", uom="Kg")],
        POLICY,
    )
    (q,) = by_type(f, ExceptionType.QTY_EXCEEDS_RECEIPT)
    assert q.facts["unmatched_qty"] == "0.25"
    assert q.exposure == D("0.83")  # 0.25 * 3.333 = 0.83325 -> 0.83


# -- cancelled / drafts / unsupported ---------------------------------------------------------
def test_cancelled_and_draft_documents_are_excluded_and_noted():
    f = reconcile(
        [po()],
        [pr("PR1", "100"), pr("PR2", "50", docstatus=0)],
        [pi("PI1", "100"), pi("PI2", "100", docstatus=2)],
        POLICY,
    )
    assert by_type(f, ExceptionType.QTY_EXCEEDS_RECEIPT) == []
    assert f == []


def test_cancelled_invoice_noted_on_finding():
    f = reconcile(
        [po()], [pr("PR1", "80")], [pi("PI1", "100"), pi("PI2", "5", docstatus=2)], POLICY
    )
    (q,) = by_type(f, ExceptionType.QTY_EXCEEDS_RECEIPT)
    assert any("PI2 (cancelled)" in n for n in q.notes)


def test_cancelled_po_is_ignored_but_linked_invoice_flagged():
    f = reconcile([po(docstatus=2)], [], [pi("PI1", "10")], POLICY)
    assert [x.key for x in f] == ["manual:Purchase Invoice:PI1:PI1-1:cancelled-po"]


@pytest.mark.parametrize(
    "kwargs,suffix",
    [
        ({"is_return": True}, "return"),
        ({"currency": "USD"}, "currency"),
    ],
)
def test_unsupported_invoice_documents_flagged_not_calculated(kwargs, suffix):
    f = reconcile([po()], [pr("PR1", "100")], [pi("PI1", "100", **kwargs)], POLICY)
    assert [x.key for x in f] == [f"manual:Purchase Invoice:PI1:{suffix}"]
    assert all(x.exposure == 0 for x in f)


def test_foreign_currency_po_flagged():
    f = reconcile([po(currency="USD")], [], [], POLICY)
    assert [x.exception_type for x in f] == [ExceptionType.MANUAL_REVIEW]


def test_uom_mismatch_blocks_line_calculation():
    f = reconcile(
        [po(uom="Box")],
        [pr("PR1", "100", uom="Box")],
        [pi("PI1", "1000", "0.125", uom="Nos")],
        POLICY,
    )
    assert [x.key for x in f] == ["manual:Purchase Invoice:PI1:PI1-1:uom"]


def test_item_mismatch_flagged():
    f = reconcile([po()], [pr("PR1", "100")], [pi("PI1", "100", item="OTHER")], POLICY)
    assert [x.key for x in f] == ["manual:Purchase Invoice:PI1:PI1-1:item"]


def test_orphan_and_unlinked_lines_flagged():
    f = reconcile(
        [po()], [pr("PR1", "100", po_line="L9")], [pi("PI1", "1", po_id=None, po_line=None)], POLICY
    )
    keys = {x.key for x in f}
    assert "manual:Purchase Receipt:PR1:PR1-1:orphan" in keys
    assert "manual:Purchase Invoice:PI1:PI1-1:unlinked" in keys


def test_non_positive_po_values_flagged():
    f = reconcile([po(qty_="0")], [], [], POLICY)
    assert f[0].exception_type == ExceptionType.MANUAL_REVIEW


# -- price -----------------------------------------------------------------------------------
def test_price_within_tolerance_no_exception():
    f = reconcile([po(rate="100")], [pr("PR1", "100")], [pi("PI1", "100", "102")], POLICY)
    assert f == []


def test_price_above_tolerance_per_invoice_line():
    f = reconcile(
        [po(rate="410")],
        [pr("PR1", "100")],
        [pi("PI1", "50", "428"), pi("PI2", "50", "410")],
        POLICY,
    )
    (p,) = by_type(f, ExceptionType.PRICE_VARIANCE)
    assert p.facts["deviation_pct"] == "4.39"
    assert p.exposure == D("900.00")
    assert p.key == "price:PO1:L1:PI1:PI1-1"


def test_price_below_po_flagged_with_direction():
    f = reconcile([po(rate="100")], [pr("PR1", "100")], [pi("PI1", "100", "90")], POLICY)
    (p,) = by_type(f, ExceptionType.PRICE_VARIANCE)
    assert "below" in p.calculation[1]


# -- overdue -----------------------------------------------------------------------------------
def test_overdue_after_grace_only():
    within = reconcile([po(sched=date(2026, 10, 12))], [pr("PR1", "50")], [], POLICY)
    assert by_type(within, ExceptionType.OVERDUE_DELIVERY) == []  # 3 days == grace
    late = reconcile([po(sched=date(2026, 10, 11))], [pr("PR1", "50")], [], POLICY)
    (o,) = by_type(late, ExceptionType.OVERDUE_DELIVERY)
    assert o.days_overdue == 4 and o.facts["outstanding_qty"] == "50"
    assert o.exposure == D("625.00")


def test_closed_po_not_overdue_and_rejected_goods_still_outstanding():
    assert reconcile([po(sched=date(2026, 9, 1), status="Closed")], [], [], POLICY) == []
    f = reconcile([po(sched=date(2026, 9, 1))], [pr("PR1", "90", rec="100", rej="10")], [], POLICY)
    assert by_type(f, ExceptionType.OVERDUE_DELIVERY)[0].facts["outstanding_qty"] == "10"


def test_critical_item_flag():
    f = reconcile([po(item="CRIT", sched=date(2026, 9, 1))], [], [], POLICY)
    assert f[0].critical_item


def test_fingerprint_stable_and_sensitive():
    a = {"x": "1", "y": "2"}
    assert fingerprint(a) == fingerprint({"y": "2", "x": "1"})
    assert fingerprint(a) != fingerprint({"x": "1", "y": "3"})


def test_demo_dataset_expected_findings():
    from procurelens.adapters.erpnext.demo import DEMO_AS_OF, DemoSource
    from procurelens.adapters.erpnext.mapping import normalize_document
    from procurelens.adapters.persistence.repositories import SourceRepository

    src = DemoSource()

    class FakeRepo(SourceRepository):
        def __init__(self):
            pass

        def documents(self, dataset, doctype=None):
            class Row:
                pass

            rows = []
            for dt in ("Purchase Order", "Purchase Receipt", "Purchase Invoice"):
                for raw in src.fetch(dt).records:
                    d = normalize_document(dt, raw)
                    r = Row()
                    r.__dict__.update(d.__dict__)
                    rows.append(r)
            return rows

    orders, receipts, invoices = FakeRepo().load_domain("demo")
    policy = ReconciliationPolicy("EUR", D("2.0"), 3, DEMO_AS_OF, frozenset({"MTR-750"}))
    keys = sorted(f.key for f in reconcile(orders, receipts, invoices, policy))
    assert keys == sorted(
        [
            "qty:PO-2026-0001:PO-2026-0001-L1",
            "overdue:PO-2026-0001:PO-2026-0001-L1",
            "price:PO-2026-0003:PO-2026-0003-L1:PI-2026-0004:PI-2026-0004-L1",
            "overdue:PO-2026-0005:PO-2026-0005-L1",
            "qty:PO-2026-0006:PO-2026-0006-L1",
            "manual:PO:PO-2026-0007:currency",
            "manual:Purchase Invoice:PI-2026-0009:currency",
            "manual:Purchase Receipt:PR-2026-0009:return",
            "manual:Purchase Invoice:PI-2026-0010:PI-2026-0010-L1:uom",
            "manual:Purchase Invoice:PI-2026-0011:PI-2026-0011-L1:unlinked",
        ]
    )

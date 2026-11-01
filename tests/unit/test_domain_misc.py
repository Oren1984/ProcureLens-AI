from decimal import Decimal as D

import pytest

from procurelens.adapters.erpnext.mapping import MappingError, normalize_document
from procurelens.domain.lifecycle import Action, LifecycleError, allowed_actions, apply
from procurelens.domain.models import ExceptionStatus as S
from procurelens.domain.models import ExceptionType, Finding
from procurelens.domain.priority import score_finding
from procurelens.logging_setup import JsonFormatter, redact
from procurelens.security.passwords import generate_password, hash_password, verify_password


def _finding(**kw):
    base = dict(
        key="k",
        exception_type=ExceptionType.QTY_EXCEEDS_RECEIPT,
        supplier="S",
        po_id="P",
        po_line_id="L",
        item_code="I",
        title="t",
        facts={},
        calculation=(),
        exposure=D("0"),
        days_overdue=0,
        critical_item=False,
    )
    base.update(kw)
    return Finding(**base)


# -- lifecycle ---------------------------------------------------------------------------------
def test_lifecycle_happy_path_and_reopen():
    assert apply(Action.START_REVIEW, S.OPEN, None) == S.IN_REVIEW
    assert apply(Action.RESOLVE, S.IN_REVIEW, "Credit note received") == S.RESOLVED
    assert apply(Action.REOPEN, S.RESOLVED, "Credit note was cancelled") == S.OPEN


@pytest.mark.parametrize(
    "action,current,reason",
    [
        (Action.RESOLVE, S.OPEN, ""),
        (Action.RESOLVE, S.OPEN, "ok"),
        (Action.REOPEN, S.RESOLVED, None),
        (Action.REOPEN, S.OPEN, "a valid reason"),
        (Action.START_REVIEW, S.RESOLVED, None),
        (Action.RESOLVE, S.RESOLVED, "a valid reason"),
        (Action.RESOLVE, S.OPEN, "x" * 2001),
    ],
)
def test_lifecycle_rejects_invalid(action, current, reason):
    with pytest.raises(LifecycleError):
        apply(action, current, reason)


def test_allowed_actions():
    assert allowed_actions(S.RESOLVED) == [Action.REOPEN]
    assert set(allowed_actions(S.OPEN)) == {Action.START_REVIEW, Action.RESOLVE}


# -- priority ------------------------------------------------------------------------------------
def test_priority_components_are_transparent_and_capped():
    s = score_finding(_finding(exposure=D("250")))
    assert s.score == 2 and "1 point per 100" in s.reasons[0]
    big = score_finding(
        _finding(
            exposure=D("999999"),
            critical_item=True,
            exception_type=ExceptionType.OVERDUE_DELIVERY,
            days_overdue=400,
        )
    )
    assert big.score == 95 and big.band == "High"
    manual = score_finding(_finding(exception_type=ExceptionType.MANUAL_REVIEW))
    assert manual.score == 5


# -- mapping ---------------------------------------------------------------------------------------
GOOD = {
    "name": "PO-1",
    "supplier": "S",
    "currency": "eur",
    "docstatus": 1,
    "transaction_date": "2026-01-01",
    "items": [{"name": "r1", "item_code": "A", "qty": 1.5, "rate": 2, "uom": "Nos"}],
}


def test_mapping_normalises_and_uses_decimal():
    d = normalize_document("Purchase Order", GOOD)
    assert d.currency == "EUR" and d.lines[0].qty == D("1.5") and d.lines[0].po_line_id == "r1"


@pytest.mark.parametrize(
    "patch",
    [
        {"docstatus": 5},
        {"currency": "EURO"},
        {"items": []},
        {"name": ""},
        {"items": [{"name": "r1", "item_code": "A", "qty": -1, "rate": 2, "uom": "Nos"}]},
        {"items": [{"name": "r1", "item_code": "A", "qty": "abc", "rate": 2, "uom": "Nos"}]},
        {"items": [{"name": "r1", "item_code": "A", "qty": 1, "rate": 2, "uom": ""}]},
        {
            "items": [
                {"name": "r1", "item_code": "A", "qty": 1, "rate": 2, "uom": "Nos"},
                {"name": "r1", "item_code": "A", "qty": 1, "rate": 2, "uom": "Nos"},
            ]
        },
        {"transaction_date": "not-a-date"},
    ],
)
def test_mapping_rejects_malformed(patch):
    with pytest.raises(MappingError):
        normalize_document("Purchase Order", {**GOOD, **patch})


def test_mapping_allows_negative_qty_only_on_returns():
    raw = {
        "name": "PR-R",
        "supplier": "S",
        "currency": "EUR",
        "docstatus": 1,
        "is_return": 1,
        "items": [
            {
                "name": "x",
                "item_code": "A",
                "qty": -5,
                "uom": "Nos",
                "purchase_order": "PO-1",
                "purchase_order_item": "r1",
            }
        ],
    }
    assert normalize_document("Purchase Receipt", raw).is_return


# -- passwords / logging ---------------------------------------------------------------------------
def test_argon2id_hash_and_verify():
    h = hash_password("correct horse")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "correct horse")
    assert not verify_password(h, "wrong")
    assert not verify_password(None, "anything")
    assert not verify_password("garbage", "x")


def test_generated_passwords_are_strong_and_unique():
    pws = {generate_password() for _ in range(20)}
    assert len(pws) == 20 and all(len(p) == 24 for p in pws)


def test_log_redaction():
    assert "s3cr3t" not in redact("Authorization: token key123:s3cr3t")
    assert "pw" not in redact("postgresql+psycopg://user:pw@db/x").split("@")[0]
    import logging

    rec = logging.LogRecord("x", 20, "", 0, "hello", (), None)
    rec.password = "hunter2"
    rec.api_secret = "abc"
    out = JsonFormatter().format(rec)
    assert "hunter2" not in out and "abc" not in out

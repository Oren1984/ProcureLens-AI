"""Central rounding and tolerance rules.

* Money is rounded to 2 decimal places, ROUND_HALF_UP, only when presenting or storing a
  derived amount. Intermediate sums keep full Decimal precision.
* Quantities are rounded to 3 decimal places (ERPNext's default float precision for qty).
* Price tolerance is a percentage of the purchase-order rate. A deviation exactly equal to
  the tolerance is accepted; only a strictly greater deviation is an exception.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MONEY_PLACES = Decimal("0.01")
QTY_PLACES = Decimal("0.001")
PCT_PLACES = Decimal("0.01")
ZERO = Decimal("0")


def to_decimal(value: object) -> Decimal:
    """Convert ERP JSON numbers to Decimal without binary float artefacts."""
    if value is None or value == "":
        return ZERO
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise ValueError("boolean is not a number")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"not a number: {value!r}") from exc


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)


def qty(value: Decimal) -> Decimal:
    return value.quantize(QTY_PLACES, rounding=ROUND_HALF_UP)


def pct(value: Decimal) -> Decimal:
    return value.quantize(PCT_PLACES, rounding=ROUND_HALF_UP)


def price_deviation_pct(po_rate: Decimal, invoice_rate: Decimal) -> Decimal:
    """Signed deviation of the invoice rate from the PO rate, in percent (unrounded)."""
    if po_rate <= ZERO:
        raise ValueError("PO rate must be positive")
    return (invoice_rate - po_rate) / po_rate * Decimal(100)


def exceeds_tolerance(po_rate: Decimal, invoice_rate: Decimal, tolerance_pct: Decimal) -> bool:
    return abs(price_deviation_pct(po_rate, invoice_rate)) > tolerance_pct

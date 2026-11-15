"""MarginOS — deterministic money arithmetic.

Every financial figure in MarginOS is computed with Decimal, never float.
All amounts are rounded half-up to 2 decimal places (cents) at the point a
figure is *stored*; intermediate multiplication keeps full precision and is
rounded once at the end, so any reviewer can reproduce a number from its
inputs with a hand calculator. No figure is ever invented: a computed amount
always carries the inputs it was derived from (see matching.py evidence).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")
ZERO = Decimal("0")


def D(value) -> Decimal:
    """Coerce int/str/float/Decimal to Decimal without float contamination."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if value is None:
        return ZERO
    return Decimal(str(value).strip().replace(",", "").replace("$", ""))


def cents(value) -> Decimal:
    """Round to cents, half-up."""
    return D(value).quantize(CENT, rounding=ROUND_HALF_UP)


def line_total(qty, unit_price) -> Decimal:
    return cents(D(qty) * D(unit_price))


def tax_on(amount, rate) -> Decimal:
    """Tax for a taxable amount at a decimal rate (e.g. 0.05 for 5%)."""
    return cents(D(amount) * D(rate))


def diff(a, b) -> Decimal:
    return cents(D(a) - D(b))

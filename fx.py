"""FX rate lookup for tenant customer billing -- see
docs/superpowers/specs/2026-08-27-multi-currency-accounting-design.md.

Manual-entry only (no external API). Historical Payment rows never call back
into this module after creation -- they store their own locked
fx_rate_to_reporting. This module is consulted only (a) at payment-creation
time to pick the rate to lock, and (b) for live (non-historical) conversions.
"""
from decimal import Decimal
from datetime import datetime


class FxRateMissingError(Exception):
    """Raised when no applicable ExchangeRate (direct or inverse) exists for
    a tenant/currency-pair/as_of. Callers must not silently default to 1 or
    guess -- a missing rate blocks the operation that needed it."""


def get_rate(tenant_id, from_code, to_code, as_of=None):
    """Return the Decimal rate to convert 1 unit of from_code into to_code,
    as of `as_of` (default: now). Same-currency is always exactly 1, with no
    DB query. Otherwise looks up the latest direct-pair ExchangeRate row with
    effective_at <= as_of; falls back to the inverse pair (1/rate) if no
    direct row exists. Raises FxRateMissingError if neither exists."""
    from app import ExchangeRate  # local import: fx.py has no other app.py dependency

    if from_code == to_code:
        return Decimal('1')

    as_of = as_of or datetime.utcnow()

    direct = (
        ExchangeRate.query
        .filter(ExchangeRate.tenant_id == tenant_id,
                ExchangeRate.from_currency == from_code,
                ExchangeRate.to_currency == to_code,
                ExchangeRate.effective_at <= as_of)
        .order_by(ExchangeRate.effective_at.desc())
        .first()
    )
    if direct:
        return Decimal(direct.rate)

    inverse = (
        ExchangeRate.query
        .filter(ExchangeRate.tenant_id == tenant_id,
                ExchangeRate.from_currency == to_code,
                ExchangeRate.to_currency == from_code,
                ExchangeRate.effective_at <= as_of)
        .order_by(ExchangeRate.effective_at.desc())
        .first()
    )
    if inverse:
        return Decimal('1') / Decimal(inverse.rate)

    raise FxRateMissingError(
        f"No exchange rate on file for {from_code}->{to_code} (tenant {tenant_id}) as of {as_of}.")

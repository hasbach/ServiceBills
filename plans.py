"""servicesBills subscription plan catalog.

Single source of truth mapping each plan to its Stripe Price ID (dormant --
see docs/superpowers/specs/2026-08-26-whish-self-serve-billing-design.md for
why Stripe is not used) and its Whish self-serve prices, plus enforced
limits. max_customers=None means unlimited; whatsapp_api gates Meta Cloud
API mode.
"""
import os

PLANS = {
    "free": {
        "stripe_price": None,
        "whish_price_monthly": None,
        "whish_price_yearly": None,
        "max_customers": 50,
        "whatsapp_api": False,
        "whish_customer_payments": False,
    },
    "pro": {
        "stripe_price": os.environ.get("STRIPE_PRICE_PRO"),
        "whish_price_monthly": 120.0,
        "whish_price_yearly": 1000.0,
        "max_customers": None,
        "whatsapp_api": True,
        "whish_customer_payments": True,
    },
}

DEFAULT_PLAN = "free"


def limits(plan_name):
    """Return the limits dict for a plan, falling back to free."""
    return PLANS.get(plan_name, PLANS[DEFAULT_PLAN])


def plan_for_price(price_id):
    """Map a Stripe Price ID back to a plan name (free if unknown/None)."""
    if not price_id:
        return DEFAULT_PLAN
    for name, p in PLANS.items():
        if p["stripe_price"] and p["stripe_price"] == price_id:
            return name
    return DEFAULT_PLAN


def whish_price(plan_name, cycle):
    """Return the Whish price for plan_name/cycle, or None if not purchasable."""
    key = f"whish_price_{cycle}"
    return PLANS.get(plan_name, {}).get(key)

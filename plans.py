"""servicesBills subscription plan catalog.

Single source of truth mapping each plan to its Stripe Price ID and enforced
limits. max_customers=None means unlimited; whatsapp_api gates Meta Cloud API mode.
"""
import os

PLANS = {
    "free": {
        "stripe_price": None,
        "max_customers": 50,
        "whatsapp_api": False,
    },
    "pro": {
        "stripe_price": os.environ.get("STRIPE_PRICE_PRO"),
        "max_customers": None,
        "whatsapp_api": True,
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

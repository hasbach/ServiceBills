"""Stripe subscription billing.

Stripe is the source of truth for subscription state; handle_event() maps webhook
events onto Tenant.plan/status and is pure (no network) so it is unit-testable.
Checkout/portal helpers create Stripe-hosted sessions.
"""
import stripe
import plans
from config import Config

if Config.STRIPE_SECRET_KEY:
    stripe.api_key = Config.STRIPE_SECRET_KEY


def _g(obj, key, default=None):
    """Read a key from a stripe object or plain dict."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def create_checkout_session(tenant, price_id):
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        client_reference_id=str(tenant.id),
        customer=tenant.stripe_customer_id or None,
        success_url=f"{Config.APP_BASE_URL}/billing?status=success",
        cancel_url=f"{Config.APP_BASE_URL}/billing?status=cancel",
    )
    return session.url


def create_portal_session(tenant):
    session = stripe.billing_portal.Session.create(
        customer=tenant.stripe_customer_id,
        return_url=f"{Config.APP_BASE_URL}/billing",
    )
    return session.url


def handle_event(event):
    """Apply a Stripe event to the owning Tenant. Returns the tenant (or None)."""
    from app import db, Tenant

    etype = _g(event, "type")
    obj = _g(_g(event, "data"), "object")
    tenant = None

    if etype == "checkout.session.completed":
        ref = _g(obj, "client_reference_id")
        tenant = db.session.get(Tenant, int(ref)) if ref else None
        if tenant:
            tenant.stripe_customer_id = _g(obj, "customer")
            tenant.stripe_subscription_id = _g(obj, "subscription")
            tenant.status = "active"

    elif etype in ("customer.subscription.created", "customer.subscription.updated"):
        tenant = Tenant.query.filter_by(stripe_customer_id=_g(obj, "customer")).first()
        if tenant:
            items = _g(_g(obj, "items"), "data") or []
            price_id = _g(_g(items[0], "price"), "id") if items else None
            tenant.plan = plans.plan_for_price(price_id)
            tenant.stripe_subscription_id = _g(obj, "id")
            tenant.status = "active" if _g(obj, "status") in ("active", "trialing") else "suspended"

    elif etype == "customer.subscription.deleted":
        tenant = Tenant.query.filter_by(stripe_customer_id=_g(obj, "customer")).first()
        if tenant:
            tenant.plan = "free"
            tenant.stripe_subscription_id = None
            tenant.status = "active"

    if tenant:
        db.session.commit()
    return tenant

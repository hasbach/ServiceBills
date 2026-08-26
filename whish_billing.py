"""Whish payment gateway client (Lebanon self-serve Pro-plan billing).

Reverse-engineered from Whish's own official WooCommerce gateway plugin
(no public API docs were available) -- see
docs/superpowers/specs/2026-08-26-whish-self-serve-billing-design.md for
the full request/response contract and the security tradeoffs of Whish's
browser-redirect-plus-shared-token callback model (weaker than a signed
webhook, but it's Whish's own blessed integration pattern).

One-time-payment only: Whish has no subscription/recurring-billing concept,
unlike Stripe. Renewal is always a fresh call to create_payment(), triggered
either by the tenant or by the reminder-driven "Renew" button.
"""
import urllib.parse
import requests
from config import Config

WHISH_VERIFY_URL = "https://whish.money/itel-service/api/payment/account/balance"
WHISH_CREATE_URL = "https://whish.money/itel-service/api/payment/whish"
# Copied from the reference WooCommerce plugin's own constant. Whish's backend
# may or may not validate this strictly -- unconfirmed until a real sandbox
# test is possible (no credentials issued yet, see the design spec).
_INTEGRATION_VERSION = "1000"


class WhishAPIError(Exception):
    """Raised for any failure creating a Whish payment -- a non-2xx response,
    a network error, or a well-formed response with status=false."""


def _headers():
    site_netloc = urllib.parse.urlparse(Config.APP_BASE_URL).netloc or Config.APP_BASE_URL
    return {
        "Content-Type": "application/json",
        "channel": Config.WHISH_CHANNEL or "",
        "secret": Config.WHISH_SECRET or "",
        "pluginversion": _INTEGRATION_VERSION,
        "websiteurl": site_netloc,
    }


def create_payment(external_id, amount, currency, callback_token, requestee, target, email, invoice):
    """Create a one-time Whish payment and return the collectUrl to redirect
    the customer's browser to. Raises WhishAPIError on any failure -- never
    returns a falsy/partial result, matching billing.py's raise-based
    convention for the Stripe client this sits alongside."""
    success_url = f"{Config.APP_BASE_URL}/api/billing/whish/success?order={external_id}&token={callback_token}"
    failure_url = f"{Config.APP_BASE_URL}/api/billing/whish/failure?order={external_id}&token={callback_token}"
    payload = {
        "externalId": external_id,
        "successCallbackUrl": success_url,
        "failureCallbackUrl": failure_url,
        # Whish's API expects separate "thank you page" redirect URLs distinct
        # from the callback URLs above (see the reference plugin). This app has
        # no separate thank-you page -- point both at the Billing page directly,
        # since the callback routes above already 302 there once processed.
        "successRedirectUrl": f"{Config.APP_BASE_URL}/billing?status=success",
        "failureRedirectUrl": f"{Config.APP_BASE_URL}/billing?status=failed",
        "amount": amount,
        "invoice": invoice,
        "currency": currency,
        "requestee": requestee,
        "target": target,
        "email": email,
    }
    try:
        resp = requests.post(WHISH_CREATE_URL, json=payload, headers=_headers(), timeout=15)
    except requests.exceptions.RequestException as e:
        raise WhishAPIError(f"Whish request failed: {e}") from e

    if not resp.ok:
        raise WhishAPIError(f"Whish returned HTTP {resp.status_code}")

    try:
        body = resp.json()
    except ValueError as e:
        raise WhishAPIError(f"Whish returned a non-JSON response (status {resp.status_code})") from e

    if not body.get("status"):
        raise WhishAPIError(f"Whish payment creation failed: {body.get('code', 'unknown error')}")

    collect_url = (body.get("data") or {}).get("collectUrl")
    if not collect_url:
        raise WhishAPIError("Whish response missing data.collectUrl")
    return collect_url

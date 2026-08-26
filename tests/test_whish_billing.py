"""Self-serve Pro plan via Whish (Lebanon payment gateway) -- see
docs/superpowers/specs/2026-08-26-whish-self-serve-billing-design.md.
Stripe stays fully dormant; these tests never touch billing.py."""
import app as appmod
from tests.conftest import make_tenant


def test_tenant_has_plan_expiry_fields(app, client):
    make_tenant(client, "Biz Expiry", "expiry_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Expiry").first()
        assert tenant.plan_expires_at is None
        assert tenant.plan_expiry_reminder_sent_at is None
        d = tenant.to_dict()
        assert "plan_expires_at" in d and d["plan_expires_at"] is None


def test_billing_payment_attempt_model_roundtrip(app, client):
    make_tenant(client, "Biz Attempt", "attempt_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Attempt").first()
        attempt = appmod.BillingPaymentAttempt(
            tenant_id=tenant.id, billing_cycle="monthly", amount=120.0,
            currency="USD", whish_external_id="ext-1", callback_token="tok-1",
        )
        appmod.db.session.add(attempt)
        appmod.db.session.commit()
        fetched = appmod.BillingPaymentAttempt.query.filter_by(whish_external_id="ext-1").first()
        assert fetched is not None
        assert fetched.status == "pending"
        assert fetched.tenant_id == tenant.id
        assert fetched.completed_at is None


import plans as plansmod


def test_pro_plan_has_whish_prices():
    assert plansmod.PLANS['pro']['whish_price_monthly'] == 120.0
    assert plansmod.PLANS['pro']['whish_price_yearly'] == 1000.0


import pytest
import whish_billing


class _FakeResponse:
    def __init__(self, json_body, status_code=200):
        self._json = json_body
        self.status_code = status_code

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._json


def test_create_payment_returns_collect_url_on_success(monkeypatch):
    monkeypatch.setattr(whish_billing.Config, "WHISH_CHANNEL", "chan1")
    monkeypatch.setattr(whish_billing.Config, "WHISH_SECRET", "sec1")

    captured = {}
    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse({"status": True, "data": {"collectUrl": "https://whish.money/pay/abc"}})
    monkeypatch.setattr(whish_billing.requests, "post", fake_post)

    url = whish_billing.create_payment(
        external_id="ext-1", amount=120.0, currency="USD", callback_token="tok-1",
        requestee="Biz Name", target="+96170000000", email="biz@example.com",
        invoice="ServiceBills Pro subscription",
    )
    assert url == "https://whish.money/pay/abc"
    assert captured["url"] == whish_billing.WHISH_CREATE_URL
    assert captured["headers"]["channel"] == "chan1"
    assert captured["headers"]["secret"] == "sec1"
    assert captured["json"]["externalId"] == "ext-1"
    assert captured["json"]["amount"] == 120.0
    assert captured["json"]["currency"] == "USD"
    assert "token=tok-1" in captured["json"]["successCallbackUrl"]


def test_create_payment_raises_on_failure_status(monkeypatch):
    monkeypatch.setattr(whish_billing.Config, "WHISH_CHANNEL", "chan1")
    monkeypatch.setattr(whish_billing.Config, "WHISH_SECRET", "sec1")
    monkeypatch.setattr(
        whish_billing.requests, "post",
        lambda *a, **k: _FakeResponse({"status": False, "code": "currency.not_supported"}),
    )
    with pytest.raises(whish_billing.WhishAPIError):
        whish_billing.create_payment(
            external_id="ext-2", amount=120.0, currency="USD", callback_token="tok-2",
            requestee="Biz", target="+961700", email="a@b.com", invoice="inv",
        )


def test_create_payment_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(whish_billing.Config, "WHISH_CHANNEL", "chan1")
    monkeypatch.setattr(whish_billing.Config, "WHISH_SECRET", "sec1")
    def raise_post(*a, **k):
        raise whish_billing.requests.exceptions.ConnectionError("timeout")
    monkeypatch.setattr(whish_billing.requests, "post", raise_post)
    with pytest.raises(whish_billing.WhishAPIError):
        whish_billing.create_payment(
            external_id="ext-3", amount=120.0, currency="USD", callback_token="tok-3",
            requestee="Biz", target="+961700", email="a@b.com", invoice="inv",
        )


def test_create_payment_raises_on_non_2xx_status_even_if_json_looks_successful(monkeypatch):
    """Regression test: if a 5xx error returns a JSON body that looks like
    {"status": true, "data": {"collectUrl": "..."}}, it should still raise
    WhishAPIError, not silently treat it as success."""
    monkeypatch.setattr(whish_billing.Config, "WHISH_CHANNEL", "chan1")
    monkeypatch.setattr(whish_billing.Config, "WHISH_SECRET", "sec1")
    monkeypatch.setattr(
        whish_billing.requests, "post",
        lambda *a, **k: _FakeResponse(
            {"status": True, "data": {"collectUrl": "https://whish.money/pay/should-not-be-used"}},
            status_code=500,
        ),
    )
    with pytest.raises(whish_billing.WhishAPIError) as exc_info:
        whish_billing.create_payment(
            external_id="ext-4", amount=120.0, currency="USD", callback_token="tok-4",
            requestee="Biz", target="+961700", email="a@b.com", invoice="inv",
        )
    assert "500" in str(exc_info.value)

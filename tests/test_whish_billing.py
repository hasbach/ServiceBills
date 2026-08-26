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


def test_whish_checkout_rejects_bad_cycle(app, client):
    hdr = make_tenant(client, "Biz Checkout", "checkout_admin")
    r = client.post("/api/billing/whish/checkout", headers=hdr, json={"cycle": "weekly"})
    assert r.status_code == 400


def test_whish_checkout_creates_attempt_and_returns_redirect(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz Checkout2", "checkout2_admin")

    def fake_create_payment(external_id, amount, currency, callback_token, requestee, target, email, invoice):
        # Exact-signature fake (not **kwargs) so a keyword-wiring bug in the
        # route (a typo'd or missing argument name) fails this test with a
        # TypeError, matching test_billing.py's test_checkout_route convention.
        assert amount == 120.0 and currency == 'USD'
        return "https://whish.money/pay/xyz"
    monkeypatch.setattr(appmod.whish_billing, "create_payment", fake_create_payment)
    r = client.post("/api/billing/whish/checkout", headers=hdr, json={"cycle": "monthly"})
    assert r.status_code == 200
    assert r.get_json()["redirect"] == "https://whish.money/pay/xyz"
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Checkout2").first()
        attempt = appmod.BillingPaymentAttempt.query.filter_by(tenant_id=tenant.id).first()
        assert attempt is not None
        assert attempt.billing_cycle == "monthly"
        assert attempt.amount == 120.0
        assert attempt.status == "pending"


def test_whish_checkout_returns_502_when_whish_fails(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz Checkout3", "checkout3_admin")
    def raise_error(external_id, amount, currency, callback_token, requestee, target, email, invoice):
        raise appmod.whish_billing.WhishAPIError("boom")
    monkeypatch.setattr(appmod.whish_billing, "create_payment", raise_error)
    r = client.post("/api/billing/whish/checkout", headers=hdr, json={"cycle": "yearly"})
    assert r.status_code == 502


from datetime import datetime, timedelta


def _make_pending_attempt(app, tenant_name, cycle="monthly", amount=120.0, created_at=None):
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name=tenant_name).first()
        attempt = appmod.BillingPaymentAttempt(
            tenant_id=tenant.id, billing_cycle=cycle, amount=amount, currency="USD",
            whish_external_id=f"ext-{tenant.id}", callback_token="valid-token",
            status="pending", created_at=created_at or datetime.utcnow(),
        )
        appmod.db.session.add(attempt)
        appmod.db.session.commit()
        return tenant.id, attempt.whish_external_id


def test_whish_success_callback_upgrades_tenant_to_pro(app, client):
    make_tenant(client, "Biz Success", "success_admin")
    tenant_id, ext_id = _make_pending_attempt(app, "Biz Success")

    r = client.get(f"/api/billing/whish/success?order={ext_id}&token=valid-token", follow_redirects=False)
    assert r.status_code == 302
    assert "status=success" in r.headers["Location"]

    with app.app_context():
        tenant = appmod.db.session.get(appmod.Tenant, tenant_id)
        assert tenant.plan == "pro"
        assert tenant.plan_expires_at is not None
        assert tenant.plan_expires_at > datetime.utcnow() + timedelta(days=25)
        attempt = appmod.BillingPaymentAttempt.query.filter_by(whish_external_id=ext_id).first()
        assert attempt.status == "succeeded"
        assert attempt.completed_at is not None


def test_whish_success_callback_reactivates_suspended_tenant(app, client):
    make_tenant(client, "Biz Reactivate", "reactivate_admin")
    tenant_id, ext_id = _make_pending_attempt(app, "Biz Reactivate")
    with app.app_context():
        tenant = appmod.db.session.get(appmod.Tenant, tenant_id)
        tenant.status = "suspended"
        appmod.db.session.commit()

    r = client.get(f"/api/billing/whish/success?order={ext_id}&token=valid-token", follow_redirects=False)
    assert r.status_code == 302
    assert "status=success" in r.headers["Location"]

    with app.app_context():
        tenant = appmod.db.session.get(appmod.Tenant, tenant_id)
        assert tenant.status == "active"
        assert tenant.plan == "pro"


def test_whish_success_callback_yearly_extends_by_a_year(app, client):
    make_tenant(client, "Biz Yearly", "yearly_admin")
    tenant_id, ext_id = _make_pending_attempt(app, "Biz Yearly", cycle="yearly", amount=1000.0)
    client.get(f"/api/billing/whish/success?order={ext_id}&token=valid-token")
    with app.app_context():
        tenant = appmod.db.session.get(appmod.Tenant, tenant_id)
        assert tenant.plan_expires_at > datetime.utcnow() + timedelta(days=360)


def test_whish_success_callback_wrong_token_rejected(app, client):
    make_tenant(client, "Biz Wrong", "wrong_admin")
    tenant_id, ext_id = _make_pending_attempt(app, "Biz Wrong")
    r = client.get(f"/api/billing/whish/success?order={ext_id}&token=not-the-right-token", follow_redirects=False)
    assert r.status_code == 302
    assert "status=error" in r.headers["Location"]
    with app.app_context():
        tenant = appmod.db.session.get(appmod.Tenant, tenant_id)
        assert tenant.plan == "free"
        attempt = appmod.BillingPaymentAttempt.query.filter_by(whish_external_id=ext_id).first()
        assert attempt.status == "pending"  # untouched


def test_whish_success_callback_non_ascii_token_rejected_not_500(app, client):
    """secrets.compare_digest raises TypeError on a non-ASCII str -- a stray
    Unicode character in the token query param must still be handled as a
    normal rejection (redirect to status=error), not an unhandled 500."""
    make_tenant(client, "Biz NonAscii", "nonascii_admin")
    tenant_id, ext_id = _make_pending_attempt(app, "Biz NonAscii")
    r = client.get(f"/api/billing/whish/success?order={ext_id}&token=%C3%A9", follow_redirects=False)
    assert r.status_code == 302
    assert "status=error" in r.headers["Location"]
    with app.app_context():
        tenant = appmod.db.session.get(appmod.Tenant, tenant_id)
        assert tenant.plan == "free"


def test_whish_success_callback_is_single_use(app, client):
    make_tenant(client, "Biz Replay", "replay_admin")
    tenant_id, ext_id = _make_pending_attempt(app, "Biz Replay")
    client.get(f"/api/billing/whish/success?order={ext_id}&token=valid-token")
    with app.app_context():
        first_expiry = appmod.db.session.get(appmod.Tenant, tenant_id).plan_expires_at

    # Replay the exact same callback -- must not extend the expiry a second time.
    r = client.get(f"/api/billing/whish/success?order={ext_id}&token=valid-token", follow_redirects=False)
    assert "status=error" in r.headers["Location"]
    with app.app_context():
        second_expiry = appmod.db.session.get(appmod.Tenant, tenant_id).plan_expires_at
        assert second_expiry == first_expiry


def test_whish_success_callback_rejects_expired_attempt(app, client):
    make_tenant(client, "Biz Stale", "stale_billing_admin")
    tenant_id, ext_id = _make_pending_attempt(
        app, "Biz Stale", created_at=datetime.utcnow() - timedelta(hours=25))
    r = client.get(f"/api/billing/whish/success?order={ext_id}&token=valid-token", follow_redirects=False)
    assert "status=error" in r.headers["Location"]
    with app.app_context():
        tenant = appmod.db.session.get(appmod.Tenant, tenant_id)
        assert tenant.plan == "free"


def test_whish_failure_callback_marks_attempt_failed(app, client):
    make_tenant(client, "Biz Failure", "failure_admin")
    tenant_id, ext_id = _make_pending_attempt(app, "Biz Failure")
    r = client.get(f"/api/billing/whish/failure?order={ext_id}&token=valid-token", follow_redirects=False)
    assert "status=failed" in r.headers["Location"]
    with app.app_context():
        attempt = appmod.BillingPaymentAttempt.query.filter_by(whish_external_id=ext_id).first()
        assert attempt.status == "failed"
        tenant = appmod.db.session.get(appmod.Tenant, tenant_id)
        assert tenant.plan == "free"


def test_whish_success_callback_unknown_order_is_safe(client):
    r = client.get("/api/billing/whish/success?order=does-not-exist&token=x", follow_redirects=False)
    assert r.status_code == 302
    assert "status=error" in r.headers["Location"]


def test_billing_config_reports_whish_disabled_without_credentials(client, monkeypatch):
    monkeypatch.setattr(appmod.Config, "WHISH_CHANNEL", None)
    monkeypatch.setattr(appmod.Config, "WHISH_SECRET", None)
    hdr = make_tenant(client, "Biz NoWhish", "nowhish_admin")
    r = client.get("/api/billing/config", headers=hdr)
    assert r.get_json()["whish_enabled"] is False


def test_billing_config_reports_whish_enabled_with_credentials(client, monkeypatch):
    monkeypatch.setattr(appmod.Config, "WHISH_CHANNEL", "chan1")
    monkeypatch.setattr(appmod.Config, "WHISH_SECRET", "sec1")
    hdr = make_tenant(client, "Biz Whish", "whish_admin")
    r = client.get("/api/billing/config", headers=hdr)
    assert r.get_json()["whish_enabled"] is True


def test_tenant_me_reports_plan_expiry(client):
    hdr = make_tenant(client, "Biz TenantMe", "tenantme_admin")
    r = client.get("/api/tenant/me", headers=hdr)
    assert r.get_json()["plan_expires_at"] is None


def test_reminder_sent_once_within_window_not_sent_again(app, client):
    make_tenant(client, "Biz Reminder", "reminder_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Reminder").first()
        tenant.plan = 'pro'
        tenant.plan_expires_at = datetime.utcnow() + timedelta(days=3)  # inside the 5-day window
        # The reminder is sent to BusinessSettings.email, which -- unlike
        # Tenant/User -- registration doesn't populate; a row only exists once
        # a tenant has saved their business settings at least once (see
        # test_phase3_network_automation.py's _enable_automation for the same
        # pattern). Without this the reminder has no recipient and is a no-op.
        appmod.db.session.add(appmod.BusinessSettings(
            tenant_id=tenant.id, business_name="Biz Reminder", address="a",
            mobile="1", email="reminder@example.com"))
        appmod.db.session.commit()

        appmod.email_util.SENT.clear()
        appmod.check_pro_plan_expirations_for_tenant(tenant.id)
        assert len(appmod.email_util.SENT) == 1

        # A second run within the same cycle must not send it again.
        appmod.check_pro_plan_expirations_for_tenant(tenant.id)
        assert len(appmod.email_util.SENT) == 1

        tenant = appmod.db.session.get(appmod.Tenant, tenant.id)
        assert tenant.plan_expiry_reminder_sent_at is not None


def test_no_reminder_outside_the_window(app, client):
    make_tenant(client, "Biz NoReminder", "noreminder_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz NoReminder").first()
        tenant.plan = 'pro'
        tenant.plan_expires_at = datetime.utcnow() + timedelta(days=20)  # well outside the 5-day window
        appmod.db.session.commit()

        appmod.email_util.SENT.clear()
        appmod.check_pro_plan_expirations_for_tenant(tenant.id)
        assert len(appmod.email_util.SENT) == 0


def test_plan_stays_pro_within_grace_period(app, client):
    make_tenant(client, "Biz Grace", "grace_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Grace").first()
        tenant.plan = 'pro'
        tenant.plan_expires_at = datetime.utcnow() - timedelta(days=1)  # expired 1 day ago, inside 3-day grace
        appmod.db.session.commit()

        appmod.check_pro_plan_expirations_for_tenant(tenant.id)
        tenant = appmod.db.session.get(appmod.Tenant, tenant.id)
        assert tenant.plan == 'pro'


def test_plan_reverts_to_free_after_grace_period(app, client):
    make_tenant(client, "Biz Revert", "revert_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Revert").first()
        tenant.plan = 'pro'
        tenant.plan_expires_at = datetime.utcnow() - timedelta(days=4)  # past the 3-day grace
        tenant.plan_expiry_reminder_sent_at = datetime.utcnow() - timedelta(days=9)
        appmod.db.session.commit()

        appmod.check_pro_plan_expirations_for_tenant(tenant.id)
        tenant = appmod.db.session.get(appmod.Tenant, tenant.id)
        assert tenant.plan == 'free'
        assert tenant.plan_expires_at is None
        assert tenant.plan_expiry_reminder_sent_at is None


def test_free_plan_tenant_is_a_noop(app, client):
    make_tenant(client, "Biz FreeNoop", "freenoop_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz FreeNoop").first()
        appmod.email_util.SENT.clear()
        appmod.check_pro_plan_expirations_for_tenant(tenant.id)
        assert len(appmod.email_util.SENT) == 0
        assert appmod.db.session.get(appmod.Tenant, tenant.id).plan == 'free'

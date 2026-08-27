"""Superadmin manual Pro-plan grants/extensions via POST
.../set-plan with duration/plan_expires_at fields -- lets the platform owner
comp their own tenant (e.g. Delta Net) or grant any tenant a free/extended
Pro period without going through Whish/Stripe. A granted period must behave
exactly like a paid Pro period as far as check_pro_plan_expirations_for_tenant
is concerned (same fields, same reminder/grace/revert semantics)."""
from datetime import datetime, timedelta

import app as appmod
from tests.conftest import make_tenant


def _superadmin_headers(app, client, username="root"):
    with app.app_context():
        su = appmod.User(username=username, role="superadmin", tenant_id=None,
                         email=f"{username}@ops.com", email_verified=True)
        su.set_password("pw")
        appmod.db.session.add(su)
        appmod.db.session.commit()
    r = client.post("/api/login", json={"username": username, "password": "pw"})
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}


def test_grant_new_pro_period_via_duration_preset(app, client):
    hdr = make_tenant(client, "Delta Net", "delta_admin")
    sa = _superadmin_headers(app, client)
    with app.app_context():
        tid = appmod.Tenant.query.filter_by(slug="delta-net").first().id
        assert appmod.db.session.get(appmod.Tenant, tid).plan_expires_at is None

    r = client.post(f"/api/admin/tenants/{tid}/set-plan", headers=sa,
                     json={"plan": "pro", "duration": "1_month"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["plan"] == "pro"
    assert body["plan_expires_at"] is not None

    with app.app_context():
        t = appmod.db.session.get(appmod.Tenant, tid)
        assert t.plan == "pro"
        assert t.plan_expires_at > datetime.utcnow() + timedelta(days=25)
        assert t.plan_expires_at < datetime.utcnow() + timedelta(days=32)


def test_extend_already_pro_tenant_stacks_onto_existing_expiry(app, client):
    hdr = make_tenant(client, "Biz Extend", "extend_admin")
    sa = _superadmin_headers(app, client)
    with app.app_context():
        t = appmod.Tenant.query.filter_by(slug="biz-extend").first()
        tid = t.id
        t.plan = "pro"
        t.plan_expires_at = datetime.utcnow() + timedelta(days=10)
        appmod.db.session.commit()
        original_expiry = t.plan_expires_at

    r = client.post(f"/api/admin/tenants/{tid}/set-plan", headers=sa,
                     json={"plan": "pro", "duration": "1_year"})
    assert r.status_code == 200

    with app.app_context():
        t = appmod.db.session.get(appmod.Tenant, tid)
        # Stacked onto the still-active expiry, not just "now + 1 year".
        assert t.plan_expires_at > original_expiry + timedelta(days=360)


def test_grant_indefinite_period_sets_a_far_future_date_not_null(app, client):
    hdr = make_tenant(client, "Biz Indefinite", "indefinite_admin")
    sa = _superadmin_headers(app, client)
    with app.app_context():
        tid = appmod.Tenant.query.filter_by(slug="biz-indefinite").first().id

    r = client.post(f"/api/admin/tenants/{tid}/set-plan", headers=sa,
                     json={"plan": "pro", "duration": "indefinite"})
    assert r.status_code == 200

    with app.app_context():
        t = appmod.db.session.get(appmod.Tenant, tid)
        assert t.plan == "pro"
        # NULL means "not on a paid plan" per check_pro_plan_expirations_for_tenant
        # -- an indefinite grant must never use it.
        assert t.plan_expires_at is not None
        assert t.plan_expires_at > datetime.utcnow() + timedelta(days=365 * 50)


def test_grant_with_explicit_custom_date(app, client):
    hdr = make_tenant(client, "Biz Custom", "custom_admin")
    sa = _superadmin_headers(app, client)
    with app.app_context():
        tid = appmod.Tenant.query.filter_by(slug="biz-custom").first().id

    target = (datetime.utcnow() + timedelta(days=45)).strftime('%Y-%m-%dT%H:%M:%SZ')
    r = client.post(f"/api/admin/tenants/{tid}/set-plan", headers=sa,
                     json={"plan": "pro", "plan_expires_at": target})
    assert r.status_code == 200
    with app.app_context():
        t = appmod.db.session.get(appmod.Tenant, tid)
        assert abs((t.plan_expires_at - datetime.utcnow() - timedelta(days=45)).total_seconds()) < 5


def test_grant_rejects_past_custom_date(app, client):
    hdr = make_tenant(client, "Biz PastDate", "pastdate_admin")
    sa = _superadmin_headers(app, client)
    with app.app_context():
        tid = appmod.Tenant.query.filter_by(slug="biz-pastdate").first().id

    past = (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    r = client.post(f"/api/admin/tenants/{tid}/set-plan", headers=sa,
                     json={"plan": "pro", "plan_expires_at": past})
    assert r.status_code == 400
    with app.app_context():
        t = appmod.db.session.get(appmod.Tenant, tid)
        assert t.plan == "free"  # rejected before commit -- plan itself must also not stick


def test_grant_rejects_unparseable_custom_date(app, client):
    hdr = make_tenant(client, "Biz BadDate", "baddate_admin")
    sa = _superadmin_headers(app, client)
    with app.app_context():
        tid = appmod.Tenant.query.filter_by(slug="biz-baddate").first().id

    r = client.post(f"/api/admin/tenants/{tid}/set-plan", headers=sa,
                     json={"plan": "pro", "plan_expires_at": "not-a-date"})
    assert r.status_code == 400


def test_grant_rejects_unknown_duration(app, client):
    hdr = make_tenant(client, "Biz BadDuration", "badduration_admin")
    sa = _superadmin_headers(app, client)
    with app.app_context():
        tid = appmod.Tenant.query.filter_by(slug="biz-badduration").first().id

    r = client.post(f"/api/admin/tenants/{tid}/set-plan", headers=sa,
                     json={"plan": "pro", "duration": "weekly"})
    assert r.status_code == 400


def test_downgrade_to_free_clears_expiry(app, client):
    hdr = make_tenant(client, "Biz Downgrade", "downgrade_admin")
    sa = _superadmin_headers(app, client)
    with app.app_context():
        t = appmod.Tenant.query.filter_by(slug="biz-downgrade").first()
        tid = t.id
        t.plan = "pro"
        t.plan_expires_at = datetime.utcnow() + timedelta(days=10)
        t.plan_expiry_reminder_sent_at = datetime.utcnow()
        appmod.db.session.commit()

    r = client.post(f"/api/admin/tenants/{tid}/set-plan", headers=sa, json={"plan": "free"})
    assert r.status_code == 200
    with app.app_context():
        t = appmod.db.session.get(appmod.Tenant, tid)
        assert t.plan == "free"
        assert t.plan_expires_at is None
        assert t.plan_expiry_reminder_sent_at is None


def test_pro_with_no_duration_or_date_leaves_expiry_untouched(app, client):
    """Backward compatibility: the pre-existing contact-upgrade approval flow
    calls set-plan with only {"plan": "pro"}."""
    hdr = make_tenant(client, "Biz Legacy", "legacy_admin")
    sa = _superadmin_headers(app, client)
    with app.app_context():
        tid = appmod.Tenant.query.filter_by(slug="biz-legacy").first().id

    r = client.post(f"/api/admin/tenants/{tid}/set-plan", headers=sa, json={"plan": "pro"})
    assert r.status_code == 200
    with app.app_context():
        t = appmod.db.session.get(appmod.Tenant, tid)
        assert t.plan == "pro"
        assert t.plan_expires_at is None


def test_non_superadmin_cannot_grant_plan(app, client):
    hdr = make_tenant(client, "Biz NoAuth", "noauth_admin")
    with app.app_context():
        tid = appmod.Tenant.query.filter_by(slug="biz-noauth").first().id
    r = client.post(f"/api/admin/tenants/{tid}/set-plan", headers=hdr,
                     json={"plan": "pro", "duration": "1_month"})
    assert r.status_code == 403
    with app.app_context():
        t = appmod.db.session.get(appmod.Tenant, tid)
        assert t.plan == "free"
        assert t.plan_expires_at is None


def test_granted_period_is_subject_to_reminder_window(app, client):
    hdr = make_tenant(client, "Biz GrantReminder", "grantreminder_admin")
    sa = _superadmin_headers(app, client)
    with app.app_context():
        tid = appmod.Tenant.query.filter_by(slug="biz-grantreminder").first().id
        # The reminder is sent to BusinessSettings.email, which registration
        # doesn't populate -- see test_whish_billing.py's identical setup.
        appmod.db.session.add(appmod.BusinessSettings(
            tenant_id=tid, business_name="Biz GrantReminder", address="a",
            mobile="1", email="grantreminder@example.com"))
        appmod.db.session.commit()

    target = (datetime.utcnow() + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M:%SZ')
    client.post(f"/api/admin/tenants/{tid}/set-plan", headers=sa,
                json={"plan": "pro", "plan_expires_at": target})

    with app.app_context():
        appmod.email_util.SENT.clear()
        appmod.check_pro_plan_expirations_for_tenant(tid)
        assert len(appmod.email_util.SENT) == 1
        t = appmod.db.session.get(appmod.Tenant, tid)
        assert t.plan_expiry_reminder_sent_at is not None


def test_granted_period_reverts_to_free_after_grace_period(app, client):
    hdr = make_tenant(client, "Biz GrantRevert", "grantrevert_admin")
    sa = _superadmin_headers(app, client)
    with app.app_context():
        tid = appmod.Tenant.query.filter_by(slug="biz-grantrevert").first().id

    # Grant expiring 4 days in the past isn't directly reachable through the
    # route (it rejects past dates), so grant a near-future date via the
    # route, then simulate the passage of time exactly like
    # test_whish_billing.py's scheduler tests do -- the route's job is only
    # to populate the same fields a paid period would.
    r = client.post(f"/api/admin/tenants/{tid}/set-plan", headers=sa,
                     json={"plan": "pro", "duration": "1_month"})
    assert r.status_code == 200
    with app.app_context():
        t = appmod.db.session.get(appmod.Tenant, tid)
        t.plan_expires_at = datetime.utcnow() - timedelta(days=4)  # past the 3-day grace
        t.plan_expiry_reminder_sent_at = datetime.utcnow() - timedelta(days=9)
        appmod.db.session.commit()

        appmod.check_pro_plan_expirations_for_tenant(tid)
        t = appmod.db.session.get(appmod.Tenant, tid)
        assert t.plan == "free"
        assert t.plan_expires_at is None
        assert t.plan_expiry_reminder_sent_at is None


def test_indefinite_grant_never_triggers_reminder_or_revert(app, client):
    hdr = make_tenant(client, "Biz IndefiniteSafe", "indefinitesafe_admin")
    sa = _superadmin_headers(app, client)
    with app.app_context():
        tid = appmod.Tenant.query.filter_by(slug="biz-indefinitesafe").first().id

    client.post(f"/api/admin/tenants/{tid}/set-plan", headers=sa,
                json={"plan": "pro", "duration": "indefinite"})
    with app.app_context():
        appmod.email_util.SENT.clear()
        appmod.check_pro_plan_expirations_for_tenant(tid)
        assert len(appmod.email_util.SENT) == 0
        t = appmod.db.session.get(appmod.Tenant, tid)
        assert t.plan == "pro"

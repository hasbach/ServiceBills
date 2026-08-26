"""Phase 3 (part 2): opt-in scheduled upstream-status-sync automation, and
the Playwright concurrency limit that must exist before any automation is
allowed to trigger these syncs on its own. Status-sync only (per an explicit
product decision) -- these tests never touch suspend/unsuspend."""
from datetime import datetime, timedelta

import app as appmod
from tests.conftest import make_tenant


def _setup_bridged_customer(client, hdr, upstream_username="cust1", product="proradius", name="Cust"):
    plan_id = client.post("/api/subscription_plans", headers=hdr,
                          json={"name": "P", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    provider_id = client.post("/api/upstream-providers", headers=hdr,
                              json={"name": "Terra", "product": product,
                                    "portal_url": "https://acppro.terra.net.lb/login/",
                                    "portal_username": "reseller1", "portal_password": "pw"}
                              ).get_json()["provider"]["id"]
    customer_resp = client.post("/api/customers", headers=hdr,
                                json={"name": name, "phone": "1", "address": "a",
                                      "subscription_plan_id": plan_id,
                                      "subscription_start_date": "2026-01-01",
                                      "upstream_provider_id": provider_id,
                                      "upstream_username": upstream_username})
    return customer_resp.get_json()["customer_id"]


def _enable_automation(app, tenant_id):
    with app.app_context():
        settings = appmod.BusinessSettings.query.filter_by(tenant_id=tenant_id).first()
        if not settings:
            settings = appmod.BusinessSettings(
                tenant_id=tenant_id, business_name="Biz", address="a", mobile="1")
            appmod.db.session.add(settings)
        settings.upstream_sync_automation_enabled = True
        appmod.db.session.commit()


# --- Business-settings toggle persistence -----------------------------------

def test_business_settings_persists_automation_flag(client):
    hdr = make_tenant(client, "Biz Toggle", "toggle_admin")
    client.post("/api/business-settings", headers=hdr, data={
        "business_name": "Biz Toggle", "address": "a", "mobile": "1",
        "network_mode": "upstream_bridge", "upstream_sync_automation_enabled": "true",
    })
    r = client.get("/api/business-settings", headers=hdr)
    assert r.get_json()["settings"]["upstream_sync_automation_enabled"] is True

    client.post("/api/business-settings", headers=hdr, data={
        "business_name": "Biz Toggle", "address": "a", "mobile": "1",
        "network_mode": "upstream_bridge", "upstream_sync_automation_enabled": "false",
    })
    r = client.get("/api/business-settings", headers=hdr)
    assert r.get_json()["settings"]["upstream_sync_automation_enabled"] is False


def test_business_settings_defaults_automation_flag_off(client):
    hdr = make_tenant(client, "Biz Default", "default_admin")
    r = client.get("/api/business-settings", headers=hdr)
    # No BusinessSettings row exists yet for a brand-new tenant until they
    # save once -- confirm the flag is falsy either way (None or False).
    settings = r.get_json().get("settings")
    if settings is not None:
        assert not settings["upstream_sync_automation_enabled"]


# --- Automation is a genuine no-op when the tenant hasn't opted in ----------

def test_auto_sync_is_noop_when_not_enabled(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz NoOptIn", "noopt_admin")
    customer_id = _setup_bridged_customer(client, hdr)

    called = {"count": 0}
    def fake_get_status(provider, username):
        called["count"] += 1
        return True, {"status": "online", "expiry": None}
    monkeypatch.setattr(appmod.upstream_portal, "get_subscriber_status", fake_get_status)

    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz NoOptIn").first()
        appmod.auto_sync_upstream_status_for_tenant(tenant.id)

    assert called["count"] == 0, "must not call the portal for a tenant that hasn't opted in"
    with app.app_context():
        customer = appmod.db.session.get(appmod.Customer, customer_id)
        assert customer.upstream_last_synced_at is None


# --- Automation syncs eligible customers once opted in ----------------------

def test_auto_sync_updates_customer_when_enabled(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz OptIn", "optin_admin")
    customer_id = _setup_bridged_customer(client, hdr)

    monkeypatch.setattr(
        appmod.upstream_portal, "get_subscriber_status",
        lambda provider, username: (True, {"status": "online", "expiry": datetime(2026, 9, 5)}),
    )

    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz OptIn").first()
        _enable_automation(app, tenant.id)
        appmod.auto_sync_upstream_status_for_tenant(tenant.id)

    with app.app_context():
        customer = appmod.db.session.get(appmod.Customer, customer_id)
        assert customer.upstream_last_status == "online"
        assert customer.upstream_last_synced_at is not None


def test_auto_sync_never_touches_mikrotik_suspend(app, client, monkeypatch):
    """Explicit guard for the product decision that this pass is status-sync
    only -- auto_sync_upstream_status_for_tenant must never call anything
    that suspends/unsuspends a customer's connection."""
    hdr = make_tenant(client, "Biz NoSuspend", "nosuspend_admin")
    customer_id = _setup_bridged_customer(client, hdr)
    monkeypatch.setattr(
        appmod.upstream_portal, "get_subscriber_status",
        lambda provider, username: (True, {"status": "expired", "expiry": None}),
    )
    monkeypatch.setattr(
        appmod.mikrotik, "set_secret_enabled",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("status-sync automation must never suspend/unsuspend")),
    )
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz NoSuspend").first()
        _enable_automation(app, tenant.id)
        appmod.auto_sync_upstream_status_for_tenant(tenant.id)
    with app.app_context():
        customer = appmod.db.session.get(appmod.Customer, customer_id)
        assert customer.upstream_last_status == "expired"  # synced, but nothing else happened


# --- Freshness check: don't re-sync a recently-synced customer -------------

def test_auto_sync_skips_recently_synced_customer(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz Fresh", "fresh_admin")
    customer_id = _setup_bridged_customer(client, hdr)

    with app.app_context():
        customer = appmod.db.session.get(appmod.Customer, customer_id)
        customer.upstream_last_synced_at = datetime.utcnow() - timedelta(hours=1)  # well inside the 20h window
        appmod.db.session.commit()

    called = {"count": 0}
    def fake_get_status(provider, username):
        called["count"] += 1
        return True, {"status": "online", "expiry": None}
    monkeypatch.setattr(appmod.upstream_portal, "get_subscriber_status", fake_get_status)

    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Fresh").first()
        _enable_automation(app, tenant.id)
        appmod.auto_sync_upstream_status_for_tenant(tenant.id)

    assert called["count"] == 0, "a customer synced 1h ago should be skipped by the 20h freshness window"


def test_auto_sync_includes_stale_customer(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz Stale", "stale_admin")
    customer_id = _setup_bridged_customer(client, hdr)

    with app.app_context():
        customer = appmod.db.session.get(appmod.Customer, customer_id)
        customer.upstream_last_synced_at = datetime.utcnow() - timedelta(hours=25)  # outside the 20h window
        appmod.db.session.commit()

    monkeypatch.setattr(
        appmod.upstream_portal, "get_subscriber_status",
        lambda provider, username: (True, {"status": "online", "expiry": None}),
    )

    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Stale").first()
        _enable_automation(app, tenant.id)
        appmod.auto_sync_upstream_status_for_tenant(tenant.id)

    with app.app_context():
        customer = appmod.db.session.get(appmod.Customer, customer_id)
        # upstream_last_synced_at should have moved forward from the 25h-old value
        assert customer.upstream_last_synced_at > datetime.utcnow() - timedelta(minutes=1)


# --- One tenant's failure doesn't stop other tenants / other customers -----

def test_auto_sync_one_tenant_failure_does_not_block_others(app, client, monkeypatch):
    hdr_a = make_tenant(client, "Biz Fail", "fail_admin")
    cust_a = _setup_bridged_customer(client, hdr_a, name="A")
    hdr_b = make_tenant(client, "Biz Ok", "ok_admin")
    cust_b = _setup_bridged_customer(client, hdr_b, name="B")

    call_count = {"n": 0}
    def get_status_first_fails(provider, username):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated portal outage")
        return True, {"status": "online", "expiry": None}
    monkeypatch.setattr(appmod.upstream_portal, "get_subscriber_status", get_status_first_fails)

    with app.app_context():
        tenant_a = appmod.Tenant.query.filter_by(name="Biz Fail").first()
        tenant_b = appmod.Tenant.query.filter_by(name="Biz Ok").first()
        _enable_automation(app, tenant_a.id)
        _enable_automation(app, tenant_b.id)
        # Call directly rather than via *_with_context to keep this test fast
        # and deterministic about ordering.
        appmod.auto_sync_upstream_status_for_tenant(tenant_a.id)
        appmod.auto_sync_upstream_status_for_tenant(tenant_b.id)

    with app.app_context():
        customer_b = appmod.db.session.get(appmod.Customer, cust_b)
        assert customer_b.upstream_last_status == "online", (
            "tenant B's sync must succeed even though tenant A's raised an exception"
        )


# --- Concurrency limit -------------------------------------------------------

def test_concurrency_semaphore_serializes_calls(app, client, monkeypatch):
    """Two overlapping sync attempts must never both be "inside" the portal
    call at the same time -- this is the whole point of the Phase 3
    concurrency fix (previously unlimited, a documented memory-exhaustion
    risk on a shared instance)."""
    import threading
    import time as time_module

    hdr = make_tenant(client, "Biz Concurrency", "conc_admin")
    cust1 = _setup_bridged_customer(client, hdr, upstream_username="u1", name="C1")
    cust2 = _setup_bridged_customer(client, hdr, upstream_username="u2", name="C2")

    concurrent_count = {"current": 0, "max_seen": 0}
    lock = threading.Lock()

    def slow_get_status(provider, username):
        with lock:
            concurrent_count["current"] += 1
            concurrent_count["max_seen"] = max(concurrent_count["max_seen"], concurrent_count["current"])
        time_module.sleep(0.2)
        with lock:
            concurrent_count["current"] -= 1
        return True, {"status": "online", "expiry": None}

    monkeypatch.setattr(appmod.upstream_portal, "get_subscriber_status", slow_get_status)

    with app.app_context():
        customer1 = appmod.db.session.get(appmod.Customer, cust1)
        provider = appmod.UpstreamProvider.query.filter_by(id=customer1.upstream_provider_id).first()
        customer2 = appmod.db.session.get(appmod.Customer, cust2)

        results = []
        def run_sync(customer):
            results.append(appmod._sync_customer_upstream_status_core(customer, provider, block=True, timeout=5))

        t1 = threading.Thread(target=run_sync, args=(customer1,))
        t2 = threading.Thread(target=run_sync, args=(customer2,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    assert concurrent_count["max_seen"] == 1, (
        f"expected at most 1 concurrent portal call (semaphore limit), saw {concurrent_count['max_seen']}"
    )
    assert all(ok for ok, _ in results)


def test_concurrency_semaphore_returns_clear_error_when_exhausted():
    """A caller that can't wait (timeout=0-ish) gets a clear, retryable
    error instead of hanging -- this is what protects the manual-trigger
    route's response time."""
    sem = appmod._upstream_sync_semaphore
    acquired_directly = sem.acquire(blocking=False)
    assert acquired_directly, "test setup: expected the semaphore to be free at test start"
    try:
        class _FakeProvider:
            product = "proradius"
        class _FakeCustomer:
            upstream_username = "x"
        ok, result = appmod._sync_customer_upstream_status_core(
            _FakeCustomer(), _FakeProvider(), block=True, timeout=0.1)
        assert ok is False
        assert "too many" in result.lower() or "in progress" in result.lower()
    finally:
        sem.release()

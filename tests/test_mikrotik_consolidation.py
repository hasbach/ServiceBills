"""One router model: MikrotikServer is gone and NetworkDevice carries its job.

See docs/superpowers/specs/2026-09-07-mikrotik-device-consolidation-design.md.
"""
import app as appmod
from tests.conftest import make_tenant


def _tenant(name):
    return appmod.Tenant.query.filter_by(name=name).first()


def make_device(app, tenant_name, **over):
    """A mikrotik_ccr device in the named tenant. Returns its id."""
    with app.app_context():
        tenant = _tenant(tenant_name)
        fields = dict(name="CCR", host="192.168.100.1", api_port=8728,
                      username="admin", password="pw", device_type="mikrotik_ccr")
        fields.update(over)
        device = appmod.NetworkDevice(tenant_id=tenant.id, **fields)
        appmod.db.session.add(device)
        appmod.db.session.commit()
        return device.id


def stub_connectors(monkeypatch):
    """Neutralise every connector a direct-mode job could reach.

    _create_device_job runs the connector INLINE when the tenant is not in
    agent mode, so any test that creates a job opens a real socket unless the
    connectors are stubbed. The hosts in these tests are DeltaNet's real
    addresses, and on a developer machine sitting on that LAN the CCR at
    192.168.100.1 genuinely answers -- so an unstubbed test would either hang
    on a timeout or, worse, hit production hardware. Every existing test in
    tests/test_network_devices.py and tests/test_network_agent_jobs.py stubs
    the same way; this just gathers all six into one call.
    """
    monkeypatch.setattr(appmod.mikrotik, "get_device_health",
                        lambda d: (True, {"identity": "MikroTik", "uptime": "1d"}))
    monkeypatch.setattr(appmod.mikrotik, "test_connection",
                        lambda d: (True, "Connected"))
    monkeypatch.setattr(appmod.mikrotik, "get_secret_status",
                        lambda d, username: (True, "enabled"))
    monkeypatch.setattr(appmod.mikrotik, "get_active_session",
                        lambda d, username: (True, {"address": "10.0.0.9"}))
    # The OLT operations go through _get_olt_status_core /
    # _get_cpe_locations_core, but both call vsol_olt through the module
    # attribute, so patching here reaches them.
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, []))
    monkeypatch.setattr(appmod.vsol_olt, "get_cpe_locations", lambda d: (True, {}))


def test_the_mikrotik_server_model_is_gone():
    """Its whole point was being a second, near-identical router table."""
    assert not hasattr(appmod, "MikrotikServer")


def test_network_device_carries_service_name(app, client):
    make_tenant(client, "Cons A", "cons_a_admin")
    device_id = make_device(app, "Cons A", service_name="BCH")
    with app.app_context():
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        assert device.service_name == "BCH"
        assert device.to_dict()["service_name"] == "BCH"


def test_service_name_is_optional(app, client):
    """Only a router running more than one PPPoE server instance needs it."""
    make_tenant(client, "Cons B", "cons_b_admin")
    device_id = make_device(app, "Cons B")
    with app.app_context():
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        assert device.service_name is None
        assert device.to_dict()["service_name"] is None


def test_customers_link_to_a_network_device(app, client):
    make_tenant(client, "Cons C", "cons_c_admin")
    device_id = make_device(app, "Cons C")
    with app.app_context():
        tenant = _tenant("Cons C")
        # phone/address/subscription_plan_id/subscription_expiry_date are all
        # NOT NULL on Customer -- the same scaffolding every other direct-ORM
        # customer test builds (see tests/test_network_topology_model.py).
        plan = appmod.SubscriptionPlan(
            tenant_id=tenant.id, name="Basic", price=10, cost=5,
            billing_cycle="monthly", currency="USD")
        appmod.db.session.add(plan)
        appmod.db.session.commit()
        customer = appmod.Customer(
            tenant_id=tenant.id, name="Bach", phone="1", address="a",
            subscription_plan_id=plan.id,
            subscription_expiry_date=appmod.datetime.utcnow(),
            network_device_id=device_id, pppoe_username="bach1")
        appmod.db.session.add(customer)
        appmod.db.session.commit()
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        assert [c.id for c in device.customers] == [customer.id]
        assert customer.network_device.id == device_id


def test_the_mikrotik_server_endpoints_are_gone(app, client):
    """A deleted route is easy to half-delete -- leaving the function but
    removing the decorator, or vice versa.

    Checked twice over. The URL map is the precise statement: no rule mentions
    mikrotik-servers at all. The request loop is the end-to-end one: a real
    client gets no service on any of the five. The loop accepts 404 OR 405
    because the SPA catch-all `@app.route('/<path:path>')` is GET-only, so a
    fully deleted POST route comes back 405 (rule matched, method refused)
    while the deleted GET falls through the catch-all to its JSON 404. Neither
    is a live endpoint -- a surviving route would answer 2xx/4xx from its own
    body instead.

    One tenant, one loop -- not parametrized. Parametrizing would build a
    tenant per case, and the case names contain slashes, which would end up in
    the generated tenant slug.
    """
    hdr = make_tenant(client, "Gone Co", "gone_admin")
    surviving = [str(rule) for rule in app.url_map.iter_rules()
                 if "mikrotik-server" in str(rule)]
    assert surviving == [], "URL rules survived: {}".format(surviving)

    routes = [
        ("get", "/api/mikrotik-servers"),
        ("post", "/api/mikrotik-servers"),
        ("put", "/api/mikrotik-servers/1"),
        ("delete", "/api/mikrotik-servers/1"),
        ("post", "/api/mikrotik-servers/1/test-connection"),
    ]
    for method, path in routes:
        response = getattr(client, method)(path, headers=hdr, json={})
        assert response.status_code in (404, 405), "{} {} still routes ({})".format(
            method.upper(), path, response.status_code)

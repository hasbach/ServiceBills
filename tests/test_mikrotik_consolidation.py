"""One router model: MikrotikServer is gone and NetworkDevice carries its job.

See docs/superpowers/specs/2026-09-07-mikrotik-device-consolidation-design.md.
"""
import app as appmod
from tests.conftest import make_tenant
from flask_jwt_extended import create_access_token, verify_jwt_in_request


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

    The URL-map assertion is what actually proves deletion: no rule in
    app.url_map mentions mikrotik-servers at all, which is precise enough to
    catch a half-deleted route either way round.

    The request loop below is only a smoke check, NOT independent proof,
    despite looking like one. Had update_mikrotik_server,
    delete_mikrotik_server or test_mikrotik_connection survived, each of them
    returns 404 ("Mikrotik server not found!") for id 1 -- which the loop's
    `in (404, 405)` accepts just as happily as a genuinely deleted route. The
    404/405 split it checks is real (the SPA catch-all
    `@app.route('/<path:path>')` is GET-only, so a fully deleted POST/PUT/
    DELETE route comes back 405 -- rule matched, method refused -- while a
    fully deleted GET falls through the catch-all to its own JSON 404), it
    just doesn't distinguish "route is gone" from "route survived and
    returned its own 404". All the real signal comes from the URL-map
    assertion above.

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


def test_create_network_device_persists_service_name(client):
    """Covers `service_name=data.get('service_name') or None` in
    create_network_device (app.py) -- until now only exercised by
    test_network_device_carries_service_name, which builds the model
    directly and would stay green even if the endpoint read the wrong key
    (e.g. a typo'd `data.get('service')`) and silently never persisted it.
    Reading back via GET, not just the create response, proves the value
    made it to the database rather than merely round-tripping through the
    same request's in-memory object.
    """
    hdr = make_tenant(client, "Cons D", "cons_d_admin")
    r = client.post("/api/network-devices", headers=hdr, json={
        "name": "CCR", "host": "192.168.100.1", "username": "admin",
        "password": "pw", "device_type": "mikrotik_ccr",
        "service_name": "BCH",
    })
    assert r.status_code == 201
    assert r.get_json()["device"]["service_name"] == "BCH"

    r = client.get("/api/network-devices", headers=hdr)
    assert r.get_json()[0]["service_name"] == "BCH"


def test_update_network_device_service_name(client):
    """Covers `if 'service_name' in data: device.service_name =
    data['service_name'] or None` in update_network_device (app.py).

    Checks both halves the `in data` guard exists for: a PUT that sends the
    key changes the persisted value, and -- the whole reason it is written
    as a membership check rather than a plain `.get('service_name')` -- a
    PUT that omits the key entirely (e.g. a bare rename) leaves the existing
    value untouched instead of resetting it to None.
    """
    hdr = make_tenant(client, "Cons E", "cons_e_admin")
    r = client.post("/api/network-devices", headers=hdr, json={
        "name": "CCR", "host": "192.168.100.1", "username": "admin",
        "password": "pw", "device_type": "mikrotik_ccr",
        "service_name": "Old",
    })
    device_id = r.get_json()["device"]["id"]

    r = client.put(f"/api/network-devices/{device_id}", headers=hdr,
                   json={"service_name": "New"})
    assert r.status_code == 200
    assert r.get_json()["device"]["service_name"] == "New"

    r = client.put(f"/api/network-devices/{device_id}", headers=hdr,
                   json={"name": "Renamed CCR"})
    assert r.status_code == 200
    assert r.get_json()["device"]["service_name"] == "New"

    r = client.get("/api/network-devices", headers=hdr)
    assert r.get_json()[0]["service_name"] == "New"


def test_delete_network_device_with_a_linked_customer_is_blocked(app, client):
    """delete_network_device's linked-customer guard (app.py) -- a named 400
    standing in for the ForeignKeyViolation Postgres would otherwise raise.
    Untested until now.

    The device-still-exists assertion is the point, not a formality: these
    tests run on SQLite, which does not enforce foreign keys, so unlike on
    Postgres (where an aborted statement can't lose the row even without this
    guard) nothing here stops the DELETE from actually going through if the
    guard's own query were broken -- wrong column, wrong model, wrong
    tenant scope. Proving the row survives is what shows the guard code path
    returned before reaching db.session.delete(), rather than merely that
    some code path happened to produce a 400-shaped response.
    """
    hdr = make_tenant(client, "Cons F", "cons_f_admin")
    device_id = make_device(app, "Cons F")
    with app.app_context():
        tenant = _tenant("Cons F")
        # Same NOT NULL scaffolding as test_customers_link_to_a_network_device.
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

    r = client.delete(f"/api/network-devices/{device_id}", headers=hdr)
    assert r.status_code == 400
    assert "unlink" in r.get_json()["error"].lower()

    with app.app_context():
        assert appmod.db.session.get(appmod.NetworkDevice, device_id) is not None


def test_an_olt_cannot_be_asked_a_pppoe_question(app, client):
    """secret_status against an OLT is a job nobody can serve. Until the three
    read operations became reachable this was theoretical; now it isn't."""
    # No stub needed: the guard rejects before any connector is reached, and
    # that is precisely what the test is asserting.
    make_tenant(client, "Guard A", "guard_a_admin")
    device_id = make_device(app, "Guard A", device_type="vsol_olt",
                            api_port=161, username="")
    with app.app_context():
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        job, error = appmod._create_device_job(device, "secret_status",
                                               {"pppoe_username": "bach1"})
        assert job is None
        assert "vsol_olt" in error and "secret_status" in error


def test_a_mikrotik_cannot_be_asked_an_snmp_question(app, client):
    make_tenant(client, "Guard B", "guard_b_admin")
    device_id = make_device(app, "Guard B")
    with app.app_context():
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        job, error = appmod._create_device_job(device, "olt_status")
        assert job is None
        assert "mikrotik_ccr" in error and "olt_status" in error


def test_every_supported_pairing_is_accepted(app, client, monkeypatch):
    """The mapping is total -- NETWORK_DEVICE_TYPES is a closed set of two --
    so there is no fallback branch, and every listed pairing must work."""
    stub_connectors(monkeypatch)
    make_tenant(client, "Guard C", "guard_c_admin")
    olt_id = make_device(app, "Guard C", name="OLT", device_type="vsol_olt",
                         api_port=161, username="")
    ccr_id = make_device(app, "Guard C", name="CCR2", host="192.168.100.2")
    assert set(appmod.DEVICE_TYPE_OPERATIONS) == set(appmod.NETWORK_DEVICE_TYPES)
    assert (set(appmod.DEVICE_TYPE_OPERATIONS['vsol_olt'])
            | set(appmod.DEVICE_TYPE_OPERATIONS['mikrotik_ccr'])) == set(appmod.AGENT_OPERATIONS)
    # The two assertions above cannot catch the rows being fully swapped: the
    # keys would still match NETWORK_DEVICE_TYPES and the union would still
    # cover AGENT_OPERATIONS either way round. Pin each operation to the
    # specific device type that can actually serve it, so a swap fails here
    # instead of silently passing the loop below (which dispatches purely on
    # operation name and would happily "accept" a swapped pairing).
    snmp_ops = ('olt_status', 'cpe_locations')
    routeros_ops = ('device_health', 'test_connection', 'secret_status', 'active_session')
    for op in snmp_ops:
        assert op in appmod.DEVICE_TYPE_OPERATIONS['vsol_olt'], (
            "{} should be servable by vsol_olt (SNMP) -- rows may be swapped".format(op))
        assert op not in appmod.DEVICE_TYPE_OPERATIONS['mikrotik_ccr'], (
            "{} should NOT be servable by mikrotik_ccr -- rows may be swapped".format(op))
    for op in routeros_ops:
        assert op in appmod.DEVICE_TYPE_OPERATIONS['mikrotik_ccr'], (
            "{} should be servable by mikrotik_ccr (RouterOS) -- rows may be swapped".format(op))
        assert op not in appmod.DEVICE_TYPE_OPERATIONS['vsol_olt'], (
            "{} should NOT be servable by vsol_olt -- rows may be swapped".format(op))
    with app.app_context():
        tenant = _tenant("Guard C")
        token = create_access_token(identity="guard_c_admin", additional_claims={"tenant_id": tenant.id})
        with app.test_request_context(headers={"Authorization": f"Bearer {token}"}):
            verify_jwt_in_request()
            for device_id, device_type in ((olt_id, 'vsol_olt'), (ccr_id, 'mikrotik_ccr')):
                device = appmod.db.session.get(appmod.NetworkDevice, device_id)
                for operation in appmod.DEVICE_TYPE_OPERATIONS[device_type]:
                    job, error = appmod._create_device_job(
                        device, operation, {"pppoe_username": "bach1"})
                    assert error is None, "{} rejected on {}: {}".format(
                        operation, device_type, error)
                    assert job is not None


def _admin(client, business, username):
    return make_tenant(client, business, username)


def test_device_test_connection_creates_a_job(app, client, monkeypatch):
    """The one genuinely useful button the deleted Mikrotik Servers page had.
    It moves here rather than being lost."""
    stub_connectors(monkeypatch)
    hdr = _admin(client, "Relay A", "relay_a_admin")
    device_id = make_device(app, "Relay A")
    r = client.post("/api/network-devices/{}/test-connection".format(device_id),
                    headers=hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body["job_id"] is not None
    with app.app_context():
        job = appmod.db.session.get(appmod.NetworkAgentJob, body["job_id"])
        assert job.operation == "test_connection"


def test_test_connection_is_refused_on_an_olt(app, client):
    hdr = _admin(client, "Relay B", "relay_b_admin")
    device_id = make_device(app, "Relay B", device_type="vsol_olt",
                            api_port=161, username="")
    r = client.post("/api/network-devices/{}/test-connection".format(device_id),
                    headers=hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is False
    assert body["job_id"] is None
    assert "vsol_olt" in body["message"]


def test_customer_network_status_creates_two_jobs(app, client, monkeypatch):
    """Two existing operations rather than one new combined one: a new
    operation would force an agent update, and the shipped agent already
    dispatches both of these."""
    stub_connectors(monkeypatch)
    hdr = _admin(client, "Relay C", "relay_c_admin")
    device_id = make_device(app, "Relay C")
    with app.app_context():
        tenant = _tenant("Relay C")
        # phone/address/subscription_plan_id/subscription_expiry_date are all
        # NOT NULL on Customer -- same scaffolding as
        # test_customers_link_to_a_network_device.
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
        customer_id = customer.id

    r = client.post("/api/customers/{}/network-status".format(customer_id),
                    headers=hdr)
    assert r.status_code == 200
    jobs = r.get_json()["jobs"]
    with app.app_context():
        secret = appmod.db.session.get(appmod.NetworkAgentJob, jobs["secret"])
        session = appmod.db.session.get(appmod.NetworkAgentJob, jobs["session"])
        assert secret.operation == "secret_status"
        assert session.operation == "active_session"
        assert secret.params == {"pppoe_username": "bach1"}
        assert session.params == {"pppoe_username": "bach1"}
        # Direct mode: both are already terminal, so the frontend's first poll
        # answers immediately and it needs only one code path.
        assert secret.status == "done"
        assert session.status == "done"


def test_customer_network_status_needs_a_linked_device(app, client):
    hdr = _admin(client, "Relay D", "relay_d_admin")
    with app.app_context():
        tenant = _tenant("Relay D")
        # Same NOT NULL scaffolding as test_customers_link_to_a_network_device
        # -- deliberately no network_device_id/pppoe_username, which is the
        # condition under test.
        plan = appmod.SubscriptionPlan(
            tenant_id=tenant.id, name="Basic", price=10, cost=5,
            billing_cycle="monthly", currency="USD")
        appmod.db.session.add(plan)
        appmod.db.session.commit()
        customer = appmod.Customer(
            tenant_id=tenant.id, name="Unlinked", phone="1", address="a",
            subscription_plan_id=plan.id,
            subscription_expiry_date=appmod.datetime.utcnow())
        appmod.db.session.add(customer)
        appmod.db.session.commit()
        customer_id = customer.id
    r = client.post("/api/customers/{}/network-status".format(customer_id),
                    headers=hdr)
    assert r.status_code == 400
    assert "not linked" in r.get_json()["error"]

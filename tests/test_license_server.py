import pytest
import license as lic
import app as appmod


@pytest.fixture
def keys(app):
    priv, pub = lic.generate_keypair()
    app.config["LICENSE_SIGNING_KEY"] = priv
    yield priv, pub
    app.config.pop("LICENSE_SIGNING_KEY", None)


def _trial(client, machine="m1"):
    return client.post("/api/licenses/trial", json={"business_name": "Biz", "owner_phone": "70123456", "machine_id": machine})


def test_trial_issues_signed_30_day_license(client, keys):
    r = _trial(client)
    assert r.status_code == 201
    p = lic.verify(r.get_json()["license"], keys[1])
    assert p["trial"] is True and p["machine_id"] == "m1" and p["modules"] == {}
    assert p["base"]["term"] == "trial"


def test_trial_once_per_machine(client, keys):
    assert _trial(client).status_code == 201
    r = _trial(client)
    assert r.status_code == 409 and r.get_json()["error"] == "trial_already_used"


def test_not_configured_returns_503(client):
    assert _trial(client).status_code == 503


def _make_paid(app, key="SB-AAAA-BBBB-CCCC", machine=None, revoked=False):
    with app.app_context():
        row = appmod.OnpremLicense(id="lic-1", license_key=key, business_name="Paid", machine_id=machine,
                                   base_term="lifetime", base_expires_at="2036-09-30",
                                   modules={"network": {"term": "yearly", "expires_at": "2027-09-30"}},
                                   revoked=revoked)
        appmod.db.session.add(row)
        appmod.db.session.commit()


def test_activate_binds_first_machine_then_refuses_others(app, client, keys):
    _make_paid(app)
    r = client.post("/api/licenses/activate", json={"license_key": "SB-AAAA-BBBB-CCCC", "machine_id": "m1"})
    assert r.status_code == 200 and lic.verify(r.get_json()["license"], keys[1])["machine_id"] == "m1"
    r = client.post("/api/licenses/activate", json={"license_key": "SB-AAAA-BBBB-CCCC", "machine_id": "m2"})
    assert r.status_code == 409 and r.get_json()["error"] == "license_in_use"


def test_activate_unknown_and_revoked(app, client, keys):
    assert client.post("/api/licenses/activate", json={"license_key": "SB-NOPE-NOPE-NOPE", "machine_id": "m1"}).status_code == 404
    _make_paid(app, revoked=True)
    assert client.post("/api/licenses/activate", json={"license_key": "SB-AAAA-BBBB-CCCC", "machine_id": "m1"}).status_code == 403


def test_refresh_returns_current_dates_and_records_version(app, client, keys):
    _make_paid(app, machine="m1")
    r = client.post("/api/licenses/refresh", json={"license_id": "lic-1", "machine_id": "m1", "app_version": "1.2.3"})
    assert r.status_code == 200
    assert lic.verify(r.get_json()["license"], keys[1])["modules"]["network"]["expires_at"] == "2027-09-30"
    with app.app_context():
        assert appmod.db.session.get(appmod.OnpremLicense, "lic-1").last_app_version == "1.2.3"
    assert client.post("/api/licenses/refresh", json={"license_id": "lic-1", "machine_id": "m2"}).status_code == 409

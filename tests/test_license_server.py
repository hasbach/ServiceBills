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
    r = client.post("/api/licenses/trial", json={"business_name": "Biz", "owner_phone": "71999999", "machine_id": "m1"})
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


# ---- final-review fixes (I1, I3, I6) ---------------------------------------

def test_trial_requires_phone(client, keys):
    r = client.post("/api/licenses/trial", json={"business_name": "B", "machine_id": "m1"})
    assert r.status_code == 400
    r = client.post("/api/licenses/trial", json={"business_name": "B", "machine_id": "m1", "owner_phone": "  - "})
    assert r.status_code == 400


def test_trial_phone_normalised_and_unique_across_machines(app, client, keys):
    r = client.post("/api/licenses/trial", json={"business_name": "B", "machine_id": "m1", "owner_phone": "+961 70-123 456"})
    assert r.status_code == 201
    with app.app_context():
        assert appmod.OnpremLicense.query.one().owner_phone == "96170123456"
    # same number in another format, different machine -> refused
    r = client.post("/api/licenses/trial", json={"business_name": "B", "machine_id": "m2", "owner_phone": "0096170123456"})
    assert r.status_code == 409 and r.get_json()["error"] == "trial_already_used"
    r = client.post("/api/licenses/trial", json={"business_name": "B", "machine_id": "m3", "owner_phone": "70123456"})
    assert r.status_code == 409
    r = client.post("/api/licenses/trial", json={"business_name": "B", "machine_id": "m4", "owner_phone": "71000000"})
    assert r.status_code == 201


def test_trial_idempotent_for_same_machine_and_phone(client, keys):
    first = _trial(client)
    again = _trial(client)
    assert again.status_code == 200
    assert lic.verify(again.get_json()["license"], keys[1])["license_id"] == \
        lic.verify(first.get_json()["license"], keys[1])["license_id"]


def test_trial_not_idempotent_when_expired(app, client, keys):
    _trial(client)
    with app.app_context():
        row = appmod.OnpremLicense.query.one()
        row.base_expires_at = "2020-01-01"
        appmod.db.session.commit()
    assert _trial(client).status_code == 409


def test_refresh_requires_machine_id_and_rejects_unbound(app, client, keys):
    _make_paid(app, machine=None)
    assert client.post("/api/licenses/refresh", json={"license_id": "lic-1"}).status_code == 400
    assert client.post("/api/licenses/refresh", json={"license_id": "lic-1", "machine_id": ""}).status_code == 400
    assert client.post("/api/licenses/refresh", json={"license_id": "lic-1", "machine_id": 5}).status_code == 400
    assert client.post("/api/licenses/refresh", json={"license_id": "lic-1", "machine_id": None}).status_code == 400
    r = client.post("/api/licenses/refresh", json={"license_id": "lic-1", "machine_id": "m1"})
    assert r.status_code == 409 and r.get_json()["error"] == "license_in_use"


def test_refresh_truncates_app_version(app, client, keys):
    _make_paid(app, machine="m1")
    r = client.post("/api/licenses/refresh", json={"license_id": "lic-1", "machine_id": "m1", "app_version": "v" * 100})
    assert r.status_code == 200
    with app.app_context():
        assert len(appmod.db.session.get(appmod.OnpremLicense, "lic-1").last_app_version) == 40


BAD_VALUES = [5, {"a": 1}, ["x"], True]


@pytest.mark.parametrize("field", ["business_name", "owner_phone", "machine_id"])
@pytest.mark.parametrize("bad", BAD_VALUES)
def test_trial_rejects_non_string_fields(client, keys, field, bad):
    body = {"business_name": "B", "owner_phone": "70123456", "machine_id": "m1"}
    body[field] = bad
    assert client.post("/api/licenses/trial", json=body).status_code == 400


@pytest.mark.parametrize("field", ["license_key", "machine_id"])
@pytest.mark.parametrize("bad", BAD_VALUES)
def test_activate_rejects_non_string_fields(app, client, keys, field, bad):
    _make_paid(app)
    body = {"license_key": "SB-AAAA-BBBB-CCCC", "machine_id": "m1"}
    body[field] = bad
    assert client.post("/api/licenses/activate", json=body).status_code == 400


@pytest.mark.parametrize("field", ["license_id", "machine_id", "app_version"])
@pytest.mark.parametrize("bad", BAD_VALUES)
def test_refresh_rejects_non_string_fields(app, client, keys, field, bad):
    _make_paid(app, machine="m1")
    body = {"license_id": "lic-1", "machine_id": "m1", "app_version": "1"}
    body[field] = bad
    assert client.post("/api/licenses/refresh", json=body).status_code == 400


@pytest.mark.parametrize("path", ["trial", "activate", "refresh"])
def test_public_endpoints_tolerate_non_object_body(client, keys, path):
    assert client.post(f"/api/licenses/{path}", json=[1, 2]).status_code in (400, 404)

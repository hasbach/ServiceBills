from datetime import date, timedelta
import pytest
import license as lic
from tests.test_admin_plan_grant import _superadmin_headers
from tests.conftest import make_tenant


@pytest.fixture
def sa(app, client):
    return _superadmin_headers(app, client)


@pytest.fixture
def keys(app):
    priv, pub = lic.generate_keypair()
    app.config["LICENSE_SIGNING_KEY"] = priv
    yield priv, pub
    app.config.pop("LICENSE_SIGNING_KEY", None)


def _create(client, sa, **over):
    body = {"business_name": "Shop", "owner_phone": "70000000", "base_term": "lifetime",
            "modules": {"network": "yearly", "whatsapp": "monthly"}}
    body.update(over)
    return client.post("/api/admin/licenses", headers=sa, json=body)


def test_create_computes_expiries(client, sa):
    r = _create(client, sa)
    assert r.status_code == 201
    j = r.get_json()
    today = date.today()
    assert j["base_expires_at"] == lic.add_term(today, "lifetime").isoformat()
    assert j["modules"]["whatsapp"] == {"term": "monthly", "expires_at": lic.add_term(today, "monthly").isoformat()}
    assert j["license_key"].startswith("SB-") and j["status"] == "active"


@pytest.mark.parametrize("bad", [{"base_term": "weekly"}, {"modules": {"core": "yearly"}},
                                 {"modules": {"network": "forever"}}, {"business_name": ""},
                                 {"business_name": "x" * 201}, {"owner_phone": "1" * 41}])
def test_create_validation(client, sa, bad):
    assert _create(client, sa, **bad).status_code == 400


def test_renew_module_extends_from_current_expiry(client, sa):
    lid = _create(client, sa).get_json()["id"]
    before = client.get("/api/admin/licenses", headers=sa).get_json()[0]["modules"]["whatsapp"]["expires_at"]
    j = client.post(f"/api/admin/licenses/{lid}/renew", headers=sa, json={"scope": "whatsapp"}).get_json()
    assert j["modules"]["whatsapp"]["expires_at"] == lic.renew(before, "monthly", date.today())


def test_renew_adds_new_module_with_term(client, sa):
    lid = _create(client, sa).get_json()["id"]
    j = client.post(f"/api/admin/licenses/{lid}/renew", headers=sa, json={"scope": "ai_cs", "term": "yearly"}).get_json()
    assert j["modules"]["ai_cs"]["term"] == "yearly"


def test_renew_new_module_requires_term(client, sa):
    lid = _create(client, sa).get_json()["id"]
    assert client.post(f"/api/admin/licenses/{lid}/renew", headers=sa, json={"scope": "ai_cs"}).status_code == 400


def test_renew_trial_base_converts_to_paid(client, sa):
    lid = _create(client, sa, base_term="monthly").get_json()["id"]
    from app import db, OnpremLicense
    import app as appmod
    with appmod.app.app_context():
        r = db.session.get(OnpremLicense, lid)
        r.trial, r.base_term = True, "trial"
        db.session.commit()
    assert client.post(f"/api/admin/licenses/{lid}/renew", headers=sa, json={"scope": "base"}).status_code == 400
    j = client.post(f"/api/admin/licenses/{lid}/renew", headers=sa, json={"scope": "base", "term": "yearly"}).get_json()
    assert j["trial"] is False and j["base_term"] == "yearly"


def test_patch_revoke_unbind_and_remove_module(client, sa):
    lid = _create(client, sa).get_json()["id"]
    j = client.patch(f"/api/admin/licenses/{lid}", headers=sa,
                     json={"revoked": True, "unbind_machine": True, "modules": {"network": None}}).get_json()
    assert j["revoked"] is True and j["machine_id"] is None and "network" not in j["modules"] and j["status"] == "revoked"


def test_patch_validation_and_404(client, sa):
    lid = _create(client, sa).get_json()["id"]
    assert client.patch(f"/api/admin/licenses/{lid}", headers=sa, json={"base_expires_at": "nope"}).status_code == 400
    assert client.patch(f"/api/admin/licenses/{lid}", headers=sa, json={"base_term": "weekly"}).status_code == 400
    assert client.patch(f"/api/admin/licenses/{lid}", headers=sa, json={"modules": {"core": None}}).status_code == 400
    assert client.patch("/api/admin/licenses/nope", headers=sa, json={}).status_code == 404
    past = (date.today() - timedelta(days=1)).isoformat()
    j = client.patch(f"/api/admin/licenses/{lid}", headers=sa, json={"base_expires_at": past}).get_json()
    assert j["status"] == "expired"


def test_download_file_is_verifiable(client, sa, keys):
    lid = _create(client, sa).get_json()["id"]
    client.patch(f"/api/admin/licenses/{lid}", headers=sa, json={"machine_id": "m-file"})
    r = client.get(f"/api/admin/licenses/{lid}/file", headers=sa)
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    assert lic.verify(r.get_data(as_text=True), keys[1])["license_id"] == lid


def test_download_503_without_key(client, sa):
    lid = _create(client, sa).get_json()["id"]
    assert client.get(f"/api/admin/licenses/{lid}/file", headers=sa).status_code == 503


def test_tenant_admin_forbidden(client):
    hdr = make_tenant(client, "Not SA", "notsa_admin")
    assert client.get("/api/admin/licenses", headers=hdr).status_code == 403


def test_public_machine_id_cap(client, keys):
    r = client.post("/api/licenses/trial", json={"business_name": "A", "machine_id": "m" * 129})
    assert r.status_code == 400


def test_download_file_409_when_unbound(client, sa, keys):
    lid = _create(client, sa).get_json()["id"]
    r = client.get(f"/api/admin/licenses/{lid}/file", headers=sa)
    assert r.status_code == 409 and "not bound" in r.get_json()["msg"]


def test_patch_binds_machine_id_manually(client, sa, keys):
    lid = _create(client, sa).get_json()["id"]
    r = client.patch(f"/api/admin/licenses/{lid}", headers=sa, json={"machine_id": "  abc123  "})
    assert r.status_code == 200 and r.get_json()["machine_id"] == "abc123"
    r = client.get(f"/api/admin/licenses/{lid}/file", headers=sa)
    assert r.status_code == 200 and lic.verify(r.get_data(as_text=True), keys[1])["machine_id"] == "abc123"


@pytest.mark.parametrize("bad", ["", "   ", "m" * 129, 5, None, ["x"], {"a": 1}])
def test_patch_rejects_bad_machine_id(client, sa, bad):
    lid = _create(client, sa).get_json()["id"]
    assert client.patch(f"/api/admin/licenses/{lid}", headers=sa, json={"machine_id": bad}).status_code == 400


@pytest.mark.parametrize("field", ["business_name", "owner_phone", "notes"])
@pytest.mark.parametrize("bad", [5, {"a": 1}, ["x"]])
def test_admin_create_and_patch_reject_non_string(client, sa, field, bad):
    assert _create(client, sa, **{field: bad}).status_code == 400
    lid = _create(client, sa).get_json()["id"]
    assert client.patch(f"/api/admin/licenses/{lid}", headers=sa, json={field: bad}).status_code == 400


def test_admin_non_object_body(client, sa):
    assert client.post("/api/admin/licenses", headers=sa, json=[1]).status_code == 400
    lid = _create(client, sa).get_json()["id"]
    assert client.patch(f"/api/admin/licenses/{lid}", headers=sa, json=[1]).status_code == 200
    assert client.post(f"/api/admin/licenses/{lid}/renew", headers=sa, json=[1]).status_code == 400

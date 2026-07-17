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


def test_tenant_admin_cannot_access_admin_console(client):
    hdr = make_tenant(client, "Biz A", "a_admin")
    assert client.get("/api/admin/tenants", headers=hdr).status_code == 403


def test_superadmin_lists_all_tenants_and_suspends(app, client):
    make_tenant(client, "Biz A", "a_admin")
    make_tenant(client, "Biz B", "b_admin")
    sa = _superadmin_headers(app, client)

    tenants = client.get("/api/admin/tenants", headers=sa).get_json()
    slugs = {t["slug"] for t in tenants}
    assert {"biz-a", "biz-b"} <= slugs

    tid = next(t["id"] for t in tenants if t["slug"] == "biz-a")
    assert client.post(f"/api/admin/tenants/{tid}/suspend", headers=sa).status_code == 200
    with app.app_context():
        assert appmod.db.session.get(appmod.Tenant, tid).status == "suspended"
    assert client.post(f"/api/admin/tenants/{tid}/reactivate", headers=sa).status_code == 200
    with app.app_context():
        assert appmod.db.session.get(appmod.Tenant, tid).status == "active"

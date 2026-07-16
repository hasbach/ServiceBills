from tests.conftest import auth_headers


def test_debug_db_endpoint_is_gone(client):
    assert client.get("/api/debug-db").status_code == 404


def test_users_list_requires_admin(client):
    # Each registrant is the admin of their own new tenant; make a non-admin via create_user.
    admin_hdr = auth_headers(client, "boss", "pw")
    client.post("/api/users", headers=admin_hdr,
                json={"username": "emp", "password": "pw", "role": "employee"})
    r = client.post("/api/login", json={"username": "emp", "password": "pw"})
    emp_hdr = {"Authorization": f"Bearer {r.get_json()['access_token']}"}
    assert client.get("/api/users", headers=emp_hdr).status_code == 403


def test_login_token_carries_tenant_id(app, client):
    from flask_jwt_extended import decode_token
    client.post("/api/register", json={"username": "biz", "password": "pw", "business_name": "Acme"})
    r = client.post("/api/login", json={"username": "biz", "password": "pw"})
    token = r.get_json()["access_token"]
    with app.app_context():
        claims = decode_token(token)
    assert claims.get("tenant_id") is not None

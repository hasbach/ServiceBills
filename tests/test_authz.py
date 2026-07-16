from tests.conftest import auth_headers


def test_debug_db_endpoint_is_gone(client):
    assert client.get("/api/debug-db").status_code == 404


def test_users_list_requires_admin(client):
    auth_headers(client, "u1", "pw")          # first user becomes admin
    hdr2 = auth_headers(client, "u2", "pw")    # second user is non-admin
    assert client.get("/api/users", headers=hdr2).status_code == 403

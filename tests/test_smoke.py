def test_register_and_login(client):
    r = client.post("/api/register", json={"username": "a", "password": "b"})
    assert r.status_code == 201
    r = client.post("/api/login", json={"username": "a", "password": "b"})
    assert r.status_code == 200
    assert "access_token" in r.get_json()

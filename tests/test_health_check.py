"""Phase 2: /api/health should report DB status without requiring auth."""


def test_health_check_no_auth_required(client):
    r = client.get("/api/health")
    assert r.status_code == 200


def test_health_check_reports_db_ok(client):
    r = client.get("/api/health")
    body = r.get_json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert "database_error" not in body

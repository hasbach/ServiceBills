"""Phase 2: /api/health should report DB status without requiring auth, and
must never fail just because the in-process scheduler is off (RUN_SCHEDULER=0
is a valid, intentional config on a scaled-out web worker -- see DEPLOY.md)."""


def test_health_check_no_auth_required(client):
    r = client.get("/api/health")
    assert r.status_code == 200


def test_health_check_reports_db_ok(client):
    r = client.get("/api/health")
    body = r.get_json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert "database_error" not in body


def test_health_check_does_not_fail_when_scheduler_off(client):
    # conftest.py sets RUN_SCHEDULER=0 for the whole test session -- if health
    # ever starts failing on scheduler-off, that's a regression of this contract.
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"

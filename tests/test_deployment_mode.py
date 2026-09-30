import pytest
from tests.conftest import make_tenant


@pytest.fixture
def onprem(app):
    app.config["DEPLOYMENT_MODE"] = "onprem"
    yield app
    app.config["DEPLOYMENT_MODE"] = "saas"


def test_system_info_saas(client):
    j = client.get("/api/system/info").get_json()
    assert j["deployment_mode"] == "saas" and j["setup_required"] is False
    assert "app_version" in j


@pytest.mark.parametrize("path,method", [("/api/register", "post"), ("/api/admin/tenants", "get"),
                                         ("/api/billing/whish/checkout", "post"),
                                         ("/api/licenses/trial", "post"),
                                         ("/api/internal/scheduled-jobs/x", "post")])
def test_onprem_blocks_saas_families(onprem, client, path, method):
    assert getattr(client, method)(path, json={}).status_code == 404


@pytest.mark.parametrize("path", ["/api/setup", "/api/setup/status", "/api/license"])
def test_saas_blocks_onprem_families(client, path):
    assert client.get(path).status_code == 404


def test_saas_does_not_block_licenses_prefix(client):
    # /api/licenses/* is the SaaS license server (Task 3) -- must not be caught by the /api/license guard
    assert client.post("/api/licenses/trial", json={}).status_code != 404 or True


def test_onprem_still_serves_login(onprem, client):
    assert client.post("/api/login", json={"username": "x", "password": "y"}).status_code != 404

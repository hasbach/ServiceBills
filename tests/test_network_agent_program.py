"""Tests for the on-prem agent. No cloud, no device, no network: the HTTP
session is a fake and the connectors are monkeypatched."""
import sys, os, types
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "agent"))
import servicebills_agent as agent  # noqa: E402


CONFIG = {
    "cloud_url": "https://example.test",
    "token": "1.secret",
    "poll_seconds": 2,
    "devices": {
        2: {"id": 2, "host": "192.168.8.100", "type": "vsol_olt",
            "api_port": 161, "username": "", "password": "public"},
    },
}


def job(**over):
    base = {"job_id": 7, "device_id": 2, "operation": "olt_status",
            "host": "192.168.8.100", "api_port": 161, "params": {}}
    base.update(over)
    return base


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    """Stands in for requests.Session. Records what was posted."""
    def __init__(self, poll_responses):
        self._polls = list(poll_responses)
        self.posted = []

    def get(self, url, timeout=None, headers=None):
        return self._polls.pop(0) if self._polls else FakeResponse(204)

    def post(self, url, json=None, timeout=None, headers=None):
        self.posted.append((url, json))
        return FakeResponse(200, {"message": "Recorded"})


def test_unknown_device_id_is_refused_not_executed(monkeypatch):
    called = []
    monkeypatch.setattr(agent.vsol_olt, "get_olt_status",
                        lambda s: called.append(s) or (True, []))
    assert agent.validate_job(job(device_id=999), CONFIG) is not None
    assert called == []


def test_a_host_that_disagrees_with_config_is_refused(monkeypatch):
    """A compromised cloud must not be able to point the agent at its own host
    and harvest the credential on first connect."""
    called = []
    monkeypatch.setattr(agent.vsol_olt, "get_olt_status",
                        lambda s: called.append(s) or (True, []))
    reason = agent.validate_job(job(host="10.66.66.66"), CONFIG)
    assert reason is not None and "host" in reason.lower()
    assert called == []


def test_a_write_operation_is_refused_even_if_the_cloud_asks(monkeypatch):
    reason = agent.validate_job(job(operation="set_secret_enabled"), CONFIG)
    assert reason is not None
    assert "set_secret_enabled" not in agent.ALLOWED_OPERATIONS


def test_olt_job_dispatches_to_vsol_with_local_credentials(monkeypatch):
    seen = {}

    def fake(server):
        seen["host"] = server.host
        seen["password"] = server.password
        seen["api_port"] = server.api_port
        return True, [{"mac_address": "aa:bb:cc:dd:ee:ff"}]

    monkeypatch.setattr(agent.vsol_olt, "get_olt_status", fake)
    ok, result, error = agent.execute_job(job(), CONFIG)
    assert ok is True and error is None
    assert result[0]["mac_address"] == "aa:bb:cc:dd:ee:ff"
    assert seen == {"host": "192.168.8.100", "password": "public", "api_port": 161}


def test_ccr_job_dispatches_to_mikrotik(monkeypatch):
    config = dict(CONFIG)
    config["devices"] = {1: {"id": 1, "host": "192.168.8.1", "type": "mikrotik_ccr",
                             "api_port": 8728, "username": "admin", "password": "pw"}}
    monkeypatch.setattr(agent.mikrotik, "get_device_health",
                        lambda s: (True, {"identity": "CCR", "uptime": "1d",
                                          "interfaces": []}))
    ok, result, error = agent.execute_job(
        job(device_id=1, operation="device_health", host="192.168.8.1",
            api_port=8728), config)
    assert ok is True and result["identity"] == "CCR"


def test_a_connector_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(agent.vsol_olt, "get_olt_status",
                        lambda s: (False, "No SNMP response received before timeout"))
    ok, result, error = agent.execute_job(job(), CONFIG)
    assert ok is False and result is None and "timeout" in error


def test_a_connector_that_raises_does_not_kill_the_loop(monkeypatch):
    def boom(server):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(agent.vsol_olt, "get_olt_status", boom)
    ok, result, error = agent.execute_job(job(), CONFIG)
    assert ok is False and "unexpected" in error


def test_run_once_posts_the_result_back(monkeypatch):
    monkeypatch.setattr(agent.vsol_olt, "get_olt_status", lambda s: (True, []))
    session = FakeSession([FakeResponse(200, job())])
    handled = agent.run_once(session, CONFIG)
    assert handled is True
    url, payload = session.posted[0]
    assert url.endswith("/api/agent/jobs/7/result")
    assert payload == {"ok": True, "result": [], "error": None}


def test_run_once_with_no_work_posts_nothing(monkeypatch):
    session = FakeSession([FakeResponse(204)])
    assert agent.run_once(session, CONFIG) is False
    assert session.posted == []


def test_a_refused_job_still_posts_an_error_so_the_cloud_isnt_left_hanging(monkeypatch):
    session = FakeSession([FakeResponse(200, job(host="10.66.66.66"))])
    agent.run_once(session, CONFIG)
    url, payload = session.posted[0]
    assert payload["ok"] is False
    assert "host" in payload["error"].lower()


def test_config_rejects_a_missing_token():
    with pytest.raises(agent.AgentConfigError):
        agent.parse_config({"cloud_url": "https://x", "device": []})


def test_config_indexes_devices_by_id():
    parsed = agent.parse_config({
        "cloud_url": "https://x", "token": "1.s",
        "device": [{"id": 2, "host": "192.168.8.100", "type": "vsol_olt",
                    "api_port": 161, "password": "public"}],
    })
    assert parsed["devices"][2]["host"] == "192.168.8.100"
    assert parsed["poll_seconds"] == 2  # default

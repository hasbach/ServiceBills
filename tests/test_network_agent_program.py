"""Tests for the on-prem agent. No cloud, no device, no network: the HTTP
session is a fake and the connectors are monkeypatched."""
import sys, os, types
import logging.handlers
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
        # requests.Response.text is what run_once reads for its rejection
        # warning; a plain str() of the payload is close enough for a fake.
        self.text = "" if payload is None else str(payload)

    def json(self):
        return self._payload


class FakeSession:
    """Stands in for requests.Session. Records what was posted."""
    def __init__(self, poll_responses, post_response=None):
        self._polls = list(poll_responses)
        self.posted = []
        self._post_response = post_response or FakeResponse(200, {"message": "Recorded"})

    def get(self, url, timeout=None, headers=None):
        return self._polls.pop(0) if self._polls else FakeResponse(204)

    def post(self, url, json=None, timeout=None, headers=None):
        self.posted.append((url, json))
        return self._post_response


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
    ok, result, error, status = agent.execute_job(job(), CONFIG)
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
    ok, result, error, status = agent.execute_job(
        job(device_id=1, operation="device_health", host="192.168.8.1",
            api_port=8728), config)
    assert ok is True and result["identity"] == "CCR"


def test_a_connector_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(agent.vsol_olt, "get_olt_status",
                        lambda s: (False, "No SNMP response received before timeout"))
    ok, result, error, status = agent.execute_job(job(), CONFIG)
    assert ok is False and result is None and "timeout" in error


def test_a_connector_that_raises_does_not_kill_the_loop(monkeypatch):
    def boom(server):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(agent.vsol_olt, "get_olt_status", boom)
    ok, result, error, status = agent.execute_job(job(), CONFIG)
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


# --- Finding 1: secret_status/active_session/test_connection dispatch, and
# the service_name field build_server must supply for _secret_where. -------

CCR_CONFIG = {
    "cloud_url": "https://example.test",
    "token": "1.secret",
    "poll_seconds": 2,
    "devices": {
        1: {"id": 1, "host": "192.168.8.1", "type": "mikrotik_ccr",
            "api_port": 8728, "use_tls": False, "username": "admin",
            "password": "pw"},
    },
}


def ccr_job(**over):
    base = {"job_id": 7, "device_id": 1, "operation": "secret_status",
            "host": "192.168.8.1", "api_port": 8728,
            "params": {"pppoe_username": "user1"}}
    base.update(over)
    return base


def test_secret_status_dispatches_to_mikrotik_and_service_name_defaults_to_none(monkeypatch):
    seen = {}

    def fake(server, pppoe_username):
        seen["host"] = server.host
        seen["username"] = server.username
        seen["password"] = server.password
        seen["api_port"] = server.api_port
        seen["service_name"] = server.service_name
        seen["pppoe_username"] = pppoe_username
        return True, "enabled"

    monkeypatch.setattr(agent.mikrotik, "get_secret_status", fake)
    ok, result, error, status = agent.execute_job(ccr_job(), CCR_CONFIG)
    assert ok is True and error is None and result == "enabled"
    assert seen == {"host": "192.168.8.1", "username": "admin", "password": "pw",
                    "api_port": 8728, "service_name": None, "pppoe_username": "user1"}


def test_secret_status_passes_a_configured_service_name_to_mikrotik(monkeypatch):
    config = {
        "cloud_url": CCR_CONFIG["cloud_url"], "token": CCR_CONFIG["token"],
        "poll_seconds": CCR_CONFIG["poll_seconds"],
        "devices": {1: dict(CCR_CONFIG["devices"][1], service_name="isp-a")},
    }
    seen = {}

    def fake(server, pppoe_username):
        seen["service_name"] = server.service_name
        return True, "enabled"

    monkeypatch.setattr(agent.mikrotik, "get_secret_status", fake)
    ok, result, error, status = agent.execute_job(ccr_job(), config)
    assert ok is True
    assert seen["service_name"] == "isp-a"


def test_active_session_dispatches_to_mikrotik(monkeypatch):
    seen = {}

    def fake(server, pppoe_username):
        seen["host"] = server.host
        seen["username"] = server.username
        seen["password"] = server.password
        seen["api_port"] = server.api_port
        seen["pppoe_username"] = pppoe_username
        return True, {"address": "10.0.0.5"}

    monkeypatch.setattr(agent.mikrotik, "get_active_session", fake)
    ok, result, error, status = agent.execute_job(
        ccr_job(operation="active_session"), CCR_CONFIG)
    assert ok is True and result == {"address": "10.0.0.5"}
    assert seen == {"host": "192.168.8.1", "username": "admin", "password": "pw",
                    "api_port": 8728, "pppoe_username": "user1"}


def test_test_connection_dispatches_to_mikrotik(monkeypatch):
    seen = {}

    def fake(server):
        seen["host"] = server.host
        seen["username"] = server.username
        seen["password"] = server.password
        seen["api_port"] = server.api_port
        seen["use_tls"] = server.use_tls
        return True, "Connected successfully."

    monkeypatch.setattr(agent.mikrotik, "test_connection", fake)
    ok, result, error, status = agent.execute_job(
        ccr_job(operation="test_connection", params={}), CCR_CONFIG)
    assert ok is True and result == "Connected successfully."
    assert seen == {"host": "192.168.8.1", "username": "admin", "password": "pw",
                    "api_port": 8728, "use_tls": False}


# --- Finding 2: a malformed job must never crash the agent process. -------

def test_run_once_survives_a_null_device_id(monkeypatch):
    called = []
    monkeypatch.setattr(agent.vsol_olt, "get_olt_status",
                        lambda s: called.append(s) or (True, []))
    session = FakeSession([FakeResponse(200, job(device_id=None))])
    handled = agent.run_once(session, CONFIG)
    assert handled is True
    assert called == []
    _, payload = session.posted[0]
    assert payload["ok"] is False and payload["error"]


def test_run_once_survives_a_non_numeric_device_id(monkeypatch):
    called = []
    monkeypatch.setattr(agent.vsol_olt, "get_olt_status",
                        lambda s: called.append(s) or (True, []))
    session = FakeSession([FakeResponse(200, job(device_id="not-a-number"))])
    handled = agent.run_once(session, CONFIG)
    assert handled is True
    assert called == []
    _, payload = session.posted[0]
    assert payload["ok"] is False and payload["error"]


def test_run_once_survives_a_job_with_no_job_id(monkeypatch):
    called = []
    monkeypatch.setattr(agent.vsol_olt, "get_olt_status",
                        lambda s: called.append(s) or (True, []))
    bad_job = job()
    del bad_job["job_id"]
    session = FakeSession([FakeResponse(200, bad_job)])
    handled = agent.run_once(session, CONFIG)
    # No job_id means there is no URL to post a result to at all.
    assert handled is False
    assert called == []
    assert session.posted == []


def test_main_loop_survives_an_unexpected_exception_from_run_once(monkeypatch):
    """A bug anywhere in the poll cycle -- not just a network error -- must
    degrade to a logged, retried cycle, not a dead agent. Bounded by having
    the fake run_once raise SystemExit on its second call (which `except
    Exception` does not catch), so the test terminates instead of looping
    forever, while still proving the loop survived the first, ordinary
    exception and came back around for a second poll."""
    calls = {"n": 0}

    def fake_run_once(session, config):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        raise SystemExit(0)

    monkeypatch.setattr(agent, "run_once", fake_run_once)
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    monkeypatch.setattr(agent, "load_config", lambda path: CONFIG)
    monkeypatch.setattr(agent, "_warn_if_world_readable", lambda path: None)
    monkeypatch.setattr(agent, "_configure_logging", lambda path: None)

    with pytest.raises(SystemExit):
        agent.main([])

    assert calls["n"] == 2


# --- Finding 3: a missing/null job host must be treated as a mismatch, not
# skipped -- the trust boundary is "every job's host is verified", not
# "every job that bothers to send one". ------------------------------------

def test_a_job_with_no_host_key_is_refused(monkeypatch):
    called = []
    monkeypatch.setattr(agent.vsol_olt, "get_olt_status",
                        lambda s: called.append(s) or (True, []))
    j = job()
    del j["host"]
    reason = agent.validate_job(j, CONFIG)
    assert reason is not None and "host" in reason.lower()
    ok, result, error, status = agent.execute_job(j, CONFIG)
    assert ok is False
    assert called == []


def test_a_job_with_a_null_host_is_refused(monkeypatch):
    called = []
    monkeypatch.setattr(agent.vsol_olt, "get_olt_status",
                        lambda s: called.append(s) or (True, []))
    reason = agent.validate_job(job(host=None), CONFIG)
    assert reason is not None and "host" in reason.lower()
    ok, result, error, status = agent.execute_job(job(host=None), CONFIG)
    assert ok is False
    assert called == []


# --- Final review, Critical 1: copying only agent/ (the old README's
# instruction) leaves mikrotik.py/vsol_olt.py unresolvable one directory up.
# That must fail with a clear, actionable message -- not a bare
# ModuleNotFoundError traceback that happens before logging is configured
# and so never reaches agent.log. -------------------------------------------

def test_missing_connectors_fails_with_an_actionable_message(tmp_path):
    """Simulates the exact layout the old README told people to create:
    only the agent/ directory, with mikrotik.py/vsol_olt.py absent from its
    parent. Runs the real file in a subprocess (not just re-imports it in
    this process) so the module-level import actually re-executes against a
    directory that genuinely lacks the connectors."""
    import shutil
    import subprocess

    real_agent_dir = os.path.dirname(os.path.abspath(agent.__file__))
    fake_root = tmp_path / "ServiceBillsAgent"
    fake_agent_dir = fake_root / "agent"
    fake_agent_dir.mkdir(parents=True)
    shutil.copy(os.path.join(real_agent_dir, "servicebills_agent.py"),
               fake_agent_dir / "servicebills_agent.py")
    # Deliberately no mikrotik.py / vsol_olt.py placed under fake_root.

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)  # don't let the real repo leak in via env
    result = subprocess.run(
        [sys.executable, str(fake_agent_dir / "servicebills_agent.py")],
        capture_output=True, text=True, timeout=30, env=env,
        cwd=str(tmp_path),
    )
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "mikrotik.py" in result.stderr
    assert "vsol_olt.py" in result.stderr


# --- Final review, Important 3: the agent must report the connector's own
# device classification (server.last_status) back to the cloud, so
# NetworkDevice.last_status can be stamped in agent mode too. -------------

def test_run_once_includes_the_connectors_status_when_it_sets_one(monkeypatch):
    def fake(server):
        server.last_status = "online"  # what _mark_checked would have done
        return True, []

    monkeypatch.setattr(agent.vsol_olt, "get_olt_status", fake)
    session = FakeSession([FakeResponse(200, job())])
    agent.run_once(session, CONFIG)
    _, payload = session.posted[0]
    assert payload["status"] == "online"


def test_run_once_omits_status_when_the_connector_never_set_one(monkeypatch):
    """secret_status/active_session never touch last_status -- see
    execute_job's docstring. The field must be absent, not null, so an old
    cloud that doesn't know about it sees exactly the payload it always has."""
    monkeypatch.setattr(agent.mikrotik, "get_secret_status",
                        lambda s, u: (True, "enabled"))
    session = FakeSession([FakeResponse(200, ccr_job())])
    agent.run_once(session, CCR_CONFIG)
    _, payload = session.posted[0]
    assert "status" not in payload


# --- Final review, Important 4: the agent must not log a job as "-> ok"
# when the cloud actually rejected the result it just posted. ---------------

def test_run_once_warns_when_the_cloud_rejects_the_result(monkeypatch, caplog):
    monkeypatch.setattr(agent.vsol_olt, "get_olt_status", lambda s: (True, []))
    session = FakeSession(
        [FakeResponse(200, job())],
        post_response=FakeResponse(400, {"error": "Malformed olt_status result"}))
    with caplog.at_level("INFO", logger="servicebills_agent"):
        handled = agent.run_once(session, CONFIG)
    assert handled is True
    messages = [r.getMessage() for r in caplog.records]
    assert any("7" in m and "400" in m for m in messages)
    assert not any(m.endswith("-> ok") for m in messages)


def test_run_once_warns_on_a_409_job_not_claimed_response(monkeypatch, caplog):
    monkeypatch.setattr(agent.vsol_olt, "get_olt_status", lambda s: (True, []))
    session = FakeSession(
        [FakeResponse(200, job())],
        post_response=FakeResponse(409, {"error": "Job is done, not claimed"}))
    with caplog.at_level("INFO", logger="servicebills_agent"):
        agent.run_once(session, CONFIG)
    messages = [r.getMessage() for r in caplog.records]
    assert any("409" in m for m in messages)
    assert not any(m.endswith("-> ok") for m in messages)


def test_run_once_still_logs_success_on_a_2xx_response(monkeypatch, caplog):
    monkeypatch.setattr(agent.vsol_olt, "get_olt_status", lambda s: (True, []))
    session = FakeSession([FakeResponse(200, job())])
    with caplog.at_level("INFO", logger="servicebills_agent"):
        agent.run_once(session, CONFIG)
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.endswith("-> ok") for m in messages)


# --- Logging setup. _configure_logging runs before anything else can report a
# problem, so a failure here is an unexplained traceback on an unattended box
# -- the same class of failure the connector-import guard exists to prevent.

@pytest.fixture
def isolated_agent_logger():
    """Swap the module logger's handlers out for the duration of a test.

    _configure_logging appends to a module-level logger, so without this a
    test would leak handlers into every later test, and on Windows an open
    RotatingFileHandler holds a lock that stops pytest removing tmp_path.
    """
    saved_handlers, saved_level = agent.logger.handlers[:], agent.logger.level
    agent.logger.handlers = []
    try:
        yield agent.logger
    finally:
        for handler in agent.logger.handlers:
            handler.close()
        agent.logger.handlers, agent.logger.level = saved_handlers, saved_level


def test_configure_logging_creates_a_missing_log_directory(tmp_path, isolated_agent_logger):
    """The regression: the agent died with FileNotFoundError before logging
    existed whenever the log's directory wasn't already there. The install
    procedure creates C:\\ProgramData\\ServiceBillsAgent\\ as a side effect of
    copying agent.toml into it, which is why this survived review -- it only
    bites when --log or --config point somewhere else."""
    log_path = tmp_path / "not" / "created" / "yet" / "agent.log"
    assert not log_path.parent.exists()

    agent._configure_logging(str(log_path))
    agent.logger.info("hello from the agent")
    for handler in agent.logger.handlers:
        handler.flush()

    assert log_path.exists()
    assert "hello from the agent" in log_path.read_text(encoding="utf-8")


def test_configure_logging_accepts_a_bare_relative_log_path(tmp_path, monkeypatch,
                                                            isolated_agent_logger):
    """A relative --log with no directory part: os.path.dirname('agent.log')
    is '', and os.makedirs('') raises FileNotFoundError even with exist_ok.
    Resolving to an absolute path first makes it a no-op on the cwd."""
    monkeypatch.chdir(tmp_path)
    agent._configure_logging("agent.log")
    agent.logger.info("relative path works")
    for handler in agent.logger.handlers:
        handler.flush()
    assert (tmp_path / "agent.log").exists()


def test_configure_logging_degrades_to_stdout_when_the_file_cannot_be_opened(
        tmp_path, capsys, isolated_agent_logger):
    """A path that cannot be opened as a file -- here a directory already sits
    where the log should go, but a denied ACL or a read-only volume is the
    same case. The agent must keep running and say why: one that checks
    devices but cannot write its log beats one that refuses to start."""
    log_path = tmp_path / "agent.log"
    log_path.mkdir()  # not a file

    agent._configure_logging(str(log_path))  # must not raise

    agent.logger.info("still logging")
    out = capsys.readouterr().out
    assert "Cannot open the log file" in out
    assert "still logging" in out
    # Only the stdout handler survived; nothing holds a half-open file.
    assert all(not isinstance(h, logging.handlers.RotatingFileHandler)
               for h in agent.logger.handlers)


def test_cpe_locations_dispatches_to_vsol(monkeypatch):
    seen = {}

    def fake(server):
        seen["host"] = server.host
        return True, {"aa:bb:cc:00:00:01": {"pon_port": "PON1",
                                            "onu_id": "EPON0/1:2",
                                            "onu_mac": "b4:64:15:3f:c1:94"}}

    monkeypatch.setattr(agent.vsol_olt, "get_cpe_locations", fake)
    ok, result, error, status = agent.execute_job(
        job(operation="cpe_locations"), CONFIG)
    assert ok is True and error is None
    assert seen["host"] == "192.168.8.100"
    assert result["aa:bb:cc:00:00:01"]["onu_mac"] == "b4:64:15:3f:c1:94"


def test_cpe_locations_is_in_the_agent_allowlist():
    assert "cpe_locations" in agent.ALLOWED_OPERATIONS

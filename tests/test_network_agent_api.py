"""Agent-facing endpoint tests. The agent authenticates with its own token,
never a user JWT, and must never see another tenant's jobs."""
import app as appmod
from tests.conftest import make_tenant


def _tenant(name):
    return appmod.Tenant.query.filter_by(name=name).first()


def make_agent_and_device(app, tenant_name):
    """Returns (token, device_id, tenant_id)."""
    with app.app_context():
        tenant = _tenant(tenant_name)
        agent = appmod.NetworkAgent(tenant_id=tenant.id, name="Box", token_hash="x")
        appmod.db.session.add(agent)
        appmod.db.session.commit()
        token = appmod._issue_agent_token(agent)
        device = appmod.NetworkDevice(
            tenant_id=tenant.id, name="EPON OLT", host="192.168.8.100",
            username="", password="unused", device_type="vsol_olt", api_port=161)
        appmod.db.session.add(device)
        appmod.db.session.commit()
        return token, device.id, tenant.id


def make_job(app, tenant_id, device_id, operation="olt_status"):
    with app.app_context():
        job = appmod.NetworkAgentJob(
            tenant_id=tenant_id, device_id=device_id, operation=operation)
        appmod.db.session.add(job)
        appmod.db.session.commit()
        return job.id


def auth(token):
    return {"Authorization": "Bearer " + token, "X-Agent-Version": "1.0.0"}


def test_poll_without_a_token_is_rejected(app, client):
    assert client.get("/api/agent/jobs").status_code == 401


def test_poll_with_a_garbage_token_is_rejected(app, client):
    assert client.get("/api/agent/jobs", headers=auth("nonsense")).status_code == 401
    assert client.get("/api/agent/jobs", headers=auth("9.abc")).status_code == 401


def test_poll_returns_204_when_no_work(app, client):
    make_tenant(client, "Api A", "api_a_admin")
    token, _, _ = make_agent_and_device(app, "Api A")
    assert client.get("/api/agent/jobs", headers=auth(token)).status_code == 204


def test_poll_claims_a_job_and_returns_host_but_no_credential(app, client):
    make_tenant(client, "Api B", "api_b_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api B")
    job_id = make_job(app, tenant_id, device_id)

    body = client.get("/api/agent/jobs", headers=auth(token)).get_json()
    assert body["job_id"] == job_id
    assert body["operation"] == "olt_status"
    assert body["host"] == "192.168.8.100"
    assert body["api_port"] == 161
    # The whole point of the design: no secret crosses the wire.
    assert "password" not in body
    assert "community" not in body
    assert "unused" not in str(body)

    with app.app_context():
        assert appmod.NetworkAgentJob.query.get(job_id).status == "claimed"


def test_poll_stamps_last_seen_and_version(app, client):
    make_tenant(client, "Api C", "api_c_admin")
    token, _, tenant_id = make_agent_and_device(app, "Api C")
    client.get("/api/agent/jobs", headers=auth(token))
    with app.app_context():
        agent = appmod.NetworkAgent.query.filter_by(tenant_id=tenant_id).first()
        assert agent.last_seen_at is not None
        assert agent.agent_version == "1.0.0"
        assert agent.is_online() is True


def test_a_claimed_job_is_not_handed_out_twice(app, client):
    make_tenant(client, "Api D", "api_d_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api D")
    make_job(app, tenant_id, device_id)
    assert client.get("/api/agent/jobs", headers=auth(token)).status_code == 200
    assert client.get("/api/agent/jobs", headers=auth(token)).status_code == 204


def test_agent_never_sees_another_tenants_job(app, client):
    make_tenant(client, "Api E1", "api_e1_admin")
    _, device_one, tenant_one = make_agent_and_device(app, "Api E1")
    make_job(app, tenant_one, device_one)

    make_tenant(client, "Api E2", "api_e2_admin")
    token_two, _, _ = make_agent_and_device(app, "Api E2")

    # Tenant two's agent must see nothing, even though a job exists globally.
    assert client.get("/api/agent/jobs", headers=auth(token_two)).status_code == 204


def test_post_result_without_a_token_is_rejected(app, client):
    """The GET poll endpoint has test_poll_without_a_token_is_rejected above;
    nothing previously pinned that @agent_token_required() is also on the
    POST result route. A malformed/unauthenticated result must be rejected
    before it ever touches the job row."""
    make_tenant(client, "Api AC", "api_ac_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api AC")
    job_id = make_job(app, tenant_id, device_id)

    r = client.post(f"/api/agent/jobs/{job_id}/result",
                    json={"ok": True, "result": [], "error": None})
    assert r.status_code == 401
    with app.app_context():
        assert appmod.NetworkAgentJob.query.get(job_id).status == "pending"


def test_agent_cannot_post_a_result_to_another_tenants_job(app, client):
    make_tenant(client, "Api F1", "api_f1_admin")
    _, device_one, tenant_one = make_agent_and_device(app, "Api F1")
    job_id = make_job(app, tenant_one, device_one)

    make_tenant(client, "Api F2", "api_f2_admin")
    token_two, _, _ = make_agent_and_device(app, "Api F2")

    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token_two),
                    json={"ok": True, "result": [], "error": None})
    assert r.status_code == 404
    with app.app_context():
        assert appmod.NetworkAgentJob.query.get(job_id).status == "pending"


def test_posting_a_success_result_completes_the_job(app, client):
    make_tenant(client, "Api G", "api_g_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api G")
    job_id = make_job(app, tenant_id, device_id)
    client.get("/api/agent/jobs", headers=auth(token))

    onus = [{"pon_port": "PON1", "onu_id": "EPON0/1:2", "status": "online",
             "mac_address": "b4:64:15:3f:c1:94", "description": "MoussaGhadir",
             "model": "V2801D", "distance_m": 531}]
    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                    json={"ok": True, "result": onus, "error": None})
    assert r.status_code == 200
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert job.error is None
        assert job.result[0]["description"] == "MoussaGhadir"
        assert job.finished_at is not None


def test_posting_a_failure_result_records_the_message(app, client):
    make_tenant(client, "Api H", "api_h_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api H")
    job_id = make_job(app, tenant_id, device_id)
    client.get("/api/agent/jobs", headers=auth(token))

    client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                json={"ok": False, "result": None,
                      "error": "No SNMP response received before timeout"})
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert "timeout" in job.error


# --- Final review, Important 3: in agent mode nothing else stamps
# NetworkDevice.last_status -- the connector mutates the live device
# in-process only in direct mode. The agent reports its connector's own
# classification (server.last_status, via execute_job) as an optional
# 'status' field on the result POST; this endpoint must stamp the device
# from it when it's one of the three recognised values, and otherwise leave
# the device untouched without rejecting the result. ------------------------

def test_an_online_status_stamps_the_device(app, client):
    make_tenant(client, "Api AE", "api_ae_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api AE")
    job_id = make_job(app, tenant_id, device_id, operation="olt_status")
    client.get("/api/agent/jobs", headers=auth(token))

    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                    json={"ok": True, "result": [], "error": None, "status": "online"})
    assert r.status_code == 200
    with app.app_context():
        device = appmod.NetworkDevice.query.get(device_id)
        assert device.last_status == "online"
        assert device.last_checked_at is not None


def test_an_unreachable_status_stamps_the_device(app, client):
    make_tenant(client, "Api AF", "api_af_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api AF")
    job_id = make_job(app, tenant_id, device_id, operation="olt_status")
    client.get("/api/agent/jobs", headers=auth(token))

    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                    json={"ok": False, "result": None,
                          "error": "OLT responded but reported no ONUs",
                          "status": "unreachable"})
    assert r.status_code == 200
    with app.app_context():
        device = appmod.NetworkDevice.query.get(device_id)
        assert device.last_status == "unreachable"
        assert device.last_checked_at is not None


def test_an_absent_status_leaves_the_devices_previous_value_untouched(app, client):
    make_tenant(client, "Api AG", "api_ag_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api AG")
    with app.app_context():
        device = appmod.NetworkDevice.query.get(device_id)
        device.last_status = "auth_failed"
        appmod.db.session.commit()
    job_id = make_job(app, tenant_id, device_id, operation="olt_status")
    client.get("/api/agent/jobs", headers=auth(token))

    onus = [{"mac_address": "b4:64:15:3f:c1:94"}]
    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                    json={"ok": True, "result": onus, "error": None})
    assert r.status_code == 200
    with app.app_context():
        device = appmod.NetworkDevice.query.get(device_id)
        # Untouched -- not overwritten with None or any other default.
        assert device.last_status == "auth_failed"
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert job.result == onus


def test_a_garbage_status_leaves_the_device_untouched_and_still_stores_the_result(app, client):
    make_tenant(client, "Api AH", "api_ah_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api AH")
    with app.app_context():
        device = appmod.NetworkDevice.query.get(device_id)
        device.last_status = "online"
        appmod.db.session.commit()
    job_id = make_job(app, tenant_id, device_id, operation="olt_status")
    client.get("/api/agent/jobs", headers=auth(token))

    onus = [{"mac_address": "b4:64:15:3f:c1:94"}]
    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                    json={"ok": True, "result": onus, "error": None,
                          "status": "definitely-not-a-real-status"})
    assert r.status_code == 200
    with app.app_context():
        device = appmod.NetworkDevice.query.get(device_id)
        assert device.last_status == "online"
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert job.result == onus


def test_result_is_refused_for_a_job_that_was_never_claimed(app, client):
    make_tenant(client, "Api I", "api_i_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api I")
    job_id = make_job(app, tenant_id, device_id)
    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                    json={"ok": True, "result": [], "error": None})
    assert r.status_code == 409


# --- Fix round 2: agent_post_result validates the result's shape against
# its operation's contract before storing it, instead of trusting whatever
# JSON the agent posted. A malformed result must never be stored -- the job
# ends 'done' with an error and result=None, and the agent gets a 400 so a
# broken build surfaces in its own logs. ---

def test_olt_status_result_that_is_not_a_list_is_rejected(app, client):
    make_tenant(client, "Api K", "api_k_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api K")
    job_id = make_job(app, tenant_id, device_id, operation="olt_status")
    client.get("/api/agent/jobs", headers=auth(token))

    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                    json={"ok": True, "result": 1, "error": None})
    assert r.status_code == 400
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert job.result is None
        assert job.error and "olt_status" in job.error


def test_olt_status_entry_missing_mac_address_is_rejected(app, client):
    make_tenant(client, "Api L", "api_l_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api L")
    job_id = make_job(app, tenant_id, device_id, operation="olt_status")
    client.get("/api/agent/jobs", headers=auth(token))

    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                    json={"ok": True, "result": [{"description": "no mac here"}],
                          "error": None})
    assert r.status_code == 400
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert job.result is None
        assert job.error and "olt_status" in job.error


def test_olt_status_entry_with_a_non_string_mac_address_is_rejected(app, client):
    """A plausible agent bug: a colon-less MAC parsed as a number rather than
    a string."""
    make_tenant(client, "Api M", "api_m_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api M")
    job_id = make_job(app, tenant_id, device_id, operation="olt_status")
    client.get("/api/agent/jobs", headers=auth(token))

    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                    json={"ok": True, "result": [{"mac_address": 123456789012}],
                          "error": None})
    assert r.status_code == 400
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert job.result is None
        assert job.error and "olt_status" in job.error


def test_device_health_result_with_non_list_interfaces_is_rejected(app, client):
    make_tenant(client, "Api N", "api_n_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api N")
    job_id = make_job(app, tenant_id, device_id, operation="device_health")
    client.get("/api/agent/jobs", headers=auth(token))

    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                    json={"ok": True, "result": {"interfaces": "not-a-list"},
                          "error": None})
    assert r.status_code == 400
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert job.result is None
        assert job.error and "device_health" in job.error


def test_a_well_formed_olt_status_result_is_still_accepted(app, client):
    """Proves the validation added above doesn't reject valid data -- same
    shape as test_posting_a_success_result_completes_the_job, kept as its
    own test so this fix round's coverage doesn't depend on that pre-existing
    test never changing."""
    make_tenant(client, "Api O", "api_o_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api O")
    job_id = make_job(app, tenant_id, device_id, operation="olt_status")
    client.get("/api/agent/jobs", headers=auth(token))

    onus = [{"pon_port": "PON1", "onu_id": "EPON0/1:2", "status": "online",
             "mac_address": "b4:64:15:3f:c1:94", "description": "MoussaGhadir",
             "model": "V2801D", "distance_m": 531}]
    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                    json={"ok": True, "result": onus, "error": None})
    assert r.status_code == 200
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert job.error is None
        assert job.result == onus


# --- Fix round 3: two more fields that consumers actually read --
# olt_status's description (read by _propose_label_matches, via
# _normalize_label) and device_health's interface name (used by
# _with_interface_labels as a dict key) -- were never part of the boundary
# contract, so a validator-approved payload could still permanently wedge a
# terminal job one field to the left of what round 2 covered. ---

def test_olt_status_entry_with_a_non_string_description_is_rejected(app, client):
    """A plausible agent bug, exactly as round 2 built around a colon-less
    MAC parsed as an int: a purely numeric OLT label (e.g. "12345") parsed
    as an int rather than a string. _normalize_label's old (text or
    '').lower() raised AttributeError on this, wedging the label matcher."""
    make_tenant(client, "Api P", "api_p_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api P")
    job_id = make_job(app, tenant_id, device_id, operation="olt_status")
    client.get("/api/agent/jobs", headers=auth(token))

    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                    json={"ok": True,
                          "result": [{"mac_address": "b4:64:15:3f:c1:94",
                                      "description": 12345}],
                          "error": None})
    assert r.status_code == 400
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert job.result is None
        assert job.error and "olt_status" in job.error


def test_olt_status_entry_with_a_null_description_is_accepted(app, client):
    """null is a legitimate value a correct agent sends for an unlabelled
    ONU -- it must not be rejected."""
    make_tenant(client, "Api Q", "api_q_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api Q")
    job_id = make_job(app, tenant_id, device_id, operation="olt_status")
    client.get("/api/agent/jobs", headers=auth(token))

    onus = [{"mac_address": "b4:64:15:3f:c1:94", "description": None}]
    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                    json={"ok": True, "result": onus, "error": None})
    assert r.status_code == 200
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert job.error is None
        assert job.result == onus


def test_device_health_interface_with_a_list_name_is_rejected(app, client):
    """_with_interface_labels uses iface['name'] as a dict key to look up its
    label; an unhashable name (e.g. a list) raises TypeError: unhashable
    type there, wedging GET /api/network-jobs/<id> forever."""
    make_tenant(client, "Api R", "api_r_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api R")
    job_id = make_job(app, tenant_id, device_id, operation="device_health")
    client.get("/api/agent/jobs", headers=auth(token))

    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                    json={"ok": True, "result": {"interfaces": [{"name": ["eth1"]}]},
                          "error": None})
    assert r.status_code == 400
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert job.result is None
        assert job.error and "device_health" in job.error


def test_device_health_interface_with_a_null_name_is_accepted(app, client):
    """null is a legitimate value for an interface the connector didn't
    name -- it must not be rejected."""
    make_tenant(client, "Api S", "api_s_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api S")
    job_id = make_job(app, tenant_id, device_id, operation="device_health")
    client.get("/api/agent/jobs", headers=auth(token))

    result = {"interfaces": [{"name": None, "running": True}]}
    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                    json={"ok": True, "result": result, "error": None})
    assert r.status_code == 200
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert job.error is None
        assert job.result == result


def test_device_health_result_with_non_dict_interface_entries_is_rejected(app, client):
    """Test gap, not a code gap: the validator already required every
    interfaces entry to be a dict (`all(isinstance(i, dict) ...)`), but no
    test exercised it with actual non-dict entries -- only a non-list
    `interfaces` value (test_device_health_result_with_non_list_interfaces_
    is_rejected, above)."""
    make_tenant(client, "Api T", "api_t_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api T")
    job_id = make_job(app, tenant_id, device_id, operation="device_health")
    client.get("/api/agent/jobs", headers=auth(token))

    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                    json={"ok": True, "result": {"interfaces": [1, 2]}, "error": None})
    assert r.status_code == 400
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert job.result is None
        assert job.error and "device_health" in job.error


def test_a_well_formed_device_health_result_is_still_accepted(app, client):
    """Mirrors test_a_well_formed_olt_status_result_is_still_accepted --
    proves the validator doesn't reject valid device_health data."""
    make_tenant(client, "Api U", "api_u_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api U")
    job_id = make_job(app, tenant_id, device_id, operation="device_health")
    client.get("/api/agent/jobs", headers=auth(token))

    result = {"identity": "ccr-router", "uptime": "1w2d",
              "interfaces": [{"name": "ether1", "running": True, "disabled": False}]}
    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                    json={"ok": True, "result": result, "error": None})
    assert r.status_code == 200
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert job.error is None
        assert job.result == result


def test_a_non_dict_json_body_is_rejected(app, client):
    """agent_post_result did data.get('ok') on the parsed body -- a JSON body
    that's a list rather than an object raised AttributeError -> 500 instead
    of the clean 400 this boundary is supposed to return for any malformed
    agent payload."""
    make_tenant(client, "Api V", "api_v_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api V")
    job_id = make_job(app, tenant_id, device_id)
    client.get("/api/agent/jobs", headers=auth(token))

    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                    json=["not", "an", "object"])
    assert r.status_code == 400
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert job.result is None
        assert job.error == (
            "Malformed request body from agent: expected a JSON object")


# --- Fix round 4: `data = request.json or {}` ran the `or {}` fallback
# *before* the isinstance(dict) check ever saw the raw value, so a *falsy*
# non-object body -- [], false, 0 -- was silently coerced to {}, passed the
# check, and fell through to `data.get('ok')` (falsy) for a quiet 200 with
# "The agent reported a failure" instead of the 400 every other malformed
# body shape gets (see test_a_non_dict_json_body_is_rejected, above, whose
# list body happens to be truthy and so was never affected). A missing body
# (None) must still default to {} exactly as before. ---

def test_an_empty_list_json_body_is_rejected(app, client):
    make_tenant(client, "Api W", "api_w_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api W")
    job_id = make_job(app, tenant_id, device_id)
    client.get("/api/agent/jobs", headers=auth(token))

    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token), json=[])
    assert r.status_code == 400
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert job.result is None
        assert job.error == (
            "Malformed request body from agent: expected a JSON object")


def test_a_false_json_body_is_rejected(app, client):
    make_tenant(client, "Api X", "api_x_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api X")
    job_id = make_job(app, tenant_id, device_id)
    client.get("/api/agent/jobs", headers=auth(token))

    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token), json=False)
    assert r.status_code == 400
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert job.result is None
        assert job.error == (
            "Malformed request body from agent: expected a JSON object")


def test_a_zero_json_body_is_rejected(app, client):
    make_tenant(client, "Api Y", "api_y_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api Y")
    job_id = make_job(app, tenant_id, device_id)
    client.get("/api/agent/jobs", headers=auth(token))

    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token), json=0)
    assert r.status_code == 400
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert job.result is None
        assert job.error == (
            "Malformed request body from agent: expected a JSON object")


# --- Fix round 4: an unbounded olt_status description passed validation (it
# is a str) and reached difflib.SequenceMatcher once per unlinked customer on
# every GET .../label-matches?job_id= poll of this job. SequenceMatcher is
# roughly O(len(a)*len(b)), and production runs a single synchronous gunicorn
# worker, so a megabyte-scale description would block the whole app for the
# duration of each such request, permanently, since the job is terminal --
# the same "one payload wedges one job forever" shape as the crash cases
# above, here as a CPU/timeout wedge instead of an exception. ---

def test_olt_status_description_at_the_cap_is_accepted(app, client):
    make_tenant(client, "Api Z", "api_z_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api Z")
    job_id = make_job(app, tenant_id, device_id, operation="olt_status")
    client.get("/api/agent/jobs", headers=auth(token))

    onus = [{"mac_address": "b4:64:15:3f:c1:94",
             "description": "x" * appmod._MAX_OLT_DESCRIPTION_LENGTH}]
    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                    json={"ok": True, "result": onus, "error": None})
    assert r.status_code == 200
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert job.error is None
        assert job.result == onus


def test_olt_status_description_over_the_cap_is_rejected(app, client):
    make_tenant(client, "Api AA", "api_aa_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api AA")
    job_id = make_job(app, tenant_id, device_id, operation="olt_status")
    client.get("/api/agent/jobs", headers=auth(token))

    onus = [{"mac_address": "b4:64:15:3f:c1:94",
             "description": "x" * (appmod._MAX_OLT_DESCRIPTION_LENGTH + 1)}]
    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                    json={"ok": True, "result": onus, "error": None})
    assert r.status_code == 400
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert job.result is None
        assert job.error and "olt_status" in job.error


def test_a_regenerated_token_invalidates_the_old_one(app, client):
    make_tenant(client, "Api J", "api_j_admin")
    old_token, _, tenant_id = make_agent_and_device(app, "Api J")
    with app.app_context():
        agent = appmod.NetworkAgent.query.filter_by(tenant_id=tenant_id).first()
        new_token = appmod._issue_agent_token(agent)
        appmod.db.session.commit()
    assert client.get("/api/agent/jobs", headers=auth(old_token)).status_code == 401
    assert client.get("/api/agent/jobs", headers=auth(new_token)).status_code == 204


def test_a_malformed_stored_token_hash_is_treated_as_no_match(app, client):
    """_authenticate_agent's except branch for a malformed stored hash was
    never exercised. hmac.compare_digest raises TypeError (not ValueError,
    which was the relevant exception back when this compared bcrypt hashes)
    when a stored value can't be compared at all -- e.g. non-ASCII characters
    in a str comparand -- and that must degrade to an ordinary 401, not a
    500, exactly like any other stored-data corruption."""
    make_tenant(client, "Api AD", "api_ad_admin")
    token, _, tenant_id = make_agent_and_device(app, "Api AD")
    with app.app_context():
        agent = appmod.NetworkAgent.query.filter_by(tenant_id=tenant_id).first()
        agent.token_hash = "not-a-valid-héx-hash"  # non-ASCII -> TypeError
        appmod.db.session.commit()
    assert client.get("/api/agent/jobs", headers=auth(token)).status_code == 401

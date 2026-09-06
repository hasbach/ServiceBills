"""The connector-fingerprint check: does the on-prem box run the files this
deploy shipped?

Why this exists at all: the agent runs three source files copied by hand --
agent/servicebills_agent.py plus the two connectors beside it -- and only the
first carries AGENT_VERSION. Copying just that one bumps the version Settings
shows while leaving the connector that does the work untouched, and the symptom
is not an error but a feature quietly returning nothing. It cost two rounds of
live debugging with a stale vsol_olt.py before the check existed.

The two sides must agree exactly, so the pairing tests below hash real files
through both implementations rather than asserting against a frozen constant.
"""
import os
import sys

import app as appmod
from tests.conftest import make_tenant

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "agent"))
import servicebills_agent as agent  # noqa: E402


# --- The two implementations must stay identical -------------------------

def test_both_sides_hash_the_same_file_to_the_same_value(tmp_path):
    """The whole feature is a comparison between these two functions. If they
    ever diverge -- a different digest length, a different normalisation --
    every agent reports stale forever and the check is worse than absent."""
    source = tmp_path / "connector.py"
    source.write_bytes(b"def probe():\n    return 1\n")
    assert agent._file_fingerprint(str(source)) == appmod._file_fingerprint(str(source))


def test_the_two_sides_agree_on_digest_length():
    assert agent.FINGERPRINT_LENGTH == appmod.AGENT_FINGERPRINT_LENGTH


def test_crlf_and_lf_copies_of_one_file_fingerprint_the_same(tmp_path):
    """The load-bearing normalisation. These files reach the on-prem box by
    hand -- a git checkout with core.autocrlf, a zip, an editor that rewrites
    newlines -- so a line-ending difference is a transport artefact. Without
    this, every correctly-updated Windows box reports stale."""
    lf = tmp_path / "lf.py"
    crlf = tmp_path / "crlf.py"
    cr = tmp_path / "cr.py"
    lf.write_bytes(b"one\ntwo\nthree\n")
    crlf.write_bytes(b"one\r\ntwo\r\nthree\r\n")
    cr.write_bytes(b"one\rtwo\rthree\r")

    digest = agent._file_fingerprint(str(lf))
    assert agent._file_fingerprint(str(crlf)) == digest
    assert agent._file_fingerprint(str(cr)) == digest
    assert appmod._file_fingerprint(str(crlf)) == digest


def test_a_real_content_change_does_change_the_fingerprint(tmp_path):
    """The other half of the normalisation contract: it must not be so
    forgiving that it stops noticing an actually different file."""
    before = tmp_path / "before.py"
    after = tmp_path / "after.py"
    before.write_bytes(b"MAX = 6\n")
    after.write_bytes(b"MAX = 7\n")
    assert agent._file_fingerprint(str(before)) != agent._file_fingerprint(str(after))


def test_an_unreadable_file_yields_none_rather_than_raising(tmp_path):
    """Both sides drop what they cannot read instead of failing. A file that
    vanished must not take down the agent's poll or this server's to_dict."""
    missing = str(tmp_path / "not-here.py")
    assert agent._file_fingerprint(missing) is None
    assert appmod._file_fingerprint(missing) is None
    assert agent._file_fingerprint("") is None


# --- The agent's reported value ------------------------------------------

def test_the_agent_reports_all_three_files_it_runs():
    reported = appmod._parse_connector_fingerprint(agent.compute_connector_fingerprint())
    assert sorted(reported) == ["agent", "mikrotik", "vsol_olt"]
    assert all(len(v) == agent.FINGERPRINT_LENGTH for v in reported.values())


def test_the_agent_sends_the_fingerprint_as_a_header():
    headers = agent._headers({"token": "1.secret"})
    assert headers["X-Agent-Connectors"] == agent.CONNECTOR_FINGERPRINT
    assert headers["X-Agent-Version"] == agent.AGENT_VERSION


def test_the_header_is_omitted_entirely_when_nothing_could_be_hashed(monkeypatch):
    """Sending an empty header would blank a good previous reading on the
    server, which only overwrites when the header is present."""
    monkeypatch.setattr(agent, "CONNECTOR_FINGERPRINT", "")
    assert "X-Agent-Connectors" not in agent._headers({"token": "1.secret"})


def test_an_unreadable_connector_is_left_out_not_reported_as_empty(monkeypatch):
    monkeypatch.setattr(agent, "_fingerprint_sources", lambda: {
        "agent": os.path.abspath(agent.__file__),
        "mikrotik": "",
        "vsol_olt": "",
    })
    assert sorted(appmod._parse_connector_fingerprint(
        agent.compute_connector_fingerprint())) == ["agent"]


def test_the_agent_running_this_checkout_matches_this_checkout():
    """End to end on the real files: an agent started from this working tree
    is by definition not stale against this working tree."""
    status, stale = appmod._connector_status(agent.compute_connector_fingerprint())
    assert (status, stale) == ("current", [])


# --- Parsing an untrusted header -----------------------------------------

def test_parsing_keeps_well_formed_pairs_and_drops_the_rest():
    parsed = appmod._parse_connector_fingerprint(
        "agent=aa11, mikrotik =bb22 ,nonsense,=novalue,noname=,vsol_olt=cc33")
    assert parsed == {"agent": "aa11", "mikrotik": "bb22", "vsol_olt": "cc33"}


def test_parsing_an_empty_or_missing_header_is_an_empty_mapping():
    assert appmod._parse_connector_fingerprint(None) == {}
    assert appmod._parse_connector_fingerprint("") == {}
    assert appmod._parse_connector_fingerprint(",,,") == {}


# --- The verdict ----------------------------------------------------------

def _expected_header():
    return ",".join("{}={}".format(name, digest) for name, digest
                    in sorted(appmod._expected_connector_fingerprints().items()))


def test_a_matching_report_is_current():
    assert appmod._connector_status(_expected_header()) == ("current", [])


def test_a_mismatch_names_the_specific_file():
    """'Something is stale' would not have shortened either debugging round.
    'vsol_olt.py is stale' would have ended them."""
    expected = appmod._expected_connector_fingerprints()
    header = _expected_header().replace(expected["vsol_olt"], "000000000000")
    assert appmod._connector_status(header) == ("stale", ["vsol_olt"])


def test_every_file_being_stale_names_every_file():
    header = ",".join("{}=000000000000".format(n)
                      for n in sorted(appmod._expected_connector_fingerprints()))
    status, stale = appmod._connector_status(header)
    assert status == "stale"
    assert stale == ["agent", "mikrotik", "vsol_olt"]


def test_an_agent_that_reports_nothing_is_unknown_not_stale():
    """An agent predating this check sends no header. Telling that owner their
    files are stale would send them to re-copy files they may have copied ten
    minutes ago; what actually produces an answer is restarting the agent."""
    assert appmod._connector_status(None) == ("unknown", [])
    assert appmod._connector_status("") == ("unknown", [])


def test_a_report_sharing_no_names_with_this_deploy_is_unknown():
    """Forward compatibility: a future agent that renamed everything gives us
    nothing to compare, which is 'unknown' -- never a false 'stale'."""
    assert appmod._connector_status("something_else=aabbccddeeff") == ("unknown", [])


def test_only_the_names_present_on_both_sides_are_compared():
    """A file this server cannot see is not evidence of staleness, and neither
    is one the agent could not read."""
    expected = appmod._expected_connector_fingerprints()
    partial = "vsol_olt={}".format(expected["vsol_olt"])
    assert appmod._connector_status(partial) == ("current", [])
    assert appmod._connector_status(partial + ",unknown_file=ffffffffffff") == ("current", [])


# --- The wire: header in, verdict out ------------------------------------

def _make_agent(app, tenant_name):
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name=tenant_name).first()
        record = appmod.NetworkAgent(tenant_id=tenant.id, name="Box", token_hash="x")
        appmod.db.session.add(record)
        appmod.db.session.commit()
        token = appmod._issue_agent_token(record)  # sets token_hash; caller commits
        appmod.db.session.commit()
        return token, tenant.id


def _stored(app, tenant_id):
    with app.app_context():
        return appmod.NetworkAgent.query.filter_by(tenant_id=tenant_id).first()


def test_polling_stores_the_reported_fingerprint(app, client):
    make_tenant(client, "Fp A", "fp_a_admin")
    token, tenant_id = _make_agent(app, "Fp A")
    client.get("/api/agent/jobs", headers={
        "Authorization": "Bearer " + token,
        "X-Agent-Version": "1.2.0",
        "X-Agent-Connectors": _expected_header(),
    })
    stored = _stored(app, tenant_id)
    assert stored.connector_fingerprint == _expected_header()
    assert stored.to_dict()["connectors_status"] == "current"


def test_a_stale_connector_surfaces_in_the_agents_api(app, client):
    make_tenant(client, "Fp B", "fp_b_admin")
    token, tenant_id = _make_agent(app, "Fp B")
    expected = appmod._expected_connector_fingerprints()
    client.get("/api/agent/jobs", headers={
        "Authorization": "Bearer " + token,
        "X-Agent-Connectors": _expected_header().replace(
            expected["vsol_olt"], "000000000000"),
    })
    payload = _stored(app, tenant_id).to_dict()
    assert payload["connectors_status"] == "stale"
    assert payload["stale_connectors"] == ["vsol_olt"]


def test_an_agent_that_never_reported_reads_as_unknown(app, client):
    make_tenant(client, "Fp C", "fp_c_admin")
    token, tenant_id = _make_agent(app, "Fp C")
    client.get("/api/agent/jobs", headers={
        "Authorization": "Bearer " + token, "X-Agent-Version": "1.1.0"})
    payload = _stored(app, tenant_id).to_dict()
    assert payload["agent_version"] == "1.1.0"
    assert payload["connectors_status"] == "unknown"
    assert payload["stale_connectors"] == []


def test_a_poll_without_the_header_keeps_the_last_good_reading(app, client):
    """A transient read failure on the box must not blank what we know. The
    agent omits the header rather than sending it empty precisely so this
    stays a no-op."""
    make_tenant(client, "Fp D", "fp_d_admin")
    token, tenant_id = _make_agent(app, "Fp D")
    headers = {"Authorization": "Bearer " + token}
    client.get("/api/agent/jobs", headers=dict(headers, **{
        "X-Agent-Connectors": _expected_header()}))
    client.get("/api/agent/jobs", headers=headers)
    assert _stored(app, tenant_id).connector_fingerprint == _expected_header()


def test_an_oversized_header_is_truncated_not_stored_whole(app, client):
    """The header is untrusted input; the column is String(200). Postgres
    rejects an over-length value outright, so an unbounded store would turn a
    malformed header into a failed poll for the whole agent."""
    make_tenant(client, "Fp E", "fp_e_admin")
    token, tenant_id = _make_agent(app, "Fp E")
    client.get("/api/agent/jobs", headers={
        "Authorization": "Bearer " + token,
        "X-Agent-Connectors": "agent=" + "a" * 5000,
    })
    stored = _stored(app, tenant_id)
    assert len(stored.connector_fingerprint) == appmod.MAX_CONNECTOR_FINGERPRINT_LENGTH
    # Garbage in, but a verdict rather than a crash.
    assert stored.to_dict()["connectors_status"] == "stale"


def test_a_garbage_header_does_not_break_the_poll(app, client):
    make_tenant(client, "Fp F", "fp_f_admin")
    token, tenant_id = _make_agent(app, "Fp F")
    response = client.get("/api/agent/jobs", headers={
        "Authorization": "Bearer " + token,
        "X-Agent-Connectors": "<<<not a fingerprint at all>>>",
    })
    assert response.status_code == 204
    assert _stored(app, tenant_id).to_dict()["connectors_status"] == "unknown"

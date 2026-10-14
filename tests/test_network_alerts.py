"""Offline / back-online alerts for agent-mode network devices. See
docs/superpowers/specs/2026-10-14-network-offline-alerts-design.md."""
from datetime import timedelta

import pytest

import app as appmod
from tests.conftest import make_tenant


@pytest.fixture
def pushes(monkeypatch):
    sent = []

    def fake_push(payload, tenant_id=None, roles=None, topic=None):
        sent.append({'payload': payload, 'tenant_id': tenant_id, 'roles': roles, 'topic': topic})
        return 1

    monkeypatch.setattr(appmod, 'send_push_notification', fake_push)
    return sent


def _setup(app, client, name, agent_seen_ago=timedelta(seconds=1), device_type='mikrotik_ccr'):
    """Returns (tenant_id, device_id) for an agent-mode tenant with one agent
    (seen `agent_seen_ago` ago, or never if None) and one device."""
    make_tenant(client, name, name.lower().replace(' ', '_') + '_admin')
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name=name).first()
        appmod.db.session.add(appmod.BusinessSettings(
            tenant_id=tenant.id, business_name=name, address="x", mobile="+1",
            network_access_mode='agent'))
        appmod.db.session.add(appmod.NetworkAgent(
            tenant_id=tenant.id, name="Box", token_hash="x",
            last_seen_at=(appmod.datetime.utcnow() - agent_seen_ago) if agent_seen_ago is not None else None))
        device = appmod.NetworkDevice(
            tenant_id=tenant.id, name="CCR" if device_type == 'mikrotik_ccr' else "OLT",
            host="192.168.8.1", username="u", password="p", device_type=device_type)
        appmod.db.session.add(device)
        appmod.db.session.commit()
        return tenant.id, device.id


def _events(tenant_id):
    return [e.kind for e in appmod.NetworkAlertEvent.query.filter_by(
        tenant_id=tenant_id).order_by(appmod.NetworkAlertEvent.id).all()]


def _stamp(device_id, tenant_id, status, at):
    job = appmod.NetworkAgentJob(tenant_id=tenant_id, device_id=device_id, operation='device_health')
    appmod._stamp_device_status_from_agent(job, status, now=at)
    appmod.db.session.commit()


# --- device down / recovered ------------------------------------------------

def test_single_failure_does_not_alert(app, client, pushes):
    tenant_id, device_id = _setup(app, client, "Alert A")
    with app.app_context():
        t0 = appmod.datetime.utcnow()
        _stamp(device_id, tenant_id, 'online', t0)
        _stamp(device_id, tenant_id, 'unreachable', t0 + timedelta(minutes=1))
        # same tick, second result (olt_status + cpe_locations): still no alert
        _stamp(device_id, tenant_id, 'unreachable', t0 + timedelta(minutes=2))
        assert pushes == []
        assert _events(tenant_id) == []


def test_confirmed_failure_alerts_once_then_recovers_once(app, client, pushes):
    tenant_id, device_id = _setup(app, client, "Alert B")
    with app.app_context():
        t0 = appmod.datetime.utcnow()
        _stamp(device_id, tenant_id, 'online', t0)
        _stamp(device_id, tenant_id, 'unreachable', t0 + timedelta(minutes=15))
        _stamp(device_id, tenant_id, 'unreachable', t0 + timedelta(minutes=30))
        _stamp(device_id, tenant_id, 'unreachable', t0 + timedelta(minutes=45))
        _stamp(device_id, tenant_id, 'unreachable', t0 + timedelta(minutes=60))
        assert len(pushes) == 1
        assert pushes[0]['topic'] == 'network'
        assert pushes[0]['roles'] == ['admin', 'employee']
        assert pushes[0]['tenant_id'] == tenant_id
        assert 'CCR' in pushes[0]['payload']['title']
        _stamp(device_id, tenant_id, 'online', t0 + timedelta(minutes=75))
        _stamp(device_id, tenant_id, 'online', t0 + timedelta(minutes=90))
        assert len(pushes) == 2
        assert _events(tenant_id) == ['device_down', 'device_up']
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        assert device.alert_status == 'online'
        assert device.failing_since is None


def test_flap_resets_confirmation_window(app, client, pushes):
    tenant_id, device_id = _setup(app, client, "Alert C")
    with app.app_context():
        t0 = appmod.datetime.utcnow()
        _stamp(device_id, tenant_id, 'unreachable', t0)
        _stamp(device_id, tenant_id, 'online', t0 + timedelta(minutes=5))
        _stamp(device_id, tenant_id, 'unreachable', t0 + timedelta(minutes=15))
        assert pushes == []


def test_recovery_without_prior_down_alert_is_silent(app, client, pushes):
    tenant_id, device_id = _setup(app, client, "Alert D")
    with app.app_context():
        _stamp(device_id, tenant_id, 'online', appmod.datetime.utcnow())
        assert pushes == []


def test_auth_failed_counts_as_down(app, client, pushes):
    tenant_id, device_id = _setup(app, client, "Alert E")
    with app.app_context():
        t0 = appmod.datetime.utcnow()
        _stamp(device_id, tenant_id, 'auth_failed', t0)
        _stamp(device_id, tenant_id, 'auth_failed', t0 + timedelta(minutes=15))
        assert len(pushes) == 1
        assert 'log in' in pushes[0]['payload']['body']


def test_device_alert_suppressed_while_agent_flagged_offline(app, client, pushes):
    tenant_id, device_id = _setup(app, client, "Alert F")
    with app.app_context():
        agent = appmod.NetworkAgent.query.filter_by(tenant_id=tenant_id).first()
        agent.offline_alerted_at = appmod.datetime.utcnow()
        appmod.db.session.commit()
        t0 = appmod.datetime.utcnow()
        _stamp(device_id, tenant_id, 'unreachable', t0)
        _stamp(device_id, tenant_id, 'unreachable', t0 + timedelta(minutes=15))
        assert pushes == []
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        assert device.alert_status != 'down'  # not advanced, so it can still fire later
        agent.offline_alerted_at = None
        appmod.db.session.commit()
        _stamp(device_id, tenant_id, 'unreachable', t0 + timedelta(minutes=30))
        assert len(pushes) == 1


def test_stamp_ignores_unknown_status(app, client, pushes):
    tenant_id, device_id = _setup(app, client, "Alert G")
    with app.app_context():
        _stamp(device_id, tenant_id, None, appmod.datetime.utcnow())
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        assert device.last_status is None and device.failing_since is None


def test_push_failure_does_not_break_the_stamp(app, client, monkeypatch):
    tenant_id, device_id = _setup(app, client, "Alert H")

    def boom(*a, **k):
        raise RuntimeError("push service down")

    monkeypatch.setattr(appmod, 'send_push_notification', boom)
    with app.app_context():
        t0 = appmod.datetime.utcnow()
        _stamp(device_id, tenant_id, 'unreachable', t0)
        _stamp(device_id, tenant_id, 'unreachable', t0 + timedelta(minutes=15))
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        assert device.alert_status == 'down'
        assert _events(tenant_id) == ['device_down']


# --- agent heartbeat ----------------------------------------------------------

def test_agent_offline_alerts_once_and_recovers_once(app, client, pushes):
    tenant_id, _ = _setup(app, client, "Alert I", agent_seen_ago=timedelta(minutes=10))
    with app.app_context():
        appmod._check_agent_heartbeat_alert(tenant_id)
        appmod._check_agent_heartbeat_alert(tenant_id)
        assert len(pushes) == 1
        assert 'offline' in pushes[0]['payload']['title'].lower()
        agent = appmod.NetworkAgent.query.filter_by(tenant_id=tenant_id).first()
        assert agent.offline_alerted_at is not None
        agent.last_seen_at = appmod.datetime.utcnow()
        appmod.db.session.commit()
        appmod._check_agent_heartbeat_alert(tenant_id)
        appmod._check_agent_heartbeat_alert(tenant_id)
        assert len(pushes) == 2
        assert _events(tenant_id) == ['agent_offline', 'agent_online']
        assert appmod.NetworkAgent.query.filter_by(tenant_id=tenant_id).first().offline_alerted_at is None


def test_agent_short_gap_does_not_alert(app, client, pushes):
    tenant_id, _ = _setup(app, client, "Alert J", agent_seen_ago=timedelta(minutes=2))
    with app.app_context():
        appmod._check_agent_heartbeat_alert(tenant_id)
        assert pushes == []


def test_never_connected_agent_does_not_alert(app, client, pushes):
    tenant_id, _ = _setup(app, client, "Alert K", agent_seen_ago=None)
    with app.app_context():
        appmod._check_agent_heartbeat_alert(tenant_id)
        assert pushes == []


def test_scheduled_refresh_runs_heartbeat_and_queues_ccr_health(app, client, pushes):
    tenant_id, device_id = _setup(app, client, "Alert L")
    with app.app_context():
        appmod.refresh_agent_mode_network_status_for_tenant(tenant_id)
        ops = [j.operation for j in appmod.NetworkAgentJob.query.filter_by(device_id=device_id).all()]
        assert ops == ['device_health']
    tenant2, _ = _setup(app, client, "Alert M", agent_seen_ago=timedelta(minutes=30))
    with app.app_context():
        appmod.refresh_agent_mode_network_status_for_tenant(tenant2)
        assert _events(tenant2) == ['agent_offline']


# --- ONU mass drop ------------------------------------------------------------

def _onus(pon, states):
    """states: list of 'online'/'offline' -> ONU rows on one PON port."""
    return [{'pon_port': pon, 'mac_address': '{}-{}'.format(pon, i), 'status': s,
             'onu_id': '{}:{}'.format(pon, i)} for i, s in enumerate(states)]


def test_mass_drop_on_one_port_detected():
    prev = _onus('PON1', ['online'] * 8) + _onus('PON2', ['online'] * 8)
    cur = _onus('PON1', ['offline'] * 6 + ['online'] * 2) + _onus('PON2', ['online'] * 7 + ['offline'])
    drops, recoveries, state = appmod._onu_mass_drop_changes(prev, cur, {})
    assert [d['pon_port'] for d in drops] == ['PON1']
    assert drops[0]['dropped'] == 6 and drops[0]['was_online'] == 8
    assert recoveries == []
    assert set(state) == {'PON1'}


def test_single_onu_offline_is_not_a_mass_drop():
    prev = _onus('PON1', ['online'] * 8)
    cur = _onus('PON1', ['offline'] + ['online'] * 7)
    drops, recoveries, state = appmod._onu_mass_drop_changes(prev, cur, {})
    assert drops == [] and state == {}


def test_small_port_needs_min_count():
    prev = _onus('PON3', ['online'] * 2)
    cur = _onus('PON3', ['offline'] * 2)
    drops, _, _ = appmod._onu_mass_drop_changes(prev, cur, {})
    assert drops == []


def test_missing_onus_count_as_dropped():
    prev = _onus('PON1', ['online'] * 6)
    cur = _onus('PON1', ['online'] * 2)  # four ONUs vanished from the walk
    drops, _, _ = appmod._onu_mass_drop_changes(prev, cur, {})
    assert drops and drops[0]['dropped'] == 4


def test_active_port_does_not_realert_and_recovers():
    prev = _onus('PON1', ['online'] * 8)
    down = _onus('PON1', ['offline'] * 6 + ['online'] * 2)
    drops, _, state = appmod._onu_mass_drop_changes(prev, down, {})
    assert len(drops) == 1
    drops, recoveries, state = appmod._onu_mass_drop_changes(down, down, state)
    assert drops == [] and recoveries == [] and 'PON1' in state
    back = _onus('PON1', ['online'] * 4 + ['offline'] * 2 + ['online'] * 2)
    drops, recoveries, state = appmod._onu_mass_drop_changes(down, back, state)
    assert drops == [] and len(recoveries) == 1 and state == {}


def test_onu_alert_from_posted_olt_result(app, client, pushes):
    tenant_id, device_id = _setup(app, client, "Alert N", device_type='vsol_olt')
    with app.app_context():
        prev_job = appmod.NetworkAgentJob(
            tenant_id=tenant_id, device_id=device_id, operation='olt_status', status='done',
            result=_onus('PON1', ['online'] * 8), finished_at=appmod.datetime.utcnow())
        appmod.db.session.add(prev_job)
        job = appmod.NetworkAgentJob(
            tenant_id=tenant_id, device_id=device_id, operation='olt_status', status='done',
            result=_onus('PON1', ['offline'] * 7 + ['online']), finished_at=appmod.datetime.utcnow())
        appmod.db.session.add(job)
        appmod.db.session.commit()
        appmod._evaluate_onu_alerts(job)
        appmod._evaluate_onu_alerts(job)  # re-evaluating the same result never repeats
        assert len(pushes) == 1
        assert 'PON1' in pushes[0]['payload']['body']
        assert _events(tenant_id) == ['onu_mass_drop']


def _with_reasons(rows, reasons):
    """Stamp dereg_reason on the offline rows, in order."""
    it = iter(reasons)
    for row in rows:
        if row['status'] == 'offline':
            row['dereg_reason'] = next(it)
    return rows


def _drop_body(reasons, total=8):
    prev = _onus('PON1', ['online'] * total)
    cur = _with_reasons(_onus('PON1', ['offline'] * len(reasons) + ['online'] * (total - len(reasons))),
                        reasons)
    drops, _, _ = appmod._onu_mass_drop_changes(prev, cur, {})
    assert len(drops) == 1
    return drops[0], appmod._onu_mass_drop_body(drops[0])


def test_mass_drop_mostly_power_off_says_power_outage():
    drop, body = _drop_body(['power_off'] * 5 + ['wire_down'])
    assert drop['reasons'] == {'power_off': 5, 'wire_down': 1}
    assert 'mostly powered off (5 of 6' in body
    assert 'area power outage' in body and 'cut' not in body


def test_mass_drop_mostly_wire_down_says_fibre_cut():
    _, body = _drop_body(['wire_down'] * 4 + [None, 'power_off'])
    assert 'mostly fibre LOS (4 of 6' in body
    assert 'probable fibre/splitter cut' in body and 'power outage' not in body


def test_mass_drop_without_a_majority_keeps_the_generic_wording():
    # Half power-off, half unknown: unknown counts against the majority.
    _, body = _drop_body(['power_off'] * 3 + [None] * 3)
    assert 'mostly' not in body
    assert '(3 powered off, 0 fibre LOS, 3 unknown)' in body
    assert 'fibre/splitter cut or an area power outage' in body


def test_mass_drop_with_no_reasons_is_the_old_message():
    # An agent on an older vsol_olt.py sends no dereg_reason at all.
    prev = _onus('PON1', ['online'] * 8)
    cur = _onus('PON1', ['offline'] * 6 + ['online'] * 2)
    drops, _, _ = appmod._onu_mass_drop_changes(prev, cur, {})
    assert drops[0]['reasons'] == {None: 6}
    assert appmod._onu_mass_drop_body(drops[0]) == (
        '6 of 8 online ONUs on PON1 went offline at once -- probable fibre/splitter cut '
        'or an area power outage on that branch.')


def test_unrecognised_reason_counts_as_unknown():
    drop, body = _drop_body(['dying_gasp_v2'] * 6)
    assert drop['reasons'] == {None: 6}
    assert 'mostly' not in body


def test_vanished_onus_count_as_unknown_reason():
    prev = _onus('PON1', ['online'] * 6)
    cur = _onus('PON1', ['online'] * 2)  # four ONUs vanished from the walk
    drops, _, _ = appmod._onu_mass_drop_changes(prev, cur, {})
    assert drops[0]['reasons'] == {None: 4}


def test_olt_status_contract_accepts_and_bounds_dereg_reason():
    row = {'mac_address': 'aa:bb:cc:dd:ee:ff', 'description': None}
    assert appmod._validate_agent_result('olt_status', [dict(row)]) is None
    assert appmod._validate_agent_result('olt_status', [dict(row, dereg_reason=None)]) is None
    assert appmod._validate_agent_result('olt_status', [dict(row, dereg_reason='power_off')]) is None
    assert appmod._validate_agent_result('olt_status', [dict(row, dereg_reason=['x'])])
    assert appmod._validate_agent_result('olt_status', [dict(row, dereg_reason='x' * 33)])


def test_posted_power_off_result_pushes_power_outage_wording(app, client, pushes):
    tenant_id, device_id = _setup(app, client, "Alert R", device_type='vsol_olt')
    with app.app_context():
        appmod.db.session.add(appmod.NetworkAgentJob(
            tenant_id=tenant_id, device_id=device_id, operation='olt_status', status='done',
            result=_onus('PON3', ['online'] * 8), finished_at=appmod.datetime.utcnow()))
        job = appmod.NetworkAgentJob(
            tenant_id=tenant_id, device_id=device_id, operation='olt_status', status='done',
            result=_with_reasons(_onus('PON3', ['offline'] * 7 + ['online']), ['power_off'] * 7),
            finished_at=appmod.datetime.utcnow())
        appmod.db.session.add(job)
        appmod.db.session.commit()
        appmod._evaluate_onu_alerts(job)
        assert len(pushes) == 1
        assert 'mostly powered off' in pushes[0]['payload']['body']


def test_network_is_a_push_topic():
    assert 'network' in appmod.DEFAULT_PUSH_TOPICS

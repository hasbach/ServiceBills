from datetime import datetime, timedelta

import app as appmod
from tests.conftest import make_tenant, auth_headers


def test_network_node_is_tenant_owned_and_in_delete_order():
    assert appmod.NetworkNode in appmod.TENANT_OWNED_MODELS
    assert appmod.NetworkNode in appmod._TENANT_DELETE_ORDER


def test_node_kinds_are_exactly_the_three_documented_values():
    assert appmod.NODE_KINDS == ('root', 'junction', 'onu')


def test_to_dict_returns_coordinates_as_floats(app):
    from app import db
    with app.app_context():
        node = appmod.NetworkNode(
            tenant_id=1, olt_device_id=1, kind='root', label='Control Room',
            latitude=34.436700, longitude=35.849700)
        db.session.add(node)
        db.session.commit()
        # Round-trip through the database so Numeric columns become Decimal
        db.session.expire_all()
        node = db.session.get(appmod.NetworkNode, node.id)

        # The raw attribute after round-trip must be Decimal, proving the round-trip happened
        from decimal import Decimal
        assert isinstance(node.latitude, Decimal), \
            f"Expected Decimal after round-trip, got {type(node.latitude)}"
        assert isinstance(node.longitude, Decimal), \
            f"Expected Decimal after round-trip, got {type(node.longitude)}"

        # to_dict() must convert Decimal to float for JSON serialization
        data = node.to_dict()
        assert isinstance(data['latitude'], float)
        assert isinstance(data['longitude'], float)
        assert data['latitude'] == 34.4367


def _olt(client, headers):
    """Create a vsol_olt device and return its id."""
    r = client.post('/api/network-devices', headers=headers, json={
        'name': 'OLT', 'host': '192.168.8.100', 'api_port': 161,
        'username': '', 'password': 'public', 'device_type': 'vsol_olt'})
    assert r.status_code in (200, 201), r.get_json()
    # POST /api/network-devices nests the created row under 'device' (see
    # create_network_device in app.py) -- it does not return a bare 'id'.
    return r.get_json()['device']['id']


def test_map_read_is_allowed_for_employee(app, client):
    """The map must render for the field-facing roles, and must NOT call any
    admin-only endpoint to do it -- Tree v2 shipped exactly that defect."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    emp = auth_headers(client, 'emp', 'pw', role='employee')
    r = client.get(f'/api/network-map?olt_device_id={olt}', headers=emp)
    assert r.status_code == 200
    body = r.get_json()
    assert body['nodes'] == []
    assert body['spans'] == []
    assert body['orphans'] == []


def test_map_read_rejects_a_role_outside_network_view(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    other = auth_headers(client, 'nobody', 'pw', role='customer')
    r = client.get(f'/api/network-map?olt_device_id={olt}', headers=other)
    assert r.status_code == 403


def test_map_read_404s_for_an_unknown_device(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    assert client.get('/api/network-map?olt_device_id=99999',
                      headers=admin).status_code == 404


def test_map_read_rejects_a_device_that_is_not_an_olt(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    r = client.post('/api/network-devices', headers=admin, json={
        'name': 'CCR', 'host': '192.168.100.1', 'api_port': 8728,
        'username': 'admin', 'password': 'x', 'device_type': 'mikrotik_ccr'})
    ccr = r.get_json()['device']['id']
    assert client.get(f'/api/network-map?olt_device_id={ccr}',
                      headers=admin).status_code == 400


def test_a_failed_walk_must_not_blank_the_map(app, client):
    """A failed connector run is stored as status='done', result=None, error
    set -- in BOTH direct and agent mode. Tree v2 shipped a defect where that
    row became 'the cached result', blanking the tree AND advancing its
    freshness stamp so it never retried. Symptom on this page would be worse:
    a map claiming the whole town is offline. The failed job must be ignored
    and the last good result kept.

    Note this fabricates job rows the way the connector really writes them --
    status='done', NOT status='failed'. An existing Tree v2 test hand-set
    'failed', a state the connector path never produces, and therefore proved
    nothing."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    good = [{'mac_address': 'aa:aa:aa:aa:aa:aa', 'status': 'online',
             'pon_port': 1, 'onu_id': 1, 'description': 'villaEid',
             'distance': 856}]
    # Explicit, ordered finished_at values -- a good walk 5 minutes ago,
    # then a failed one just now -- so the scenario (older good result,
    # newer failure) is spelled out rather than left to whatever order
    # the two commits happen to land in.
    now = datetime.utcnow()
    with app.app_context():
        appmod.db.session.add(appmod.NetworkAgentJob(
            tenant_id=1, device_id=olt, operation='olt_status',
            status='done', result=good, error=None,
            finished_at=now - timedelta(minutes=5)))
        appmod.db.session.commit()
        appmod.db.session.add(appmod.NetworkAgentJob(
            tenant_id=1, device_id=olt, operation='olt_status',
            status='done', result=None, error='SNMP timeout',
            finished_at=now))
        appmod.db.session.commit()
    body = client.get(f'/api/network-map?olt_device_id={olt}',
                      headers=admin).get_json()
    # _normalize_mac's canonical form is colon-lowercase (see _canonical_mac),
    # not bare hex -- and it must match here, because _compute_map_status
    # (Task 2) looks up this same dict via _normalize_mac(node.onu_mac) too.
    assert body['onu_status'] == {'aa:aa:aa:aa:aa:aa': 'online'}
    assert body['last_result_at'] is not None


def test_unplaced_onus_excludes_already_placed_ones(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    onus = [{'mac_address': 'aa:aa:aa:aa:aa:aa', 'status': 'online',
             'pon_port': 1, 'onu_id': 1, 'description': 'villaEid', 'distance': 856},
            {'mac_address': 'bb:bb:bb:bb:bb:bb', 'status': 'offline',
             'pon_port': 1, 'onu_id': 2, 'description': 'Lions', 'distance': 0}]
    with app.app_context():
        appmod.db.session.add(appmod.NetworkAgentJob(
            tenant_id=1, device_id=olt, operation='olt_status',
            status='done', result=onus, error=None,
            finished_at=datetime.utcnow()))
        appmod.db.session.add(appmod.NetworkNode(
            tenant_id=1, olt_device_id=olt, kind='root', label='CR',
            latitude=34.4367, longitude=35.8497))
        appmod.db.session.commit()
        appmod.db.session.add(appmod.NetworkNode(
            tenant_id=1, olt_device_id=olt, kind='onu', label='Villa Eid',
            latitude=34.4368, longitude=35.8498, parent_node_id=1,
            onu_mac='aa:aa:aa:aa:aa:aa'))
        appmod.db.session.commit()
    body = client.get(f'/api/network-map/unplaced-onus?olt_device_id={olt}',
                      headers=admin).get_json()
    assert [o['mac_address'] for o in body['onus']] == ['bb:bb:bb:bb:bb:bb']


def test_unplaced_onus_matches_a_placed_mac_across_separator_styles(app, client):
    """The OLT connector always reports colon-lowercase MACs, but a placed
    node's onu_mac can be entered by hand with different separators/case (see
    _normalize_mac's docstring: the project accepts ':', '-' and '.'). A raw
    string comparison here would treat the same ONU as still-unplaced and
    list it twice -- once on the map (placed) and once in this "needs
    placement" list -- so the comparison must go through _normalize_mac on
    both sides, not just one."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    onus = [{'mac_address': 'aa:aa:aa:aa:aa:aa', 'status': 'online',
             'pon_port': 1, 'onu_id': 1, 'description': 'villaEid', 'distance': 856},
            {'mac_address': 'bb:bb:bb:bb:bb:bb', 'status': 'offline',
             'pon_port': 1, 'onu_id': 2, 'description': 'Lions', 'distance': 0}]
    with app.app_context():
        appmod.db.session.add(appmod.NetworkAgentJob(
            tenant_id=1, device_id=olt, operation='olt_status',
            status='done', result=onus, error=None,
            finished_at=datetime.utcnow()))
        appmod.db.session.add(appmod.NetworkNode(
            tenant_id=1, olt_device_id=olt, kind='root', label='CR',
            latitude=34.4367, longitude=35.8497))
        appmod.db.session.commit()
        # Hyphenated and upper-case -- same ONU as 'aa:aa:aa:aa:aa:aa' above,
        # spelled differently, exactly as _normalize_mac's docstring expects
        # callers to tolerate.
        appmod.db.session.add(appmod.NetworkNode(
            tenant_id=1, olt_device_id=olt, kind='onu', label='Villa Eid',
            latitude=34.4368, longitude=35.8498, parent_node_id=1,
            onu_mac='AA-AA-AA-AA-AA-AA'))
        appmod.db.session.commit()
    body = client.get(f'/api/network-map/unplaced-onus?olt_device_id={olt}',
                      headers=admin).get_json()
    assert [o['mac_address'] for o in body['onus']] == ['bb:bb:bb:bb:bb:bb']

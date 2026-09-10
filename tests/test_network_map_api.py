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


def _node(client, headers, olt, **kw):
    payload = {'olt_device_id': olt, 'kind': 'junction', 'label': 'P',
               'latitude': 34.4367, 'longitude': 35.8497}
    payload.update(kw)
    return client.post('/api/network-map/nodes', headers=headers, json=payload)


def test_writes_are_refused_to_employee(app, client):
    """Read is widened to field roles; placing nodes is not. Widening the page
    must not quietly widen who can rewrite the customer-to-ONU mapping."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    emp = auth_headers(client, 'emp', 'pw', role='employee')
    assert _node(client, emp, olt, kind='root', label='CR').status_code == 403


def test_root_must_have_no_parent_and_be_unique_per_olt(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    first = _node(client, admin, olt, kind='root', label='CR')
    assert first.status_code == 201
    assert _node(client, admin, olt, kind='root', label='CR2').status_code == 400
    root_id = first.get_json()['id']
    bad = _node(client, admin, olt, kind='root', label='CR3',
                parent_node_id=root_id)
    assert bad.status_code == 400


def test_a_non_root_requires_a_parent(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    assert _node(client, admin, olt, kind='junction').status_code == 400


def test_mac_is_required_on_onu_and_forbidden_elsewhere(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    root = _node(client, admin, olt, kind='root', label='CR').get_json()['id']
    assert _node(client, admin, olt, kind='onu', parent_node_id=root
                 ).status_code == 400
    assert _node(client, admin, olt, kind='junction', parent_node_id=root,
                 onu_mac='aa:aa:aa:aa:aa:aa').status_code == 400
    ok = _node(client, admin, olt, kind='onu', parent_node_id=root,
               onu_mac='AA-AA-AA-AA-AA-AA')
    assert ok.status_code == 201
    assert ok.get_json()['onu_mac'] == 'aa:aa:aa:aa:aa:aa'   # canonicalised


def test_one_onu_cannot_be_placed_twice(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    root = _node(client, admin, olt, kind='root', label='CR').get_json()['id']
    _node(client, admin, olt, kind='onu', parent_node_id=root,
          onu_mac='aa:aa:aa:aa:aa:aa')
    dup = _node(client, admin, olt, kind='onu', parent_node_id=root,
                onu_mac='aa:aa:aa:aa:aa:aa')
    assert dup.status_code == 400


def test_a_malformed_mac_is_rejected(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    root = _node(client, admin, olt, kind='root', label='CR').get_json()['id']
    assert _node(client, admin, olt, kind='onu', parent_node_id=root,
                 onu_mac='not-a-mac').status_code == 400


def test_out_of_range_coordinates_are_rejected(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    assert _node(client, admin, olt, kind='root', label='CR',
                 latitude=91.0).status_code == 400
    assert _node(client, admin, olt, kind='root', label='CR',
                 longitude=-181.0).status_code == 400


def test_a_node_cannot_be_reparented_under_its_own_descendant(app, client):
    """The cycle guard. _compute_map_status defends on read, but a cycle must
    not be creatable in the first place."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    root = _node(client, admin, olt, kind='root', label='CR').get_json()['id']
    mid = _node(client, admin, olt, kind='junction', label='M',
                parent_node_id=root).get_json()['id']
    leaf = _node(client, admin, olt, kind='junction', label='L',
                 parent_node_id=mid).get_json()['id']
    r = client.put(f'/api/network-map/nodes/{mid}', headers=admin,
                   json={'parent_node_id': leaf})
    assert r.status_code == 400
    self_parent = client.put(f'/api/network-map/nodes/{mid}', headers=admin,
                             json={'parent_node_id': mid})
    assert self_parent.status_code == 400


def test_deleting_a_node_with_children_is_refused_with_409(app, client):
    """Naming the children matters: re-tracing a run by hand is expensive, so
    an accidental delete must be both blocked and explained."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    root = _node(client, admin, olt, kind='root', label='CR').get_json()['id']
    child = _node(client, admin, olt, kind='junction', label='Pole 4',
                  parent_node_id=root).get_json()['id']
    r = client.delete(f'/api/network-map/nodes/{root}', headers=admin)
    assert r.status_code == 409
    assert 'Pole 4' in r.get_json()['message']
    assert client.delete(f'/api/network-map/nodes/{child}',
                         headers=admin).status_code == 200


def test_a_node_from_another_tenant_is_invisible(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    node = _node(client, admin, olt, kind='root', label='CR').get_json()['id']
    make_tenant(client, 'Other ISP', 'admin2')
    other = auth_headers(client, 'admin2', 'pw', role='admin')
    assert client.put(f'/api/network-map/nodes/{node}', headers=other,
                      json={'label': 'hijacked'}).status_code == 404
    assert client.delete(f'/api/network-map/nodes/{node}',
                         headers=other).status_code == 404


def test_partial_update_changes_only_the_given_fields(app, client):
    """A drag-to-reposition sends only coordinates; a rename sends only a
    label. Neither may blank onu_mac, parent_node_id or kind -- and since a
    rename resubmits the node's own unchanged MAC, this also pins that the
    uniqueness check excludes the row being updated from its own clash query."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    root = _node(client, admin, olt, kind='root', label='CR').get_json()['id']
    onu = _node(client, admin, olt, kind='onu', parent_node_id=root,
                onu_mac='aa:aa:aa:aa:aa:aa').get_json()['id']

    renamed = client.put(f'/api/network-map/nodes/{onu}', headers=admin,
                         json={'label': 'Villa Eid'})
    assert renamed.status_code == 200
    body = renamed.get_json()
    assert body['label'] == 'Villa Eid'
    assert body['onu_mac'] == 'aa:aa:aa:aa:aa:aa'
    assert body['parent_node_id'] == root
    assert body['kind'] == 'onu'
    assert body['latitude'] == 34.4367
    assert body['longitude'] == 35.8497

    moved = client.put(f'/api/network-map/nodes/{onu}', headers=admin,
                       json={'latitude': 34.44, 'longitude': 35.86})
    assert moved.status_code == 200
    body = moved.get_json()
    assert body['latitude'] == 34.44
    assert body['longitude'] == 35.86
    # Unspecified fields on this second, coordinates-only PUT must still
    # survive -- including the label changed by the *previous* PUT.
    assert body['label'] == 'Villa Eid'
    assert body['onu_mac'] == 'aa:aa:aa:aa:aa:aa'
    assert body['parent_node_id'] == root
    assert body['kind'] == 'onu'

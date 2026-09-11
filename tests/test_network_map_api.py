import threading
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


def test_map_read_response_has_exactly_the_documented_keys(app, client):
    """FINDING 7 (final whole-branch review): the frontend reads
    'distance_warnings' off this payload (among others), but nothing on
    either side asserted the key even existed -- deleting it from
    get_network_map's response passed every other backend test. Pin the
    exact top-level key set so a future key removal or rename fails here
    instead of only being caught in production."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    body = client.get(f'/api/network-map?olt_device_id={olt}',
                      headers=admin).get_json()
    assert set(body.keys()) == {
        'nodes', 'spans', 'node_status', 'orphans', 'onu_status',
        'last_result_at', 'distance_warnings',
    }


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


# --- FINDING 1: parent_node_id / olt_device_id must be coerced to int -------

def test_a_string_parent_node_id_cannot_bypass_the_cycle_guard(app, client):
    """The critical finding. `"<id>" in _node_descendant_ids(...)` (a
    set[int]) is always False regardless of whether <id> is genuinely in
    the set, because the database's own filter_by(id=parent_id) resolves a
    JSON string parent id fine via column affinity while the Python `in`
    check downstream never matches a str against a set of ints. Mirrors
    test_a_node_cannot_be_reparented_under_its_own_descendant exactly,
    except both parent ids are sent as strings."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    root = _node(client, admin, olt, kind='root', label='CR').get_json()['id']
    mid = _node(client, admin, olt, kind='junction', label='M',
                parent_node_id=root).get_json()['id']
    leaf = _node(client, admin, olt, kind='junction', label='L',
                 parent_node_id=mid).get_json()['id']
    descendant = client.put(f'/api/network-map/nodes/{mid}', headers=admin,
                            json={'parent_node_id': str(leaf)})
    assert descendant.status_code == 400
    self_parent = client.put(f'/api/network-map/nodes/{mid}', headers=admin,
                             json={'parent_node_id': str(mid)})
    assert self_parent.status_code == 400


def test_a_non_coercible_parent_node_id_is_rejected(app, client):
    """None (absent) must stay legitimate -- a root's parent, meaning "no
    parent" -- while any present-but-junk value 400s instead of reaching
    the database or the descendant-set check in some half-coerced state."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    for bad in ('abc', [], {}, 12.7):
        r = _node(client, admin, olt, kind='junction', label='X',
                  parent_node_id=bad)
        assert r.status_code == 400, f'{bad!r} should 400, got {r.status_code}'


def test_a_string_olt_device_id_on_post_is_rejected(app, client):
    """olt_device_id has the identical weakness parent_node_id had -- read
    with a bare payload.get(...) instead of the type=int coercion the read
    endpoints use for the same field via request.args.get(...)."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    r = client.post('/api/network-map/nodes', headers=admin, json={
        'olt_device_id': 'abc', 'kind': 'root', 'label': 'CR',
        'latitude': 34.4367, 'longitude': 35.8497})
    assert r.status_code == 400


# --- FINDING 2: MAC uniqueness must compare normalised values both ways ----

def test_duplicate_onu_rejected_when_stored_mac_is_non_canonical(app, client):
    """The uniqueness query used to canonicalise only the *incoming* value
    and string-match it against the *stored* column -- so a hyphenated-
    uppercase MAC already on file would not catch a colon-lowercase repeat
    of the very same ONU. get_unplaced_onus already normalises both sides;
    this write path must match it. Stored directly via the ORM because
    every write path through the API always runs onu_mac through
    _canonical_mac before saving, so the non-canonical form can only arise
    from a value that predates that invariant (or a future writer that
    doesn't share it) -- exactly the scenario this guards against."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    root = _node(client, admin, olt, kind='root', label='CR').get_json()['id']
    with app.app_context():
        appmod.db.session.add(appmod.NetworkNode(
            tenant_id=1, olt_device_id=olt, kind='onu', label='Villa Eid',
            latitude=34.4368, longitude=35.8498, parent_node_id=root,
            onu_mac='AA-AA-AA-AA-AA-AA'))
        appmod.db.session.commit()
    dup = _node(client, admin, olt, kind='onu', label='Dup', parent_node_id=root,
                onu_mac='aa:aa:aa:aa:aa:aa')
    assert dup.status_code == 400


# --- FINDING 3: a non-string label must 400, not 500 ------------------------

def test_a_non_string_label_is_rejected(app, client):
    """_canonical_mac guards this exact case (a non-string reaching a method
    only strings have) by design; the label branch must follow the same
    house idiom instead of calling .strip() on whatever payload['label'] is
    and raising AttributeError."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    assert _node(client, admin, olt, kind='root', label=12345).status_code == 400
    assert _node(client, admin, olt, kind='root', label=['Pole 4']).status_code == 400


# --- FINDING 4: tenant isolation, pinned against each of the 5 guards ------

def test_a_foreign_tenants_node_cannot_be_used_as_a_parent(app, client):
    """Pins tenant_query on the parent lookup in _validate_node_payload.
    Under data the API itself could ever produce, tenant_id and
    olt_device_id always agree (one OLT device belongs to exactly one
    tenant), so a plain cross-tenant attempt is already stopped by the
    olt_device_id half of that same filter_by(...) regardless of whether
    tenant_query is still there -- it would pass this test either way and
    prove nothing. Only a row that violates that invariant actually
    exercises the tenant_query half, so that's what gets forged: a node
    tagged tenant A but sitting on tenant B's real OLT id, the way a stray
    row from data drift or an unrelated bug might."""
    make_tenant(client, 'DeltaNet', 'admin')
    auth_headers(client, 'admin', 'pw', role='admin')  # tenant A, id=1

    make_tenant(client, 'Other ISP', 'admin_b')
    admin_b = auth_headers(client, 'admin_b', 'pw', role='admin')
    olt_b = _olt(client, admin_b)

    with app.app_context():
        foreign = appmod.NetworkNode(
            tenant_id=1, olt_device_id=olt_b, kind='junction', label='ghost',
            latitude=34.4367, longitude=35.8497, parent_node_id=None)
        appmod.db.session.add(foreign)
        appmod.db.session.commit()
        foreign_id = foreign.id

    r = _node(client, admin_b, olt_b, kind='junction', label='X',
             parent_node_id=foreign_id)
    assert r.status_code == 400


def test_a_foreign_tenants_mac_does_not_clash(app, client):
    """Pins tenant_query on the MAC-clash query, by the same reasoning and
    the same kind of forged row as the parent-lookup test above."""
    make_tenant(client, 'DeltaNet', 'admin')
    auth_headers(client, 'admin', 'pw', role='admin')  # tenant A, id=1

    make_tenant(client, 'Other ISP', 'admin_b')
    admin_b = auth_headers(client, 'admin_b', 'pw', role='admin')
    olt_b = _olt(client, admin_b)
    root_b = _node(client, admin_b, olt_b, kind='root', label='CR').get_json()['id']

    with app.app_context():
        foreign = appmod.NetworkNode(
            tenant_id=1, olt_device_id=olt_b, kind='onu', label='ghost-onu',
            latitude=34.4367, longitude=35.8497, parent_node_id=None,
            onu_mac='cc:cc:cc:cc:cc:cc')
        appmod.db.session.add(foreign)
        appmod.db.session.commit()

    r = _node(client, admin_b, olt_b, kind='onu', label='Real', parent_node_id=root_b,
             onu_mac='cc:cc:cc:cc:cc:cc')
    assert r.status_code == 201


def test_deleting_a_node_is_unaffected_by_a_foreign_tenants_children(app, client):
    """Pins tenant_query on delete's children lookup. Forges a row whose
    parent_node_id equals a real tenant-B node's id but is itself tagged
    tenant A -- again a combination the API can never produce on its own,
    since a node's parent is always resolved through the same
    tenant-scoped lookup the first test in this group pins."""
    make_tenant(client, 'DeltaNet', 'admin')
    auth_headers(client, 'admin', 'pw', role='admin')  # tenant A, id=1

    make_tenant(client, 'Other ISP', 'admin_b')
    admin_b = auth_headers(client, 'admin_b', 'pw', role='admin')
    olt_b = _olt(client, admin_b)
    leaf_b = _node(client, admin_b, olt_b, kind='root', label='CR').get_json()['id']

    with app.app_context():
        foreign = appmod.NetworkNode(
            tenant_id=1, olt_device_id=olt_b, kind='junction', label='ghost-child',
            latitude=34.4367, longitude=35.8497, parent_node_id=leaf_b)
        appmod.db.session.add(foreign)
        appmod.db.session.commit()

    r = client.delete(f'/api/network-map/nodes/{leaf_b}', headers=admin_b)
    assert r.status_code == 200


def test_descendant_walk_does_not_cross_tenants(app, client):
    """Pins the tenant filter inside _node_descendant_ids itself -- distinct
    from the parent-lookup guard above, which gates *which* node is
    accepted as a parent; this one gates what counts as *inside* a node's
    own subtree. A plain cross-tenant attempt can't even reach this code
    (the parent lookup 400s first), so this bridges through a forged
    foreign-tenant row: target_b's subtree is walked, and if the tenant
    filter were dropped from the walk, it would leak through the foreign
    row into deep_b -- a real tenant-B row that, through the forged bridge
    alone, looks like target_b's descendant."""
    make_tenant(client, 'DeltaNet', 'admin')
    auth_headers(client, 'admin', 'pw', role='admin')  # tenant A, id=1

    make_tenant(client, 'Other ISP', 'admin_b')
    admin_b = auth_headers(client, 'admin_b', 'pw', role='admin')
    olt_b = _olt(client, admin_b)
    root_b = _node(client, admin_b, olt_b, kind='root', label='CR').get_json()['id']
    target_b = _node(client, admin_b, olt_b, kind='junction', label='target',
                     parent_node_id=root_b).get_json()['id']

    with app.app_context():
        ghost = appmod.NetworkNode(
            tenant_id=1, olt_device_id=olt_b, kind='junction', label='ghost',
            latitude=34.4367, longitude=35.8497, parent_node_id=target_b)
        appmod.db.session.add(ghost)
        appmod.db.session.commit()
        deep_b = appmod.NetworkNode(
            tenant_id=2, olt_device_id=olt_b, kind='junction', label='deep',
            latitude=34.4367, longitude=35.8497, parent_node_id=ghost.id)
        appmod.db.session.add(deep_b)
        appmod.db.session.commit()
        deep_b_id = deep_b.id

    r = client.put(f'/api/network-map/nodes/{target_b}', headers=admin_b,
                   json={'parent_node_id': deep_b_id})
    assert r.status_code == 200


def test_cannot_create_a_node_on_a_foreign_tenants_olt(app, client):
    """Pins _require_olt's own tenant scope on POST. Unlike the other four
    guards in this group, this one needs no forged row: tenant B can just
    hand tenant A's real, valid OLT device id straight to POST."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin_a = auth_headers(client, 'admin', 'pw', role='admin')
    olt_a = _olt(client, admin_a)

    make_tenant(client, 'Other ISP', 'admin_b')
    admin_b = auth_headers(client, 'admin_b', 'pw', role='admin')

    r = _node(client, admin_b, olt_a, kind='root', label='hijack-root')
    assert r.status_code == 404


# --- FINDING 5: existing guards, previously untested against regression ---

def test_an_invalid_kind_is_rejected(app, client):
    """Must send a valid parent along with the bad kind -- otherwise a
    'banana' with no parent_node_id also fails the separate "non-root
    requires a parent" check, and the test would 400 for the wrong reason
    even with the kind check itself deleted (kind has no DB-level CHECK
    constraint, just a comment saying it should be one of NODE_KINDS)."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    root = _node(client, admin, olt, kind='root', label='CR').get_json()['id']
    assert _node(client, admin, olt, kind='banana', label='X',
                parent_node_id=root).status_code == 400


def test_a_blank_label_is_rejected(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    assert _node(client, admin, olt, kind='root', label='   ').status_code == 400


def test_a_label_is_stripped_of_surrounding_whitespace(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    r = _node(client, admin, olt, kind='root', label='  Pole 4  ')
    assert r.status_code == 201
    assert r.get_json()['label'] == 'Pole 4'


def test_a_label_is_truncated_to_100_characters(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    r = _node(client, admin, olt, kind='root', label='x' * 200)
    assert r.status_code == 201
    assert len(r.get_json()['label']) == 100


def test_updating_a_root_does_not_collide_with_itself(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    root = _node(client, admin, olt, kind='root', label='CR').get_json()['id']
    r = client.put(f'/api/network-map/nodes/{root}', headers=admin,
                   json={'label': 'Control Room'})
    assert r.status_code == 200
    assert r.get_json()['label'] == 'Control Room'


def test_changing_kind_from_onu_to_junction_clears_onu_mac(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    root = _node(client, admin, olt, kind='root', label='CR').get_json()['id']
    onu = _node(client, admin, olt, kind='onu', parent_node_id=root,
                onu_mac='aa:aa:aa:aa:aa:aa').get_json()['id']
    r = client.put(f'/api/network-map/nodes/{onu}', headers=admin,
                   json={'kind': 'junction', 'onu_mac': None})
    assert r.status_code == 200
    assert r.get_json()['onu_mac'] is None
    assert r.get_json()['kind'] == 'junction'


# --- FINDING 6: a partial update must not be blocked by a pre-existing cycle

def test_a_partial_update_succeeds_inside_an_existing_cycle(app, client):
    """A label-only PUT on a node that already sits inside a cycle must not
    be rejected on account of that pre-existing corruption -- dragging a
    pin or renaming it is exactly how an operator repairs one. Forges a
    genuine cycle directly since the write endpoints themselves can never
    create one through the API."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    with app.app_context():
        a = appmod.NetworkNode(tenant_id=1, olt_device_id=olt, kind='junction',
                               label='A', latitude=34.4367, longitude=35.8497,
                               parent_node_id=None)
        appmod.db.session.add(a)
        appmod.db.session.commit()
        b = appmod.NetworkNode(tenant_id=1, olt_device_id=olt, kind='junction',
                               label='B', latitude=34.4367, longitude=35.8497,
                               parent_node_id=a.id)
        appmod.db.session.add(b)
        appmod.db.session.commit()
        a.parent_node_id = b.id   # close the cycle: a -> b -> a
        appmod.db.session.commit()
        a_id = a.id

    r = client.put(f'/api/network-map/nodes/{a_id}', headers=admin,
                   json={'label': 'A renamed'})
    assert r.status_code == 200
    assert r.get_json()['label'] == 'A renamed'


# --- FINDING 7: the descendant walk must terminate on cyclic data ---------

def test_a_reparent_against_a_cycle_returns_400_promptly(app, client):
    """Removing the seen-set filter from _node_descendant_ids survives the
    suite otherwise, so this pins its termination property directly: a
    reparent attempt that forces the walk to actually traverse a forged
    cycle must come back with 400, not hang. Three nodes, not two -- a
    two-node mutual cycle can't distinguish "reparent onto the unchanged
    current parent" (skipped entirely by FINDING 6's gating) from a walk
    that genuinely needs to run."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    with app.app_context():
        a = appmod.NetworkNode(tenant_id=1, olt_device_id=olt, kind='junction',
                               label='A', latitude=34.4367, longitude=35.8497,
                               parent_node_id=None)
        b = appmod.NetworkNode(tenant_id=1, olt_device_id=olt, kind='junction',
                               label='B', latitude=34.4367, longitude=35.8497,
                               parent_node_id=None)
        c = appmod.NetworkNode(tenant_id=1, olt_device_id=olt, kind='junction',
                               label='C', latitude=34.4367, longitude=35.8497,
                               parent_node_id=None)
        appmod.db.session.add_all([a, b, c])
        appmod.db.session.commit()
        # A's parent is C, B's parent is A, C's parent is B: A -> B -> C -> A.
        a.parent_node_id = c.id
        b.parent_node_id = a.id
        c.parent_node_id = b.id
        appmod.db.session.commit()
        a_id, b_id = a.id, b.id

    # Run the request off the main thread and join with a timeout: if the
    # seen-set guard inside _node_descendant_ids ever regresses, the walk
    # over this forged cycle hangs forever rather than raising, and without
    # pytest-timeout installed (deliberately not added -- see FINDING 5) an
    # un-timed call here would hang the whole suite/CI instead of failing
    # this one test. 15s is generous for a walk that normally takes
    # milliseconds.
    result = {}

    def run():
        result['response'] = client.put(
            f'/api/network-map/nodes/{a_id}', headers=admin,
            json={'parent_node_id': b_id})

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=15)
    assert not t.is_alive(), (
        'descendant walk did not terminate within 15s -- the seen-set '
        'termination guard in _node_descendant_ids appears to have regressed')
    assert result['response'].status_code == 400


# --- FINDING 8: coordinates are required on create, not just in-range -----

def test_missing_latitude_on_create_is_rejected(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    r = client.post('/api/network-map/nodes', headers=admin, json={
        'olt_device_id': olt, 'kind': 'root', 'label': 'CR',
        'longitude': 35.8497})
    assert r.status_code == 400


def test_missing_longitude_on_create_is_rejected(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    r = client.post('/api/network-map/nodes', headers=admin, json={
        'olt_device_id': olt, 'kind': 'root', 'label': 'CR',
        'latitude': 34.4367})
    assert r.status_code == 400


# --- Fix round 2, FINDING 1: _coerce_fk_id's own branches, previously
# untested -- each of the following passed with all 41 prior tests green
# even with the corresponding branch mutated away. ------------------------

def test_a_boolean_parent_node_id_does_not_silently_become_id_1(app, client):
    """isinstance(True, int) is True in Python and int(True) == 1, so
    without _coerce_fk_id's explicit bool guard, {"parent_node_id": true}
    would silently resolve to whatever row has id 1 -- usually the root,
    exactly the node created first below in this fresh per-test database."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    root = _node(client, admin, olt, kind='root', label='CR').get_json()['id']
    assert root == 1, 'test assumes a fresh per-test DB where the first node is id 1'
    for bad in (True, False):
        r = _node(client, admin, olt, kind='junction', label='X',
                  parent_node_id=bad)
        assert r.status_code == 400, f'{bad!r} should 400, got {r.status_code}'


def test_a_whole_valued_float_parent_node_id_is_rejected_against_a_real_row(app, client):
    """The existing 'abc'/[]/{}}/12.7 coercion test happens to use a float
    (12.7) that matches no row either way, so a mutation truncating floats
    via int() instead of rejecting them outright would still 400 there --
    just for the wrong reason (parent not found, not "must be an integer").
    12.0 aimed at a node id that genuinely exists closes that gap: if
    truncation crept back in, this would silently succeed instead of 400."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    root = _node(client, admin, olt, kind='root', label='CR').get_json()['id']
    last_id = root
    for i in range(11):
        last_id = _node(client, admin, olt, kind='junction', label=f'J{i}',
                        parent_node_id=root).get_json()['id']
    assert last_id == 12, 'test assumes a fresh per-test DB with sequential ids'
    r = _node(client, admin, olt, kind='junction', label='X', parent_node_id=12.0)
    assert r.status_code == 400


def test_a_list_or_dict_parent_node_id_on_a_root_is_rejected(app, client):
    """Distinct from the existing 'abc'/[]/{}/12.7 test, which uses kind=
    junction: there, a mutation changing _coerce_fk_id's final fallback from
    (None, False) to (None, True) would still 400 -- just for the wrong
    reason ("a non-root node requires a parent"), because [] and {} would
    be treated as an absent (None) parent, which a non-root node still
    lacks. A root payload has no such fallback error to hide behind: a
    root's parent is legitimately None, so only the coercion's own
    ok=False keeps this a 400."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    for bad in ([], {}):
        r = _node(client, admin, olt, kind='root', label='CR', parent_node_id=bad)
        assert r.status_code == 400, f'{bad!r} should 400, got {r.status_code}'


# --- Fix round 2, FINDING 2: booleans must not be accepted as coordinates -

def test_boolean_coordinates_are_rejected(app, client):
    """float(True) is 1.0 and float(False) is 0.0, so without an explicit
    bool guard {"latitude": true, "longitude": false} would 201 a node at a
    confidently wrong 0N 1E -- a real dispatch-location bug on a map used to
    send technicians. Mirrors the bool guard _coerce_fk_id already has."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    for bad in (True, False):
        assert _node(client, admin, olt, kind='root', label='CR',
                     latitude=bad).status_code == 400, f'latitude={bad!r}'
        assert _node(client, admin, olt, kind='root', label='CR',
                     longitude=bad).status_code == 400, f'longitude={bad!r}'


def test_int_and_float_coordinates_are_still_accepted(app, client):
    """The bool guard must not overreach: genuine ints and floats are still
    real coordinates."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    r = _node(client, admin, olt, kind='root', label='CR',
              latitude=34, longitude=35.8497)
    assert r.status_code == 201
    body = r.get_json()
    assert body['latitude'] == 34.0
    assert body['longitude'] == 35.8497

    r2 = _node(client, admin, olt, kind='junction', label='J',
               parent_node_id=body['id'], latitude=34.4367, longitude=35.8497)
    assert r2.status_code == 201


# --- Fix round 2, FINDING 3: out-of-range and non-positive ids must 400,
# not reach the database and crash. -----------------------------------------

def test_an_oversized_parent_node_id_is_rejected_not_500(app, client):
    """100000000000000000000 reaching the database raises OverflowError on
    SQLite (a DataError on Postgres) instead of 400ing cleanly."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    r = _node(client, admin, olt, kind='junction', label='X',
              parent_node_id=100000000000000000000)
    assert r.status_code == 400


def test_an_oversized_olt_device_id_is_rejected_not_500(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    r = client.post('/api/network-map/nodes', headers=admin, json={
        'olt_device_id': 100000000000000000000, 'kind': 'root', 'label': 'CR',
        'latitude': 34.4367, 'longitude': 35.8497})
    assert r.status_code == 400


def test_zero_and_negative_parent_node_ids_are_rejected(app, client):
    """No valid row id is ever zero or negative, so these must 400 directly
    out of _coerce_fk_id rather than fall through to a "parent not found"
    lookup."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    for bad in (0, -1, -100000000000000000000):
        r = _node(client, admin, olt, kind='junction', label='X',
                  parent_node_id=bad)
        assert r.status_code == 400, f'{bad!r} should 400, got {r.status_code}'


# --- Fix round 2, FINDING 6: MAC uniqueness is scoped per OLT, not tenant --

def test_same_mac_allowed_under_two_different_olts_in_one_tenant(app, client):
    """The clash query is scoped with filter_by(olt_device_id=device_id);
    dropping that filter would survive the rest of the suite (there is
    already a cross-tenant test, test_a_foreign_tenants_mac_does_not_clash,
    but no same-tenant/different-OLT one). The same ONU model or a genuine
    data-entry repeat of a MAC across two different OLTs owned by the same
    tenant must not collide -- only a repeat under the *same* OLT should."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt1 = _olt(client, admin)
    r = client.post('/api/network-devices', headers=admin, json={
        'name': 'OLT2', 'host': '192.168.8.101', 'api_port': 161,
        'username': '', 'password': 'public', 'device_type': 'vsol_olt'})
    olt2 = r.get_json()['device']['id']
    root1 = _node(client, admin, olt1, kind='root', label='CR1').get_json()['id']
    root2 = _node(client, admin, olt2, kind='root', label='CR2').get_json()['id']
    r1 = _node(client, admin, olt1, kind='onu', label='Onu1', parent_node_id=root1,
               onu_mac='dd:dd:dd:dd:dd:dd')
    r2 = _node(client, admin, olt2, kind='onu', label='Onu2', parent_node_id=root2,
               onu_mac='dd:dd:dd:dd:dd:dd')
    assert r1.status_code == 201, r1.get_json()
    assert r2.status_code == 201, r2.get_json()


# --- Fix round 2, FINDING 7: a corrupt stored MAC must not block an
# unrelated partial update. --------------------------------------------------

def test_a_partial_update_is_not_blocked_by_a_corrupt_stored_mac(app, client):
    """Same class of bug as the cycle case already fixed in the previous
    round: pre-existing corruption in a field an edit doesn't touch must not
    block that edit. Forges a node with a malformed onu_mac directly via the
    ORM -- every write path through the API always runs onu_mac through
    _canonical_mac before saving, so a non-canonical/invalid stored value
    can only arise from data that predates that invariant -- then confirms a
    label-only PUT still succeeds instead of 400ing on "onu_mac is not a
    valid MAC"."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    root = _node(client, admin, olt, kind='root', label='CR').get_json()['id']
    with app.app_context():
        onu = appmod.NetworkNode(
            tenant_id=1, olt_device_id=olt, kind='onu', label='Corrupt',
            latitude=34.4367, longitude=35.8497, parent_node_id=root,
            onu_mac='not-a-mac')
        appmod.db.session.add(onu)
        appmod.db.session.commit()
        onu_id = onu.id

    r = client.put(f'/api/network-map/nodes/{onu_id}', headers=admin,
                   json={'label': 'Renamed'})
    assert r.status_code == 200
    body = r.get_json()
    assert body['label'] == 'Renamed'
    # Untouched and unvalidated -- still the corrupt value that was stored.
    assert body['onu_mac'] == 'not-a-mac'

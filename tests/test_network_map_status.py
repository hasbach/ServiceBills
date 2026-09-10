"""The status algorithm, tested as the pure function it is. No app context, no
database, no device -- these use a tiny stand-in with the three attributes the
algorithm actually reads, so a schema change cannot silently break these tests
into vacuous passes."""
import app as appmod


class FakeNode:
    def __init__(self, id, kind, parent_node_id=None, onu_mac=None):
        self.id = id
        self.kind = kind
        self.parent_node_id = parent_node_id
        self.onu_mac = onu_mac


def chain(*macs):
    """root -> onu(mac[0]) -> onu(mac[1]) -> ... as a single line."""
    nodes = [FakeNode(1, 'root')]
    for i, mac in enumerate(macs, start=2):
        nodes.append(FakeNode(i, 'onu', parent_node_id=i - 1, onu_mac=mac))
    return nodes


def span_for(result, child_id):
    return next(s for s in result['spans'] if s['child_node_id'] == child_id)


A, B, C = 'aa:aa:aa:aa:aa:aa', 'bb:bb:bb:bb:bb:bb', 'cc:cc:cc:cc:cc:cc'


def test_all_online_is_all_green():
    nodes = chain(A, B, C)
    r = appmod._compute_map_status(
        nodes, {A: 'online', B: 'online', C: 'online'})
    assert [s['status'] for s in r['spans']] == ['green', 'green', 'green']
    assert not any(s['is_fault_boundary'] for s in r['spans'])


def test_offline_node_with_live_downstream_keeps_the_trunk_green():
    """The owner's case 1: C being up proves the fibre through B is intact, so
    the fault is LOCAL to B -- its own drop or its power -- not a cut span."""
    nodes = chain(A, B, C)
    r = appmod._compute_map_status(
        nodes, {A: 'online', B: 'offline', C: 'online'})
    assert span_for(r, 3)['status'] == 'green'      # root -> B stays green
    assert r['node_status'][3] == 'offline'         # B's own dot is red
    assert not any(s['is_fault_boundary'] for s in r['spans'])


def test_dead_downstream_marks_the_cut_span_as_the_fault_boundary():
    """The owner's case 2: nothing downstream survives, so the span into B is
    cut. B->C is also red but is consequence, not cause."""
    nodes = chain(A, B, C)
    r = appmod._compute_map_status(
        nodes, {A: 'online', B: 'offline', C: 'offline'})
    assert span_for(r, 3)['status'] == 'red'
    assert span_for(r, 4)['status'] == 'red'
    assert span_for(r, 3)['is_fault_boundary'] is True
    assert span_for(r, 4)['is_fault_boundary'] is False


def test_branch_point_with_one_live_and_one_dead_child():
    """A junction is alive if ANY child subtree is alive, so the span to the
    dead child becomes the boundary with no special-casing."""
    nodes = [FakeNode(1, 'root'),
             FakeNode(2, 'junction', parent_node_id=1),
             FakeNode(3, 'onu', parent_node_id=2, onu_mac=A),
             FakeNode(4, 'onu', parent_node_id=2, onu_mac=B)]
    r = appmod._compute_map_status(nodes, {A: 'online', B: 'offline'})
    assert span_for(r, 2)['status'] == 'green'
    assert span_for(r, 3)['status'] == 'green'
    assert span_for(r, 4)['status'] == 'red'
    assert span_for(r, 4)['is_fault_boundary'] is True
    assert r['node_status'][2] == 'online'


def test_entirely_dead_tree_yields_no_boundary():
    """Nothing is alive, so no red span has a live parent. The fault is the OLT
    or upstream of it, which the caller handles as its own case -- the absence
    of a boundary must not be mistaken for the absence of a fault."""
    nodes = chain(A, B)
    r = appmod._compute_map_status(nodes, {A: 'offline', B: 'offline'})
    assert all(s['status'] == 'red' for s in r['spans'])
    assert not any(s['is_fault_boundary'] for s in r['spans'])


def test_unknown_mac_is_grey_not_red():
    """A placed ONU the OLT has never reported is unknown, not down. Colouring
    it red would invent an outage."""
    nodes = chain(A)
    r = appmod._compute_map_status(nodes, {})
    assert span_for(r, 2)['status'] == 'grey'
    assert r['node_status'][2] == 'unknown'


def test_a_junction_with_no_known_onu_below_it_is_grey():
    nodes = [FakeNode(1, 'root'), FakeNode(2, 'junction', parent_node_id=1)]
    r = appmod._compute_map_status(nodes, {})
    assert span_for(r, 2)['status'] == 'grey'


def test_mac_join_is_separator_insensitive():
    """The OLT emits colons; a staff-entered MAC may use hyphens or dots.

    Checks the span colour too, not just node_status: node_status for an ONU
    is read straight off onu_status, but a span's colour comes from the
    alive/known state the bottom-up walk accumulates from a SEPARATE lookup.
    A version that normalizes one and not the other would still get this
    node's own status right while silently leaving every span through it
    grey -- asserting on node_status alone would not catch that."""
    nodes = [FakeNode(1, 'root'),
             FakeNode(2, 'onu', parent_node_id=1, onu_mac='AA-AA-AA-AA-AA-AA')]
    r = appmod._compute_map_status(
        nodes, {appmod._normalize_mac(A): 'online'})
    assert r['node_status'][2] == 'online'
    assert span_for(r, 2)['status'] == 'green'


def test_a_cycle_is_reported_as_orphans_not_silently_dropped():
    """Tree v2 shipped a builder that silently dropped whole cyclic
    components. A cycle here must be surfaced, and must not crash."""
    nodes = [FakeNode(1, 'root'),
             FakeNode(2, 'onu', parent_node_id=3, onu_mac=A),
             FakeNode(3, 'onu', parent_node_id=2, onu_mac=B)]
    r = appmod._compute_map_status(nodes, {A: 'online', B: 'online'})
    assert r['orphans'] == [2, 3]
    assert all(s['child_node_id'] == 1 or s['child_node_id'] not in (2, 3)
               for s in r['spans'])


def test_a_node_parented_to_itself_is_treated_as_a_root():
    nodes = [FakeNode(1, 'root'), FakeNode(2, 'junction', parent_node_id=2)]
    r = appmod._compute_map_status(nodes, {})
    assert r['orphans'] == []


def test_empty_input_is_not_an_error():
    r = appmod._compute_map_status([], {})
    assert r == {'spans': [], 'node_status': {}, 'orphans': []}


def test_output_is_deterministic_across_input_ordering():
    """Dict and set iteration order must not leak into the response."""
    nodes = chain(A, B, C)
    status = {A: 'online', B: 'offline', C: 'offline'}
    first = appmod._compute_map_status(nodes, status)
    second = appmod._compute_map_status(list(reversed(nodes)), status)
    assert first == second

"""The check is a backstop against a data-entry blunder, NOT a measure of
accuracy. An earlier spec draft proposed a 15% tolerance; the owner reports an
ONU physically 1m from the OLT reading as 6m, so the error is a fixed offset of
several metres, not proportional -- 500% at 1m, 0.3% at 1500m. Any single
percentage is therefore wrong at one end or the other, and the check requires
BOTH a proportional and an absolute overshoot before it says anything."""
import app as appmod


class FakeNode:
    def __init__(self, id, kind, parent_node_id=None, onu_mac=None,
                 latitude=34.4367, longitude=35.8497):
        self.id = id
        self.kind = kind
        self.parent_node_id = parent_node_id
        self.onu_mac = onu_mac
        self.latitude = latitude
        self.longitude = longitude


def onu_row(mac, distance):
    return {'mac_address': mac, 'status': 'online', 'distance': distance}


MAC = 'aa:aa:aa:aa:aa:aa'


def test_the_owners_one_metre_reads_as_six_metres_case_does_not_flag():
    """The case that killed the percentage rule. Must stay silent."""
    nodes = [FakeNode(1, 'root'),
             FakeNode(2, 'onu', parent_node_id=1, onu_mac=MAC,
                      latitude=34.43671, longitude=35.84970)]
    assert appmod._map_distance_warnings(nodes, [onu_row(MAC, 6)]) == []


def test_short_reported_distances_never_flag_whatever_the_chain_says():
    """Below the floor, ranging error swamps the signal entirely."""
    nodes = [FakeNode(1, 'root'),
             FakeNode(2, 'onu', parent_node_id=1, onu_mac=MAC,
                      latitude=34.4467, longitude=35.8497)]   # ~1.1km away
    assert appmod._map_distance_warnings(nodes, [onu_row(MAC, 99)]) == []


def test_a_pin_in_the_wrong_village_flags():
    """~5.5km of chain against a reported 400m: both conditions met."""
    nodes = [FakeNode(1, 'root'),
             FakeNode(2, 'onu', parent_node_id=1, onu_mac=MAC,
                      latitude=34.4867, longitude=35.8497)]
    warnings = appmod._map_distance_warnings(nodes, [onu_row(MAC, 400)])
    assert len(warnings) == 1
    assert warnings[0]['node_id'] == 2
    assert warnings[0]['reported_metres'] == 400
    assert warnings[0]['chain_metres'] > 5000


def test_proportional_overshoot_with_gap_above_floor_flags():
    """A brief draft of this test expected this input to stay silent, on the
    theory that only the proportional condition was met. It is not: with
    MIN_GAP_METRES == MIN_METRES == 100 and FACTOR == 2.0, chain > FACTOR *
    distance algebraically forces chain - distance > distance whenever
    distance >= MIN_METRES -- so the absolute gate can never independently
    save a distance that has already cleared the floor and the proportional
    check. (Proportional-alone genuinely IS silent below the floor -- see
    test_short_reported_distances_never_flag_whatever_the_chain_says -- just
    not above it.) Both conditions hold here, so this correctly flags."""
    nodes = [FakeNode(1, 'root'),
             FakeNode(2, 'onu', parent_node_id=1, onu_mac=MAC,
                      latitude=34.44025, longitude=35.8497)]   # ~395m
    warnings = appmod._map_distance_warnings(nodes, [onu_row(MAC, 150)])
    assert len(warnings) == 1
    assert warnings[0]['node_id'] == 2


def test_absolute_overshoot_alone_does_not_flag():
    """A 900m gap on a 4km reported run is well under 2x -- legitimate slack
    on a long route, so silent."""
    nodes = [FakeNode(1, 'root'),
             FakeNode(2, 'onu', parent_node_id=1, onu_mac=MAC,
                      latitude=34.4808, longitude=35.8497)]   # ~4.9km
    assert appmod._map_distance_warnings(nodes, [onu_row(MAC, 4000)]) == []


def test_a_chain_shorter_than_reported_never_flags():
    nodes = [FakeNode(1, 'root'),
             FakeNode(2, 'onu', parent_node_id=1, onu_mac=MAC,
                      latitude=34.4394, longitude=35.8497)]   # ~300m
    assert appmod._map_distance_warnings(nodes, [onu_row(MAC, 1200)]) == []


def test_chain_length_accumulates_through_intermediate_hops():
    """The chain is the sum of its spans, not the straight line from root."""
    nodes = [FakeNode(1, 'root', latitude=34.4367, longitude=35.8497),
             FakeNode(2, 'junction', parent_node_id=1,
                      latitude=34.4667, longitude=35.8497),
             FakeNode(3, 'onu', parent_node_id=2, onu_mac=MAC,
                      latitude=34.4367, longitude=35.8497)]
    # Doubles back, so the chain is ~6.6km while the endpoint sits on the root.
    warnings = appmod._map_distance_warnings(nodes, [onu_row(MAC, 500)])
    assert len(warnings) == 1
    assert warnings[0]['chain_metres'] > 6000


def test_an_onu_with_no_reported_distance_is_skipped():
    nodes = [FakeNode(1, 'root'),
             FakeNode(2, 'onu', parent_node_id=1, onu_mac=MAC,
                      latitude=34.4867, longitude=35.8497)]
    assert appmod._map_distance_warnings(nodes, [onu_row(MAC, 0)]) == []
    assert appmod._map_distance_warnings(nodes, []) == []


def test_a_cycle_does_not_hang_the_check():
    nodes = [FakeNode(2, 'onu', parent_node_id=3, onu_mac=MAC),
             FakeNode(3, 'junction', parent_node_id=2)]
    assert appmod._map_distance_warnings(nodes, [onu_row(MAC, 400)]) == []


def test_to_int_or_none_distinguishes_missing_from_genuinely_zero():
    """_map_distance_warnings' own filter (`if mac and distance:`) treats
    None and 0 identically -- both falsy -- so this distinction is invisible
    through that path alone and needs its own direct check. Reusing
    vsol_olt._to_int's default=0 here would collapse a missing/malformed
    reading into the same value as a genuine 'offline' report."""
    assert appmod._to_int_or_none('not-a-number') is None
    assert appmod._to_int_or_none(None) is None
    assert appmod._to_int_or_none('42') == 42
    assert appmod._to_int_or_none(0) == 0


def test_mismatched_mac_separator_styles_still_match():
    """The stored node MAC and the OLT-reported MAC may differ in separator
    style (colon vs hyphen); the lookup must normalize both sides."""
    nodes = [FakeNode(1, 'root'),
             FakeNode(2, 'onu', parent_node_id=1, onu_mac='AA-AA-AA-AA-AA-AA',
                      latitude=34.4867, longitude=35.8497)]
    warnings = appmod._map_distance_warnings(nodes, [onu_row(MAC, 400)])
    assert len(warnings) == 1
    assert warnings[0]['node_id'] == 2

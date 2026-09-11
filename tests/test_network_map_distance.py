"""The check is a backstop against a data-entry blunder, NOT a measure of
accuracy. An earlier spec draft proposed a 15% tolerance; the owner reports an
ONU physically 1m from the OLT reading as 6m, so the error is a fixed offset of
several metres, not proportional -- 500% at 1m, 0.3% at 1500m. Any single
percentage is therefore wrong at one end or the other, and the check requires
BOTH a proportional and an absolute overshoot before it says anything."""
import math
import threading

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
    """Removing the seen-set filter inside _map_distance_warnings survives
    the suite otherwise, so this pins its termination property directly.

    Run off the main thread and join with a timeout: if the seen-set guard
    ever regresses, the walk over this forged cycle hangs forever rather
    than returning, and without pytest-timeout installed (deliberately not
    added) an un-timed call here would hang the whole suite/CI instead of
    failing this one test. 15s is generous for a walk that normally takes
    milliseconds. Mirrors the same pattern used in test_network_map_api.py
    for _node_descendant_ids' own termination guard.
    """
    nodes = [FakeNode(2, 'onu', parent_node_id=3, onu_mac=MAC),
             FakeNode(3, 'junction', parent_node_id=2)]
    result = {}

    def run():
        result['warnings'] = appmod._map_distance_warnings(
            nodes, [onu_row(MAC, 400)])

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=15)
    assert not t.is_alive(), (
        'distance-check walk did not terminate within 15s -- the seen-set '
        'termination guard in _map_distance_warnings appears to have '
        'regressed')
    assert result['warnings'] == []


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


# --- FINDING 2: boundary tests pinning the three constants -----------------
#
# Before these, DISTANCE_CHECK_MIN_METRES, DISTANCE_CHECK_FACTOR and
# DISTANCE_CHECK_MIN_GAP_METRES could each be changed (100->150, 2.0->1.5,
# 100->50 or ->200) with the suite staying green, and every `<`/`>` in the
# condition could be flipped to `<=`/`>=` with no test noticing either.

def test_reported_distance_at_the_floor_boundary():
    """Pins DISTANCE_CHECK_MIN_METRES at exactly 100, and the `<` direction
    (not `<=`): 99 must be skipped no matter how egregious the chain; 100
    must be evaluated, and here -- with a chain long enough to clear the
    other condition regardless -- must flag."""
    assert appmod.DISTANCE_CHECK_MIN_METRES == 100
    nodes = [FakeNode(1, 'root'),
             FakeNode(2, 'onu', parent_node_id=1, onu_mac=MAC,
                      latitude=34.4867, longitude=35.8497)]   # ~5.5km chain

    assert appmod._map_distance_warnings(nodes, [onu_row(MAC, 99)]) == []

    warnings = appmod._map_distance_warnings(nodes, [onu_row(MAC, 100)])
    assert len(warnings) == 1
    assert warnings[0]['reported_metres'] == 100


def test_chain_at_the_factor_boundary():
    """Pins DISTANCE_CHECK_FACTOR at exactly 2.0, and the `>` direction (not
    `>=`): with the floor already cleared (reported=1000), a chain a hair
    under 2x reported must not flag, while a chain meaningfully above 2x
    must.

    The "hair under" is a deliberate 0.1mm nudge, not a typo: the haversine
    round-trip (metres -> latitude offset -> _great_circle_metres) carries
    its own float noise of roughly 1e-10m at this scale, so landing exactly
    on the mathematical tie is not reproducible. A nudge five orders of
    magnitude larger than that noise floor lands reliably on the "must not
    flag" side while still pinning both the 2.0 factor (a lower factor would
    make this chain flag) and the meaningfully-above case (a higher factor
    would make it stay silent).
    """
    assert appmod.DISTANCE_CHECK_FACTOR == 2.0
    distance = 1000

    at_nodes = [FakeNode(1, 'root'),
                FakeNode(2, 'onu', parent_node_id=1, onu_mac=MAC,
                         latitude=34.45468643121905, longitude=35.8497)]
    assert appmod._map_distance_warnings(at_nodes, [onu_row(MAC, distance)]) == []

    above_nodes = [FakeNode(1, 'root'),
                   FakeNode(2, 'onu', parent_node_id=1, onu_mac=MAC,
                            latitude=34.456485075330214, longitude=35.8497)]
    warnings = appmod._map_distance_warnings(above_nodes, [onu_row(MAC, distance)])
    assert len(warnings) == 1


def test_absolute_gap_clause_is_implied_by_the_factor_clause_given_the_floor():
    """The absolute-gap clause is provably redundant for any input that
    clears the floor: given reported >= DISTANCE_CHECK_MIN_METRES, chain >
    reported * DISTANCE_CHECK_FACTOR algebraically forces
    chain - reported > DISTANCE_CHECK_MIN_GAP_METRES (both currently 100).
    That means no case exists where the gap clause alone decides the
    outcome, so it cannot be pinned with a boundary case the way the other
    two constants are -- fabricating one would misrepresent the code. This
    instead asserts the documented relationship itself, directly, over a
    handful of representative reported distances at or above the floor."""
    assert appmod.DISTANCE_CHECK_MIN_GAP_METRES == 100
    for distance in (100, 150, 400, 1000, 5000):
        assert distance >= appmod.DISTANCE_CHECK_MIN_METRES
        # The smallest chain that clears the proportional clause.
        chain = distance * appmod.DISTANCE_CHECK_FACTOR + 1
        assert chain - distance > appmod.DISTANCE_CHECK_MIN_GAP_METRES

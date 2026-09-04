"""Tests for vsol_olt.py -- no real OLT involved. The seam is
`_walk_onu_table`, monkeypatched to return the {(col, row): value} cell map a
real walk produces. The fixtures below are real rows captured from DeltaNet's
V1600D on 2026-09-04, including two genuine duplicate-MAC pairs (stale
authorization entries left behind when an ONU was moved between PON ports).

See docs/superpowers/specs/2026-09-01-network-topology-tree-design.md."""
import asyncio
import types

import pytest

import vsol_olt


def make_device(id=1, host="192.168.8.100", api_port=161, password="public"):
    return types.SimpleNamespace(
        id=id, host=host, api_port=api_port, password=password,
        last_checked_at=None, last_status=None,
    )


# Real rows: (pon, onu_index, status, mac, model, description, distance)
REAL_ROWS = [
    ("1", "1",  "0", "4c:d7:c8:cc:49:e8", "unknow", "AlaycheOil",   "0"),
    ("1", "2",  "1", "b4:64:15:3f:c1:94", "V2801D", "MoussaGhadir", "531"),
    ("1", "3",  "1", "f4:c4:d6:4d:88:81", "unknow", "aLIhACHEM",    "502"),
    # Stale ghost of PON1:3 -- same MAC, offline, unlabelled, zero distance.
    ("8", "3",  "0", "f4:c4:d6:4d:88:81", "unknow", "NULL",         "0"),
    # Duplicate pair where the ONLINE row is the one on the higher PON port.
    ("3", "28", "0", "4c:d7:c8:f9:c8:20", "unknow", "NULL",         "0"),
    ("4", "29", "1", "4c:d7:c8:f9:c8:20", "V2801S", "MehyaKhatib",  "700"),
]


def cells_from(rows):
    """Build the {(col, row_index): value} map a real table walk produces."""
    cells = {}
    for row_index, (pon, onu, status, mac, model, desc, dist) in enumerate(rows):
        cells[("2", str(row_index))] = pon
        cells[("3", str(row_index))] = onu
        cells[("5", str(row_index))] = status
        cells[("6", str(row_index))] = mac
        cells[("7", str(row_index))] = model
        cells[("10", str(row_index))] = desc
        cells[("13", str(row_index))] = dist
    return cells


def patch_walk(monkeypatch, cells=None, exc=None):
    async def fake_walk(host, port, community):
        if exc is not None:
            raise exc
        return cells
    monkeypatch.setattr(vsol_olt, "_walk_onu_table", fake_walk)


def test_parses_every_pon_port_and_both_statuses(monkeypatch):
    patch_walk(monkeypatch, cells_from(REAL_ROWS))
    ok, onus = vsol_olt.get_olt_status(make_device())
    assert ok is True
    # 6 rows in, 4 out -- two duplicate MACs collapsed.
    assert len(onus) == 4
    assert {o["pon_port"] for o in onus} == {"PON1", "PON4"}
    first = onus[0]
    assert first["onu_id"] == "EPON0/1:1"
    assert first["status"] == "offline"
    assert first["mac_address"] == "4c:d7:c8:cc:49:e8"
    assert first["description"] == "AlaycheOil"
    assert first["distance_m"] == 0
    online = [o for o in onus if o["status"] == "online"]
    assert len(online) == 3


def test_literal_NULL_description_becomes_none(monkeypatch):
    patch_walk(monkeypatch, cells_from([
        ("1", "1", "1", "aa:bb:cc:dd:ee:ff", "V2801D", "NULL", "100"),
    ]))
    ok, onus = vsol_olt.get_olt_status(make_device())
    assert ok is True
    assert onus[0]["description"] is None


def test_dedupe_prefers_the_online_row(monkeypatch):
    patch_walk(monkeypatch, cells_from(REAL_ROWS))
    ok, onus = vsol_olt.get_olt_status(make_device())
    by_mac = {o["mac_address"]: o for o in onus}
    # The PON8 ghost must lose to the live PON1 row.
    assert by_mac["f4:c4:d6:4d:88:81"]["pon_port"] == "PON1"
    assert by_mac["f4:c4:d6:4d:88:81"]["status"] == "online"
    # ...and the winner is the online row even when it sits on a HIGHER port.
    assert by_mac["4c:d7:c8:f9:c8:20"]["pon_port"] == "PON4"
    assert by_mac["4c:d7:c8:f9:c8:20"]["description"] == "MehyaKhatib"


def test_dedupe_all_offline_prefers_the_labelled_row(monkeypatch):
    patch_walk(monkeypatch, cells_from([
        ("8", "2", "0", "11:22:33:44:55:66", "unknow", "NULL",   "0"),
        ("1", "9", "0", "11:22:33:44:55:66", "unknow", "Golden", "0"),
    ]))
    ok, onus = vsol_olt.get_olt_status(make_device())
    assert len(onus) == 1
    assert onus[0]["description"] == "Golden"
    assert onus[0]["pon_port"] == "PON1"


def test_dedupe_all_offline_and_unlabelled_prefers_lowest_pon(monkeypatch):
    patch_walk(monkeypatch, cells_from([
        ("8", "2", "0", "11:22:33:44:55:66", "unknow", "NULL", "0"),
        ("3", "9", "0", "11:22:33:44:55:66", "unknow", "NULL", "0"),
    ]))
    ok, onus = vsol_olt.get_olt_status(make_device())
    assert len(onus) == 1
    assert onus[0]["pon_port"] == "PON3"


def test_dedupe_online_beats_description_and_pon(monkeypatch):
    """Isolates preference term 1 (online). The online row has NO
    description and sits on the HIGHER PON port; the offline row HAS a
    description and sits on the LOWER PON port. Only the online term can
    pick the correct winner -- description or PON alone would pick the
    offline row."""
    patch_walk(monkeypatch, cells_from([
        ("5", "1", "1", "aa:11:11:11:11:11", "unknow", "NULL",   "0"),
        ("2", "9", "0", "aa:11:11:11:11:11", "unknow", "Golden", "0"),
    ]))
    ok, onus = vsol_olt.get_olt_status(make_device())
    assert ok is True
    assert len(onus) == 1
    assert onus[0]["status"] == "online"
    assert onus[0]["pon_port"] == "PON5"
    assert onus[0]["description"] is None


def test_dedupe_description_beats_pon_when_both_offline(monkeypatch):
    """Isolates preference term 2 (description). Both rows are offline
    (tied on term 1) and at equal distance (tied on term 3), but the
    labelled row sits on a HIGHER PON port than the NULL one -- PON alone
    would pick the wrong, unlabelled row."""
    patch_walk(monkeypatch, cells_from([
        ("8", "5", "0", "aa:22:22:22:22:22", "unknow", "Golden", "50"),
        ("3", "9", "0", "aa:22:22:22:22:22", "unknow", "NULL",   "50"),
    ]))
    ok, onus = vsol_olt.get_olt_status(make_device())
    assert ok is True
    assert len(onus) == 1
    assert onus[0]["pon_port"] == "PON8"
    assert onus[0]["description"] == "Golden"


def test_dedupe_distance_beats_pon_when_description_ties(monkeypatch):
    """Isolates preference term 3 (distance). Both rows are offline and
    unlabelled (tied on terms 1-2), but the greater-distance row sits on a
    HIGHER PON port than the shorter-distance one -- PON alone would pick
    the wrong, shorter-distance row."""
    patch_walk(monkeypatch, cells_from([
        ("7", "4", "0", "aa:33:33:33:33:33", "unknow", "NULL", "500"),
        ("2", "6", "0", "aa:33:33:33:33:33", "unknow", "NULL", "100"),
    ]))
    ok, onus = vsol_olt.get_olt_status(make_device())
    assert ok is True
    assert len(onus) == 1
    assert onus[0]["pon_port"] == "PON7"
    assert onus[0]["distance_m"] == 500


def test_result_is_sorted_by_pon_then_onu_numerically(monkeypatch):
    patch_walk(monkeypatch, cells_from([
        ("4", "29", "1", "aa:00:00:00:00:01", "x", "d1", "1"),
        ("1", "10", "1", "aa:00:00:00:00:02", "x", "d2", "1"),
        ("1", "2",  "1", "aa:00:00:00:00:03", "x", "d3", "1"),
    ]))
    ok, onus = vsol_olt.get_olt_status(make_device())
    assert [o["onu_id"] for o in onus] == ["EPON0/1:2", "EPON0/1:10", "EPON0/4:29"]


def test_marks_device_online_on_success(monkeypatch):
    patch_walk(monkeypatch, cells_from(REAL_ROWS))
    device = make_device()
    ok, _ = vsol_olt.get_olt_status(device)
    assert ok is True
    assert device.last_status == "online"
    assert device.last_checked_at is not None


def test_unreachable_olt_never_raises(monkeypatch):
    patch_walk(monkeypatch, exc=OSError("No SNMP response received before timeout"))
    device = make_device()
    ok, message = vsol_olt.get_olt_status(device)
    assert ok is False
    assert isinstance(message, str) and message
    assert device.last_status == "unreachable"


def test_snmp_error_status_is_reported_as_auth_failed(monkeypatch):
    patch_walk(monkeypatch, exc=vsol_olt.OltRejected("noAccess"))
    device = make_device()
    ok, message = vsol_olt.get_olt_status(device)
    assert ok is False
    assert "noAccess" in message
    assert device.last_status == "auth_failed"


def test_empty_table_is_a_failure_not_an_empty_success(monkeypatch):
    """A wrong community on SNMPv2c looks like silence, and a non-VSOL device
    answers the walk with nothing -- neither should read as 'zero ONUs'."""
    patch_walk(monkeypatch, {})
    device = make_device()
    ok, message = vsol_olt.get_olt_status(device)
    assert ok is False
    assert "no ONUs" in message
    assert device.last_status == "unreachable"


def test_rows_without_a_mac_are_skipped(monkeypatch):
    cells = cells_from([("1", "1", "1", "aa:bb:cc:dd:ee:ff", "x", "ok", "5")])
    cells[("6", "1")] = ""      # a second row whose MAC column is blank
    cells[("2", "1")] = "1"
    cells[("3", "1")] = "2"
    cells[("5", "1")] = "0"
    patch_walk(monkeypatch, cells)
    ok, onus = vsol_olt.get_olt_status(make_device())
    assert ok is True
    assert len(onus) == 1


def test_dedupe_onu_index_breaks_the_final_tie(monkeypatch):
    """Isolates preference term 5 (the -_onu totality term). Both rows share
    the same MAC and PON port, are both offline, both unlabelled (NULL), and
    tied on distance -- terms 1-4 are all tied. Only the ONU index can break
    the tie; without it, the winner falls through to walk order instead of
    being decided by the row data, and the lower ONU index would not
    reliably win. Realistic on a device that leaves a stale authorization
    entry with a different ONU index on the same PON port."""
    patch_walk(monkeypatch, cells_from([
        ("6", "9", "0", "aa:44:44:44:44:44", "unknow", "NULL", "200"),
        ("6", "3", "0", "aa:44:44:44:44:44", "unknow", "NULL", "200"),
    ]))
    ok, onus = vsol_olt.get_olt_status(make_device())
    assert ok is True
    assert len(onus) == 1
    assert onus[0]["onu_id"] == "EPON0/6:3"


def test_walk_onu_table_closes_engine_when_the_walk_raises_partway_through(monkeypatch):
    """Pins the try/finally in _walk_onu_table: the SNMP engine must be
    released even when the walk raises after already yielding data. Every
    other test in this file patches _walk_onu_table wholesale, so nothing
    else would notice if that finally block (and its _safe_close call) were
    removed."""

    class FakeEngine:
        def __init__(self):
            self.closed = False

        def close_dispatcher(self):
            self.closed = True

    class FakeTransportTarget:
        @staticmethod
        async def create(*args, **kwargs):
            return object()

    fake_engine = FakeEngine()

    async def fake_bulk_walk_cmd(*args, **kwargs):
        yield (None, None, None, [])  # one clean chunk, no error
        raise RuntimeError("simulated failure mid-walk")

    monkeypatch.setattr(vsol_olt, "SnmpEngine", lambda: fake_engine)
    monkeypatch.setattr(vsol_olt, "UdpTransportTarget", FakeTransportTarget)
    monkeypatch.setattr(vsol_olt, "bulk_walk_cmd", fake_bulk_walk_cmd)

    with pytest.raises(RuntimeError):
        asyncio.run(vsol_olt._walk_onu_table("192.168.8.100", 161, "public"))

    assert fake_engine.closed is True

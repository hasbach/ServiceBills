"""get_cpe_locations joins the OLT's MAC-learning table to its interface
names, so a customer's own router identifies which ONU they sit behind.

The varbind shapes here were recorded from DeltaNet's real V-SOL V1600D on
2026-09-05: bridge port 10 is GE0/10, the uplink, carrying 186 of the 316
learned MACs and attributable to nobody. See
docs/superpowers/specs/2026-09-06-cpe-mac-linking-design.md.
"""
import types

import vsol_olt


ONUS = [
    {"pon_port": "PON1", "onu_id": "EPON0/1:2", "status": "online",
     "mac_address": "b4:64:15:3f:c1:94", "description": "MoussaGhadir",
     "model": "V2801D", "distance_m": 531},
    {"pon_port": "PON3", "onu_id": "EPON0/3:1", "status": "online",
     "mac_address": "4c:d7:c8:cc:4a:5c", "description": "AbirKerdy",
     "model": "V2801S", "distance_m": 619},
]

# {bridge port -> ifName}, exactly the shape ifName returns.
IFNAMES = {
    10: "GE0/10",          # the uplink
    19: "EPON0/3",         # a PON interface, not an ONU
    26: "EPON01ONU2 MoussaGhadir",
    41: "EPON03ONU1 AbirKerdy",
    99: "EPON07ONU4 Nobody",   # a real ONU port with no matching ONU row
}

FDB = {
    "00:0c:42:db:51:be": 10,   # uplink -- must be dropped
    "00:14:78:54:c5:4f": 10,   # uplink -- must be dropped
    "aa:bb:cc:00:00:01": 26,   # behind MoussaGhadir's ONU
    "aa:bb:cc:00:00:02": 26,   # a second device behind the same ONU
    "aa:bb:cc:00:00:03": 41,   # behind AbirKerdy's ONU
    "aa:bb:cc:00:00:04": 19,   # PON-level -- identifies a tree, not a leaf
    "aa:bb:cc:00:00:05": 99,   # ONU-shaped but no ONU row to join to
    "aa:bb:cc:00:00:06": 77,   # a port ifName never reported
}


def _patch(monkeypatch, fdb=None, ifnames=None, onus=None):
    monkeypatch.setattr(vsol_olt, "_walk_fdb_ports",
                        lambda *a, **k: FDB if fdb is None else fdb)
    monkeypatch.setattr(vsol_olt, "_walk_if_names",
                        lambda *a, **k: IFNAMES if ifnames is None else ifnames)
    monkeypatch.setattr(vsol_olt, "get_olt_status",
                        lambda s: (True, ONUS if onus is None else onus))


def _server():
    return types.SimpleNamespace(host="192.168.8.100", api_port=161,
                                 password="public", last_status=None)


def test_uplink_macs_are_excluded(monkeypatch):
    _patch(monkeypatch)
    ok, value = vsol_olt.get_cpe_locations(_server())
    assert ok
    assert "00:0c:42:db:51:be" not in value
    assert "00:14:78:54:c5:4f" not in value


def test_a_pon_level_port_is_excluded(monkeypatch):
    _patch(monkeypatch)
    ok, value = vsol_olt.get_cpe_locations(_server())
    assert ok
    assert "aa:bb:cc:00:00:04" not in value


def test_several_cpes_behind_one_onu_all_resolve(monkeypatch):
    _patch(monkeypatch)
    ok, value = vsol_olt.get_cpe_locations(_server())
    assert ok
    assert value["aa:bb:cc:00:00:01"] == {
        "pon_port": "PON1", "onu_id": "EPON0/1:2",
        "onu_mac": "b4:64:15:3f:c1:94"}
    assert value["aa:bb:cc:00:00:02"]["onu_mac"] == "b4:64:15:3f:c1:94"
    assert value["aa:bb:cc:00:00:03"]["onu_mac"] == "4c:d7:c8:cc:4a:5c"


def test_an_onu_port_with_no_matching_onu_row_is_skipped(monkeypatch):
    _patch(monkeypatch)
    ok, value = vsol_olt.get_cpe_locations(_server())
    assert ok
    assert "aa:bb:cc:00:00:05" not in value


def test_a_port_with_no_ifname_is_skipped(monkeypatch):
    _patch(monkeypatch)
    ok, value = vsol_olt.get_cpe_locations(_server())
    assert ok
    assert "aa:bb:cc:00:00:06" not in value


def test_an_onu_walk_failure_is_reported_not_raised(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setattr(vsol_olt, "get_olt_status", lambda s: (False, "timeout"))
    ok, value = vsol_olt.get_cpe_locations(_server())
    assert ok is False
    assert "timeout" in value


def test_an_snmp_failure_is_reported_not_raised(monkeypatch):
    def boom(*a, **k):
        raise OSError("No SNMP response received before timeout")
    _patch(monkeypatch)
    monkeypatch.setattr(vsol_olt, "_walk_fdb_ports", boom)
    ok, value = vsol_olt.get_cpe_locations(_server())
    assert ok is False
    assert "timeout" in value.lower()


def test_no_learned_macs_is_success_with_an_empty_map(monkeypatch):
    _patch(monkeypatch, fdb={})
    ok, value = vsol_olt.get_cpe_locations(_server())
    assert ok is True
    assert value == {}


# Real dot1dTpFdbPort indices recorded from DeltaNet's V-SOL V1600D on
# 2026-09-06: every one of the device's 311 entries carries a leading OCTET
# STRING length ("6") before the six MAC octets, not the bare six-component
# form the MIB nominally specifies. _walk_fdb_ports must parse both shapes
# from the last six components of the index, and must skip -- never raise --
# a malformed row.
_FDB_INDEX_CELLS = {
    # Length-prefixed (7 components), the real device's actual shape.
    "6.0.12.66.219.81.190": "10",   # -> 00:0c:42:db:51:be
    "6.0.20.120.84.197.79": "10",   # -> 00:14:78:54:c5:4f
    "6.0.35.90.215.110.46": "10",   # -> 00:23:5a:d7:6e:2e
    # Bare (6 components), the form the MIB nominally specifies.
    "170.187.204.0.0.7": "26",      # -> aa:bb:cc:00:00:07
    # Too few components -- not a MAC index at all.
    "1.2.3.4": "10",
    # A component above 255 -- not a valid octet.
    "6.0.12.66.219.81.300": "10",
    # A well-formed index, but the value (bridge port) isn't an integer.
    "6.0.12.66.219.81.191": "not-a-number",
}


def test_walk_fdb_ports_parses_the_real_devices_length_prefixed_index(monkeypatch):
    async def fake_walk_oid(host, port, community, oid):
        assert oid == vsol_olt.FDB_PORT_OID
        return _FDB_INDEX_CELLS

    monkeypatch.setattr(vsol_olt, "_walk_oid", fake_walk_oid)

    result = vsol_olt._walk_fdb_ports("192.168.8.100", 161, "public")

    assert result == {
        "00:0c:42:db:51:be": 10,
        "00:14:78:54:c5:4f": 10,
        "00:23:5a:d7:6e:2e": 10,
        "aa:bb:cc:00:00:07": 26,
    }
    # The malformed rows must be skipped, not raise and not appear.
    assert len(result) == 4

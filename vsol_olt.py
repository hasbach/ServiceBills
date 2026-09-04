"""SNMP adapter for V-SOL EPON OLTs -- NetworkDevice rows with
device_type='vsol_olt'. The counterpart to mikrotik.py, which handles
device_type='mikrotik_ccr'.

Everything the Network Tree needs comes from ONE table walk, verified live
against DeltaNet's V1600D on 2026-09-04:

    1.3.6.1.4.1.37950.1.1.5.12.1.12.1.<column>.<row>

with a flat row index (0..N-1) and the columns mapped below. That table lists
every authorized ONU, online AND offline -- the offline ones are exactly what
the tree exists to surface, and they are why this is a single SNMP walk rather
than the multi-page web-UI scrape originally specced.

Never walk the 1.3.6.1.4.1.37950 enterprise root: it is ~16k varbinds and
times out on the real device.

The device reports STALE ROWS. Moving an ONU between PON ports leaves the old
authorization entry behind, so a MAC can appear on two rows -- 87 rows carried
only 75 unique MACs on the real OLT. Since customers are matched to ONUs by
MAC, this module collapses duplicates before returning (see _preference_key).

Do not consume this device's registration timestamps: it has no RTC/NTP, so
they read 1970/01/2x and are uptime-relative, not wall-clock.

Like mikrotik.py, every public function here catches connection/protocol
failures and returns (False, message) instead of raising. An OLT being offline
must never crash a page render.

See docs/superpowers/specs/2026-09-01-network-topology-tree-design.md.
"""
import asyncio
import logging
from datetime import datetime

from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    bulk_walk_cmd,
)

logger = logging.getLogger(__name__)

ONU_TABLE_OID = "1.3.6.1.4.1.37950.1.1.5.12.1.12.1"

_COL_PON_PORT = "2"
_COL_ONU_INDEX = "3"
_COL_STATUS = "5"
_COL_MAC = "6"
_COL_MODEL = "7"
_COL_DESCRIPTION = "10"
_COL_DISTANCE_M = "13"

DEFAULT_SNMP_PORT = 161
_TIMEOUT_SECONDS = 5
_RETRIES = 1
# Hard ceiling so a misbehaving agent can't stream forever. The real OLT
# returns ~600 varbinds for 87 ONUs; this leaves an order of magnitude spare.
_MAX_CELLS = 20000


class OltError(Exception):
    """Base for failures this module converts into (False, message)."""


class OltRejected(OltError):
    """The agent answered but refused the request (SNMP errorStatus set)."""


def _mark_checked(server, status):
    server.last_checked_at = datetime.utcnow()
    server.last_status = status


async def _walk_onu_table(host, port, community):
    """Walk the ONU table and return {(column, row): value}.

    Raises OltRejected if the agent returns an SNMP errorStatus, or OSError
    (via pysnmp's errorIndication) if it never answers. Callers wrap this.
    """
    engine = SnmpEngine()
    target = await UdpTransportTarget.create(
        (host, port), timeout=_TIMEOUT_SECONDS, retries=_RETRIES,
    )
    prefix = ONU_TABLE_OID + "."
    cells = {}
    async for err_indication, err_status, _err_index, var_binds in bulk_walk_cmd(
        engine,
        CommunityData(community, mpModel=1),  # SNMPv2c
        target,
        ContextData(),
        0, 25,
        ObjectType(ObjectIdentity(ONU_TABLE_OID)),
        lexicographicMode=False,
    ):
        if err_indication:
            raise OSError(str(err_indication))
        if err_status:
            raise OltRejected(err_status.prettyPrint())
        for name, value in var_binds:
            oid = str(name.get_oid())
            if not oid.startswith(prefix):
                continue
            parts = oid[len(prefix):].split(".")
            if len(parts) != 2:
                continue
            cells[(parts[0], parts[1])] = value.prettyPrint()
        if len(cells) >= _MAX_CELLS:
            logger.warning("VSOL OLT walk hit the %s-cell ceiling at %s", _MAX_CELLS, host)
            break
    return cells


def _to_int(text, default=0):
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return default


def _assemble_rows(cells):
    """Turn the flat cell map into one dict per ONU row."""
    by_row = {}
    for (column, row), value in cells.items():
        by_row.setdefault(row, {})[column] = value

    rows = []
    for row_key in sorted(by_row, key=lambda k: _to_int(k)):
        raw = by_row[row_key]
        mac = (raw.get(_COL_MAC) or "").strip().lower()
        if not mac:
            # An authorization slot the OLT reports with no MAC is not an ONU.
            continue
        pon = (raw.get(_COL_PON_PORT) or "").strip()
        onu_index = (raw.get(_COL_ONU_INDEX) or "").strip()
        description = (raw.get(_COL_DESCRIPTION) or "").strip()
        model = (raw.get(_COL_MODEL) or "").strip()
        rows.append({
            "pon_port": "PON{}".format(pon),
            "onu_id": "EPON0/{}:{}".format(pon, onu_index),
            "status": "online" if (raw.get(_COL_STATUS) or "").strip() == "1" else "offline",
            "mac_address": mac,
            # The OLT writes the literal string NULL for an unlabelled ONU.
            "description": None if description in ("", "NULL") else description,
            "model": model,
            "distance_m": _to_int(raw.get(_COL_DISTANCE_M)),
            "_pon": _to_int(pon),
            "_onu": _to_int(onu_index),
        })
    return rows


def _preference_key(row):
    """Ranks two rows claiming the same MAC. Highest wins.

    1. Online beats offline -- a live ONU is never the stale copy.
    2. Then a real description beats the OLT's literal 'NULL'.
    3. Then the greater distance (a real ranging figure beats 0).
    4. Then the LOWEST PON port -- negated so 'higher key wins' still holds.

    Every term is total and derived only from the row, so the winner is stable
    across checks and the tree does not flicker.
    """
    return (
        1 if row["status"] == "online" else 0,
        1 if row["description"] else 0,
        row["distance_m"],
        -row["_pon"],
    )


def _dedupe_by_mac(rows):
    best = {}
    for row in rows:
        incumbent = best.get(row["mac_address"])
        if incumbent is None or _preference_key(row) > _preference_key(incumbent):
            best[row["mac_address"]] = row
    winners = sorted(best.values(), key=lambda r: (r["_pon"], r["_onu"]))
    for row in winners:
        row.pop("_pon", None)
        row.pop("_onu", None)
    return winners


def get_olt_status(server):
    """Read the OLT's full ONU inventory in one SNMP walk.

    Side effect: sets server.last_checked_at / server.last_status -- the
    caller is responsible for committing.

    Returns (ok: bool, onus_or_message). On success, onus_or_message is a list
    of dicts sorted by (PON port, ONU index), deduplicated by MAC:
    {"pon_port", "onu_id", "status", "mac_address", "description", "model",
     "distance_m"}. On failure it is a human-readable message.
    """
    port = server.api_port or DEFAULT_SNMP_PORT
    try:
        cells = asyncio.run(_walk_onu_table(server.host, port, server.password))
    except OltRejected as exc:
        _mark_checked(server, "auth_failed")
        logger.warning("VSOL OLT rejected the walk for device %s: %s", server.id, exc)
        return False, "OLT rejected the SNMP request: {}".format(exc)
    except Exception as exc:  # noqa: BLE001 -- this module never raises out
        _mark_checked(server, "unreachable")
        logger.warning("VSOL OLT walk failed for device %s: %s", server.id, exc)
        return False, str(exc) or exc.__class__.__name__

    onus = _dedupe_by_mac(_assemble_rows(cells))
    if not onus:
        # SNMPv2c answers a wrong community with silence, and a non-VSOL agent
        # answers this OID with nothing -- both land here. Reporting an empty
        # success would render an empty tree as though the OLT had no ONUs.
        _mark_checked(server, "unreachable")
        return False, ("OLT responded but reported no ONUs -- check the SNMP "
                       "community and that this device is a V-SOL OLT.")
    _mark_checked(server, "online")
    return True, onus

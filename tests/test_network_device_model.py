"""Model-level tests for NetworkDevice -- tenant-scoped device-health
monitoring, independent of MikrotikServer/PPPoE. See
docs/superpowers/specs/2026-09-01-network-device-health-monitoring-design.md.
"""
from datetime import datetime

import app as appmod
from tests.conftest import make_tenant


def test_network_device_to_dict_shape(app, client):
    hdr = make_tenant(client, "Biz NetDev", "netdev_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz NetDev").first()
        device = appmod.NetworkDevice(
            tenant_id=tenant.id, name="Core CCR", host="10.0.0.1",
            username="admin", password="secret",
        )
        appmod.db.session.add(device)
        appmod.db.session.commit()

        data = device.to_dict()
        assert data["name"] == "Core CCR"
        assert data["host"] == "10.0.0.1"
        assert data["api_port"] == 8728  # default
        assert data["use_tls"] is False  # default
        assert data["status"] == "active"  # default
        assert data["last_checked_at"] is None
        assert data["last_status"] is None
        assert data["interface_labels"] == {}  # default
        assert "password" not in data


def test_network_device_last_checked_at_serializes(app, client):
    hdr = make_tenant(client, "Biz NetDev2", "netdev2_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz NetDev2").first()
        device = appmod.NetworkDevice(
            tenant_id=tenant.id, name="Core CCR", host="10.0.0.1",
            username="admin", password="secret",
            last_checked_at=datetime(2026, 9, 1, 12, 0, 0), last_status="online",
        )
        appmod.db.session.add(device)
        appmod.db.session.commit()

        data = device.to_dict()
        assert data["last_checked_at"] == "2026-09-01 12:00:00"
        assert data["last_status"] == "online"

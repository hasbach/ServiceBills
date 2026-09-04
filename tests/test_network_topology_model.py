"""Model-level tests for the topology columns that turn the flat NetworkDevice
list into a CCR -> OLT tree, plus the Customer -> ONU link. See
docs/superpowers/specs/2026-09-01-network-topology-tree-design.md."""
import app as appmod
from tests.conftest import make_tenant


def _tenant(name):
    return appmod.Tenant.query.filter_by(name=name).first()


def test_device_type_defaults_to_mikrotik_ccr(app, client):
    make_tenant(client, "Topo A", "topo_a_admin")
    with app.app_context():
        tenant = _tenant("Topo A")
        device = appmod.NetworkDevice(
            tenant_id=tenant.id, name="Core CCR", host="10.0.0.1",
            username="admin", password="secret",
        )
        appmod.db.session.add(device)
        appmod.db.session.commit()

        assert device.device_type == "mikrotik_ccr"
        assert device.parent_device_id is None
        data = device.to_dict()
        assert data["device_type"] == "mikrotik_ccr"
        assert data["parent_device_id"] is None


def test_olt_can_be_parented_to_the_ccr(app, client):
    make_tenant(client, "Topo B", "topo_b_admin")
    with app.app_context():
        tenant = _tenant("Topo B")
        ccr = appmod.NetworkDevice(
            tenant_id=tenant.id, name="Core CCR", host="10.0.0.1",
            username="admin", password="secret", device_type="mikrotik_ccr",
        )
        appmod.db.session.add(ccr)
        appmod.db.session.commit()

        olt = appmod.NetworkDevice(
            tenant_id=tenant.id, name="EPON OLT", host="192.168.8.100",
            username="", password="public", device_type="vsol_olt",
            api_port=161, parent_device_id=ccr.id,
        )
        appmod.db.session.add(olt)
        appmod.db.session.commit()

        assert olt.to_dict()["device_type"] == "vsol_olt"
        assert olt.to_dict()["parent_device_id"] == ccr.id
        assert olt.parent.id == ccr.id
        assert [c.id for c in ccr.children] == [olt.id]
        # The community string is still never serialized, same as a password.
        assert "password" not in olt.to_dict()


def test_many_customers_can_share_one_onu_mac(app, client):
    make_tenant(client, "Topo C", "topo_c_admin")
    with app.app_context():
        tenant = _tenant("Topo C")
        plan = appmod.SubscriptionPlan(
            tenant_id=tenant.id, name="Basic", price=10, cost=5,
            billing_cycle="monthly", currency="USD",
        )
        appmod.db.session.add(plan)
        appmod.db.session.commit()

        shared_mac = "b4:64:15:3f:c1:94"
        for name in ("Cust One", "Cust Two"):
            appmod.db.session.add(appmod.Customer(
                tenant_id=tenant.id, name=name, phone="1", address="a",
                subscription_plan_id=plan.id,
                subscription_expiry_date=appmod.datetime.utcnow(),
                onu_mac_address=shared_mac,
            ))
        appmod.db.session.commit()

        rows = appmod.Customer.query.filter_by(onu_mac_address=shared_mac).all()
        assert len(rows) == 2


def test_onu_mac_address_defaults_to_none(app, client):
    make_tenant(client, "Topo D", "topo_d_admin")
    with app.app_context():
        tenant = _tenant("Topo D")
        plan = appmod.SubscriptionPlan(
            tenant_id=tenant.id, name="Basic", price=10, cost=5,
            billing_cycle="monthly", currency="USD",
        )
        appmod.db.session.add(plan)
        appmod.db.session.commit()
        customer = appmod.Customer(
            tenant_id=tenant.id, name="Unlinked", phone="1", address="a",
            subscription_plan_id=plan.id,
            subscription_expiry_date=appmod.datetime.utcnow(),
        )
        appmod.db.session.add(customer)
        appmod.db.session.commit()
        assert customer.onu_mac_address is None

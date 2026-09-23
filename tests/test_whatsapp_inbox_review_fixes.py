"""Regression tests for the final whole-branch review of the WhatsApp inbox:
FK ondelete behaviour, push kept off the webhook request path, AI flag
precedence, and session rollbacks in cs_agent_tools."""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

import app as appmod
import cs_agent_tools
import storage
import whatsapp_inbox as wi
from tests.conftest import make_tenant
from tests.inbox_helpers import FakeResponse, seed_customer, setup_wa_tenant, signed_post, wa_payload


def _tid(app, username):
    with app.app_context():
        return appmod.User.query.filter_by(username=username).first().tenant_id


# --- 1. deleting a customer linked to a conversation -------------------------

def test_deleting_customer_unlinks_conversation(app, client):
    hdr = make_tenant(client, "Biz DelC", "delc_admin")
    tid = _tid(app, "delc_admin")
    seed_customer(client, hdr, "70111222", name="Gone")
    with app.app_context():
        cust = appmod.Customer.query.filter_by(tenant_id=tid).first()
        conv = wi.upsert_conversation(appmod, tid, "96170111222")
        appmod.db.session.commit()
        assert conv.customer_id == cust.id
        cust_id, conv_id = cust.id, conv.id
    r = client.delete(f"/api/customers/{cust_id}", headers=hdr)
    assert r.status_code == 200, r.get_json()
    with app.app_context():
        conv = appmod.db.session.get(appmod.WhatsAppConversation, conv_id)
        assert conv is not None and conv.customer_id is None


def _fk_engine():
    engine = sa.create_engine("sqlite:///:memory:")

    @sa.event.listens_for(engine, "connect")
    def _on(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    appmod.db.metadata.create_all(bind=engine)
    return engine


def test_conversation_and_message_fks_set_null_under_real_fk_enforcement():
    engine = _fk_engine()
    session = sessionmaker(bind=engine)()
    try:
        tenant = appmod.Tenant(name="FK Inbox", slug="fk-inbox", status="active", plan="free")
        session.add(tenant)
        session.add(appmod.Currency(code="USD", name="US Dollar", decimal_places=2))
        session.commit()
        plan = appmod.SubscriptionPlan(tenant_id=tenant.id, name="B", price=10, cost=5,
                                       billing_cycle="monthly", currency="USD")
        user = appmod.User(username="fk_inbox_admin", role="admin", tenant_id=tenant.id)
        user.set_password("pw")
        session.add_all([plan, user])
        session.commit()
        cust = appmod.Customer(tenant_id=tenant.id, name="C", phone="1", address="a",
                               subscription_plan_id=plan.id, subscription_expiry_date=datetime.utcnow())
        session.add(cust)
        session.commit()
        conv = appmod.WhatsAppConversation(tenant_id=tenant.id, wa_phone="9611", customer_id=cust.id)
        session.add(conv)
        session.commit()
        msg = appmod.WhatsAppMessage(tenant_id=tenant.id, conversation_id=conv.id, direction="out",
                                     sender="admin", sent_by_user_id=user.id, msg_type="text", text="hi")
        session.add(msg)
        session.commit()
        conv_id, msg_id, cust_id, user_id = conv.id, msg.id, cust.id, user.id

        session.delete(session.get(appmod.Customer, cust_id))
        session.delete(session.get(appmod.User, user_id))
        session.commit()
        session.close()

        session = sessionmaker(bind=engine)()
        assert session.get(appmod.WhatsAppConversation, conv_id).customer_id is None
        assert session.get(appmod.WhatsAppMessage, msg_id).sent_by_user_id is None
    finally:
        session.close()
        engine.dispose()


# --- 2. deleting an admin who sent an inbox message --------------------------

def test_deleting_user_unlinks_sent_messages(app, client):
    hdr = make_tenant(client, "Biz DelU", "delu_admin")
    tid = _tid(app, "delu_admin")
    with app.app_context():
        other = appmod.User(username="delu_other", role="admin", tenant_id=tid)
        other.set_password("pw")
        appmod.db.session.add(other)
        appmod.db.session.commit()
        conv = wi.upsert_conversation(appmod, tid, "96170333444")
        msg = wi.record_outbound(appmod, conv, sender="admin", msg_type="text", text="hi",
                                 sent_by_user_id=other.id, wa_message_id="wamid.U1")
        appmod.db.session.commit()
        other_id, msg_id = other.id, msg.id
    r = client.delete(f"/api/users/{other_id}", headers=hdr)
    assert r.status_code == 200, r.get_json()
    with app.app_context():
        assert appmod.db.session.get(appmod.WhatsAppMessage, msg_id).sent_by_user_id is None

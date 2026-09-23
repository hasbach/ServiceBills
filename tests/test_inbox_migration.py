"""Runs the inbox migration's upgrade()/downgrade() directly through Alembic
Operations against a throwaway SQLite file. The full chain can't be walked on
SQLite (see tests/test_topology_migration.py for why), so this stubs the few
parent tables the migration references."""
import importlib.util
import os

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "migrations", "versions", "a7c3e9d1b2f4_add_whatsapp_inbox.py")


def _load():
    spec = importlib.util.spec_from_file_location("inbox_migration", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_inbox_migration_up_and_down(tmp_path):
    mod = _load()
    assert mod.down_revision == "f1a2b3c4d5e6"
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    with engine.begin() as conn:
        for ddl in ("CREATE TABLE tenant (id INTEGER PRIMARY KEY)",
                    "CREATE TABLE user (id INTEGER PRIMARY KEY)",
                    "CREATE TABLE customer (id INTEGER PRIMARY KEY)",
                    "CREATE TABLE push_subscription (id INTEGER PRIMARY KEY, tenant_id INTEGER, "
                    "user_id INTEGER, subscription_info TEXT, created_at DATETIME)"):
            conn.exec_driver_sql(ddl)
        ctx = MigrationContext.configure(conn, opts={"render_as_batch": True})
        with Operations.context(ctx):
            mod.upgrade()
        insp = sa.inspect(conn)
        assert {"whatsapp_conversation", "whatsapp_message"} <= set(insp.get_table_names())
        assert "topics" in {c["name"] for c in insp.get_columns("push_subscription")}
        msg_cols = {c["name"] for c in insp.get_columns("whatsapp_message")}
        assert {"wa_message_id", "media_key", "media_playback_key", "reaction_emoji", "transcript"} <= msg_cols

        with Operations.context(ctx):
            mod.downgrade()
        insp = sa.inspect(conn)
        assert "whatsapp_message" not in insp.get_table_names()
        assert "whatsapp_conversation" not in insp.get_table_names()
        assert "topics" not in {c["name"] for c in insp.get_columns("push_subscription")}


def test_models_exist_and_serialize(app, client):
    import app as appmod
    from tests.conftest import make_tenant
    make_tenant(client, "Biz Model", "model_admin")
    with app.app_context():
        tid = appmod.User.query.filter_by(username="model_admin").first().tenant_id
        c = appmod.WhatsAppConversation(tenant_id=tid, wa_phone="96170123456")
        appmod.db.session.add(c); appmod.db.session.commit()
        m = appmod.WhatsAppMessage(tenant_id=tid, conversation_id=c.id, direction="in",
                                   sender="customer", msg_type="text", text="hi", status="received")
        appmod.db.session.add(m); appmod.db.session.commit()
        d = c.to_dict()
        assert d["wa_phone"] == "96170123456" and d["needs_attention"] is False
        assert m.to_dict()["text"] == "hi"
        assert appmod.WhatsAppConversation in appmod.TENANT_OWNED_MODELS
        sub = appmod.PushSubscription(tenant_id=tid, user_id=1, subscription_info='{"endpoint": "https://e/1"}')
        assert sub.topic_list() == ["tickets", "whatsapp_inbox"]
        assert sub.endpoint() == "https://e/1"

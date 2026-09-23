import json
from datetime import datetime, timedelta

import app as appmod
import whatsapp_inbox as wi
from tests.conftest import make_tenant, auth_headers


def _sub(tid, uid, endpoint, topics=None):
    s = appmod.PushSubscription(tenant_id=tid, user_id=uid,
                                subscription_info=json.dumps({"endpoint": endpoint, "keys": {}}),
                                topics=json.dumps(topics) if topics is not None else None)
    appmod.db.session.add(s)
    return s


def _capture(monkeypatch):
    sent = []
    monkeypatch.setattr(appmod, "VAPID_PRIVATE_KEY", "test-key")
    monkeypatch.setattr(appmod, "webpush", lambda **kw: sent.append(kw["subscription_info"]["endpoint"]))
    return sent


def test_targets_admins_of_tenant_with_topic(app, client, monkeypatch):
    make_tenant(client, "Biz Push", "push_admin")
    auth_headers(client, "push_collector", role="collector")
    make_tenant(client, "Biz Other", "other_admin")
    sent = _capture(monkeypatch)
    with app.app_context():
        admin = appmod.User.query.filter_by(username="push_admin").first()
        coll = appmod.User.query.filter_by(username="push_collector").first()
        other = appmod.User.query.filter_by(username="other_admin").first()
        # auth_headers(role=...) attaches to the most recent tenant; move the
        # collector into push_admin's tenant explicitly.
        coll.tenant_id = admin.tenant_id
        _sub(admin.tenant_id, admin.id, "https://e/admin")
        _sub(admin.tenant_id, admin.id, "https://e/admin-tickets-only", ["tickets"])
        _sub(admin.tenant_id, coll.id, "https://e/collector")
        _sub(other.tenant_id, other.id, "https://e/other-tenant")
        appmod.db.session.commit()
        n = appmod.send_push_notification({"title": "t", "body": "b"}, tenant_id=admin.tenant_id,
                                          roles=["admin"], topic="whatsapp_inbox")
    assert n == 1 and sent == ["https://e/admin"]


def test_legacy_call_inside_request_still_pushes_everyone(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz Legacy", "legacy_admin")
    sent = _capture(monkeypatch)
    with app.app_context():
        u = appmod.User.query.filter_by(username="legacy_admin").first()
        _sub(u.tenant_id, u.id, "https://e/legacy")
        appmod.db.session.commit()
    r = client.post("/api/subscription_plans", headers=hdr, json={"name": "P", "price": 1, "billing_cycle": "monthly"})
    pid = r.get_json()["plan"]["id"]
    r = client.post("/api/customers", headers=hdr, json={"name": "C", "phone": "70111222", "address": "a",
                                                          "subscription_plan_id": pid, "subscription_start_date": "2026-01-01"})
    cid = r.get_json()["customer_id"]
    client.post("/api/support-tickets", headers=hdr, json={"customer_id": cid, "title": "x", "description": "y", "priority": "low"})
    assert sent == ["https://e/legacy"]


def test_notify_conversation_throttles(app, client, monkeypatch):
    make_tenant(client, "Biz Thr", "thr_admin")
    calls = []
    monkeypatch.setattr(appmod, "send_push_notification", lambda payload, **kw: calls.append((payload, kw)) or 1)
    with app.app_context():
        tid = appmod.User.query.filter_by(username="thr_admin").first().tenant_id
        conv = wi.upsert_conversation(appmod, tid, "96170999888", "Nada")
        wi.flag_attention(conv, "ai_failed")
        conv.last_message_preview = "where is my internet"
        appmod.db.session.commit()
        now = datetime(2026, 9, 23, 12, 0)
        assert wi.notify_conversation(appmod, conv, now=now) is True
        assert wi.notify_conversation(appmod, conv, now=now + timedelta(seconds=60)) is False
        assert wi.notify_conversation(appmod, conv, now=now + timedelta(minutes=3)) is True
    payload, kw = calls[0]
    assert payload["title"] == "Nada"
    assert payload["body"] == "AI couldn't answer: where is my internet"
    assert payload["tag"] == f"wa-conv-{conv.id}" and payload["url"] == f"/?view=messaging&inbox={conv.id}"
    assert kw == {"tenant_id": tid, "roles": ["admin"], "topic": "whatsapp_inbox"}


def test_topics_routes_and_unsubscribe(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz Top", "top_admin")
    sub = {"endpoint": "https://e/dev1", "keys": {"p256dh": "x", "auth": "y"}}
    assert client.post("/api/push-subscribe", headers=hdr, json={"subscription": sub}).status_code == 200
    # Re-subscribing the same endpoint must not duplicate the row.
    client.post("/api/push-subscribe", headers=hdr, json={"subscription": sub})
    with app.app_context():
        assert appmod.PushSubscription.query.count() == 1
    r = client.get("/api/push-subscription/topics", headers=hdr, query_string={"endpoint": "https://e/dev1"})
    assert r.get_json() == {"subscribed": True, "topics": ["tickets", "whatsapp_inbox"]}
    r = client.put("/api/push-subscription/topics", headers=hdr, json={"endpoint": "https://e/dev1", "topics": ["tickets"]})
    assert r.status_code == 200
    r = client.get("/api/push-subscription/topics", headers=hdr, query_string={"endpoint": "https://e/dev1"})
    assert r.get_json()["topics"] == ["tickets"]
    r = client.put("/api/push-subscription/topics", headers=hdr, json={"endpoint": "https://e/dev1", "topics": ["bogus"]})
    assert r.status_code == 400
    sent = _capture(monkeypatch)
    assert client.post("/api/push-test", headers=hdr, json={"endpoint": "https://e/dev1"}).status_code == 200
    assert sent == ["https://e/dev1"]
    assert client.post("/api/push-unsubscribe", headers=hdr, json={"endpoint": "https://e/dev1"}).status_code == 200
    r = client.get("/api/push-subscription/topics", headers=hdr, query_string={"endpoint": "https://e/dev1"})
    assert r.get_json() == {"subscribed": False, "topics": []}

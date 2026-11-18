"""Background WhatsApp campaigns (Messaging -> bulk sends): saved first,
sent off the request one number at a time, resumable, with a saved report."""
from datetime import datetime, timedelta

import pytest

from tests.conftest import make_tenant


@pytest.fixture
def bg(monkeypatch):
    """Capture background jobs instead of running them, so a test can prove
    nothing is sent while the request is open, then run them by hand."""
    import app as appmod
    jobs = []
    monkeypatch.setattr(appmod.whatsapp_inbox, "run_background",
                        lambda flask_app, fn, pool=None: jobs.append(fn))

    def run_all():
        while jobs:
            jobs.pop(0)()
    return jobs, run_all


def _fake_custom_sender(monkeypatch, fail_phones=(), crash_phones=()):
    import app as appmod
    sent = []

    def fake(tenant_id, phone_raw, template_name, language=None, body_params=None, header_url=None):
        sent.append((phone_raw, template_name, list(body_params or []), header_url))
        if phone_raw in crash_phones:
            raise RuntimeError("boom")
        if phone_raw in fail_phones:
            return {'success': False, 'status': 'Failed', 'error': 'Meta API Error (400): bad number'}
        return {'success': True, 'status': 'Sent (Accepted by Meta)', 'message_id': f'wamid.{phone_raw}'}
    monkeypatch.setattr(appmod, "send_custom_wa_template", fake)
    return sent


def _custom_payload(phones, template="promo"):
    return {"audience": "custom_list", "custom_phones": phones, "custom_template": template,
            "template_language": "en", "custom_variables": ["Hi"], "header_url": ""}


def _make_customer(client, hdr, name, phone):
    plan = client.post("/api/subscription_plans", headers=hdr,
                       json={"name": f"P-{name}", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    r = client.post("/api/customers", headers=hdr,
                    json={"name": name, "phone": phone, "address": "a",
                          "subscription_plan_id": plan, "subscription_start_date": "2026-01-01"})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    return r.get_json()["customer_id"]


def test_custom_campaign_returns_at_once_and_sends_in_background(client, bg, monkeypatch):
    jobs, run_all = bg
    sent = _fake_custom_sender(monkeypatch, fail_phones=("+96170000002",))
    hdr = make_tenant(client, "Biz", "camp_admin")

    r = client.post("/api/messages/bulk_send", headers=hdr,
                    json=_custom_payload(["+96170000001", "70000002x", "+96170000002",
                                          "+96170000003", "+96170000001", "abc"]))
    assert r.status_code == 202, r.get_data(as_text=True)
    cid = r.get_json()["campaign_id"]
    assert sent == []      # nothing sent while the request was open
    assert len(jobs) == 1

    # Saved before sending: duplicates dropped (70000002x normalises to the
    # same number as +96170000002), the unusable number pre-failed.
    rep = client.get(f"/api/messages/campaigns/{cid}", headers=hdr).get_json()
    assert rep["status"] == "queued"
    assert rep["total"] == 4
    assert rep["pending"] == 3 and rep["failed"] == 1

    run_all()

    rep = client.get(f"/api/messages/campaigns/{cid}", headers=hdr).get_json()
    assert rep["status"] == "completed" and rep["finished_at"]
    # The failure in the middle did not stop the numbers after it.
    assert rep["sent"] == 2 and rep["failed"] == 2 and rep["pending"] == 0
    by_phone = {x["phone"]: x for x in rep["recipients"]}
    assert by_phone["+96170000003"]["status"] == "sent"
    assert by_phone["+96170000003"]["wa_message_id"] == "wamid.+96170000003"
    assert "bad number" in by_phone["+96170000002"]["error"]
    assert by_phone["abc"]["status"] == "failed"
    assert [s[0] for s in sent].count("+96170000001") == 1
    assert sent[0][1:] == ("promo", ["Hi"], None)


def test_a_crash_on_one_number_is_recorded_and_the_rest_still_go(client, bg, monkeypatch):
    jobs, run_all = bg
    _fake_custom_sender(monkeypatch, crash_phones=("+1555000002",))
    hdr = make_tenant(client, "Biz", "crash_admin")
    cid = client.post("/api/messages/bulk_send", headers=hdr,
                      json=_custom_payload(["+1555000001", "+1555000002", "+1555000003"])).get_json()["campaign_id"]
    run_all()
    rep = client.get(f"/api/messages/campaigns/{cid}", headers=hdr).get_json()
    assert [x["status"] for x in rep["recipients"]] == ["sent", "failed", "sent"]
    assert "boom" in rep["recipients"][1]["error"]


def test_customer_campaign_sends_each_customer(client, bg, monkeypatch):
    import app as appmod
    jobs, run_all = bg
    hdr = make_tenant(client, "Biz", "cust_admin")
    a = _make_customer(client, hdr, "Alice", "96170111111")
    b = _make_customer(client, hdr, "Bob", "96170222222")
    calls = []

    def fake(customer, event_type, context=None):
        calls.append((customer.id, event_type, context))
        if customer.id == b:
            return {'success': False, 'status': 'Skipped', 'error': 'notifications off'}
        return {'success': True, 'status': 'Sent (Accepted by Meta)', 'message_id': 'wamid.A'}
    monkeypatch.setattr(appmod, "send_whatsapp_message", fake)

    r = client.post("/api/messages/bulk_send", headers=hdr,
                    json={"audience": "all", "event_type": "outage",
                          "variables": {"message": "down"}, "sector": ""})
    assert r.status_code == 202, r.get_data(as_text=True)
    assert calls == []
    run_all()

    rep = client.get(f"/api/messages/campaigns/{r.get_json()['campaign_id']}", headers=hdr).get_json()
    assert rep["kind"] == "customers" and rep["title"].startswith("Outage notice to all customers")
    assert sorted(c[0] for c in calls) == sorted([a, b])
    assert {c[1] for c in calls} == {"bulk_outage"}
    assert calls[0][2] == {"message": "down"}
    st = {x["name"]: x["status"] for x in rep["recipients"]}
    assert st == {"Alice": "sent", "Bob": "skipped"}


def test_unknown_event_type_and_empty_audience_are_rejected(client, bg):
    hdr = make_tenant(client, "Biz", "rej_admin")
    assert client.post("/api/messages/bulk_send", headers=hdr,
                       json={"audience": "all", "event_type": "nope"}).status_code == 400
    assert client.post("/api/messages/bulk_send", headers=hdr,
                       json={"audience": "all", "event_type": "outage", "variables": {}}).status_code == 400


def test_dead_worker_is_resumed_without_resending(client, bg, monkeypatch):
    import app as appmod
    jobs, run_all = bg
    sent = _fake_custom_sender(monkeypatch)
    hdr = make_tenant(client, "Biz", "resume_admin")
    cid = client.post("/api/messages/bulk_send", headers=hdr,
                      json=_custom_payload(["+1555000001", "+1555000002", "+1555000003"])).get_json()["campaign_id"]
    jobs.clear()  # the original worker never ran to completion...

    # ...simulate it dying after sending #1 and while #2 was in flight.
    C, R = appmod.WhatsAppCampaign, appmod.WhatsAppCampaignRecipient
    rows = R.query.filter_by(campaign_id=cid).order_by(R.id).all()
    rows[0].status, rows[0].wa_message_id = 'sent', 'wamid.1'
    rows[1].status = 'sending'
    camp = appmod.db.session.get(C, cid)
    camp.status = 'running'
    camp.heartbeat_at = datetime.utcnow() - timedelta(minutes=10)
    appmod.db.session.commit()

    # Opening the Campaigns list restarts it.
    assert client.get("/api/messages/campaigns", headers=hdr).status_code == 200
    assert len(jobs) == 1
    run_all()

    rep = client.get(f"/api/messages/campaigns/{cid}", headers=hdr).get_json()
    assert rep["status"] == "completed"
    assert [x["status"] for x in rep["recipients"]] == ["sent", "failed", "sent"]
    assert "Interrupted" in rep["recipients"][1]["error"]
    assert [s[0] for s in sent] == ["+1555000003"]  # only the untouched number


def test_live_worker_is_not_doubled(client, bg, monkeypatch):
    import app as appmod
    jobs, run_all = bg
    sent = _fake_custom_sender(monkeypatch)
    hdr = make_tenant(client, "Biz", "live_admin")
    cid = client.post("/api/messages/bulk_send", headers=hdr,
                      json=_custom_payload(["+1555000001"])).get_json()["campaign_id"]
    camp = appmod.db.session.get(appmod.WhatsAppCampaign, cid)
    camp.status, camp.heartbeat_at = 'running', datetime.utcnow()
    appmod.db.session.commit()

    appmod.wa_campaigns.run_campaign(appmod, cid)  # someone else owns it
    assert sent == []
    assert appmod.wa_campaigns.resume_stale(appmod) == 0


def test_scheduled_job_resumes_stale_campaigns(client, bg, monkeypatch):
    import app as appmod
    jobs, run_all = bg
    sent = _fake_custom_sender(monkeypatch)
    hdr = make_tenant(client, "Biz", "sched_admin")
    cid = client.post("/api/messages/bulk_send", headers=hdr,
                      json=_custom_payload(["+1555000001"])).get_json()["campaign_id"]
    jobs.clear()
    camp = appmod.db.session.get(appmod.WhatsAppCampaign, cid)
    camp.created_at = datetime.utcnow() - timedelta(minutes=10)  # queued, never picked up
    appmod.db.session.commit()

    assert "resume_whatsapp_campaigns" in appmod._SCHEDULED_JOBS
    assert appmod.SCHEDULE["resume_whatsapp_campaigns"] == "*/10 * * * *"
    appmod.wa_campaigns.resume_stale(appmod)
    run_all()
    assert [s[0] for s in sent] == ["+1555000001"]


def test_retry_failed_resends_only_failed(client, bg, monkeypatch):
    jobs, run_all = bg
    hdr = make_tenant(client, "Biz", "retry_admin")
    _fake_custom_sender(monkeypatch, fail_phones=("+1555000002",))
    cid = client.post("/api/messages/bulk_send", headers=hdr,
                      json=_custom_payload(["+1555000001", "+1555000002"])).get_json()["campaign_id"]
    # Retrying while it is still sending is refused.
    assert client.post(f"/api/messages/campaigns/{cid}/retry_failed", headers=hdr).status_code == 409
    run_all()

    sent = _fake_custom_sender(monkeypatch)  # the number works now
    r = client.post(f"/api/messages/campaigns/{cid}/retry_failed", headers=hdr)
    assert r.status_code == 202 and r.get_json()["retried"] == 1
    run_all()
    assert [s[0] for s in sent] == ["+1555000002"]
    rep = client.get(f"/api/messages/campaigns/{cid}", headers=hdr).get_json()
    assert rep["sent"] == 2 and rep["failed"] == 0 and rep["status"] == "completed"
    assert client.post(f"/api/messages/campaigns/{cid}/retry_failed", headers=hdr).status_code == 400


def test_cancel_stops_remaining_numbers(client, bg, monkeypatch):
    jobs, run_all = bg
    sent = _fake_custom_sender(monkeypatch)
    hdr = make_tenant(client, "Biz", "cancel_admin")
    cid = client.post("/api/messages/bulk_send", headers=hdr,
                      json=_custom_payload(["+1555000001", "+1555000002"])).get_json()["campaign_id"]
    assert client.post(f"/api/messages/campaigns/{cid}/cancel", headers=hdr).status_code == 200
    run_all()
    assert sent == []
    rep = client.get(f"/api/messages/campaigns/{cid}", headers=hdr).get_json()
    assert rep["status"] == "cancelled" and rep["cancelled"] == 2
    assert client.post(f"/api/messages/campaigns/{cid}/cancel", headers=hdr).status_code == 409


def test_cancel_mid_run_keeps_sent_and_cancels_the_rest(client, bg, monkeypatch):
    import app as appmod
    jobs, run_all = bg
    hdr = make_tenant(client, "Biz", "midcancel_admin")
    sent = []

    def fake(tenant_id, phone_raw, *a, **k):
        sent.append(phone_raw)
        # The admin presses Stop while the first number is going out.
        appmod.WhatsAppCampaign.query.update({appmod.WhatsAppCampaign.status: 'cancelled'})
        appmod.db.session.commit()
        return {'success': True, 'status': 'Sent', 'message_id': 'w1'}
    monkeypatch.setattr(appmod, "send_custom_wa_template", fake)
    cid = client.post("/api/messages/bulk_send", headers=hdr,
                      json=_custom_payload(["+1555000001", "+1555000002", "+1555000003"])).get_json()["campaign_id"]
    run_all()
    assert sent == ["+1555000001"]
    rep = client.get(f"/api/messages/campaigns/{cid}", headers=hdr).get_json()
    assert [x["status"] for x in rep["recipients"]] == ["sent", "cancelled", "cancelled"]


def test_meta_receipts_update_the_report(client, bg, monkeypatch):
    import app as appmod
    jobs, run_all = bg
    _fake_custom_sender(monkeypatch)
    hdr = make_tenant(client, "Biz", "receipt_admin")
    cid = client.post("/api/messages/bulk_send", headers=hdr,
                      json=_custom_payload(["+1555000001", "+1555000002"])).get_json()["campaign_id"]
    run_all()
    tid = appmod.db.session.get(appmod.WhatsAppCampaign, cid).tenant_id
    apply = appmod.wa_campaigns.apply_status

    assert apply(appmod, tid, {'id': 'wamid.+1555000001', 'status': 'delivered'})
    assert apply(appmod, tid, {'id': 'wamid.+1555000001', 'status': 'read'})
    assert not apply(appmod, tid, {'id': 'wamid.+1555000001', 'status': 'sent'})  # never goes back
    assert apply(appmod, tid, {'id': 'wamid.+1555000002', 'status': 'failed',
                               'errors': [{'code': 131026, 'error_data': {'details': 'not on WhatsApp'}}]})
    assert not apply(appmod, tid + 999, {'id': 'wamid.+1555000001', 'status': 'read'})  # other tenant
    assert not apply(appmod, tid, {'id': 'unknown', 'status': 'read'})
    appmod.db.session.commit()

    rep = client.get(f"/api/messages/campaigns/{cid}", headers=hdr).get_json()
    assert rep["delivered"] == 1 and rep["read"] == 1
    assert rep["sent"] == 1 and rep["failed"] == 1
    r2 = [x for x in rep["recipients"] if x["phone"] == "+1555000002"][0]
    assert r2["delivery_status"] == "failed" and "not on WhatsApp" in r2["error"]
    only_read = client.get(f"/api/messages/campaigns/{cid}?status=read", headers=hdr).get_json()
    assert [x["phone"] for x in only_read["recipients"]] == ["+1555000001"]


def test_campaigns_are_tenant_scoped_and_admin_only(client, bg, monkeypatch):
    from tests.conftest import auth_headers
    _fake_custom_sender(monkeypatch)
    a = make_tenant(client, "Biz A", "iso_a")
    cid = client.post("/api/messages/bulk_send", headers=a,
                      json=_custom_payload(["+1555000001"])).get_json()["campaign_id"]
    b = make_tenant(client, "Biz B", "iso_b")
    assert client.get(f"/api/messages/campaigns/{cid}", headers=b).status_code == 404
    assert client.post(f"/api/messages/campaigns/{cid}/cancel", headers=b).status_code == 404
    assert client.get("/api/messages/campaigns", headers=b).get_json()["campaigns"] == []
    lst = client.get("/api/messages/campaigns", headers=a).get_json()
    assert lst["total"] == 1 and lst["campaigns"][0]["id"] == cid
    assert lst["campaigns"][0]["created_by"] == "iso_a"

    staff = auth_headers(client, "iso_b_staff", role="staff")
    assert client.get("/api/messages/campaigns", headers=staff).status_code == 403
    assert client.post("/api/messages/bulk_send", headers=staff,
                       json=_custom_payload(["+1555000001"])).status_code == 403


def test_export_csv(client, bg, monkeypatch):
    jobs, run_all = bg
    _fake_custom_sender(monkeypatch, fail_phones=("+1555000002",))
    hdr = make_tenant(client, "Biz", "csv_admin")
    cid = client.post("/api/messages/bulk_send", headers=hdr,
                      json=_custom_payload(["+1555000001", "+1555000002"])).get_json()["campaign_id"]
    run_all()
    r = client.get(f"/api/messages/campaigns/{cid}/export", headers=hdr)
    assert r.status_code == 200 and r.mimetype == "text/csv"
    lines = r.get_data(as_text=True).strip().splitlines()
    assert lines[0].startswith("Name,Phone,Status")
    assert len(lines) == 3 and "bad number" in lines[2]


def test_send_custom_wa_template_simulates_without_credentials(app):
    import app as appmod
    from app import Tenant
    t = Tenant(name="Sim", slug="sim-campaign")
    appmod.db.session.add(t)
    appmod.db.session.commit()
    res = appmod.send_custom_wa_template(t.id, "+1555000001", "promo")
    assert res["success"] and "Simulated" in res["status"]
    bad = appmod.send_custom_wa_template(t.id, "abc", "promo")
    assert not bad["success"]

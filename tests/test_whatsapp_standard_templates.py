"""Standard template catalog: editable wording with enforced variables,
renumbering by order of appearance + param_map, and the send path honouring
param_map (FR-015..FR-015d). See specs/001-whatsapp-auto-connect/research.md R-8."""
from datetime import datetime

import pytest

import app as appmod
import wa_standard_templates as wst
from tests.conftest import make_tenant


# ---- catalog pinned to the real send path ---------------------------------

SEND_EVENTS = ["payment_paid", "subscription_created", "subscription_renewed", "payment_reminder",
               "current_balance", "payment_link", "bulk_outage", "bulk_maintenance",
               "bulk_feature", "bulk_offer"]


def test_catalog_has_every_standard_key():
    assert set(wst.CATALOG) == set(SEND_EVENTS) | {"forward_alert", "forward_keepalive"}
    for key, entry in wst.CATALOG.items():
        assert entry["default_name"]
        assert hasattr(appmod.WhatsAppSettings, entry["settings_field"]), key
        assert entry["category"] in ("UTILITY", "MARKETING")


@pytest.mark.parametrize("event", SEND_EVENTS)
def test_catalog_variables_match_send_mapping(app, client, event):
    make_tenant(client, "Biz Pin", "pin_admin")
    with app.app_context():
        tid = appmod.User.query.filter_by(username="pin_admin").first().tenant_id
        cust = appmod.Customer(tenant_id=tid, name="Ali", phone="96170123456", balance=5,
                               subscription_expiry_date=datetime(2026, 12, 1))
        sem = appmod._wa_semantic_params(event, cust, {"amount": 10, "plan_name": "Gold",
                                                       "expiry_date": "2026-12-01", "view_token": "TOK",
                                                       "message": "m", "location": "Hamra",
                                                       "estimated_time": "2h", "balance": 5})
    entry = wst.CATALOG[event]
    expected = [v["key"] for v in entry["variables"]] + [v["key"] for v in entry.get("button_variables", [])]
    assert list(sem) == expected


def test_legacy_param_order_unchanged(app, client):
    make_tenant(client, "Biz Leg", "leg_admin")
    with app.app_context():
        tid = appmod.User.query.filter_by(username="leg_admin").first().tenant_id
        cust = appmod.Customer(tenant_id=tid, name="Ali", phone="1", balance=5,
                               subscription_expiry_date=datetime(2026, 12, 1))
        assert appmod._wa_legacy_params("payment_paid", cust, {"amount": 10}) == ["Ali", "10.00", "5.00"]
        assert appmod._wa_legacy_params("bulk_maintenance", cust, {"location": "L", "estimated_time": "E"}) == ["L", "E"]
        # subscription_created never had a mapping: legacy behaviour (no params) is kept.
        assert appmod._wa_legacy_params("subscription_created", cust, {"plan_name": "Gold"}) == []


# ---- validation -------------------------------------------------------------

BODY_OK = "Dear {customer_name}, we received {amount}. Balance: {balance}. Thanks!"


def test_validate_ok():
    assert wst.validate_item("payment_paid", None, BODY_OK, None, None) == []


@pytest.mark.parametrize("body,needle", [
    ("Dear {customer_name}, we received {amount}. Thanks!", "Missing variable {balance}"),
    (BODY_OK.replace("Thanks!", "{foo} Thanks!"), "Unknown variable {foo}"),
    (BODY_OK.replace("Thanks!", "{amount} Thanks!"), "{amount} is used more than once"),
    ("{customer_name}, we received {amount}. Balance: {balance}. Thanks!", "cannot start with a variable"),
    ("Dear {customer_name}, we received {amount}. Balance: {balance}", "cannot end with a variable"),
    ("x" * 1025 + " {customer_name} {amount} {balance} end", "1024"),
])
def test_validate_rejects(body, needle):
    errors = wst.validate_item("payment_paid", None, body, None, None)
    assert any(needle in e for e in errors), errors


def test_validate_header_footer_limits_and_no_variables():
    errs = wst.validate_item("payment_paid", "h" * 61, BODY_OK, "f" * 61, None)
    assert any("Header" in e and "60" in e for e in errs)
    assert any("Footer" in e and "60" in e for e in errs)
    errs = wst.validate_item("payment_paid", "Hi {customer_name}", BODY_OK, None, None)
    assert any("Header" in e and "variable" in e for e in errs)


def test_validate_fixed_order_template():
    # forward_alert is sent by a fixed-order sender, so its variables can't be reordered.
    ok = "Customer {customer} needs help: {message}. Reply from the inbox."
    assert wst.validate_item("forward_alert", None, ok, None, None) == []
    swapped = "About {message}: customer {customer} needs help. Reply from the inbox."
    assert any("order" in e for e in wst.validate_item("forward_alert", None, swapped, None, None))


# ---- renumbering ----------------------------------------------------------

def test_renumber_by_order_of_appearance():
    comps, param_map = wst.to_meta_components(
        "payment_paid", None, "Paid {amount} by {customer_name}, balance {balance}.", None, None,
        base_url="https://x")
    body = next(c for c in comps if c["type"] == "BODY")
    assert body["text"] == "Paid {{1}} by {{2}}, balance {{3}}."
    assert param_map == ["amount", "customer_name", "balance"]
    samples = wst.CATALOG["payment_paid"]["sample_values"]
    assert body["example"]["body_text"] == [[samples["amount"], samples["customer_name"], samples["balance"]]]


def test_payment_link_button_url():
    entry = wst.CATALOG["payment_link"]
    comps, param_map = wst.to_meta_components("payment_link", None, entry["body"], None,
                                              entry["buttons"], base_url="https://pay.example")
    btns = next(c for c in comps if c["type"] == "BUTTONS")["buttons"]
    assert btns[0]["type"] == "URL"
    assert btns[0]["url"] == "https://pay.example/pay?token={{1}}"
    assert btns[0]["example"] == ["https://pay.example/pay?token=" + entry["sample_values"]["pay_token"]]
    assert param_map == []
    body = next(c for c in comps if c["type"] == "BODY")
    assert "{{" not in body["text"] and "example" not in body


# ---- routes -----------------------------------------------------------------

class Resp:
    def __init__(self, body, status=200):
        self.status_code, self.ok, self._b = status, 200 <= status < 300, body
        self.text = str(body)

    def json(self):
        return self._b


def _api_tenant(app, client, slug_name, user):
    hdr = make_tenant(client, slug_name, user)
    with app.app_context():
        tid = appmod.User.query.filter_by(username=user).first().tenant_id
        appmod.db.session.get(appmod.Tenant, tid).plan = "pro"
        appmod.db.session.add(appmod.WhatsAppSettings(
            tenant_id=tid, mode="api", enabled=True, phone_number_id="PN", business_account_id="WABA",
            access_token="tok", api_version="v19.0"))
        appmod.db.session.commit()
    return hdr, tid


def test_get_standard_templates_merges_existing(app, client):
    hdr, tid = _api_tenant(app, client, "Biz ST1", "st1_admin")
    with app.app_context():
        appmod.db.session.add(appmod.WhatsAppTemplate(
            tenant_id=tid, name="payment_confirmation", language="en", category="UTILITY",
            status="APPROVED", components=[{"type": "BODY", "text": "x"}]))
        appmod.db.session.commit()
    r = client.get("/api/whatsapp/standard-templates", headers=hdr)
    assert r.status_code == 200
    items = {t["key"]: t for t in r.get_json()["templates"]}
    assert items["payment_paid"]["name"] == "payment_confirmation"
    assert items["payment_paid"]["existing"]["status"] == "APPROVED"
    assert items["bulk_offer"]["existing"] is None
    assert {"key": "customer_name", "label": "Customer name"} in items["payment_paid"]["variables"]


def test_submit_valid_and_invalid_items(app, client, monkeypatch):
    hdr, tid = _api_tenant(app, client, "Biz ST2", "st2_admin")
    posted = []

    def fake_post(url, headers=None, json=None, timeout=None):
        posted.append((url, json))
        return Resp({"id": "META_T1", "status": "PENDING"})
    monkeypatch.setattr(appmod.requests, "post", fake_post)

    r = client.post("/api/whatsapp/standard-templates/submit", headers=hdr, json={
        "language": "en",
        "items": [
            {"key": "payment_paid", "name": "my_payment_conf", "header": None,
             "body": "Paid {amount} by {customer_name}, balance {balance}.", "footer": None, "buttons": []},
            {"key": "bulk_offer", "name": "my_offer", "header": None,
             "body": "Special offer just for you!", "footer": None, "buttons": []},
        ]})
    assert r.status_code == 200, r.get_json()
    results = {x["key"]: x for x in r.get_json()["results"]}
    assert results["payment_paid"]["ok"] is True and results["payment_paid"]["status"] == "PENDING"
    assert results["bulk_offer"]["ok"] is False and "Missing variable {message}" in results["bulk_offer"]["error"]
    assert len(posted) == 1 and posted[0][0].endswith("/WABA/message_templates")
    assert posted[0][1]["name"] == "my_payment_conf" and posted[0][1]["category"] == "UTILITY"
    with app.app_context():
        row = appmod.WhatsAppTemplate.query.filter_by(tenant_id=tid, name="my_payment_conf").one()
        assert row.standard_key == "payment_paid"
        assert row.param_map == ["amount", "customer_name", "balance"]
        s = appmod.WhatsAppSettings.query.filter_by(tenant_id=tid).first()
        assert s.template_payment_paid == "my_payment_conf"
        assert s.template_bulk_offer == "special_offer"      # invalid item left untouched


def test_submit_use_existing_makes_no_meta_call(app, client, monkeypatch):
    hdr, tid = _api_tenant(app, client, "Biz ST3", "st3_admin")
    monkeypatch.setattr(appmod.requests, "post", lambda *a, **k: pytest.fail("no Meta call expected"))
    r = client.post("/api/whatsapp/standard-templates/submit", headers=hdr, json={
        "language": "en", "items": [{"key": "payment_paid", "name": "payment_confirmation", "use_existing": True}]})
    assert r.get_json()["results"][0]["ok"] is True


def test_submit_requires_api_mode(app, client):
    hdr = make_tenant(client, "Biz ST4", "st4_admin")
    r = client.post("/api/whatsapp/standard-templates/submit", headers=hdr, json={"language": "en", "items": []})
    assert r.status_code == 400


# ---- send path honours param_map --------------------------------------------

def _send_capture(app, client, monkeypatch, param_map):
    hdr, tid = _api_tenant(app, client, f"Biz SP{len(str(param_map))}", f"sp{len(str(param_map))}_admin")
    with app.app_context():
        appmod.db.session.add(appmod.WhatsAppTemplate(
            tenant_id=tid, name="payment_confirmation", language="en", category="UTILITY",
            status="APPROVED", components=[{"type": "BODY", "text": "a {{1}} b {{2}} c {{3}} d"}],
            param_map=param_map))
        appmod.db.session.commit()
    monkeypatch.setattr(appmod, "get_meta_template_definition", lambda s, n: {
        "language": "en", "components": [{"type": "BODY", "text": "a {{1}} b {{2}} c {{3}} d"}]})
    sent = []
    monkeypatch.setattr(appmod.requests, "post",
                        lambda url, json=None, headers=None, timeout=None: sent.append(json) or Resp({"messages": [{"id": "w"}]}))
    with app.app_context():
        cust = appmod.Customer(tenant_id=tid, name="Ali", phone="96170123456", balance=5,
                               whatsapp_notifications_enabled=True)
        appmod.send_whatsapp_message(cust, "payment_paid", {"amount": 10, "balance": 5})
    return [p["text"] for p in sent[0]["template"]["components"][0]["parameters"]]


def test_send_uses_param_map_order(app, client, monkeypatch):
    assert _send_capture(app, client, monkeypatch, ["amount", "customer_name", "balance"]) == ["10.00", "Ali", "5.00"]


def test_send_without_param_map_keeps_legacy_order(app, client, monkeypatch):
    assert _send_capture(app, client, monkeypatch, None) == ["Ali", "10.00", "5.00"]

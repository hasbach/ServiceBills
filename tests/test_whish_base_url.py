import whish_billing
from config import Config


class _Resp:
    ok = True
    status_code = 200

    def json(self):
        return {"status": True, "data": {"collectUrl": "https://whish.example/pay"}}


def _capture(monkeypatch):
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen["json"], seen["headers"] = json, headers
        return _Resp()
    monkeypatch.setattr(whish_billing.requests, "post", fake_post)
    return seen


def _call(**kw):
    return whish_billing.create_payment("ext1", 10, "USD", "tok", "Cust", "target", "a@b.c", "inv", **kw)


def test_base_url_drives_redirects_and_websiteurl(monkeypatch):
    seen = _capture(monkeypatch)
    _call(base_url="https://biz.example.com:8443")
    j = seen["json"]
    assert j["successCallbackUrl"].startswith("https://biz.example.com:8443/")
    assert j["successRedirectUrl"] == "https://biz.example.com:8443/billing?status=success"
    assert j["failureRedirectUrl"] == "https://biz.example.com:8443/billing?status=failed"
    assert seen["headers"]["websiteurl"] == "biz.example.com:8443"


def test_default_uses_app_base_url(monkeypatch):
    seen = _capture(monkeypatch)
    _call()
    assert seen["json"]["successRedirectUrl"] == f"{Config.APP_BASE_URL}/billing?status=success"
    assert seen["headers"]["websiteurl"].startswith(Config.APP_BASE_URL.split("://")[-1].split("/")[0])

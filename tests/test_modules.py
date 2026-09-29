from types import SimpleNamespace
import pytest
import modules


def T(plan="free", overrides=None):
    return SimpleNamespace(plan=plan, module_overrides=overrides)


@pytest.fixture(autouse=True)
def _no_license(monkeypatch):
    monkeypatch.setattr(modules, "license_provider", None)


def test_free_bundle():
    assert modules.enabled_for(T("free")) == {"core", "office", "whatsapp", "network", "upstream_sync"}


def test_pro_bundle_is_everything():
    assert modules.enabled_for(T("pro")) == modules.ALWAYS_ON | modules.PAID


def test_unknown_plan_falls_back_to_free():
    assert modules.enabled_for(T("weird")) == modules.enabled_for(T("free"))


def test_override_adds_and_removes():
    got = modules.enabled_for(T("free", {"whish_payments": True, "network": False}))
    assert "whish_payments" in got and "network" not in got


def test_override_cannot_remove_always_on():
    got = modules.enabled_for(T("free", {"core": False, "office": False}))
    assert {"core", "office"} <= got


def test_unknown_override_keys_ignored():
    assert modules.enabled_for(T("free", {"bogus": True})) == modules.enabled_for(T("free"))


def test_ai_cs_requires_whatsapp():
    assert "ai_cs" not in modules.enabled_for(T("pro", {"whatsapp": False}))
    assert "ai_cs" in modules.enabled_for(T("free", {"ai_cs": True}))


def test_none_tenant_gets_only_always_on():
    assert modules.enabled_for(None) == set(modules.ALWAYS_ON)


def test_license_provider_takes_precedence_and_honours_expiry(monkeypatch):
    lic = {"modules": {
        "network": {"term": "yearly", "expires_at": "2999-01-01"},
        "whatsapp": {"term": "monthly", "expires_at": "2000-01-01"},
        "bogus": {"term": "yearly", "expires_at": "2999-01-01"},
    }}
    monkeypatch.setattr(modules, "license_provider", lambda t: lic)
    # plan/overrides ignored when a license is present
    assert modules.enabled_for(T("pro", {"ai_cs": True})) == {"core", "office", "network"}


def test_is_enabled():
    assert modules.is_enabled(T("free"), "network") is True
    assert modules.is_enabled(T("free"), "ai_cs") is False

from datetime import date
import pytest
import license as lic


@pytest.fixture(scope="module")
def keys():
    return lic.generate_keypair()


def _payload(**over):
    p = {"license_id": "L1", "business_name": "Biz", "machine_id": "m1", "trial": False,
         "base": {"term": "lifetime", "expires_at": "2036-09-30"},
         "modules": {"network": {"term": "yearly", "expires_at": "2027-09-30"},
                     "whatsapp": {"term": "monthly", "expires_at": "2026-10-15"}},
         "issued_at": "2026-09-30T00:00:00Z"}
    p.update(over)
    return p


def test_sign_verify_round_trip(keys):
    priv, pub = keys
    text = lic.sign(_payload(), priv)
    assert text.startswith(lic.PREFIX)
    assert lic.verify(text, pub) == _payload()


def test_tampered_payload_rejected(keys):
    priv, pub = keys
    text = lic.sign(_payload(), priv)
    body, sig = text[len(lic.PREFIX):].split(".")
    other = lic.sign(_payload(business_name="Evil"), priv)[len(lic.PREFIX):].split(".")[0]
    with pytest.raises(lic.LicenseError):
        lic.verify(lic.PREFIX + other + "." + sig, pub)


def test_wrong_key_rejected(keys):
    priv, _ = keys
    _, other_pub = lic.generate_keypair()
    with pytest.raises(lic.LicenseError):
        lic.verify(lic.sign(_payload(), priv), other_pub)


@pytest.mark.parametrize("bad", ["", "garbage", lic.PREFIX + "x", lic.PREFIX + "a.b.c"])
def test_malformed_rejected(keys, bad):
    with pytest.raises(lic.LicenseError):
        lic.verify(bad, keys[1])


def test_add_term():
    d = date(2026, 1, 31)
    assert lic.add_term(d, "monthly") == date(2026, 2, 28)
    assert lic.add_term(d, "yearly") == date(2027, 1, 31)
    assert lic.add_term(d, "lifetime") == date(2036, 1, 31)
    assert lic.add_term(d, "trial") == date(2026, 3, 2)
    with pytest.raises(ValueError):
        lic.add_term(d, "weekly")


def test_renew_extends_from_later_of_today_and_expiry():
    today = date(2026, 9, 30)
    assert lic.renew("2026-12-01", "monthly", today) == "2027-01-01"   # early renewal keeps days
    assert lic.renew("2026-01-01", "monthly", today) == "2026-10-30"   # lapsed: from today
    assert lic.renew(None, "yearly", today) == "2027-09-30"


def test_evaluate_valid_with_modules():
    s = lic.evaluate(_payload(), date(2026, 10, 1), "m1")
    assert s.state == "valid" and s.reason is None
    assert s.active_modules == {"network", "whatsapp"}
    assert s.base_expires_at == "2036-09-30"


def test_evaluate_expiry_day_is_inclusive_and_module_expiry_only_drops_module():
    s = lic.evaluate(_payload(), date(2026, 10, 16), "m1")
    assert s.state == "valid" and s.active_modules == {"network"}
    s = lic.evaluate(_payload(), date(2026, 10, 15), "m1")
    assert "whatsapp" in s.active_modules


def test_evaluate_readonly_reasons():
    assert lic.evaluate(None, date(2026, 10, 1), "m1").reason == "no_license"
    assert lic.evaluate(_payload(), date(2026, 10, 1), "OTHER").reason == "machine_mismatch"
    assert lic.evaluate(_payload(), date(2036, 10, 1), "m1").reason == "expired"
    assert lic.evaluate(_payload(), date(2026, 10, 1), "m1", revoked=True).reason == "revoked"
    s = lic.evaluate(_payload(), date(2026, 10, 1), "m1", release_date="2037-01-01")
    assert s.state == "readonly" and s.reason == "version_not_covered"
    for s in (lic.evaluate(None, date(2026, 10, 1), "m1"),):
        assert s.active_modules == set()


def test_trial_may_run_any_version_and_has_no_modules():
    p = _payload(trial=True, base={"term": "trial", "expires_at": "2026-10-30"}, modules={})
    s = lic.evaluate(p, date(2026, 10, 1), "m1", release_date="2030-01-01")
    assert s.state == "valid" and s.active_modules == set()


def test_warnings_within_seven_days():
    s = lic.evaluate(_payload(), date(2026, 10, 9), "m1")
    assert {"scope": "whatsapp", "expires_at": "2026-10-15"} in s.warnings
    assert all(w["scope"] != "base" for w in s.warnings)


@pytest.mark.parametrize("payload_mid,arg_mid", [(None, None), ("", ""), (None, "m1"), ("m1", None), ("m1", ""), ("", "m1")])
def test_evaluate_falsy_machine_id_is_machine_mismatch(payload_mid, arg_mid):
    s = lic.evaluate(_payload(machine_id=payload_mid), date(2026, 10, 1), arg_mid)
    assert s.state == "readonly" and s.reason == "machine_mismatch"

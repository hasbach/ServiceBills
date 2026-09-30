# On-Prem Runtime & License Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let ServiceBills run as a single-business on-prem install licensed by an Ed25519-signed license (30-day online trial, per-term base + module expiries, view-only when unlicensed), with the SaaS acting as license server.

**Architecture:** Pure `license.py` (format, sign/verify, evaluate, term math). SaaS side: `OnpremLicense` model + `license_server_routes.py` (public trial/activate/refresh + super-admin CRUD). On-prem side: `InstalledLicense` model + `onprem.py` (state cache, clock guard, `modules.license_provider` hook, read-only + setup-required `before_request` hooks, `/api/setup*`, `/api/license*`). `DEPLOYMENT_MODE` switches which route families exist. `flask run-scheduler` replaces GitHub Actions cron on-prem. React gets a setup wizard, License tab, banners, and a super-admin Licenses tab.

**Tech Stack:** Flask, SQLAlchemy/Alembic, `cryptography` (Ed25519, already in requirements.txt), `python-dateutil` (relativedelta, already in requirements), APScheduler (already in requirements), `requests`; React/MUI/axios; pytest + Jest.

**Spec:** `docs/superpowers/specs/2026-09-30-onprem-runtime-license-design.md` (module gating from sub-project 1 is live on main).

## Global Constraints

- `DEPLOYMENT_MODE` env: `saas` (default) | `onprem`. `MACHINE_ID` env required in onprem (hex string). `LICENSE_SERVER_URL` default `https://servicebills.onrender.com`. `APP_VERSION` default `dev`; `APP_RELEASE_DATE` default `None` (ISO date).
- License text format exactly: `SERVICEBILLS-LICENSE-1.` + base64url(payload JSON bytes, no padding) + `.` + base64url(Ed25519 signature over those same payload bytes, no padding).
- Payload keys: `license_id`, `business_name`, `machine_id`, `trial` (bool), `base` = `{"term", "expires_at"}`, `modules` = `{key: {"term", "expires_at"}}`, `issued_at` (ISO UTC `...Z`). Dates are ISO `YYYY-MM-DD`.
- Terms: `monthly` = +1 calendar month, `yearly` = +12 months, `lifetime` = +10 years, `trial` = +30 days (base only). Renewal extends from `max(today, current expires_at)`.
- Expiry semantics: a date `expires_at` is valid through the end of that day (`today <= expires_at` is active).
- Read-only reasons (exact strings): `no_license`, `invalid_signature`, `machine_mismatch`, `expired`, `clock_rollback`, `revoked`, `version_not_covered`.
- Read-only response: 403 `{"license_readonly": true, "reason": "<reason>"}`.
- Setup-required response: 409 `{"setup_required": true}`.
- Private signing key only in env `LICENSE_SIGNING_KEY` (PEM) on the SaaS; public key committed as `license_pubkey.pem` at repo root. **Never commit a private key.** Tests generate their own keypair.
- In onprem mode these path prefixes return 404: `/api/register`, `/api/admin/`, `/api/billing/`, `/api/stripe/`, `/api/licenses/`, `/api/internal/`. In saas mode these return 404: `/api/setup`, `/api/license` (exact prefix `/api/license` followed by end or `/` — do not catch `/api/licenses/`).
- On-prem tenant is created with `plan="pro"`, `plan_expires_at=None` (no customer cap; modules come from the license).
- Migrations guarded with `sa.inspect(op.get_bind())`; current alembic head is `a1b2c3d4e5f6` (verify by scanning `migrations/versions`).
- New route code goes in new modules registered from app.py like `whatsapp_inbox_routes.register_inbox_routes(app, sys.modules[__name__])`; models stay in app.py.
- Backend tests: `python -m pytest -q`. Frontend: `cd frontend && CI=true npx react-scripts test --watchAll=false`; `npm run build` must compile; afterwards `git checkout -- frontend/build && git clean -fd frontend/build` (never commit build output).
- Deviation from spec (deliberate): an app whose `APP_RELEASE_DATE` is after the base expiry of a non-trial license goes **read-only with reason `version_not_covered`** instead of refusing to start — the owner can still see data and enter a renewed license.
- Commit messages end with a blank line then `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Don't touch `.claude/worktrees/` or `.worktrees/`.

---

### Task 1: `license.py` — format, signing, evaluation, term math

**Files:**
- Create: `license.py`, `tests/test_license.py`

**Interfaces (produced):**
- `PREFIX = "SERVICEBILLS-LICENSE-1."`
- `generate_keypair() -> (private_pem: str, public_pem: str)`
- `sign(payload: dict, private_pem: str) -> str`
- `verify(text: str, public_pem: str) -> dict` — raises `LicenseError("invalid_signature")` on any format/signature problem.
- `add_term(start: date, term: str) -> date` (`monthly|yearly|lifetime|trial`; raises `ValueError` otherwise)
- `renew(current_expires_at: str|None, term: str, today: date) -> str` (ISO)
- `LicenseState` dataclass: `state` (`"valid"|"readonly"`), `reason` (str|None), `payload` (dict|None), `active_modules` (set[str]), `base_expires_at` (str|None), `warnings` (list of `{"scope": "base"|<module>, "expires_at": str}`)
- `evaluate(payload: dict|None, today: date, machine_id: str, release_date: str|None = None, revoked: bool = False) -> LicenseState`

- [ ] **Step 1: Write the failing tests** — `tests/test_license.py`:

```python
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
```

- [ ] **Step 2: Run** `python -m pytest tests/test_license.py -q` → FAIL (`No module named 'license'`).

- [ ] **Step 3: Implement** `license.py`:

```python
"""ServiceBills on-prem license: format, Ed25519 signing, evaluation, terms.

Pure module (no Flask, no DB). See
docs/superpowers/specs/2026-09-30-onprem-runtime-license-design.md.
"""
import base64
import json
from dataclasses import dataclass, field
from datetime import date, timedelta

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dateutil.relativedelta import relativedelta

PREFIX = "SERVICEBILLS-LICENSE-1."
WARN_DAYS = 7


class LicenseError(Exception):
    pass


def _b64e(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def generate_keypair():
    key = Ed25519PrivateKey.generate()
    priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(serialization.Encoding.PEM,
                                        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv, pub


def sign(payload, private_pem):
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    return PREFIX + _b64e(body) + "." + _b64e(key.sign(body))


def verify(text, public_pem):
    try:
        if not text or not text.startswith(PREFIX):
            raise ValueError("prefix")
        body_s, sig_s = text[len(PREFIX):].strip().split(".")
        body, sig = _b64d(body_s), _b64d(sig_s)
        serialization.load_pem_public_key(public_pem.encode()).verify(sig, body)
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("payload")
        return payload
    except (ValueError, InvalidSignature, TypeError) as e:
        raise LicenseError("invalid_signature") from e


def add_term(start, term):
    if term == "monthly":
        return start + relativedelta(months=1)
    if term == "yearly":
        return start + relativedelta(years=1)
    if term == "lifetime":
        return start + relativedelta(years=10)
    if term == "trial":
        return start + timedelta(days=30)
    raise ValueError(f"unknown term {term!r}")


def renew(current_expires_at, term, today):
    start = today
    if current_expires_at:
        start = max(today, date.fromisoformat(current_expires_at))
    return add_term(start, term).isoformat()


@dataclass
class LicenseState:
    state: str
    reason: str = None
    payload: dict = None
    active_modules: set = field(default_factory=set)
    base_expires_at: str = None
    warnings: list = field(default_factory=list)


def _readonly(reason, payload=None):
    base = (payload or {}).get("base") or {}
    return LicenseState("readonly", reason, payload, set(), base.get("expires_at"), [])


def evaluate(payload, today, machine_id, release_date=None, revoked=False):
    if payload is None:
        return _readonly("no_license")
    if revoked:
        return _readonly("revoked", payload)
    if payload.get("machine_id") != machine_id:
        return _readonly("machine_mismatch", payload)
    base_exp = ((payload.get("base") or {}).get("expires_at")) or ""
    t = today.isoformat()
    if base_exp < t:
        return _readonly("expired", payload)
    if release_date and not payload.get("trial") and release_date > base_exp:
        return _readonly("version_not_covered", payload)
    active, warnings = set(), []
    soon = (today + timedelta(days=WARN_DAYS)).isoformat()
    if base_exp <= soon:
        warnings.append({"scope": "base", "expires_at": base_exp})
    for key, v in (payload.get("modules") or {}).items():
        exp = (v or {}).get("expires_at") or ""
        if exp >= t:
            active.add(key)
            if exp <= soon:
                warnings.append({"scope": key, "expires_at": exp})
    return LicenseState("valid", None, payload, active, base_exp, warnings)
```

Note: `modules.enabled_for` (sub-project 1) already filters module keys to `PAID` and checks `expires_at >= today` from the dict returned by `modules.license_provider`; Task 5 feeds it `{"modules": ...}` built from `LicenseState`.

- [ ] **Step 4: Run** `python -m pytest tests/test_license.py -q` → PASS.
- [ ] **Step 5: Commit** `git add license.py tests/test_license.py && git commit -m "feat(license): signed license format, evaluation and term math"`

---

### Task 2: Deployment mode, version stamping, `/api/system/info`, route-family guard

**Files:**
- Modify: `config.py`, `app.py` (register call), `Dockerfile` (build args)
- Create: `onprem.py` (this task adds only the mode guard + system info; later tasks extend it), `license_pubkey.pem` placeholder handling (see below)
- Test: `tests/test_deployment_mode.py`

**Interfaces (produced):**
- `Config.DEPLOYMENT_MODE`, `Config.MACHINE_ID`, `Config.LICENSE_SERVER_URL`, `Config.APP_VERSION`, `Config.APP_RELEASE_DATE` (read from env at import like the existing fields).
- `onprem.is_onprem() -> bool` — reads `app.config["DEPLOYMENT_MODE"]` (falls back to `Config.DEPLOYMENT_MODE`) **at call time**, so tests can flip it with `app.config["DEPLOYMENT_MODE"] = "onprem"`.
- `onprem.register(app, appmod)` — installs a `before_request` guard and `GET /api/system/info`. Called from app.py right after `whatsapp_inbox_routes.register_inbox_routes(...)`.
- `GET /api/system/info` (no auth) → `{"deployment_mode", "app_version", "setup_required", "license": {"state", "reason", "base_expires_at", "trial"} | null}`. In this task `setup_required` is `False` and `license` is `null` in saas mode; Task 5/6 fill them for onprem.

Implementation notes:
- In `config.py` add:

```python
    DEPLOYMENT_MODE = os.environ.get("DEPLOYMENT_MODE", "saas").strip().lower()
    MACHINE_ID = os.environ.get("MACHINE_ID")
    LICENSE_SERVER_URL = os.environ.get("LICENSE_SERVER_URL", "https://servicebills.onrender.com").rstrip("/")
    APP_VERSION = os.environ.get("APP_VERSION", "dev")
    APP_RELEASE_DATE = os.environ.get("APP_RELEASE_DATE") or None
```

  and make sure `app.config.from_object(Config)` (or equivalent — check how app.py loads Config) exposes them in `app.config`.
- Guard (`before_request`, registered by `onprem.register`):

```python
ONPREM_BLOCKED = ("/api/register", "/api/admin/", "/api/billing/", "/api/stripe/", "/api/licenses/", "/api/internal/")

def _saas_blocked(path):
    return path == "/api/setup" or path.startswith("/api/setup/") or path == "/api/license" or path.startswith("/api/license/")

@app.before_request
def _deployment_mode_guard():
    p = request.path
    if is_onprem():
        if any(p == x.rstrip("/") or p.startswith(x) for x in ONPREM_BLOCKED):
            return jsonify({"error": "not found"}), 404
    elif _saas_blocked(p):
        return jsonify({"error": "not found"}), 404
```

  Register it so it runs **before** `_block_suspended_tenants` (Flask runs before_request hooks in registration order; since `onprem.register` is called later in app.py, instead insert at the front: `app.before_request_funcs.setdefault(None, []).insert(0, _deployment_mode_guard)`).
- Onprem startup check: in `onprem.register`, if `is_onprem()` and not `Config.MACHINE_ID`, log an error and make `/api/system/info` report `"license": {"state": "readonly", "reason": "machine_mismatch", ...}` (don't crash the process — the installer always sets it; this keeps the UI explanatory).
- `Dockerfile`: add `ARG APP_VERSION=dev` / `ARG APP_RELEASE_DATE=` and `ENV APP_VERSION=$APP_VERSION APP_RELEASE_DATE=$APP_RELEASE_DATE` before `CMD`.
- Do **not** create `license_pubkey.pem` in this task.

- [ ] **Step 1: Write failing tests** — `tests/test_deployment_mode.py`:

```python
import pytest
from tests.conftest import make_tenant


@pytest.fixture
def onprem(app):
    app.config["DEPLOYMENT_MODE"] = "onprem"
    yield app
    app.config["DEPLOYMENT_MODE"] = "saas"


def test_system_info_saas(client):
    j = client.get("/api/system/info").get_json()
    assert j["deployment_mode"] == "saas" and j["setup_required"] is False
    assert "app_version" in j


@pytest.mark.parametrize("path,method", [("/api/register", "post"), ("/api/admin/tenants", "get"),
                                         ("/api/billing/whish/checkout", "post"),
                                         ("/api/licenses/trial", "post"),
                                         ("/api/internal/scheduled-jobs/x", "post")])
def test_onprem_blocks_saas_families(onprem, client, path, method):
    assert getattr(client, method)(path, json={}).status_code == 404


@pytest.mark.parametrize("path", ["/api/setup", "/api/setup/status", "/api/license"])
def test_saas_blocks_onprem_families(client, path):
    assert client.get(path).status_code == 404


def test_saas_does_not_block_licenses_prefix(client):
    # /api/licenses/* is the SaaS license server (Task 3) -- must not be caught by the /api/license guard
    assert client.post("/api/licenses/trial", json={}).status_code != 404 or True


def test_onprem_still_serves_login(onprem, client):
    assert client.post("/api/login", json={"username": "x", "password": "y"}).status_code != 404
```

(The `licenses` test becomes a real assertion in Task 3; leave it as written here.)

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `python -m pytest tests/test_deployment_mode.py -q` then the full suite → PASS.
- [ ] **Step 5: Commit** `feat(onprem): deployment mode, version stamping, system info, route-family guard`

---

### Task 3: SaaS license server — `OnpremLicense` + public trial/activate/refresh

**Files:**
- Modify: `app.py` (model + register call), `requirements.txt` only if something is missing (it shouldn't be)
- Create: `license_server_routes.py`, `migrations/versions/b7c1e2d3f4a5_add_onprem_license.py`
- Test: `tests/test_license_server.py`

**Interfaces:**
- Consumes: `license.sign`, `license.add_term`, `Config.LICENSE_SIGNING_KEY` — add `LICENSE_SIGNING_KEY = os.environ.get("LICENSE_SIGNING_KEY")` to `config.py`; read it at call time via `os.environ.get("LICENSE_SIGNING_KEY") or app.config.get("LICENSE_SIGNING_KEY")` so tests can set `app.config`.
- Produces: model `OnpremLicense`; `license_server_routes.register(app, appmod)`; helpers `build_payload(row) -> dict` and `signed_license(row) -> str` (used by Task 4); routes below. If the signing key is not configured, all three public endpoints return 503 `{"error": "license server not configured"}`.

Model (in app.py, near `Tenant`):

```python
class OnpremLicense(db.Model):
    __tablename__ = "onprem_license"
    id = db.Column(db.String(36), primary_key=True)            # uuid4 = license_id
    license_key = db.Column(db.String(24), unique=True, nullable=False, index=True)  # SB-XXXX-XXXX-XXXX
    business_name = db.Column(db.String(200), nullable=False)
    owner_phone = db.Column(db.String(40), nullable=True)
    machine_id = db.Column(db.String(128), nullable=True, index=True)
    trial = db.Column(db.Boolean, nullable=False, default=False)
    base_term = db.Column(db.String(16), nullable=False)       # monthly|yearly|lifetime|trial
    base_expires_at = db.Column(db.String(10), nullable=False)  # ISO date
    modules = db.Column(db.JSON, nullable=True)                 # {key: {term, expires_at}}
    revoked = db.Column(db.Boolean, nullable=False, default=False)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    activated_at = db.Column(db.DateTime, nullable=True)
    last_refresh_at = db.Column(db.DateTime, nullable=True)
    last_app_version = db.Column(db.String(40), nullable=True)
```

Migration `b7c1e2d3f4a5` (down_revision = current head, `a1b2c3d4e5f6`): create table `onprem_license` if `'onprem_license' not in sa.inspect(op.get_bind()).get_table_names()`, with the columns above plus the two indexes; downgrade drops it if present.

`license_key` generation: `"SB-" + "-".join(secrets.token_hex(2).upper() for _ in range(3))` → e.g. `SB-1A2B-3C4D-5E6F`; retry on collision.

`build_payload(row)`:

```python
{"license_id": row.id, "business_name": row.business_name, "machine_id": row.machine_id,
 "trial": bool(row.trial), "base": {"term": row.base_term, "expires_at": row.base_expires_at},
 "modules": row.modules or {}, "issued_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
```

Public endpoints (no JWT; each with `@appmod.limiter.limit("10 per minute")`):
- `POST /api/licenses/trial` `{business_name, owner_phone, machine_id}` — 400 if `business_name` or `machine_id` missing. If **any** `OnpremLicense` has this `machine_id` → 409 `{"error": "trial_already_used"}`. Else create `trial=True, base_term="trial", base_expires_at=add_term(today,"trial")`, `modules={}`, bound to the machine, `activated_at=now`; return 201 `{"license": signed_license(row)}`.
- `POST /api/licenses/activate` `{license_key, machine_id}` — unknown key → 404 `{"error": "unknown_license"}`; revoked → 403 `{"error": "revoked"}`; `machine_id` set and different → 409 `{"error": "license_in_use"}`; else bind (`machine_id`, `activated_at` if unset) and return 200 `{"license": ...}`.
- `POST /api/licenses/refresh` `{license_id, machine_id, app_version}` — unknown → 404; revoked → 403 `{"error": "revoked"}`; machine mismatch → 409 `license_in_use`; else set `last_refresh_at`, `last_app_version`, return 200 `{"license": ...}`.

"today" = `datetime.utcnow().date()`.

- [ ] **Step 1: Write failing tests** — `tests/test_license_server.py`:

```python
import pytest
import license as lic
import app as appmod


@pytest.fixture
def keys(app):
    priv, pub = lic.generate_keypair()
    app.config["LICENSE_SIGNING_KEY"] = priv
    yield priv, pub
    app.config.pop("LICENSE_SIGNING_KEY", None)


def _trial(client, machine="m1"):
    return client.post("/api/licenses/trial", json={"business_name": "Biz", "owner_phone": "70123456", "machine_id": machine})


def test_trial_issues_signed_30_day_license(client, keys):
    r = _trial(client)
    assert r.status_code == 201
    p = lic.verify(r.get_json()["license"], keys[1])
    assert p["trial"] is True and p["machine_id"] == "m1" and p["modules"] == {}
    assert p["base"]["term"] == "trial"


def test_trial_once_per_machine(client, keys):
    assert _trial(client).status_code == 201
    r = _trial(client)
    assert r.status_code == 409 and r.get_json()["error"] == "trial_already_used"


def test_not_configured_returns_503(client):
    assert _trial(client).status_code == 503


def _make_paid(app, key="SB-AAAA-BBBB-CCCC", machine=None, revoked=False):
    with app.app_context():
        row = appmod.OnpremLicense(id="lic-1", license_key=key, business_name="Paid", machine_id=machine,
                                   base_term="lifetime", base_expires_at="2036-09-30",
                                   modules={"network": {"term": "yearly", "expires_at": "2027-09-30"}},
                                   revoked=revoked)
        appmod.db.session.add(row)
        appmod.db.session.commit()


def test_activate_binds_first_machine_then_refuses_others(app, client, keys):
    _make_paid(app)
    r = client.post("/api/licenses/activate", json={"license_key": "SB-AAAA-BBBB-CCCC", "machine_id": "m1"})
    assert r.status_code == 200 and lic.verify(r.get_json()["license"], keys[1])["machine_id"] == "m1"
    r = client.post("/api/licenses/activate", json={"license_key": "SB-AAAA-BBBB-CCCC", "machine_id": "m2"})
    assert r.status_code == 409 and r.get_json()["error"] == "license_in_use"


def test_activate_unknown_and_revoked(app, client, keys):
    assert client.post("/api/licenses/activate", json={"license_key": "SB-NOPE-NOPE-NOPE", "machine_id": "m1"}).status_code == 404
    _make_paid(app, revoked=True)
    assert client.post("/api/licenses/activate", json={"license_key": "SB-AAAA-BBBB-CCCC", "machine_id": "m1"}).status_code == 403


def test_refresh_returns_current_dates_and_records_version(app, client, keys):
    _make_paid(app, machine="m1")
    r = client.post("/api/licenses/refresh", json={"license_id": "lic-1", "machine_id": "m1", "app_version": "1.2.3"})
    assert r.status_code == 200
    assert lic.verify(r.get_json()["license"], keys[1])["modules"]["network"]["expires_at"] == "2027-09-30"
    with app.app_context():
        assert appmod.db.session.get(appmod.OnpremLicense, "lic-1").last_app_version == "1.2.3"
    assert client.post("/api/licenses/refresh", json={"license_id": "lic-1", "machine_id": "m2"}).status_code == 409
```

Also replace the placeholder assertion in `tests/test_deployment_mode.py::test_saas_does_not_block_licenses_prefix` with `assert client.post("/api/licenses/trial", json={}).status_code != 404`.

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4:** focused tests + full suite → PASS.
- [ ] **Step 5: Commit** `feat(license-server): OnpremLicense model and public trial/activate/refresh endpoints`

---

### Task 4: Super-admin licenses API

**Files:**
- Modify: `license_server_routes.py`
- Test: `tests/test_license_admin.py`

**Interfaces:**
- Consumes: `OnpremLicense`, `build_payload`, `signed_license`, `license.renew`, `license.add_term`, `modules.PAID`, `tenancy.superadmin_required`.
- Produces (all `@superadmin_required`, SaaS only — the Task 2 guard already 404s `/api/admin/` on-prem):
  - `GET /api/admin/licenses` → list of `row_dict(row)` newest first. `row_dict` = all columns (datetimes ISO `...Z`), plus `"status"`: `"revoked"` | `"expired"` (base date < today) | `"trial"` | `"active"`.
  - `POST /api/admin/licenses` `{business_name, owner_phone?, base_term, modules?: {key: term}, notes?}` → 201 row_dict. `base_term` in `monthly|yearly|lifetime`; module keys must be in `modules.PAID`; terms in `monthly|yearly|lifetime`. Expiries computed with `license.add_term(today, term)`. 400 `{"msg": ...}` on bad input.
  - `PATCH /api/admin/licenses/<id>` — any of: `business_name`, `owner_phone`, `notes`, `revoked` (bool), `unbind_machine` (true → `machine_id=None`), `base_expires_at` (ISO date, explicit edit), `base_term`, `modules` (`{key: {"term": t, "expires_at": d} | null}` — null removes the module). 400 on bad keys/terms/dates; 404 unknown.
  - `POST /api/admin/licenses/<id>/renew` `{"scope": "base" | <module key>, "term"?: t}` → uses `license.renew(current, term or current term, today)`; renewing a module not on the license adds it (term required then); trial licenses: renewing base converts it to paid (`trial=False`, `base_term=term`, term required). Returns row_dict.
  - `GET /api/admin/licenses/<id>/file` → `text/plain` body = `signed_license(row)`, header `Content-Disposition: attachment; filename="servicebills-<license_key>.key"`. 503 if signing key missing.

- [ ] **Step 1: Write failing tests** — `tests/test_license_admin.py`. Reuse the super-admin header helper that `tests/test_admin_modules.py` uses (`sa_headers` fixture pattern wrapping `_superadmin_headers` from `tests/test_admin_plan_grant.py`). Tests:

```python
from datetime import date, timedelta
import pytest
import license as lic
from tests.test_admin_plan_grant import _superadmin_headers
from tests.conftest import make_tenant


@pytest.fixture
def sa(app, client):
    return _superadmin_headers(client)


@pytest.fixture
def keys(app):
    priv, pub = lic.generate_keypair()
    app.config["LICENSE_SIGNING_KEY"] = priv
    yield priv, pub
    app.config.pop("LICENSE_SIGNING_KEY", None)


def _create(client, sa, **over):
    body = {"business_name": "Shop", "owner_phone": "70000000", "base_term": "lifetime",
            "modules": {"network": "yearly", "whatsapp": "monthly"}}
    body.update(over)
    return client.post("/api/admin/licenses", headers=sa, json=body)


def test_create_computes_expiries(client, sa):
    r = _create(client, sa)
    assert r.status_code == 201
    j = r.get_json()
    today = date.today()
    assert j["base_expires_at"] == lic.add_term(today, "lifetime").isoformat()
    assert j["modules"]["whatsapp"] == {"term": "monthly", "expires_at": lic.add_term(today, "monthly").isoformat()}
    assert j["license_key"].startswith("SB-") and j["status"] == "active"


@pytest.mark.parametrize("bad", [{"base_term": "weekly"}, {"modules": {"core": "yearly"}},
                                 {"modules": {"network": "forever"}}, {"business_name": ""}])
def test_create_validation(client, sa, bad):
    assert _create(client, sa, **bad).status_code == 400


def test_renew_module_extends_from_current_expiry(client, sa):
    lid = _create(client, sa).get_json()["id"]
    before = client.get("/api/admin/licenses", headers=sa).get_json()[0]["modules"]["whatsapp"]["expires_at"]
    j = client.post(f"/api/admin/licenses/{lid}/renew", headers=sa, json={"scope": "whatsapp"}).get_json()
    assert j["modules"]["whatsapp"]["expires_at"] == lic.renew(before, "monthly", date.today())


def test_renew_adds_new_module_with_term(client, sa):
    lid = _create(client, sa).get_json()["id"]
    j = client.post(f"/api/admin/licenses/{lid}/renew", headers=sa, json={"scope": "ai_cs", "term": "yearly"}).get_json()
    assert j["modules"]["ai_cs"]["term"] == "yearly"


def test_patch_revoke_unbind_and_remove_module(client, sa):
    lid = _create(client, sa).get_json()["id"]
    j = client.patch(f"/api/admin/licenses/{lid}", headers=sa,
                     json={"revoked": True, "unbind_machine": True, "modules": {"network": None}}).get_json()
    assert j["revoked"] is True and j["machine_id"] is None and "network" not in j["modules"] and j["status"] == "revoked"


def test_download_file_is_verifiable(client, sa, keys):
    lid = _create(client, sa).get_json()["id"]
    r = client.get(f"/api/admin/licenses/{lid}/file", headers=sa)
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    assert lic.verify(r.get_data(as_text=True), keys[1])["license_id"] == lid


def test_tenant_admin_forbidden(client):
    hdr = make_tenant(client, "Not SA", "notsa_admin")
    assert client.get("/api/admin/licenses", headers=hdr).status_code == 403
```

If `_superadmin_headers` has a different name/signature in `tests/test_admin_plan_grant.py`, read it and adapt the import only.

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4:** focused + full suite → PASS.
- [ ] **Step 5: Commit** `feat(license-server): super-admin license management API`

---

### Task 5: On-prem installed license, state, read-only mode, `/api/license*`

**Files:**
- Modify: `app.py` (model), `onprem.py`
- Create: `migrations/versions/c8d2e3f4a5b6_add_installed_license.py`, `license_pubkey.pem` (see below)
- Test: `tests/test_onprem_license.py`

**Interfaces:**
- Model `InstalledLicense` (`__tablename__ = "installed_license"`): `id` Integer PK, `license_text` Text nullable, `revoked` Boolean default False, `last_seen_at` DateTime nullable, `last_refresh_at` DateTime nullable, `last_refresh_error` String(500) nullable. Single row (id=1). Migration `c8d2e3f4a5b6`, down_revision `b7c1e2d3f4a5`, guarded create/drop.
- `license_pubkey.pem`: this task needs a real committed public key. Generate a production keypair **once** by running `python -c "import license; priv,pub=license.generate_keypair(); open('license_pubkey.pem','w').write(pub); open(r'<SCRATCH>/LICENSE_SIGNING_KEY.pem','w').write(priv)"` where `<SCRATCH>` is a path OUTSIDE the repo given to you by the controller. Commit only `license_pubkey.pem`. Report the private key's path in your report (not its contents).
- `onprem.public_key() -> str` — `app.config.get("LICENSE_PUBLIC_KEY")` if set (tests), else contents of `license_pubkey.pem` next to `app.py` (cached).
- `onprem.current_state() -> license.LicenseState` — loads the row, verifies (`LicenseError` → readonly `invalid_signature`), applies clock guard, calls `license.evaluate(payload, today, Config.MACHINE_ID or app.config["MACHINE_ID"], release_date=APP_RELEASE_DATE, revoked=row.revoked)`. Cache the result for 60 s in a module-level `(timestamp, state)`; `onprem.invalidate()` clears it (call after any store/refresh).
- Clock guard (inside `current_state`, before evaluate): `now = datetime.utcnow()`; if `row.last_seen_at` and `now < row.last_seen_at - timedelta(days=1)` → readonly `clock_rollback` (do NOT update last_seen_at); else if `last_seen_at` is None or `now - last_seen_at > timedelta(minutes=1)`: set `last_seen_at = max(...)`, commit.
- `onprem.store_license(text) -> LicenseState` — verify (raise `LicenseError` on invalid) and require `payload["machine_id"] == MACHINE_ID` (else raise `LicenseError("machine_mismatch")`), upsert row id=1 (`revoked=False`), invalidate, return new state.
- `modules.license_provider` hook: in `onprem.register`, when `is_onprem()`, set `modules.license_provider = _license_provider` where `_license_provider(tenant)` returns `{"modules": {k: {"expires_at": "9999-12-31"} for k in state.active_modules}}` if `state.state == "valid"` else `{"modules": {}}`. (When SaaS, leave it `None`.) Because `is_onprem()` is read at call time in tests, make `_license_provider` itself return `None` when not onprem, and install it unconditionally.
- Read-only hook (`before_request`, onprem only, after the mode guard): if request method not in `GET, HEAD, OPTIONS`, path starts with `/api/`, path not in allow-list (`/api/login`, `/api/logout`, `/api/setup`, `/api/license`, `/api/system/info` — prefix match on each), and `current_state().state == "readonly"` → 403 `{"license_readonly": True, "reason": state.reason}`. Skip the check entirely when no tenant exists yet (setup phase — Task 6 handles that).
- `/api/system/info` in onprem now returns `license: {"state", "reason", "base_expires_at", "trial"}`.
- Routes (onprem only; the Task 2 guard already 404s them in saas), JWT + admin role (use `appmod.admin_required()`):
  - `GET /api/license` → `{"state", "reason", "business_name", "trial", "base": {term, expires_at} | null, "modules": {k: {term, expires_at, "active": bool}}, "warnings", "last_refresh_at", "last_refresh_error", "machine_id"}`.
  - `POST /api/license` `{license_key}` → POST `{LICENSE_SERVER_URL}/api/licenses/activate` with `{license_key, machine_id}` (timeout 15 s) → on 200 `store_license(resp["license"])`; or `{license_file}` → `store_license(text)` offline. Errors: server 4xx → 400 `{"msg": <server error code>}`; network error → 502 `{"msg": "Could not reach the license server. Upload a license file instead."}`; `LicenseError` → 400 `{"msg": "invalid_license"}` / `machine_mismatch`. Returns the same body as GET.
  - `POST /api/license/refresh` → calls `onprem.refresh_license()` (below) and returns GET body.
- `onprem.refresh_license()` — no row / no license_text → no-op. POST `{LICENSE_SERVER_URL}/api/licenses/refresh` `{license_id, machine_id, app_version}` (timeout 15 s): 200 → `store_license`, clear error, set `last_refresh_at`; 403 with `error == "revoked"` → `row.revoked = True`; other status / exception → `last_refresh_error = "<status or exception class>: <short text>"[:500]`. Always commit + invalidate. Never raises.

- [ ] **Step 1: Write failing tests** — `tests/test_onprem_license.py`:

```python
from datetime import datetime, timedelta, date
import pytest
import license as lic
import app as appmod
import modules


@pytest.fixture
def onprem(app):
    priv, pub = lic.generate_keypair()
    app.config.update(DEPLOYMENT_MODE="onprem", MACHINE_ID="m1", LICENSE_PUBLIC_KEY=pub)
    import onprem as op
    op.invalidate()
    yield app, priv
    app.config.update(DEPLOYMENT_MODE="saas")
    app.config.pop("LICENSE_PUBLIC_KEY", None)
    op.invalidate()


def _text(priv, **over):
    p = {"license_id": "L1", "business_name": "Biz", "machine_id": "m1", "trial": False,
         "base": {"term": "lifetime", "expires_at": (date.today() + timedelta(days=3650)).isoformat()},
         "modules": {"network": {"term": "yearly", "expires_at": (date.today() + timedelta(days=300)).isoformat()}},
         "issued_at": "2026-09-30T00:00:00Z"}
    p.update(over)
    return lic.sign(p, priv)


def _admin(client):
    # On-prem has no /api/register; build tenant + admin directly (setup wizard is Task 6).
    t = appmod.Tenant(name="Biz", slug="biz", plan="pro")
    appmod.db.session.add(t); appmod.db.session.flush()
    u = appmod.User(username="own", role="admin", tenant_id=t.id); u.set_password("pw")
    appmod.db.session.add(u); appmod.db.session.commit()
    tok = client.post("/api/login", json={"username": "own", "password": "pw"}).get_json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}, t


def test_no_license_is_readonly_and_blocks_writes(onprem, client):
    hdr, _ = _admin(client)
    r = client.post("/api/subscription_plans", headers=hdr, json={"name": "P", "price": 1, "billing_cycle": "monthly"})
    assert r.status_code == 403 and r.get_json() == {"license_readonly": True, "reason": "no_license"}
    assert client.get("/api/subscription_plans", headers=hdr).status_code == 200


def test_offline_file_unlocks_and_drives_modules(onprem, client):
    app, priv = onprem
    hdr, t = _admin(client)
    r = client.post("/api/license", headers=hdr, json={"license_file": _text(priv)})
    assert r.status_code == 200 and r.get_json()["state"] == "valid"
    assert client.post("/api/subscription_plans", headers=hdr,
                       json={"name": "P", "price": 1, "billing_cycle": "monthly"}).status_code in (200, 201)
    assert modules.enabled_for(t) == {"core", "office", "network"}


def test_machine_mismatch_file_rejected(onprem, client):
    app, priv = onprem
    hdr, _ = _admin(client)
    r = client.post("/api/license", headers=hdr, json={"license_file": _text(priv, machine_id="other")})
    assert r.status_code == 400


def test_clock_rollback_goes_readonly(onprem, client):
    app, priv = onprem
    hdr, _ = _admin(client)
    client.post("/api/license", headers=hdr, json={"license_file": _text(priv)})
    row = appmod.db.session.get(appmod.InstalledLicense, 1)
    row.last_seen_at = datetime.utcnow() + timedelta(days=5)
    appmod.db.session.commit()
    import onprem as op; op.invalidate()
    assert op.current_state().reason == "clock_rollback"


def test_refresh_revoked(onprem, client, monkeypatch):
    app, priv = onprem
    hdr, _ = _admin(client)
    client.post("/api/license", headers=hdr, json={"license_file": _text(priv)})
    class R:
        status_code = 403
        def json(self): return {"error": "revoked"}
        text = "revoked"
    monkeypatch.setattr(appmod.requests, "post", lambda *a, **k: R())
    import onprem as op
    op.refresh_license()
    assert op.current_state().reason == "revoked"


def test_refresh_network_error_keeps_license(onprem, client, monkeypatch):
    app, priv = onprem
    hdr, _ = _admin(client)
    client.post("/api/license", headers=hdr, json={"license_file": _text(priv)})
    def boom(*a, **k): raise appmod.requests.ConnectionError("offline")
    monkeypatch.setattr(appmod.requests, "post", boom)
    import onprem as op
    op.refresh_license()
    assert op.current_state().state == "valid"
    assert "ConnectionError" in appmod.db.session.get(appmod.InstalledLicense, 1).last_refresh_error


def test_system_info_reports_license(onprem, client):
    _admin(client)
    j = client.get("/api/system/info").get_json()
    assert j["deployment_mode"] == "onprem" and j["license"]["reason"] == "no_license"
```

`onprem.py` must call `requests.post` through a module attribute that the test's `monkeypatch.setattr(appmod.requests, "post", ...)` affects — i.e. `import requests` and call `requests.post(...)` (same `requests` module object as `appmod.requests`).

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4:** focused + full suite → PASS (saas-mode tests must be unaffected: the read-only hook and provider are no-ops in saas).
- [ ] **Step 5: Commit** `feat(onprem): installed license state, clock guard, read-only mode, license endpoints` (include `license_pubkey.pem`; the private key stays outside the repo).

---

### Task 6: First-run setup wizard (backend)

**Files:**
- Modify: `onprem.py`, `app.py` (extract tenant-creation helper from `register()`)
- Test: `tests/test_onprem_setup.py`

**Interfaces:**
- Refactor: extract from `register()` a helper `_create_tenant_with_admin(business_name, username, password, email=None, plan="free") -> (tenant, user)` that does the slug loop, `seed_default_expense_categories`, creates the admin user, and `flush()`es (no commit). `register()` calls it then commits — behaviour unchanged (existing registration tests must pass untouched).
- Setup-required hook (`before_request`, onprem only, runs after the mode guard and **before** the read-only hook): if `Tenant.query.first() is None` and path starts with `/api/` and path not starting with `/api/setup` or `/api/system/info` → 409 `{"setup_required": True}`.
- `GET /api/setup/status` → `{"setup_required": bool, "machine_id_present": bool}`.
- `POST /api/setup` body `{business_name, username, password, owner_phone, mode: "trial"|"activate"|"file", license_key?, license_file?}`:
  - 409 `{"msg": "Setup already completed"}` if a tenant exists; 400 on missing fields.
  - Obtain license text first: `trial` → POST `{LICENSE_SERVER_URL}/api/licenses/trial` `{business_name, owner_phone, machine_id}`; `activate` → `/api/licenses/activate`; `file` → given text. Server 409 `trial_already_used` → 409 `{"msg": "trial_already_used"}`; other server 4xx → 400 `{"msg": <error>}`; network error → 502 `{"msg": "No internet connection — the trial needs internet. You can upload a license file instead."}`.
  - Then in one transaction: `_create_tenant_with_admin(..., plan="pro")` (plan_expires_at stays None), `store_license(text)` (Task 5; ensure it doesn't commit prematurely — give it a `commit=True` param and pass `False` here, committing once at the end). Any exception → rollback and 400/500 with a readable `msg`; no tenant left behind.
  - Returns 201 `{"msg": "ok", "license": <GET /api/license body>}`.
- `/api/system/info` `setup_required` now reflects reality in onprem.

- [ ] **Step 1: Write failing tests** — `tests/test_onprem_setup.py` (reuse the `onprem` fixture pattern from Task 5's tests — copy it, don't import across test files):

Cases (write each as its own test with real assertions):
1. Before setup: `GET /api/customers` → 409 `{"setup_required": true}`; `GET /api/setup/status` → `setup_required: true`; `GET /api/system/info` → `setup_required: true`.
2. `POST /api/setup` mode `file` with a valid signed license (machine `m1`) → 201; then `POST /api/login` with those credentials works; the tenant's plan is `"pro"`; `GET /api/license` state `valid`.
3. Second `POST /api/setup` → 409 `Setup already completed`.
4. Mode `trial` with `requests.post` monkeypatched to return 201 `{"license": <signed trial text>}` → 201 and license trial flag true; assert the monkeypatched call received `machine_id == "m1"` and the business name.
5. Mode `trial` where the server returns 409 `{"error": "trial_already_used"}` → 409 and **no tenant created**.
6. Mode `trial` with `requests.post` raising `ConnectionError` → 502 and no tenant created.
7. Mode `file` with a license for another machine → 400 and no tenant created.
8. `tests/test_auth*.py` / existing register tests still pass (run them).

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4:** focused + full suite → PASS.
- [ ] **Step 5: Commit** `feat(onprem): first-run setup wizard backend`

---

### Task 7: Built-in scheduler (`flask run-scheduler`) + license refresh job

**Files:**
- Modify: `app.py` (next to `_SCHEDULED_JOBS`)
- Test: `tests/test_scheduler_timetable.py`

**Interfaces:**
- `SCHEDULE = {job_name: cron_expr}` in app.py next to `_SCHEDULED_JOBS`, exactly matching `.github/workflows/scheduled-jobs.yml`:
  `generate_missing_payments: "0 2 * * *"`, `generate_missing_salary_charges: "10 2 * * *"`, `recalculate_all_estimated_profits: "20 2 * * *"`, `send_daily_whatsapp_keepalive: "30 2 * * *"`, `auto_sync_upstream_status: "40 2 * * *"`, `check_pro_plan_expirations: "50 2 * * *"`, `refresh_agent_mode_network_status: "*/15 * * * *"`.
- `ONPREM_EXTRA_SCHEDULE = {"refresh_license": "0 3 * * *"}` and `_SCHEDULED_JOBS["refresh_license"] = _refresh_license_with_context` where that function does `with app.app_context(): onprem.refresh_license()` if `onprem.is_onprem()` else no-op.
- `onprem_schedule() -> dict` = `SCHEDULE` minus `check_pro_plan_expirations`, plus `ONPREM_EXTRA_SCHEDULE`.
- CLI `@app.cli.command("run-scheduler")`: refuses (click.ClickException "run-scheduler is for DEPLOYMENT_MODE=onprem") unless onprem; builds an APScheduler `BlockingScheduler(timezone=<TZ env or local>)`, adds one `CronTrigger.from_crontab(expr)` job per `onprem_schedule()` entry calling `_run_scheduled_job(name, _SCHEDULED_JOBS[name])` with `max_instances=1, coalesce=True, misfire_grace_time=3600`; also schedules `refresh_license` once 60 s after start (`'date'` trigger). Logs each registration, then `start()`.

- [ ] **Step 1: Write failing tests** — `tests/test_scheduler_timetable.py`:

```python
import re
from pathlib import Path
import app as appmod


def _workflow_crons():
    text = Path(".github/workflows/scheduled-jobs.yml").read_text()
    return {m.group(2): m.group(1) for m in re.finditer(r'-\s*cron:\s*"([^"]+)"\s*#\s*(\w+)', text)}


def test_schedule_matches_github_workflow():
    assert appmod.SCHEDULE == _workflow_crons()


def test_every_scheduled_job_exists():
    for name in list(appmod.SCHEDULE) + list(appmod.ONPREM_EXTRA_SCHEDULE):
        assert name in appmod._SCHEDULED_JOBS


def test_onprem_schedule_drops_saas_billing_and_adds_license_refresh():
    s = appmod.onprem_schedule()
    assert "check_pro_plan_expirations" not in s and s["refresh_license"] == "0 3 * * *"


def test_run_scheduler_refuses_in_saas(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["run-scheduler"])
    assert result.exit_code != 0 and "onprem" in result.output


def test_run_scheduler_registers_jobs(app, monkeypatch):
    app.config["DEPLOYMENT_MODE"] = "onprem"
    added = []
    class FakeSched:
        def __init__(self, *a, **k): pass
        def add_job(self, fn, trigger=None, **k): added.append(k.get("id") or k.get("name"))
        def start(self): pass
    monkeypatch.setattr(appmod, "BlockingScheduler", FakeSched, raising=False)
    try:
        result = app.test_cli_runner().invoke(args=["run-scheduler"])
        assert result.exit_code == 0, result.output
    finally:
        app.config["DEPLOYMENT_MODE"] = "saas"
    assert set(appmod.onprem_schedule()) <= set(added)
```

So the fake can be injected, import `BlockingScheduler` at app.py module level (`from apscheduler.schedulers.blocking import BlockingScheduler`) and pass `id=name` to each `add_job`.

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4:** focused + full suite → PASS.
- [ ] **Step 5: Commit** `feat(onprem): built-in scheduler and daily license refresh`

---

### Task 8: Public URL for on-prem (webhook + customer-facing links)

**Files:**
- Modify: `app.py` (BusinessSettings column + helper + call sites), `migrations/versions/d9e3f4a5b6c7_add_business_settings_public_url.py`
- Test: `tests/test_public_url.py`

**Interfaces:**
- `BusinessSettings.public_url` String(300) nullable; migration `d9e3f4a5b6c7`, down_revision `c8d2e3f4a5b6`, guarded. Include it in `BusinessSettings.to_dict()` and accept it in the business-settings save endpoint (`/api/business-settings` POST/PUT — find it); validate: empty → None; must start with `https://` or `http://`; strip trailing `/`; 400 otherwise.
- `_public_base_url(tenant_id=None) -> str`: if `onprem.is_onprem()` and the tenant's (or, with `tenant_id=None`, the first tenant's) `BusinessSettings.public_url` is set → it; else `Config.APP_BASE_URL`.
- Replace `Config.APP_BASE_URL` with `_public_base_url(<tenant id in scope>)` in the **customer-facing** builders/redirects only: `/pay-business?slug=` URLs and redirects, `/pay?token=` URLs and redirects (the lines around 6639-7095 on main at plan time — find with `grep -n 'APP_BASE_URL}/pay' app.py`). Leave `/verify`, `/reset-password`, `/billing` untouched.
- `GET /api/whatsapp-settings` response gains `"webhook_url": _public_base_url(tid) + "/api/whatsapp/webhook"` (saas: `Config.APP_BASE_URL` based — note the SaaS frontend currently uses `window.location.origin`; both are equivalent there).

- [ ] **Step 1: Write failing tests**: (a) saas: pay-link URL uses `Config.APP_BASE_URL` even when `public_url` is set; (b) onprem (config flag) with `public_url="https://biz.example.com"`: the public-pay-link endpoint returns a URL starting with it; (c) business-settings save rejects `ftp://x` with 400 and normalises `https://a.b/` → `https://a.b`; (d) `GET /api/whatsapp-settings` has `webhook_url` ending in `/api/whatsapp/webhook`. For (b) you need a tenant with whish_payments and a pay slug — reuse the setup from `tests/test_module_gating_whish.py::test_public_pay_page_404_when_module_off` (saas-mode tenant is fine: set `app.config["DEPLOYMENT_MODE"]="onprem"` only around the call and restore it; the onprem guards only affect specific path families and the read-only hook needs a license — to avoid it, call the GET endpoint, which the read-only hook allows).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4:** focused + full suite → PASS.
- [ ] **Step 5: Commit** `feat(onprem): public URL for webhook and customer payment links`

---

### Task 9: Frontend — on-prem setup wizard, License tab, banners, read-only, Public URL

**Files:**
- Create: `frontend/src/components/SetupWizardView.js`, `frontend/src/components/LicenseTab.js`, `frontend/src/components/LicenseBanner.js`, `frontend/src/utils/licenseStatus.js`, `frontend/src/utils/licenseStatus.test.js`, `frontend/src/components/WhatsAppPublicUrlHelp.js`
- Modify: `frontend/src/context/AppContext.js`, `frontend/src/App.js`, `frontend/src/components/SettingsView.js`

**Interfaces (consumed):** `GET /api/system/info`; `GET/POST /api/license`, `POST /api/license/refresh`; `GET /api/setup/status`, `POST /api/setup`; 403 `{license_readonly, reason}`; 409 `{setup_required}`; business settings `public_url`; `GET /api/whatsapp-settings` → `webhook_url`.

**Produces:**
- `apiService`: `systemInfo()`, `setupStatus()`, `runSetup(body)`, `getLicense()`, `activateLicense(license_key)`, `uploadLicenseFile(license_file)`, `refreshLicense()`.
- Context: `systemInfo` (null while loading), `isOnprem` (bool), `readOnly` (bool = onprem && license.state === 'readonly'), `refreshSystemInfo()`. Fetch `systemInfo` once on app load (no auth needed) and after login/setup/license changes.
- Interceptor: 403 with `license_readonly` → dispatch `sb:license-readonly` → snackbar "ServiceBills is in view-only mode — enter a license in Settings → License."; 409 with `setup_required` → `window.location.assign('/')`.
- `licenseStatus.js` pure helpers (unit-tested): `reasonText(reason)` → owner-friendly sentence for each of the 7 reasons (no_license: "Your 30-day trial has ended or no license is installed.", invalid_signature: "The installed license file is not valid.", machine_mismatch: "This license belongs to a different computer.", expired: "Your ServiceBills license has expired.", clock_rollback: "This computer's date looks wrong. Correct the date and time.", revoked: "This license has been revoked.", version_not_covered: "This version was released after your license expired. Renew your license."); `expiryWarnings(license)` → list of `{label, daysLeft}` from `warnings` (label "ServiceBills" for scope `base`, else the module's display name: WhatsApp, AI customer service, Network, Upstream sync, Whish payments).

UI:
- **App.js unauthenticated branch**: if `systemInfo?.deployment_mode === 'onprem'`: `setup_required` → `<SetupWizardView />`; else `<LoginView />` for every path (no landing page, no `/register`). Public deep-link routes (`/pay`, `/pay-business`, `/reset-password`, etc.) keep working as today.
- **SetupWizardView**: MUI stepper, 2 steps. Step 1: business name, admin username, password (+confirm, min 6 chars), owner phone. Step 2: radio "Start 30-day free trial (needs internet)" / "I have a license key" (text field `SB-XXXX-XXXX-XXXX`) / "Upload license file" (`<input type=file accept=".key,.txt">`, read as text). Submit → `runSetup`; on 201 auto-login with the entered credentials via context `login()` and `refreshSystemInfo()`. Show server `msg` errors inline; map `trial_already_used` to "A trial was already used on this computer. Enter a license key or file."
- **LicenseBanner** (rendered at the top of MainApp when `isOnprem`): red persistent banner with `reasonText` + button "Enter license" (navigates to Settings → License tab) when `readOnly`; amber dismissible banner for `expiryWarnings` ("Network expires in 3 days — renew to keep using it.").
- **Read-only UX**: when `readOnly`, MainApp passes nothing new to views; instead rely on the interceptor snackbar for blocked writes (keep scope small), plus the red banner.
- **Settings → License tab** (`LicenseTab`, key `license`, only when `isOnprem`, admin only): shows state chip, business name, base term + expiry, a table of modules (name, term, expiry, active chip), last refresh time/error, machine id (monospace, with copy button); actions: "Activate license key" (text field + button), "Upload license file", "Check for renewal now" (`refreshLicense`). After any action → `refreshSystemInfo()` and `refreshModules()`.
- **Settings → WhatsApp Notifications tab** (onprem only): "Public URL" text field bound to business settings `public_url` (saved with the existing business-settings save), and replace the current `window.location.origin + '/api/whatsapp/webhook'` display with `webhook_url` from the WhatsApp settings response when present; add a "How do I make this PC reachable from the internet?" link opening `WhatsAppPublicUrlHelp` (MUI Dialog with plain-language steps: get a domain or dynamic-DNS name, forward TCP 443 on the router to this PC, put an HTTPS reverse proxy such as Caddy in front of port 8000, paste the https URL here, then paste the webhook URL and verify token into Meta's WhatsApp configuration).
- **Hide in onprem**: nav item `billing` (add `visibleWhen`-style check using `isOnprem` — extend `filterNavItems` options with `isOnprem` and an item flag `saasOnly: true`; update `navFilter.test.js` with a case).
- Footer: show `ServiceBills v{systemInfo.app_version}` small text at the bottom of MainApp.

- [ ] **Step 1: Write failing tests**: `licenseStatus.test.js` (every reason maps to non-empty text; unknown reason → generic text; `expiryWarnings` computes daysLeft from a fixed `today` argument and labels base/module scopes); extend `navFilter.test.js` for `saasOnly`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4:** full frontend suite + `npm run build` → PASS/compiles; restore `frontend/build`.
- [ ] **Step 5: Commit** `feat(onprem): setup wizard, license tab, banners, public URL (frontend)`

---

### Task 10: Frontend — super-admin Licenses tab

**Files:**
- Create: `frontend/src/components/LicensesAdmin.js`
- Modify: `frontend/src/components/SuperAdminView.js`, `frontend/src/context/AppContext.js`

**Interfaces (consumed):** `GET/POST /api/admin/licenses`, `PATCH /api/admin/licenses/<id>`, `POST /api/admin/licenses/<id>/renew`, `GET /api/admin/licenses/<id>/file`.

**Produces:** `apiService.adminLicenses()`, `adminCreateLicense(body)`, `adminUpdateLicense(id, body)`, `adminRenewLicense(id, scope, term)`, `adminLicenseFile(id)` (axios `responseType: 'text'`).

UI:
- SuperAdminView gets top-level MUI Tabs: "Tenants" (existing content unchanged) and "On-prem licenses" (`LicensesAdmin`).
- `LicensesAdmin`: table — business, phone, license key (monospace + copy), status chip (active/trial/expired/revoked), base term + expiry, modules summary (e.g. "Network · yearly · 2027-09-30"), bound machine (short hash or "not activated"), last refresh + app version. Trials are shown with a "Lead" chip.
- "New license" dialog: business name, phone, notes, base term select (monthly/yearly/lifetime), one row per paid module (checkbox + term select). Shows computed expiry preview (client-side: today + term, for display only; server is authoritative).
- Row actions: "Renew…" dialog (scope select: ServiceBills base or a module; term select defaulting to the current term; shows old → new expiry after the server responds), "Edit" (business, phone, notes, explicit base/module expiry date fields, remove module), "Unbind machine" (confirm), "Revoke"/"Un-revoke" (confirm), "Download .key" (creates a Blob and triggers download as `servicebills-<license_key>.key`).
- Errors via the existing snackbar pattern.

- [ ] **Step 1:** Implement (no new unit-testable pure logic beyond display; if you extract a pure helper e.g. `formatModuleSummary`, add a Jest test for it).
- [ ] **Step 2:** full frontend suite + `npm run build`; restore `frontend/build`.
- [ ] **Step 3: Commit** `feat(license-server): super-admin licenses tab`

---

## Final verification (orchestrator)

- Full backend + frontend suites green; build compiles.
- `git grep -n "BEGIN PRIVATE KEY"` → no matches.
- Browser QA on scratch DBs: (1) SaaS mode as super-admin: create license, renew a module, download `.key`. (2) On-prem mode (`DEPLOYMENT_MODE=onprem`, `MACHINE_ID=m1`, `LICENSE_SERVER_URL` pointing at the SaaS scratch instance, public key matching the scratch signing key): setup wizard → trial → app usable → License tab → activate paid key → modules appear; set base expiry to yesterday on the SaaS side → refresh → red view-only banner, writes blocked, reads fine.

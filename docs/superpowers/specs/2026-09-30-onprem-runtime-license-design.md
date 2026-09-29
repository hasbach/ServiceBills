# On-Prem Runtime & License — Design (ServiceBills sub-project 2 of 3)

Date: 2026-09-30
Status: approved in brainstorming, pending spec review
Depends on: `2026-09-30-module-gating-design.md` (sub-project 1)

## Goal

Let the same ServiceBills codebase run as a single-business on-prem install on
a Windows PC (inside Docker, packaged by sub-project 3), licensed by a signed
license file issued from the SaaS, with a 30-day online-registered trial and
view-only mode when unlicensed.

## Deployment mode

New config `DEPLOYMENT_MODE` = `saas` (default) | `onprem`. In `onprem`:

- `/api/register`, the landing page, all `@superadmin_required` routes, and the
  tenant's own ServiceBills subscription billing (`/api/billing/*`, Billing
  nav item) are disabled (404 / hidden).
- `modules.enabled_for` uses the license provider (the hook left by
  sub-project 1).
- `STORAGE_BACKEND=local`, upload root on the data volume.
- `MAIL_BACKEND` defaults to `console`; optional SMTP settings still honoured.
  When mail is not configured, "Forgot password" is hidden and admins reset
  other users' passwords from User Management (add an admin "set password"
  action if one does not exist).
- `MACHINE_ID` env var (set by the installer: SHA-256 of the Windows
  `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid`, hex). Required in
  onprem; the app refuses to start without it.
- `LICENSE_SERVER_URL` env var, default `https://servicebills.onrender.com`.
- New unauthenticated `GET /api/system/info` returns `deployment_mode`,
  `app_version`, `setup_required`, and a non-sensitive license summary
  (`state`, `base_expires_at`, `trial`). Used by the frontend before login and
  by the updater script (sub-project 3).

## First-run setup wizard

When `DEPLOYMENT_MODE=onprem` and no `Tenant` row exists, every non-setup API
returns 409 `{"setup_required": true}` and the frontend shows the setup wizard.

- `GET /api/setup/status` → `{setup_required, machine_id_present}`
- `POST /api/setup` body: business name, admin username/password, owner phone,
  and one of:
  - `{"mode": "trial"}` → calls the license server's trial endpoint (below)
  - `{"mode": "activate", "license_key": "..."}` → calls activate
  - `{"mode": "file", "license_file": "<contents>"}` → offline verify only
- Creates the tenant (same seeding as `register()`), the admin user, stores the
  license, and permanently locks: once a tenant exists, `/api/setup` returns
  409. Runs in one transaction; license-server failure aborts with a readable
  error ("No internet connection — trial needs internet, or upload a license
  file").

## License format

A license file is ASCII: `SERVICEBILLS-LICENSE-1.` + base64url(payload JSON) +
`.` + base64url(Ed25519 signature over the payload bytes).

Payload:

```json
{
  "license_id": "uuid",
  "business_name": "…",
  "machine_id": "hex|null",
  "trial": false,
  "base_expires_at": "2036-09-30",
  "modules": {"network": "2027-09-30", "whatsapp": "2027-03-30"},
  "issued_at": "2026-09-30T12:00:00Z"
}
```

- Base license covers `core` + `office` until `base_expires_at` (typically
  10 years for a purchase; 30 days for a trial). Trials have `modules: {}`.
- Each paid module has its own expiry; an expired module is simply off.
- `machine_id` must equal the running `MACHINE_ID` (null only accepted before
  first activation binds it — the server always returns a bound license).
- Keys: the Ed25519 **private** key lives only on the SaaS as the Render
  secret `LICENSE_SIGNING_KEY` (PEM). The **public** key is committed as
  `license_pubkey.pem` and loaded at startup. A dev keypair is generated in
  tests; never commit a private key.

`license.py` (new): `sign(payload, private_key)`, `verify(text, public_key) ->
payload`, `evaluate(payload, now, machine_id) -> LicenseState`
(`valid | readonly`, active modules, days until base expiry, reason).

## Storage & clock guard (on-prem)

New table `installed_license` (single row): `license_text`, `last_seen_at`,
`last_refresh_at`, `last_refresh_error`. Stored in the DB so backups carry it.

Clock-rollback guard: on each request (cached, at most once per minute) and on
each scheduler tick, update `last_seen_at = max(last_seen_at, now)`. If
`now < last_seen_at - 1 day`, state = `readonly` with reason `clock_rollback`
until the clock is corrected.

## View-only (read-only) mode

State is `readonly` when: no license, base license expired, machine mismatch,
invalid signature, or clock rollback.

- A `before_request` hook rejects every non-GET/HEAD/OPTIONS request with
  403 `{"license_readonly": true, "reason": …}`, except an allow-list: login,
  logout, token refresh, `/api/setup*`, `/api/license*`, and export endpoints
  that use POST (audit and list them in the plan).
- Frontend: a persistent red banner "ServiceBills is in view-only mode —
  enter a license" linking to Settings → License; write buttons disabled via a
  `readOnly` flag from context.
- Warning banner (amber) from 7 days before `base_expires_at`, and per module
  from 7 days before its expiry.
- Data is never deleted or altered by the license state.

## On-prem license endpoints

- `GET /api/license` → state summary (business, base expiry, modules with
  expiries, trial flag, last refresh, reason if readonly).
- `POST /api/license` body `{license_key}` (online activate) or
  `{license_file}` (offline) — admin only. Replaces the stored license if it
  verifies and matches the machine.
- `POST /api/license/refresh` — admin only, triggers the refresh now.
- Settings → **License** tab shows all of this.

## SaaS license server (runs on Render)

New model `OnpremLicense`: `id` (uuid = license_id), `license_key` (short
human code, e.g. `SB-XXXX-XXXX-XXXX`, unique), `business_name`,
`owner_phone`, `machine_id` (null until activated), `trial` bool,
`base_expires_at`, `modules` JSON, `revoked` bool, `notes`, `created_at`,
`activated_at`, `last_refresh_at`, `last_app_version`.

Public endpoints (rate-limited, no JWT):

- `POST /api/licenses/trial` `{business_name, owner_phone, machine_id}` → if
  any license with this `machine_id` already exists (trial or paid): 409
  `trial_already_used`. Else create a trial (`base_expires_at = today + 30
  days`) bound to the machine and return the signed license.
- `POST /api/licenses/activate` `{license_key, machine_id}` → unknown/revoked:
  404/403; unbound: bind to machine; bound to another machine: 409
  `license_in_use`; returns signed license.
- `POST /api/licenses/refresh` `{license_id, machine_id, app_version}` →
  same checks; returns the current signed license (with renewed dates /
  modules) or 403 `revoked`. Records `last_refresh_at`, `last_app_version`.

Super-admin (`@superadmin_required`, SaaS only):

- `GET/POST /api/admin/licenses`, `PATCH /api/admin/licenses/<id>` (edit
  dates, modules, revoke, unbind machine for a hardware change),
  `GET /api/admin/licenses/<id>/file` (download signed `.key`).
- SuperAdminView gets a **Licenses** tab: list (trials show as leads with
  phone), create/renew dialog with a date per module, download button.

## Refresh behaviour

- The on-prem scheduler runs `refresh_license` daily (and on startup after
  60 s). Success replaces the stored license. `revoked` → store and go
  readonly. Network errors are recorded in `last_refresh_error` and are
  harmless — the stored license stays authoritative offline.

## Built-in scheduler

- New CLI `flask run-scheduler`: a single long-running loop (APScheduler
  `BlockingScheduler` or a simple minute loop) that runs the existing
  `_run_scheduled_job(name)` on the same timetable as
  `.github/workflows/scheduled-jobs.yml` (daily jobs 02:00-02:50 local time,
  `refresh_agent_mode_network_status` every 15 min), plus `refresh_license`
  daily and the nightly backup trigger (sub-project 3).
- Runs as its own `scheduler` container, so exactly one process schedules —
  avoiding the multi-worker duplicate-fire problems documented in
  `render.yaml`. SaaS keeps GitHub Actions; `run-scheduler` is never started
  there.
- The job timetable is defined once in Python (`SCHEDULE` next to
  `_SCHEDULED_JOBS`); a test asserts the workflow YAML crons match it.
- `check_pro_plan_expirations` is skipped in onprem (no SaaS plans).

## Version stamping

Image build args `APP_VERSION` and `APP_RELEASE_DATE` → env → shown in the UI
footer and sent on refresh. The app refuses to start (clear log + setup page
message) if `APP_RELEASE_DATE > base_expires_at` of a non-trial license —
updates are only valid while the base license is. (A trial may run any
version.)

## WhatsApp on-prem

ServiceBills does not host a tunnel or relay. Settings → WhatsApp (onprem)
gets a **Public URL** field (stored in business settings, overrides
`APP_BASE_URL` for URL generation), shows the resulting webhook URL and verify
token to paste into Meta, and links an in-app help page covering port
forwarding, a domain, and HTTPS via a reverse proxy. The owner does their own
router and Meta setup.

## Testing

- `license.py`: sign/verify round trip; tampered payload; wrong key; expired
  base; per-module expiry; machine mismatch; clock rollback; trial.
- Setup wizard: only when no tenant; locks after; each mode (license server
  mocked); failure rolls back.
- Read-only hook: writes blocked, allow-list passes, GETs pass.
- Onprem mode disables register, super-admin, billing routes.
- License server endpoints: trial once per machine, activation binding,
  in-use, revoked, refresh returns renewed dates.
- Scheduler timetable matches workflow YAML; `run-scheduler` dispatches via
  `_run_scheduled_job`.
- Release-date guard.

## Out of scope

Installer, Docker Desktop, backups, updater (sub-project 3); code obfuscation;
multi-tenant on-prem.

# Upstream Portal Read-Only Status Sync — Design

**Status (2026-08-22): design only, nothing built yet.** This is the first concrete slice of the "upstream automation" work deferred in [network-enforcement-design.md](2026-08-12-network-enforcement-design.md) — deliberately scoped to read-only browser automation against one upstream (Terra, PROradius product) before any click-through action (Renew) or any other upstream is attempted.

## Problem

`Customer.subscription_expiry_date` (ServiceBills' own billing cycle) and the customer's real expiry on their upstream RADIUS account can drift apart, because:

1. Renewal automation (when built) can only click a fixed-duration "Renew/Refill" — no upstream portal supports setting an arbitrary expiry date.
2. Staff sometimes act directly on the upstream portal, outside ServiceBills entirely — e.g. a customer's upstream account ran low on balance/lapsed, so staff manually add extra days on the portal itself to keep them online while sorting out payment. This is a normal operational workaround, not a bug.

Today ServiceBills has no visibility into the upstream's actual state at all for a bridged customer — no online/offline signal, no drift detection, nothing. Staff only find out about a mismatch when a customer complains, or not at all if the mismatch is silently in the tenant's favor (upstream is generously ahead of ServiceBills' billing date).

## Explicit non-goals for this spec

- **No Renew (or any other) click-through automation.** Read-only only.
- **No auto-reconciliation.** The two dates are never forced to match by this feature. Business reasoning (confirmed with the user): if ServiceBills silently stretched a customer's billing cycle to match every upstream compensation, the tenant would be giving away free service days every time the upstream absorbs a balance hiccup on the tenant's behalf, without that being a deliberate pricing decision. Visibility, not auto-heal.
- **No CAPTCHA'd "radiusnew" family, no upstream besides Terra.** Confined to the PROradius product, proven against Terra first; the adapter is written generically enough (parameterized by `portal_url`) to reuse for the other 4 PROradius upstreams later without a rewrite.
- **No scheduled/bulk sync across all customers.** Strictly one customer, one staff-triggered click. A "check everyone nightly" version is a natural v2, but it needs real async-worker/task-queue infrastructure to run many long-lived browser sessions safely — already flagged as an open prerequisite in the network-enforcement spec for other Concept A automation. Not worth taking on that infra risk for the very first, unproven scraper.
- **No AI agent.** This is infrastructure the eventual diagnostic/AI-agent work would consume, not that work itself.

## Architecture

### New module: `upstream_portal.py`

Same contract as the existing `mikrotik.py` adapter: every public function returns `(ok: bool, value)` and never raises. A scrape failure can only ever fail to refresh the drift fields — it can never touch billing, payments, or crash the calling request.

```python
def get_subscriber_status(provider, username):
    """Logs into `provider`'s portal, finds `username` in the subscriber
    list, reads back their status + expiry. Returns (True, {'status': ...,
    'expiry': ...}) on success, (False, reason) on any failure. Never raises."""
```

`reason` is one of: `'auth_failed'`, `'not_found'`, `'timeout'`, `'scrape_failed'` (portal layout no longer matches expected selectors) — kept distinct so the UI can tell staff *what kind* of failure happened (wrong password vs. wrong username vs. "the portal changed, this needs a code fix") instead of one generic error.

### Adapter implementation

Playwright driving headless Chromium (added to `requirements.txt` + the Dockerfile — a real image-size and runtime-memory increase, roughly 150-300MB per active scrape session, worth watching against the production hosting plan even though v1 only ever runs one session at a time, on demand).

`TerraAdapter` (named generically as the PROradius adapter internally, parameterized by `UpstreamProvider.portal_url`, so the same code serves Terra/IDM/Northern Telecom/Net360/Eaglenet later without changes):

1. Launch headless Chromium, navigate to `provider.portal_url`.
2. Log in using `provider.portal_username` / `provider.portal_password` (decrypted from `EncryptedString` in-memory only for this call, never logged).
3. Navigate to the subscriber list, find the row matching the customer's `upstream_username` (already an existing field on `Customer` — no schema change needed for the lookup key itself).
4. Read that row's status indicator (green=online / red=offline / yellow=expired) and expiry date.
5. Log out, close the browser.
6. Return the parsed result via the `(ok, value)` contract above.

### Trigger: staff-initiated, synchronous, per customer

A **"Refresh Upstream Status"** button on the customer's Subscriptions edit panel — same UX slot pattern as the existing Mikrotik "Network Status" panel. Calls a new endpoint:

```
POST /api/customers/<id>/upstream-status-sync
```

Runs the scrape synchronously within the request (with a longer timeout budget than the Mikrotik API calls get — ~20-30s, since a real headless-browser page load is inherently slower than a RouterOS API round-trip) and returns the result directly. No scheduler, no task queue, no background job in v1.

## Data model

One additive, nullable-only migration on `Customer` — no existing column changed, no existing row touched:

```python
upstream_actual_expiry   = db.Column(db.DateTime, nullable=True)   # last value read from the portal
upstream_last_status     = db.Column(db.String(20), nullable=True)  # 'online' | 'offline' | 'expired'
upstream_last_synced_at  = db.Column(db.DateTime, nullable=True)    # when the last successful sync ran
```

On a failed sync, these three fields are left untouched (never cleared/zeroed) — a failed scrape never erases the last known-good reading, it just fails to refresh it. Staff can tell a stale reading from a fresh one via `upstream_last_synced_at`.

The existing `Customer.upstream_username` field (already present, already surfaced in the Subscriptions form — see [SubscriptionsView.js:739](../../../frontend/src/components/SubscriptionsView.js)) is the lookup key used by the scrape. It's deliberately independent of the customer's ServiceBills display name, since the portal login/account name is very often different from what the tenant calls the customer internally.

## Drift detection

Computed on read, not stored as its own column — always consistent with whatever the two dates currently are, no separate state that can itself go stale:

```
if upstream_actual_expiry is None:
    no drift info yet (never synced)
elif upstream_actual_expiry > subscription_expiry_date:
    SEVERITY_INFO   — upstream has more runway than ServiceBills' billing cycle.
                       Harmless (e.g. a manual top-up staff already made).
elif upstream_actual_expiry < subscription_expiry_date:
    SEVERITY_ALERT  — upstream expires sooner than ServiceBills' billing cycle.
                       Real risk: customer shows paid/active in ServiceBills
                       but the upstream could cut them off at any time.
else:
    no drift
```

The two severities are intentionally asymmetric in urgency, not just direction — confirmed with the user this maps directly onto the business risk (an unexpected outage the tenant finds out about from an angry customer, vs. a harmless bookkeeping note).

## UI

Two independent, always-visible indicators on the Subscriptions edit panel for a bridged customer with both `upstream_provider_id` and `upstream_username` set (kept separate per the user's explicit choice — not collapsed into one combined indicator):

1. **Portal status light** — green/red/yellow dot + label, straight from `upstream_last_status`, with an "as of `<upstream_last_synced_at>`" timestamp so staff can tell a fresh reading from a stale one.
2. **Drift badge** — shown only when drift exists: a neutral badge ("Upstream has N extra day(s)") for `SEVERITY_INFO`, a red/warning badge ("⚠ Upstream expires N day(s) before ServiceBills — customer may lose service") for `SEVERITY_ALERT`.

Plus the "Refresh Upstream Status" button to trigger an on-demand sync.

## Error handling

| Failure | `reason` | UI message |
|---|---|---|
| Bad/changed portal credentials | `auth_failed` | "Couldn't log into Terra — check the provider's portal credentials." |
| `upstream_username` not found in the subscriber list | `not_found` | "No subscriber found on Terra matching this username — check for a typo or a renamed account." |
| Portal page structure no longer matches expected selectors | `scrape_failed` | "Terra's page format may have changed — sync failed. This needs a code fix, not a data fix." (logged server-side with enough detail — page title, exception, ideally a saved screenshot — for debugging) |
| Portal slow/unreachable | `timeout` | "Terra didn't respond in time — try again shortly." |

All four leave the customer's existing `upstream_actual_expiry`/`upstream_last_status`/`upstream_last_synced_at` untouched.

## Rollout & data safety

This ships to a live multi-tenant production system with real tenants depending on it daily — treated with the same rigor as [tenant-RLS](2026-08-06-tenant-rls-design.md) and the network-enforcement work itself:

- **A full production database backup is a required pre-deploy step for this migration**, not optional — taken immediately before `flask db upgrade` runs, giving a concrete rollback point if anything goes wrong.
- Migration is additive-only (three new nullable columns) — reversible via a plain down-migration, no data transformation, no risk to any existing row or table.
- Feature is completely inert for every tenant except one on `network_mode = 'upstream_bridge'` with a customer that has both an `UpstreamProvider` and `upstream_username` set. Every other tenant's app behaves identically to today, unchanged.
- Adding Playwright/Chromium to the Docker image is itself a real infra change — confirm the build succeeds and check the deployed container's memory headroom before relying on this in production, even though steady-state usage is a single on-demand scrape at a time.
- **First real run is a supervised manual test against the tenant's own real Terra account**, watched live, before this is considered working — not deployed-and-hope. Mirrors how the Mikrotik adapter was verified against a real router (including a real "Unreachable" classification and a real mocked-router suspend) before that feature shipped.

## Testing

- Unit tests for `upstream_portal.py`, mocking the Playwright page object (same approach `tests/test_mikrotik.py` uses to mock `librouteros`) — covering the drift-severity logic (`INFO` / `ALERT` / none) and every `reason` branch above without touching a real network.
- One manual smoke test against the real Terra portal, run and observed by the user, before this is considered done.

## Later phases (not designed here)

- Scheduled/bulk sync across all bridged customers, once async-worker infrastructure exists.
- The `radiusnew` CAPTCHA'd family (MyISP, Smart Networks, Wise-ISP) — same read-only shape, but needs a CAPTCHA-solving or human-in-the-loop step.
- Renew click-through automation, building on the same login/session-handling this proves out for Terra.
- The AI agent / diagnostic engine described in earlier discussion — this status-sync data (online/offline + drift) is one of the inputs it would eventually need, for both Mikrotik and upstream-bridged customers.

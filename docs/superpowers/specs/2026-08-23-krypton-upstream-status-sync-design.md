# Krypton Upstream Portal Status Sync — Design

**Status (2026-08-23): design only, nothing built yet.** This extends the existing read-only status-sync feature (see [2026-08-22-upstream-status-sync-design.md](2026-08-22-upstream-status-sync-design.md), shipped for the PROradius product covering Terra and Northern Telecom) to the second confirmed upstream product family: **Krypton**, covering MyISP, Smart Networks, and Wise-ISP.

## Background

The original spec's memory notes classified MyISP/Smart Networks/Wise-ISP as an "unbranded radiusnew family... CAPTCHA on all three logins" and treated that CAPTCHA as a hard blocker requiring either a paid solving service or a human-in-the-loop step. Live discovery against the real MyISP and Smart Networks portals on 2026-08-23 corrected both assumptions:

1. **The product has a real name: Krypton.** It is a completely different codebase from PROradius (classic server-rendered jQuery/DataTables pages, not a React SPA) — a separate adapter is needed, not a variant of `upstream_portal.py`'s existing PROradius logic.
2. **The CAPTCHA is not a blocker for normal operation.** A `captcha` input field exists in both portals' login forms, but its containing element has `display: none` by default. A correct-credentials login on both MyISP and Smart Networks went straight through to the subscriber list with zero CAPTCHA interaction. This strongly suggests the CAPTCHA is a conditional anti-bruteforce measure (e.g. shown after failed attempts), not a blanket gate on every login.

## Explicit non-goals

- **Never attempt to solve, bypass, or programmatically satisfy the CAPTCHA, under any circumstance.** If a login attempt does not reach the expected post-login state within the timeout budget — for any reason, including an unexpected CAPTCHA challenge appearing — the adapter reports `auth_failed` and stops, exactly like a wrong password. This is a hard boundary, not a v1-only simplification to revisit later: staying clearly on the automation-of-your-own-legitimate-access side of the line, not the defeating-a-bot-defense side, is the point, not a compromise.
- No click-through actions (Renew/Block/Unblock) — read-only only, same as the PROradius adapter.
- No live verification of Wise-ISP before shipping. Per the tenant (who operates all three accounts daily), Wise-ISP runs the same Krypton product as the other two. The adapter is built generically (parameterized by `provider.portal_url`, exactly like the PROradius adapter already is) so it covers Wise-ISP by construction, but this is unconfirmed-by-observation, flagged the same way the PROradius adapter's `bg-danger` mapping was flagged as inferred-not-observed until Northern Telecom confirmed it.
- No scheduled/bulk sync — still manual-click (or the existing per-row refresh icon) only, same constraint as the original spec, for the same reason (no async-worker infra).

## Architecture

### New module: `upstream_portal_krypton.py`

A sibling to `upstream_portal.py`, not a modification of it — the two products share nothing but the calling contract. Same contract as the PROradius adapter and `mikrotik.py`: `get_subscriber_status(provider, username)` returns `(True, {'status': ..., 'expiry': ...})` on success or `(False, reason)` on any failure, never raises.

`app.py`'s `sync_customer_upstream_status` endpoint currently calls `upstream_portal.get_subscriber_status(...)` unconditionally. It needs to dispatch on `provider.product`: PROradius providers (`product == 'proradius'`) call the existing module, Krypton providers (`product == 'krypton'`) call the new one. This means adding `'krypton'` as a valid value for `UpstreamProvider.product` (currently `'proradius' | 'radiusnew' | 'manual'` per a plain unconstrained `String(20)` column — no migration needed, just a new accepted value in the frontend's product dropdown and the dispatch logic).

### Login flow (confirmed live on both MyISP and Smart Networks)

1. `page.goto(provider.portal_url)` — the login page itself.
2. `page.fill('input[name="login_username"]', provider.portal_username)`
3. `page.fill('input[name="login_password"]', provider.portal_password)`
4. `page.click('button:has-text("Log In")')` — note this is `<button type="button">`, not a native form submit; the page handles the click via its own JS (confirmed: an AJAX login, not a full-page form POST), so `page.click` is required, not a keyboard Enter.
5. Wait for a signal the login succeeded. Unlike PROradius (which lands on a Dashboard first), Krypton redirects directly to the subscriber list — so the same wait can double as "login succeeded" and "subscriber list is the current page": wait for the subscriber table to appear (see below). A timeout here means `auth_failed` — deliberately not distinguished from "a CAPTCHA appeared" (see non-goals).

No separate "navigate to subscriber list" step is needed (unlike PROradius's `/users` navigation) — login already lands there.

### Subscriber list: real DataTable, server-side search, user-customizable columns

Confirmed live via `window.jQuery('#example-1').DataTable().settings()[0].oFeatures.bServerSide === true` on both portals: filtering the Username column triggers a real network round-trip, not an instant client-side re-render. **This module must use the exact same "actively wait for the filtered row" pattern already fixed in `upstream_portal.py` for the PROradius pagination bug** — filling the filter box and then using a fixed delay was tried and already caused a real production regression once; do not repeat it here. Concretely:

```python
page.fill('thead input[placeholder="Username"]', username)  # exact selector TBD during implementation -- confirm live
try:
    page.wait_for_selector(f'table tbody tr:has-text("{username}")', timeout=_TIMEOUT_MS)
except PlaywrightTimeoutError:
    raise SubscriberNotFound(username)
```

**Column positions must be resolved by header text at runtime, not hardcoded.** Krypton has a "Column visibility" toggle (confirmed live — 90 defined columns, only 35 rendered by default, and this is a per-user/per-browser DataTables preference that can change). Confirmed live: among currently-*visible* columns, `Username` is index 3, `Status` is index 4, `Expiry Date` is index 11 — but hardcoding these indices the way the PROradius adapter hardcodes `EXPIRY_COLUMN_INDEX = 9` would silently break if a tenant's staff ever customizes their own column visibility. Instead:

```python
def _column_index(page, header_text):
    visible_headers = page.locator('thead th').filter(has_not=page.locator('[style*="display: none"]'))
    # ... find the index whose text matches header_text among currently-rendered headers
```

(Exact implementation detail — matching against `offsetParent !== null` per header, as confirmed live via `Array.from(document.querySelectorAll('thead th')).filter(th => th.offsetParent !== null)` — to be finalized during implementation, not guessed here.)

### URL resolution: relative join, not root-absolute

The PROradius adapter resolves the subscriber list with `urljoin(provider.portal_url, '/users')` — a **root-absolute** path, correct there because Terra/Northern's login and list pages are both directly off the domain root. Krypton upstreams are not: MyISP's login is at `https://pi.myisp.live/login.php` (site root) but Smart Networks' is at `https://rad.smartnetworkslb.net/radiusnew/login.php` (under a `/radiusnew/` path segment) — confirmed live that both resolve their subscriber list as a sibling file in the *same directory* (`resellerUsers.php`), not a *root-absolute* path. Since Krypton doesn't need a separate navigation step at all (login lands directly on the list), this distinction mostly doesn't matter for v1 — flagged here only because if a later change ever needs to re-navigate to the list explicitly, it must use a **relative** join (`urljoin(provider.portal_url, 'resellerUsers.php')`, no leading slash) to correctly preserve the `/radiusnew/` prefix on Smart Networks/Wise-ISP, not a root-absolute one.

### Status parsing: literal text, richer vocabulary than PROradius

Unlike PROradius (a CSS class on a chip, no visible text), Krypton's Status cell is plain readable text — confirmed live as `"Online"` for one real subscriber. The column's own filter dropdown lists the full vocabulary: Online, Offline, Offline > 2 days, Active, Expired, Near Expiry, Q-Exceeded, Blocked. The tenant (daily user of both portals) confirmed the badge colors: Blocked = orange, Expired = red, Offline = light blue.

This is a genuinely richer vocabulary than PROradius's three states, and two of the extra values are real, actionable information the whole feature exists to surface — "Near Expiry" in particular is arguably the single most useful signal this feature can show. Rather than force everything into the existing `online | offline | expired | unknown` set, extend it with three new real values, additive-only (no schema change — `upstream_last_status` is an unconstrained string column; no existing frontend logic branches on an exhaustive enum):

```python
_STATUS_TEXT_MAP = (
    ('near expiry', 'near_expiry'),   # check before 'expiry' substring alone -- avoid conflating with 'expired'
    ('expired', 'expired'),
    ('q-exceeded', 'quota_exceeded'),
    ('blocked', 'blocked'),
    ('offline', 'offline'),           # covers "Offline" and "Offline > 2 days"
    ('online', 'online'),
)
```

`"Active"` is deliberately left unmapped (falls through to `unknown`) — its real meaning/color was never confirmed, unlike the other seven values, and guessing wrong here is worse than an honest `unknown`.

**Frontend color additions** (`getUpstreamStatusColor` in `SubscriptionsView.js`): add `blocked`, `near_expiry`, `quota_exceeded` with distinct colors. Deliberately **not** copying Krypton's own portal colors (blocked=orange, offline=light blue) — this app's UI already has an established internal color language from the PROradius work (`offline` = red, `expired` = orange/amber), and status color means the same thing across every upstream product in *this* app regardless of what any given portal's own theme happens to use. Suggested: `blocked` = a distinct red-adjacent shade (administrative suspension, at least as urgent as `expired`), `near_expiry` = amber/yellow (a step below `expired`'s orange), `quota_exceeded` = a distinct neutral color (informational, not urgent in the same way). Exact hex values are a cosmetic implementation detail, not a design decision requiring further sign-off.

## Data model

No migration. `UpstreamProvider.product` gains one new accepted string value, `'krypton'`, alongside the existing `'proradius' | 'radiusnew' | 'manual'` — still an unconstrained `String(20)`, so this is a frontend dropdown option + a backend dispatch branch, not a schema change. `Customer.upstream_actual_expiry` / `upstream_last_status` / `upstream_last_synced_at` are reused as-is.

## Error handling

Same four-reason contract as the PROradius adapter (`auth_failed`, `not_found`, `timeout`, `scrape_failed`), with `auth_failed` deliberately covering both a genuine wrong password and an unexpected CAPTCHA challenge (see non-goals — this is intentional, not a gap). No new reason codes.

## Rollout & data safety

Same discipline as the original spec: additive-only (no migration at all this time, even simpler), fully inert for any tenant not using a Krypton-product `UpstreamProvider`, and the first real run is a supervised manual test against a real MyISP or Smart Networks account before considering it done — matching how both the original PROradius adapter and its Northern Telecom extension were verified live before shipping.

## Testing

Unit tests mirroring `tests/test_upstream_portal.py`'s fake-Playwright-double approach, covering: the status-text mapping (all eight known values plus an unrecognized one falling to `unknown`), the header-text column resolution (including a test where visible-column order differs from the default, to prove the "don't hardcode indices" fix actually matters), the wait-for-filtered-row pattern (mirroring the two regression tests already written for the PROradius pagination/timing bugs), and the auth-failure path (covering both a wrong-password-style failure and simulating a CAPTCHA-shown failure — both must produce identical `auth_failed` output, proving the deliberate non-distinction).

## Later phases (not designed here)

- Live verification against Wise-ISP once convenient (not blocking — built generically to cover it already).
- Everything already deferred in the original spec (Renew automation, scheduled/bulk sync) applies equally here.

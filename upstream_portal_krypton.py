"""Browser-automation adapter for read-only status checks against Krypton-
product upstream RADIUS reseller portals (MyISP, Smart Networks, and --
built generically but not yet live-verified -- Wise-ISP). See
docs/superpowers/specs/2026-08-23-krypton-upstream-status-sync-design.md.

A separate module from upstream_portal.py (the PROradius adapter, covering
Terra and Northern Telecom) because Krypton is a completely different
codebase -- classic server-rendered jQuery/DataTables pages, not a React
SPA -- sharing nothing with PROradius but the calling contract: every
public function returns (ok: bool, value) and never raises, same as
mikrotik.py and upstream_portal.py.

Read-only only: never clicks Renew/Block/Unblock or any other mutating
action, only logs in, fills the Username column's own search box, and
reads text -- no clicks anywhere on the subscriber list itself.

CAPTCHA HANDLING (deliberate, not a gap): a `captcha` input exists in both
confirmed portals' login forms, but its container is hidden by default,
and a correct-credentials login on both MyISP and Smart Networks went
straight through with zero CAPTCHA interaction on 2026-08-23 live
discovery -- consistent with a conditional anti-bruteforce measure, not a
blanket gate. This module NEVER attempts to detect, solve, or bypass the
CAPTCHA under any circumstance. Login succeeds by waiting for the
subscriber list to actually appear (Krypton redirects there directly, no
separate dashboard step); if that wait times out for ANY reason -- wrong
password, an unexpected CAPTCHA challenge, or anything else -- this is
reported as a plain 'auth_failed', identically and deliberately
indistinguishable from any other login failure. Do not add code that tries
to detect or react to the CAPTCHA specifically.
"""
import logging
from datetime import datetime

from dateutil import parser as date_parser
from playwright.sync_api import sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

# Same reasoning as upstream_portal.py's _TIMEOUT_MS: keeps the realistic
# worst-case total (~5 sequential waits here: login goto, wait-for-
# subscriber-list, wait-for-filtered-row, plus default timeouts on other
# calls) comfortably under the Dockerfile's `gunicorn --timeout 120`.
_TIMEOUT_MS = 10_000

# Confirmed live on both MyISP (pi.myisp.live) and Smart Networks
# (rad.smartnetworkslb.net) on 2026-08-23.
LOGIN_USERNAME_SELECTOR = 'input[name="login_username"]'
LOGIN_PASSWORD_SELECTOR = 'input[name="login_password"]'
# Confirmed live: a <button type="button"> (not a native form submit) with
# this exact text -- the page handles the click via its own JS (an AJAX
# login), so a real click is required, not a keyboard Enter.
LOGIN_SUBMIT_SELECTOR = 'button:has-text("Log In")'
# Confirmed identical on both portals via
# `window.jQuery('#example-1').DataTable()`.
SUBSCRIBER_TABLE_SELECTOR = '#example-1'

# Krypton has a user-customizable "Column visibility" toggle (confirmed
# live: 90 columns defined, only 35 rendered by default) -- a hardcoded
# column index (the approach that works fine for PROradius, which has no
# such toggle) would silently break for a tenant who's customized their own
# view. These are the only three columns this adapter needs; their
# positions are resolved by header TEXT at runtime, every call, via
# _resolve_column_indices() -- never assume a fixed index.
_REQUIRED_COLUMNS = ("Username", "Status", "Expiry Date")

# Status is plain readable text (confirmed live, e.g. "Online") -- unlike
# PROradius, which conveys status via a CSS class with no visible text.
# The portal's own Status column filter dropdown lists the full
# vocabulary: Online, Offline, Offline > 2 days, Active, Expired, Near
# Expiry, Q-Exceeded, Blocked. "Active" is deliberately NOT mapped here --
# its real meaning/color was never confirmed live, unlike the other seven
# -- it falls through to 'unknown', same safe fallback as any unrecognized
# text. Substring matching (not exact-equality) so "Offline > 2 days"
# still matches the 'offline' entry.
#
# 'disabled' -> 'blocked' confirmed live on MyISP, 2026-08-23: filtering
# the Status column to the "Blocked" dropdown option returns rows whose
# own displayed Status text reads "Disabled", never literally "Blocked" --
# the filter's category name and the rendered text are not the same
# string on this portal.
#
# 'near expiry' is kept here even though live discovery found it never
# actually appears in the Status text on MyISP (see
# _apply_near_expiry_override below, which is what actually produces
# 'near_expiry' for the common case) -- harmless to leave in case some
# other Krypton-family portal (Smart Networks, Wise-ISP -- neither
# live-verified yet) renders it as literal text after all.
_STATUS_TEXT_MAP = (
    ('near expiry', 'near_expiry'),
    ('expired', 'expired'),
    ('q-exceeded', 'quota_exceeded'),
    ('blocked', 'blocked'),
    ('disabled', 'blocked'),
    ('offline', 'offline'),
    ('online', 'online'),
)

# Confirmed live on MyISP, 2026-08-23: the Status column's "Near Expiry"
# filter category is a server-side query condition, not something
# reflected in the Status cell's own text -- filtering to "Near Expiry"
# returned real subscribers whose Status cell read plain "Online" (their
# actual connection state; the underlying `available` field is computed
# per-row for display, independent of the filter's semantic categories).
# Text-matching alone can therefore never produce 'near_expiry' for the
# common case of an online-but-expiring-soon subscriber, so it's computed
# here instead, from the same Expiry Date already parsed for every
# subscriber. 5-day window matches MyISP's own dashboard widget, which
# labels this exact bucket "Near Expiry ... WITHIN 5 DAYS".
_NEAR_EXPIRY_WINDOW_DAYS = 5


class LoginFailed(Exception):
    pass


class SubscriberNotFound(Exception):
    pass


class AmbiguousSubscriberMatch(Exception):
    pass


class ScrapeStructureChanged(Exception):
    pass


def _login(page, provider):
    page.goto(provider.portal_url, timeout=_TIMEOUT_MS)
    page.fill(LOGIN_USERNAME_SELECTOR, provider.portal_username)
    page.fill(LOGIN_PASSWORD_SELECTOR, provider.portal_password)
    page.click(LOGIN_SUBMIT_SELECTOR)
    # Krypton redirects straight to the subscriber list on success (no
    # separate dashboard step, unlike PROradius) -- waiting for the table
    # to actually have a row IS the login-success signal. This is robust
    # to not knowing exactly what a FAILED login looks like (never tested
    # live, deliberately -- see module docstring): whatever a failed
    # attempt or an unexpected CAPTCHA challenge looks like, this table
    # never appears, so the wait times out and this is reported the same
    # way regardless of the real cause.
    try:
        page.wait_for_selector(f"{SUBSCRIBER_TABLE_SELECTOR} tbody tr", timeout=_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        raise LoginFailed("Login did not reach the subscriber list")


def _resolve_column_indices(page):
    """Returns {'Username': int, 'Status': int, 'Expiry Date': int} --
    each value is the 0-indexed position of that header among currently-
    VISIBLE header cells (offsetParent !== null), matched by exact header
    text. One atomic JS evaluation, not a separate Python-side
    visibility-check-then-text-read per column -- avoids both a race
    between those two reads and needing 3x the round trips."""
    indices = page.evaluate(
        """(headerNames) => {
            const visible = Array.from(document.querySelectorAll('thead th')).filter(th => th.offsetParent !== null);
            const labelOf = (th) => (th.childNodes[0] ? th.childNodes[0].textContent : th.textContent).trim();
            return headerNames.map(name => visible.findIndex(th => labelOf(th) === name));
        }""",
        list(_REQUIRED_COLUMNS),
    )
    result = dict(zip(_REQUIRED_COLUMNS, indices))
    missing = [name for name, idx in result.items() if idx < 0]
    if missing:
        raise ScrapeStructureChanged(f"required column(s) not found: {missing}")
    return result


def _find_subscriber_row(page, username, username_col_index):
    filter_input = page.locator('thead th:visible').nth(username_col_index).locator('input')
    filter_input.fill(username)
    # Confirmed live: filtering re-queries the portal's own backend (real
    # server-side search, not instant client-side) on both MyISP and Smart
    # Networks. Actively waiting for a matching row -- succeeding as soon
    # as it appears, up to the full timeout -- instead of a fixed delay is
    # a hard requirement here: a fixed delay caused a real production
    # regression on the PROradius adapter for exactly this class of
    # server-side-search UI (see upstream_portal.py's _find_subscriber_row
    # history) and must not be repeated.
    try:
        page.wait_for_selector(f'{SUBSCRIBER_TABLE_SELECTOR} tbody tr:has-text("{username}")', timeout=_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        raise SubscriberNotFound(username)
    # `has_text` is a coarse pre-filter across the whole row's visible
    # text. The exact-match check below against just the Username column
    # is what actually determines a real match -- prevents ever silently
    # persisting a different subscriber's status/expiry as this
    # customer's.
    candidates = page.locator(f"{SUBSCRIBER_TABLE_SELECTOR} tbody tr", has_text=username)
    matches = []
    for i in range(candidates.count()):
        row = candidates.nth(i)
        # td:visible, not plain td -- Krypton rows have the same 90-vs-35
        # visible/defined column split as the header row; indexing into
        # unfiltered <td>s would silently read the wrong column entirely.
        username_cell_text = row.locator("td:visible").nth(username_col_index).inner_text().strip()
        if username_cell_text == username:
            matches.append(row)
    if len(matches) == 0:
        raise SubscriberNotFound(username)
    if len(matches) > 1:
        raise AmbiguousSubscriberMatch(username, len(matches))
    return matches[0]


def _parse_status(row, status_col_index):
    text = row.locator("td:visible").nth(status_col_index).inner_text().strip().lower()
    for needle, status in _STATUS_TEXT_MAP:
        if needle in text:
            return status
    return "unknown"


def _apply_near_expiry_override(status, expiry):
    """Overrides 'online' or 'unknown' with 'near_expiry' when `expiry` is
    today or within the next _NEAR_EXPIRY_WINDOW_DAYS days -- see the
    _NEAR_EXPIRY_WINDOW_DAYS comment above for why this can't be done from
    Status text alone. Deliberately does NOT override 'offline', 'expired',
    'blocked', or 'quota_exceeded' -- those are already more specific,
    already-confirmed-live signals and take priority over a computed one.
    """
    if status not in ("online", "unknown") or expiry is None:
        return status
    days_until_expiry = (expiry.date() - datetime.utcnow().date()).days
    if 0 <= days_until_expiry <= _NEAR_EXPIRY_WINDOW_DAYS:
        return "near_expiry"
    return status


def _parse_expiry(row, expiry_col_index):
    cells = row.locator("td:visible")
    if cells.count() <= expiry_col_index:
        return None
    text = cells.nth(expiry_col_index).inner_text().strip()
    if not text:
        return None
    try:
        return date_parser.parse(text)
    except (ValueError, OverflowError):
        return None


def get_subscriber_status(provider, username):
    """Logs into `provider`'s Krypton portal, finds `username` in the
    subscriber list, reads back their status + expiry.

    Returns (True, {'status': 'online'|'offline'|'expired'|'near_expiry'|
    'blocked'|'quota_exceeded'|'unknown', 'expiry': datetime|None}) on
    success. Returns (False, reason) on any failure, where reason is one
    of 'auth_failed', 'not_found', 'timeout', 'scrape_failed'. Never
    raises.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_default_timeout(_TIMEOUT_MS)
                _login(page, provider)
                columns = _resolve_column_indices(page)
                row = _find_subscriber_row(page, username, columns["Username"])
                status = _parse_status(row, columns["Status"])
                expiry = _parse_expiry(row, columns["Expiry Date"])
                status = _apply_near_expiry_override(status, expiry)
            finally:
                browser.close()
    except LoginFailed as e:
        logger.warning("Krypton portal login failed for provider %s: %s", provider.id, e)
        return False, "auth_failed"
    except SubscriberNotFound:
        return False, "not_found"
    except AmbiguousSubscriberMatch as e:
        matched_username, match_count = e.args
        logger.warning(
            "Krypton portal lookup for username %r on provider %s matched %d rows exactly -- "
            "refusing to guess which is correct", matched_username, provider.id, match_count
        )
        return False, "scrape_failed"
    except ScrapeStructureChanged as e:
        logger.warning("Krypton portal structure changed for provider %s: %s", provider.id, e)
        return False, "scrape_failed"
    except PlaywrightTimeoutError as e:
        logger.warning("Krypton portal timed out for provider %s: %s", provider.id, e)
        return False, "timeout"
    except PlaywrightError as e:
        logger.warning("Krypton portal scrape failed for provider %s: %s", provider.id, e)
        return False, "scrape_failed"

    return True, {"status": status, "expiry": expiry}

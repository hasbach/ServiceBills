"""Browser-automation adapter for read-only status checks against upstream
RADIUS reseller portals (Concept A / 'upstream_bridge'). See
docs/superpowers/specs/2026-08-22-upstream-status-sync-design.md.

Read-only only: this module never clicks Renew/Block/Unblock or any other
mutating action, only logs in, reads one subscriber's row, and logs out.
Every public function returns (ok: bool, value) and never raises -- a scrape
failure must never block or crash a billing-side request, same contract as
mikrotik.py.

Confirmed against the real Terra portal (PROradius product,
acppro.terra.net.lb) via live discovery on 2026-08-23. Terra has announced a
migration to a new "ProRadiusV5" portal at a different address -- per the
tenant, do NOT target that new address; this module stays pointed at the
current site until told otherwise.
"""
import logging
from urllib.parse import urljoin

from dateutil import parser as date_parser
from playwright.sync_api import sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

_TIMEOUT_MS = 25_000

# Confirmed against the real Terra login page.
LOGIN_USERNAME_SELECTOR = 'input[name="username"]'
LOGIN_PASSWORD_SELECTOR = 'input[name="password"]'
LOGIN_SUBMIT_SELECTOR = 'button[type="submit"]'
# "Log Out" only exists inside a closed dropdown menu, not visible right
# after login -- "Balance" is in the page header on every authenticated
# page instead, confirmed present immediately after a successful login.
LOGIN_SUCCESS_SELECTOR = 'text=Balance'
# Login lands on the Dashboard, NOT the subscriber list -- confirmed live
# (missed in the original design, caught by the supervised smoke test): the
# subscriber table only exists on this separate page, reached via the
# sidebar's Users -> List Users link. Resolved against provider.portal_url's
# origin, since that URL is the login page, not the site root.
SUBSCRIBER_LIST_PATH = '/users'
SUBSCRIBER_TABLE_SELECTOR = 'table'

# The subscriber table's real column order, confirmed live: Username,
# Fullname, Address, Phone, Reseller, Service, MAC, IP, Last Activity,
# Expiry -- 0-indexed, so Expiry is column 9.
EXPIRY_COLUMN_INDEX = 9

# Status is conveyed by a CSS utility class on a colored "chip" within the
# username cell, NOT by any visible text -- confirmed live: bg-success
# (green) for an active/current subscriber, bg-warning (yellow) for one
# whose Expiry date has already passed. bg-danger (red, for an active
# subscriber who is simply offline right now) was not directly observed --
# no test account happened to be in that state during discovery -- but is
# inferred with high confidence from this same UI component's standard
# 3-color convention (success/warning/danger) and matches the reseller's
# own description of the portal (green/red/yellow). Reconfirm against a
# real offline account if one becomes available.
_STATUS_CLASS_MAP = (
    ('bg-danger', 'offline'),
    ('bg-warning', 'expired'),
    ('bg-success', 'online'),
)


class LoginFailed(Exception):
    pass


class SubscriberNotFound(Exception):
    pass


def _login(page, provider):
    page.goto(provider.portal_url, timeout=_TIMEOUT_MS)
    page.fill(LOGIN_USERNAME_SELECTOR, provider.portal_username)
    page.fill(LOGIN_PASSWORD_SELECTOR, provider.portal_password)
    page.click(LOGIN_SUBMIT_SELECTOR)
    try:
        page.wait_for_selector(LOGIN_SUCCESS_SELECTOR, timeout=_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        raise LoginFailed("Login did not reach the logged-in page")


def _goto_subscriber_list(page, provider):
    page.goto(urljoin(provider.portal_url, SUBSCRIBER_LIST_PATH), timeout=_TIMEOUT_MS)


def _find_subscriber_row(page, username):
    # The subscriber list is a React SPA page -- its rows render after an
    # async fetch completes, not immediately on navigation. `.count()` never
    # auto-waits, so without this the row lookup below can run before any
    # row exists at all (caught live: this was originally missing, and
    # every lookup failed as a false "not_found"). A genuine timeout here
    # (table never loads) propagates as PlaywrightTimeoutError -> 'timeout',
    # not 'not_found' -- intentionally not caught in this function.
    page.wait_for_selector(f"{SUBSCRIBER_TABLE_SELECTOR} tr", timeout=_TIMEOUT_MS)
    row = page.locator(f"{SUBSCRIBER_TABLE_SELECTOR} tr", has_text=username)
    if row.count() == 0:
        raise SubscriberNotFound(username)
    return row.first


def _parse_status(row):
    chip = row.locator('[class*="bg-success"], [class*="bg-warning"], [class*="bg-danger"]').first
    if chip.count() == 0:
        return "unknown"
    class_attr = chip.get_attribute("class") or ""
    for needle, status in _STATUS_CLASS_MAP:
        if needle in class_attr:
            return status
    return "unknown"


def _parse_expiry(row):
    cells = row.locator("td")
    if cells.count() <= EXPIRY_COLUMN_INDEX:
        return None
    text = cells.nth(EXPIRY_COLUMN_INDEX).inner_text().strip()
    if not text:
        return None
    try:
        return date_parser.parse(text)
    except (ValueError, OverflowError):
        return None


def get_subscriber_status(provider, username):
    """Logs into `provider`'s portal, finds `username` in the subscriber
    list, reads back their status + expiry.

    Returns (True, {'status': 'online'|'offline'|'expired'|'unknown',
    'expiry': datetime|None}) on success. Returns (False, reason) on any
    failure, where reason is one of 'auth_failed', 'not_found', 'timeout',
    'scrape_failed'. Never raises.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_default_timeout(_TIMEOUT_MS)
                _login(page, provider)
                _goto_subscriber_list(page, provider)
                row = _find_subscriber_row(page, username)
                status = _parse_status(row)
                expiry = _parse_expiry(row)
            finally:
                browser.close()
    except LoginFailed as e:
        logger.warning("Upstream portal login failed for provider %s: %s", provider.id, e)
        return False, "auth_failed"
    except SubscriberNotFound:
        return False, "not_found"
    except PlaywrightTimeoutError as e:
        logger.warning("Upstream portal timed out for provider %s: %s", provider.id, e)
        return False, "timeout"
    except PlaywrightError as e:
        logger.warning("Upstream portal scrape failed for provider %s: %s", provider.id, e)
        return False, "scrape_failed"

    return True, {"status": status, "expiry": expiry}

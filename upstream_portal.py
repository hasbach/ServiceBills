"""Browser-automation adapter for read-only status checks against upstream
RADIUS reseller portals (Concept A / 'upstream_bridge'). See
docs/superpowers/specs/2026-08-22-upstream-status-sync-design.md.

Read-only only: this module never clicks Renew/Block/Unblock or any other
mutating action, only logs in, reads one subscriber's row, and closes the
browser.
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

# Applied to each of ~6 sequential Playwright waits (login goto, wait-for-
# login-success, goto /users, wait-for-rows, plus default timeouts on other
# calls) -- worst case must stay comfortably under the Dockerfile's
# `gunicorn --timeout 120`, since a worker killed mid-scrape returns nothing
# clean to the caller instead of a proper 'timeout' reason. The spec's
# ~20-30s figure was for the whole call, not each individual wait, so 25s
# per-wait was far too generous; 10s per wait keeps the realistic worst-case
# total (~6 waits) under the 120s worker timeout with a wide margin.
_TIMEOUT_MS = 10_000

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
# The subscriber list paginates once a reseller has enough customers --
# confirmed live on Northern Telecom (50 customers, 10 per page, 5 pages);
# Terra's <10 customers happened to fit on one page, which is exactly why
# this was missed during Terra-only discovery ("some users sync, others
# don't" was reported after Northern was linked). The Username column's own
# search box narrows the FULL dataset server-side before any row lookup, so
# pagination can never hide a match -- confirmed identical markup
# (data-key="username") on both Terra and Northern, same underlying
# component library.
USERNAME_FILTER_SELECTOR = 'th[data-key="username"] input'
# How long to let the table's debounced search settle after filling the
# filter box, before trusting what rows are currently rendered.
_FILTER_DEBOUNCE_MS = 600

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


class AmbiguousSubscriberMatch(Exception):
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
    page.wait_for_selector(f"{SUBSCRIBER_TABLE_SELECTOR} tbody tr", timeout=_TIMEOUT_MS)
    page.fill(USERNAME_FILTER_SELECTOR, username)
    page.wait_for_timeout(_FILTER_DEBOUNCE_MS)
    # `has_text` is a coarse pre-filter across the WHOLE row (all 10 columns
    # -- Fullname, Address, Phone, Reseller, Service, MAC, IP could all
    # coincidentally contain the username as a substring). The exact-match
    # check below against just the username cell (column 0) is what
    # actually determines a real match -- this prevents ever silently
    # persisting a different subscriber's status/expiry as this customer's.
    candidates = page.locator(f"{SUBSCRIBER_TABLE_SELECTOR} tbody tr", has_text=username)
    matches = []
    for i in range(candidates.count()):
        row = candidates.nth(i)
        username_cell_text = row.locator("td").nth(0).inner_text().strip()
        if username_cell_text == username:
            matches.append(row)
    if len(matches) == 0:
        raise SubscriberNotFound(username)
    if len(matches) > 1:
        raise AmbiguousSubscriberMatch(username, len(matches))
    return matches[0]


def _parse_status(row):
    username_cell = row.locator("td").nth(0)
    chip = username_cell.locator('[class*="bg-success"], [class*="bg-warning"], [class*="bg-danger"]').first
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
    except AmbiguousSubscriberMatch as e:
        matched_username, match_count = e.args
        logger.warning(
            "Upstream portal lookup for username %r on provider %s matched %d rows exactly -- "
            "refusing to guess which is correct", matched_username, provider.id, match_count
        )
        return False, "scrape_failed"
    except PlaywrightTimeoutError as e:
        logger.warning("Upstream portal timed out for provider %s: %s", provider.id, e)
        return False, "timeout"
    except PlaywrightError as e:
        logger.warning("Upstream portal scrape failed for provider %s: %s", provider.id, e)
        return False, "scrape_failed"

    return True, {"status": status, "expiry": expiry}

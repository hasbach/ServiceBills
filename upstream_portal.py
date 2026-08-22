"""Browser-automation adapter for read-only status checks against upstream
RADIUS reseller portals (Concept A / 'upstream_bridge'). See
docs/superpowers/specs/2026-08-22-upstream-status-sync-design.md.

Read-only only: this module never clicks Renew/Block/Unblock or any other
mutating action, only logs in, reads one subscriber's row, and logs out.
Every public function returns (ok: bool, value) and never raises -- a scrape
failure must never block or crash a billing-side request, same contract as
mikrotik.py.

Written and proven against Terra (PROradius product) first; parameterized by
provider.portal_url so the same code should serve the other PROradius
upstreams (IDM, Northern Telecom, Net360, Eaglenet) later without changes,
once the selector constants below are confirmed against each. The CAPTCHA'd
'radiusnew' family is explicitly out of scope for this module.
"""
import logging
import re

from dateutil import parser as date_parser
from playwright.sync_api import sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

_TIMEOUT_MS = 25_000

# Best-guess selectors for Terra's PROradius portal, NOT yet confirmed
# against the live site -- see the "Live discovery against Terra" task in
# docs/superpowers/plans/2026-08-22-upstream-status-sync.md. Update these
# constants (and _parse_status's text matching, if needed) once that task
# runs -- nothing else in this module should need to change just because one
# of these guesses was wrong.
LOGIN_USERNAME_SELECTOR = 'input[name="username"]'
LOGIN_PASSWORD_SELECTOR = 'input[name="password"]'
LOGIN_SUBMIT_SELECTOR = 'button[type="submit"]'
LOGIN_SUCCESS_SELECTOR = 'text=Logout'  # presence after submit == login succeeded
SUBSCRIBER_TABLE_SELECTOR = 'table'

_STATUS_TEXT_MAP = (
    ('expired', 'expired'),
    ('offline', 'offline'),
    ('online', 'online'),
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


def _find_subscriber_row(page, username):
    row = page.locator(f"{SUBSCRIBER_TABLE_SELECTOR} tr", has_text=username)
    if row.count() == 0:
        raise SubscriberNotFound(username)
    return row.first


def _parse_status(row_text):
    lowered = row_text.lower()
    for needle, status in _STATUS_TEXT_MAP:
        if needle in lowered:
            return status
    return "unknown"


_ISO_DATE_PATTERN = re.compile(r'\d{4}-\d{2}-\d{2}')


def _parse_expiry(row_text):
    # Row text usually also contains the subscriber's username (e.g.
    # "user1  Online  Expires 2026-09-01"), which can include digits that
    # confuse dateutil's fuzzy tokenizer (it may raise ParserError trying to
    # reconcile unrelated numeric fragments into one date). Extract an
    # unambiguous ISO date substring first when present, and only fall back
    # to a full fuzzy parse of the row text otherwise.
    match = _ISO_DATE_PATTERN.search(row_text)
    candidate = match.group(0) if match else row_text
    try:
        return date_parser.parse(candidate, fuzzy=True)
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
                row = _find_subscriber_row(page, username)
                row_text = row.inner_text()
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

    return True, {"status": _parse_status(row_text), "expiry": _parse_expiry(row_text)}

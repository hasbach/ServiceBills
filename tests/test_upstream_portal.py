"""Tests for upstream_portal.py -- no real browser or real portal involved.
`sync_playwright` is monkeypatched to a small fake Playwright/Browser/Page
double, mirroring the approach tests/test_mikrotik.py uses for librouteros:
assert on orchestration and error classification, not on Terra's real DOM
(which these fakes don't attempt to model)."""
import types

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import upstream_portal


def make_provider(id=1, portal_url="https://example.test/login", portal_username="u", portal_password="p"):
    return types.SimpleNamespace(id=id, portal_url=portal_url, portal_username=portal_username, portal_password=portal_password)


class FakeLocator:
    def __init__(self, text, found):
        self._text = text
        self._found = found

    def count(self):
        return 1 if self._found else 0

    @property
    def first(self):
        return self

    def inner_text(self):
        return self._text


class FakePage:
    def __init__(self, row_text="", row_found=True, login_succeeds=True, goto_raises=None):
        self._row_text = row_text
        self._row_found = row_found
        self._login_succeeds = login_succeeds
        self._goto_raises = goto_raises
        self.filled = {}
        self.clicked = []

    def set_default_timeout(self, ms):
        pass

    def goto(self, url, timeout=None):
        if self._goto_raises:
            raise self._goto_raises

    def fill(self, selector, value):
        self.filled[selector] = value

    def click(self, selector):
        self.clicked.append(selector)

    def wait_for_selector(self, selector, timeout=None):
        if not self._login_succeeds:
            raise PlaywrightTimeoutError("timed out waiting for login")

    def locator(self, selector, has_text=None):
        return FakeLocator(self._row_text, found=self._row_found)


class FakeBrowser:
    def __init__(self, page):
        self._page = page
        self.closed = False

    def new_page(self):
        return self._page

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, page):
        self._page = page

    def launch(self, headless=True):
        return FakeBrowser(self._page)


class FakePlaywright:
    def __init__(self, page):
        self.chromium = FakeChromium(page)


class FakeSyncPlaywrightCM:
    def __init__(self, page):
        self._page = page

    def __enter__(self):
        return FakePlaywright(self._page)

    def __exit__(self, *a):
        return False


def patch_playwright(monkeypatch, page):
    monkeypatch.setattr(upstream_portal, "sync_playwright", lambda: FakeSyncPlaywrightCM(page))


# --- Success cases ---

def test_get_subscriber_status_online(monkeypatch):
    page = FakePage(row_text="user1  Online  Expires 2026-09-01", row_found=True)
    patch_playwright(monkeypatch, page)

    ok, value = upstream_portal.get_subscriber_status(make_provider(), "user1")

    assert ok is True
    assert value["status"] == "online"
    assert value["expiry"].year == 2026 and value["expiry"].month == 9 and value["expiry"].day == 1


def test_get_subscriber_status_expired(monkeypatch):
    page = FakePage(row_text="user1  Expired  Expires 2026-01-15", row_found=True)
    patch_playwright(monkeypatch, page)

    ok, value = upstream_portal.get_subscriber_status(make_provider(), "user1")

    assert (ok, value["status"]) == (True, "expired")


def test_login_fills_credentials_and_submits(monkeypatch):
    page = FakePage(row_text="user1 Online", row_found=True)
    patch_playwright(monkeypatch, page)

    upstream_portal.get_subscriber_status(make_provider(portal_username="alice", portal_password="s3cret"), "user1")

    assert page.filled[upstream_portal.LOGIN_USERNAME_SELECTOR] == "alice"
    assert page.filled[upstream_portal.LOGIN_PASSWORD_SELECTOR] == "s3cret"
    assert page.clicked == [upstream_portal.LOGIN_SUBMIT_SELECTOR]


# --- Failure cases ---

def test_get_subscriber_status_not_found(monkeypatch):
    page = FakePage(row_text="", row_found=False)
    patch_playwright(monkeypatch, page)

    ok, reason = upstream_portal.get_subscriber_status(make_provider(), "ghost")

    assert (ok, reason) == (False, "not_found")


def test_get_subscriber_status_auth_failed(monkeypatch):
    page = FakePage(login_succeeds=False)
    patch_playwright(monkeypatch, page)

    ok, reason = upstream_portal.get_subscriber_status(make_provider(), "user1")

    assert (ok, reason) == (False, "auth_failed")


def test_get_subscriber_status_timeout(monkeypatch):
    page = FakePage(goto_raises=PlaywrightTimeoutError("portal did not respond"))
    patch_playwright(monkeypatch, page)

    ok, reason = upstream_portal.get_subscriber_status(make_provider(), "user1")

    assert (ok, reason) == (False, "timeout")


def test_get_subscriber_status_scrape_failed_on_generic_error(monkeypatch):
    page = FakePage(goto_raises=PlaywrightError("navigation crashed"))
    patch_playwright(monkeypatch, page)

    ok, reason = upstream_portal.get_subscriber_status(make_provider(), "user1")

    assert (ok, reason) == (False, "scrape_failed")


def test_browser_always_closed_even_on_failure(monkeypatch):
    page = FakePage(login_succeeds=False)
    patch_playwright(monkeypatch, page)
    browser_holder = {}
    real_launch = FakeChromium.launch

    def capturing_launch(self, headless=True):
        b = real_launch(self, headless)
        browser_holder["browser"] = b
        return b

    monkeypatch.setattr(FakeChromium, "launch", capturing_launch)

    upstream_portal.get_subscriber_status(make_provider(), "user1")

    assert browser_holder["browser"].closed is True

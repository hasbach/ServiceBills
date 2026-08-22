"""Tests for upstream_portal.py -- no real browser or real portal involved.
`sync_playwright` is monkeypatched to a small fake Playwright/Browser/Page
double. These fakes model the real Terra page structure confirmed via live
discovery (2026-08-23): status is a CSS class on a chip in the username
cell, and expiry is a specific table column (index 9), not free row text."""
import types

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import upstream_portal


def make_provider(id=1, portal_url="https://example.test/login", portal_username="u", portal_password="p"):
    return types.SimpleNamespace(id=id, portal_url=portal_url, portal_username=portal_username, portal_password=portal_password)


def make_cells(expiry="2026-09-01 17:38"):
    # 10 columns, matching Terra's real order; only the Expiry column
    # (index 9) is ever read by the code under test, the rest are unused
    # placeholders.
    return ["user1", "Full Name", "Address", "Phone", "Reseller", "Service", "MAC", "IP", "2026-01-01 00:00", expiry]


class FakeCell:
    def __init__(self, text):
        self._text = text

    def inner_text(self):
        return self._text


class FakeCellsLocator:
    def __init__(self, cell_texts):
        self._cell_texts = cell_texts

    def count(self):
        return len(self._cell_texts)

    def nth(self, i):
        return FakeCell(self._cell_texts[i] if i < len(self._cell_texts) else "")


class FakeChipLocator:
    def __init__(self, chip_class):
        self._chip_class = chip_class

    def count(self):
        return 1 if self._chip_class else 0

    @property
    def first(self):
        return self

    def get_attribute(self, name):
        return self._chip_class if name == "class" else None


class FakeRowLocator:
    def __init__(self, found=True, chip_class="bg-success/20 text-success-700", cell_texts=None):
        self._found = found
        self._chip_class = chip_class
        self._cell_texts = cell_texts if cell_texts is not None else make_cells()

    def count(self):
        return 1 if self._found else 0

    @property
    def first(self):
        return self

    def locator(self, selector, has_text=None):
        if selector == "td":
            return FakeCellsLocator(self._cell_texts)
        return FakeChipLocator(self._chip_class)

    def inner_text(self):
        return " ".join(self._cell_texts)


class FakePage:
    def __init__(self, row_found=True, chip_class="bg-success/20 text-success-700", cell_texts=None,
                 login_succeeds=True, goto_raises=None):
        self._row_found = row_found
        self._chip_class = chip_class
        self._cell_texts = cell_texts
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
        return FakeRowLocator(found=self._row_found, chip_class=self._chip_class, cell_texts=self._cell_texts)


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
    page = FakePage(chip_class="bg-success/20 text-success-700", cell_texts=make_cells("2026-09-01 17:38"))
    patch_playwright(monkeypatch, page)

    ok, value = upstream_portal.get_subscriber_status(make_provider(), "user1")

    assert ok is True
    assert value["status"] == "online"
    assert value["expiry"].year == 2026 and value["expiry"].month == 9 and value["expiry"].day == 1


def test_get_subscriber_status_expired(monkeypatch):
    page = FakePage(chip_class="bg-warning/20 text-warning-700", cell_texts=make_cells("2026-01-15 00:00"))
    patch_playwright(monkeypatch, page)

    ok, value = upstream_portal.get_subscriber_status(make_provider(), "user1")

    assert (ok, value["status"]) == (True, "expired")


def test_get_subscriber_status_offline(monkeypatch):
    page = FakePage(chip_class="bg-danger/20 text-danger-700", cell_texts=make_cells("2026-09-01 17:38"))
    patch_playwright(monkeypatch, page)

    ok, value = upstream_portal.get_subscriber_status(make_provider(), "user1")

    assert (ok, value["status"]) == (True, "offline")


def test_login_fills_credentials_and_submits(monkeypatch):
    page = FakePage()
    patch_playwright(monkeypatch, page)

    upstream_portal.get_subscriber_status(make_provider(portal_username="alice", portal_password="s3cret"), "user1")

    assert page.filled[upstream_portal.LOGIN_USERNAME_SELECTOR] == "alice"
    assert page.filled[upstream_portal.LOGIN_PASSWORD_SELECTOR] == "s3cret"
    assert page.clicked == [upstream_portal.LOGIN_SUBMIT_SELECTOR]


# --- Failure cases ---

def test_get_subscriber_status_not_found(monkeypatch):
    page = FakePage(row_found=False)
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

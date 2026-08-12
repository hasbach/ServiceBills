"""Tests for mikrotik.py -- no real RouterOS device involved. `_connect` is
monkeypatched to a small fake Api double so we can assert on (a) the exact
query words sent over the wire (the collision-safety property: service must
be included once server.service_name is set) and (b) that every public
function degrades to a (False, message) result instead of raising when the
router is unreachable.
"""
import types

import pytest
from librouteros.exceptions import TrapError

import mikrotik


def make_server(service_name=None, use_tls=False, id=1):
    return types.SimpleNamespace(
        id=id, host="10.0.0.1", username="admin", password="pw",
        api_port=8728, use_tls=use_tls, service_name=service_name,
        last_checked_at=None, last_status=None,
    )


class FakeQuery:
    def __init__(self, rows, captured):
        self._rows = rows
        self._captured = captured

    def where(self, *args):
        from itertools import chain
        self._captured["where_args"] = list(chain.from_iterable(args))
        return self

    def __iter__(self):
        return iter(self._rows)


class FakePath:
    def __init__(self, rows, captured, updates):
        self._rows = rows
        self._captured = captured
        self._updates = updates

    def select(self, *keys):
        return FakeQuery(self._rows, self._captured)

    def update(self, **kwargs):
        self._updates.append(kwargs)


class FakeApi:
    def __init__(self, secret_rows=None, active_rows=None):
        self.secret_rows = secret_rows if secret_rows is not None else []
        self.active_rows = active_rows if active_rows is not None else []
        self.updates = []
        self.captured = {}
        self.closed = False

    def path(self, *parts):
        if parts == ("ppp", "secret"):
            return FakePath(self.secret_rows, self.captured, self.updates)
        if parts == ("ppp", "active"):
            return FakePath(self.active_rows, self.captured, self.updates)
        if parts == ("system", "identity"):
            return FakePath([{"name": "test-router"}], self.captured, self.updates)
        raise AssertionError(f"unexpected path {parts}")

    def close(self):
        self.closed = True


# --- Collision-safety: the wire query must include `service` once configured ---

def test_secret_lookup_query_has_no_service_filter_when_unset(monkeypatch):
    server = make_server(service_name=None)
    api = FakeApi(secret_rows=[{".id": "*1", "disabled": False}])
    monkeypatch.setattr(mikrotik, "_connect", lambda s: api)

    ok, status = mikrotik.get_secret_status(server, "user1")

    assert (ok, status) == (True, "enabled")
    assert api.captured["where_args"] == ["?=name=user1"]


def test_secret_lookup_query_includes_service_filter_when_set(monkeypatch):
    server = make_server(service_name="abc")
    api = FakeApi(secret_rows=[{".id": "*1", "disabled": False}])
    monkeypatch.setattr(mikrotik, "_connect", lambda s: api)

    ok, status = mikrotik.get_secret_status(server, "user1")

    assert (ok, status) == (True, "enabled")
    # Both the name and the service must be present in the actual RouterOS
    # query -- this is the exact property that prevents a lookup from ever
    # resolving to a different ISP's identically-named subscriber.
    assert api.captured["where_args"] == ["?=name=user1", "?=service=abc", "?#&"]


# --- get_secret_status ---

def test_get_secret_status_not_found(monkeypatch):
    server = make_server()
    api = FakeApi(secret_rows=[])
    monkeypatch.setattr(mikrotik, "_connect", lambda s: api)

    assert mikrotik.get_secret_status(server, "ghost") == (True, "not_found")


def test_get_secret_status_disabled(monkeypatch):
    server = make_server()
    api = FakeApi(secret_rows=[{".id": "*1", "disabled": True}])
    monkeypatch.setattr(mikrotik, "_connect", lambda s: api)

    assert mikrotik.get_secret_status(server, "user1") == (True, "disabled")


# --- set_secret_enabled ---

def test_set_secret_enabled_updates_matching_row(monkeypatch):
    server = make_server(service_name="abc")
    api = FakeApi(secret_rows=[{".id": "*7", "disabled": True}])
    monkeypatch.setattr(mikrotik, "_connect", lambda s: api)

    ok, message = mikrotik.set_secret_enabled(server, "user1", True)

    assert ok is True
    assert api.updates == [{".id": "*7", "disabled": False}]


def test_set_secret_enabled_not_found(monkeypatch):
    server = make_server()
    api = FakeApi(secret_rows=[])
    monkeypatch.setattr(mikrotik, "_connect", lambda s: api)

    ok, message = mikrotik.set_secret_enabled(server, "ghost", False)

    assert ok is False
    assert "ghost" in message
    assert api.updates == []


# --- get_active_session ---

def test_get_active_session_found(monkeypatch):
    server = make_server()
    api = FakeApi(active_rows=[{"name": "user1", "address": "10.1.1.5"}])
    monkeypatch.setattr(mikrotik, "_connect", lambda s: api)

    ok, session = mikrotik.get_active_session(server, "user1")

    assert ok is True
    assert session == {"name": "user1", "address": "10.1.1.5"}


def test_get_active_session_not_connected(monkeypatch):
    server = make_server()
    api = FakeApi(active_rows=[])
    monkeypatch.setattr(mikrotik, "_connect", lambda s: api)

    assert mikrotik.get_active_session(server, "user1") == (True, None)


# --- Connectivity must never raise out of these calls ---

@pytest.mark.parametrize("fn,args", [
    (mikrotik.test_connection, ()),
    (mikrotik.get_secret_status, ("user1",)),
    (mikrotik.set_secret_enabled, ("user1", True)),
    (mikrotik.get_active_session, ("user1",)),
])
def test_unreachable_router_never_raises(monkeypatch, fn, args):
    server = make_server()

    def boom(s):
        raise OSError("Connection refused")

    monkeypatch.setattr(mikrotik, "_connect", boom)

    ok, message = fn(server, *args)

    assert ok is False
    assert isinstance(message, str) and message


def test_test_connection_marks_unreachable_on_socket_error(monkeypatch):
    server = make_server()
    monkeypatch.setattr(mikrotik, "_connect", lambda s: (_ for _ in ()).throw(OSError("timed out")))

    ok, message = mikrotik.test_connection(server)

    assert ok is False
    assert server.last_status == "unreachable"
    assert server.last_checked_at is not None


def test_test_connection_marks_auth_failed_on_protocol_error(monkeypatch):
    server = make_server()

    def boom(s):
        raise TrapError(message="cannot log in")

    monkeypatch.setattr(mikrotik, "_connect", boom)

    ok, message = mikrotik.test_connection(server)

    assert ok is False
    assert server.last_status == "auth_failed"


def test_test_connection_marks_online_on_success(monkeypatch):
    server = make_server()
    api = FakeApi()
    monkeypatch.setattr(mikrotik, "_connect", lambda s: api)

    ok, message = mikrotik.test_connection(server)

    assert ok is True
    assert server.last_status == "online"
    assert server.last_checked_at is not None
    assert api.closed is True

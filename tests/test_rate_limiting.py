"""Shared rate-limiting infrastructure (Flask-Limiter) -- see
docs/superpowers/plans/2026-08-27-tenant-whish-customer-payments.md, Task 1.
This module only tests the shared Limiter object exists and is wired to the
app; per-route limits on the public /pay/ routes are tested alongside those
routes in later tasks."""
import app as appmod
from tests.conftest import make_tenant


def test_limiter_is_configured_on_the_app():
    assert appmod.limiter is not None
    assert appmod.limiter.app is appmod.app


def test_limiter_does_not_throttle_existing_authenticated_routes(app, client):
    hdr = make_tenant(client, "Biz RateLimit", "ratelimit_admin")
    for _ in range(30):
        r = client.get("/api/tenant/me", headers=hdr)
        assert r.status_code == 200

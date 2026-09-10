import os
os.environ["DATABASE_PATH"] = ":memory:"
os.environ["JWT_SECRET_KEY"] = "test-secret-not-for-prod"
# The daily scheduler jobs now fire immediately on startup (see app.py), which
# during tests means "immediately at import time, before any table exists" --
# harmless (APScheduler just logs the error) but noisy, and tests shouldn't be
# depending on or racing a background scheduler thread against a per-test
# in-memory DB anyway.
os.environ["RUN_SCHEDULER"] = "0"
import pytest
from app import app as flask_app, db, User, Tenant


@pytest.fixture
def app():
    flask_app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
    with flask_app.app_context():
        db.create_all()
        # Currency is a small reference table normally seeded by its Alembic
        # migration (see migrations/versions/*_add_currency_and_exchange_rate.py);
        # tests build schema via create_all(), not migrations, so seed it here too.
        from app import Currency
        db.session.add(Currency(code='USD', name='US Dollar', decimal_places=2))
        db.session.add(Currency(code='LBP', name='Lebanese Pound', decimal_places=0))
        db.session.commit()
        yield flask_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def auth_headers(client, username="admin", password="pw", role="admin"):
    """Register (or re-authenticate) `username` and return its bearer headers.

    role="admin" (the default) goes through the public /api/register flow,
    exactly as before -- which always provisions a brand-new tenant with this
    user as its admin, or (if `username` already exists, e.g. a prior
    make_tenant() call) just re-logs into that existing account.

    Any other role has no public self-serve path: /api/register hardcodes
    role='admin', and POST /api/users (which does accept a role) requires an
    admin's own bearer token to call. Callers that pass role= want a second,
    differently-privileged user inside the tenant a preceding make_tenant()/
    auth_headers() call in the same test already created -- not yet another
    isolated tenant -- so build that row directly against the most recently
    created tenant instead of round-tripping through an admin-authenticated
    request.
    """
    if role == "admin":
        client.post("/api/register", json={"username": username, "password": password})
    else:
        tenant = Tenant.query.order_by(Tenant.id.desc()).first()
        user = User(username=username, role=role,
                    tenant_id=tenant.id if tenant else None)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
    r = client.post("/api/login", json={"username": username, "password": password})
    token = r.get_json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def make_tenant(client, business_name, username, password="pw"):
    """Register a new tenant (business) and return auth headers for its admin user."""
    client.post("/api/register", json={"username": username, "password": password,
                                       "business_name": business_name})
    r = client.post("/api/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}

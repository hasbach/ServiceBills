import os
os.environ["DATABASE_PATH"] = ":memory:"
os.environ["JWT_SECRET_KEY"] = "test-secret-not-for-prod"
import pytest
from app import app as flask_app, db


@pytest.fixture
def app():
    flask_app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
    with flask_app.app_context():
        db.create_all()
        yield flask_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def auth_headers(client, username="admin", password="pw", role="admin"):
    client.post("/api/register", json={"username": username, "password": password})
    r = client.post("/api/login", json={"username": username, "password": password})
    token = r.get_json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def make_tenant(client, business_name, username, password="pw"):
    """Register a new tenant (business) and return auth headers for its admin user."""
    client.post("/api/register", json={"username": username, "password": password,
                                       "business_name": business_name})
    r = client.post("/api/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}

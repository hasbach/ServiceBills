import re
import email_util
from app import User


def _token_from_last_email():
    return re.search(r"token=([\w.\-]+)", email_util.SENT[-1]["body"]).group(1)


def test_register_sends_verification_and_verify_flips(client, app):
    email_util.SENT.clear()
    r = client.post("/api/register", json={
        "username": "biz", "password": "pw", "business_name": "Acme", "email": "a@b.com"})
    assert r.status_code == 201
    assert email_util.SENT and email_util.SENT[-1]["to"] == "a@b.com"

    with app.app_context():
        assert User.query.filter_by(email="a@b.com").first().email_verified is False

    token = _token_from_last_email()
    assert client.post("/api/verify-email", json={"token": token}).status_code == 200
    with app.app_context():
        assert User.query.filter_by(email="a@b.com").first().email_verified is True

    assert client.post("/api/verify-email", json={"token": "garbage"}).status_code == 400


def test_register_rejects_duplicate_email(client):
    client.post("/api/register", json={"username": "u1", "password": "pw",
                                       "business_name": "A", "email": "dup@x.com"})
    r = client.post("/api/register", json={"username": "u2", "password": "pw",
                                           "business_name": "B", "email": "dup@x.com"})
    assert r.status_code == 409

import re
import email_util


def test_forgot_and_reset_password(client):
    client.post("/api/register", json={"username": "u", "password": "old",
                                       "business_name": "A", "email": "u@x.com"})
    email_util.SENT.clear()

    # forgot-password always 200; sends a link for a real email.
    assert client.post("/api/forgot-password", json={"email": "u@x.com"}).status_code == 200
    token = re.search(r"token=([\w.\-]+)", email_util.SENT[-1]["body"]).group(1)

    # reset works with the token; old password no longer valid.
    assert client.post("/api/reset-password",
                       json={"token": token, "new_password": "new"}).status_code == 200
    assert client.post("/api/login", json={"username": "u", "password": "old"}).status_code == 401
    assert client.post("/api/login", json={"username": "u", "password": "new"}).status_code == 200


def test_forgot_password_no_enumeration(client):
    email_util.SENT.clear()
    # Unknown email -> still 200, but no email sent.
    assert client.post("/api/forgot-password", json={"email": "nobody@x.com"}).status_code == 200
    assert email_util.SENT == []


def test_reset_rejects_bad_token(client):
    assert client.post("/api/reset-password",
                       json={"token": "garbage", "new_password": "x"}).status_code == 400

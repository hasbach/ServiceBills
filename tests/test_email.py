import email_util


def test_console_backend_records(monkeypatch):
    email_util.SENT.clear()
    monkeypatch.setattr(email_util.Config, "MAIL_BACKEND", "console")
    email_util.send("a@b.com", "Hi", "body text")
    assert email_util.SENT[-1] == {"to": "a@b.com", "subject": "Hi", "body": "body text"}

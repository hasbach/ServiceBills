"""Minimal email sending abstraction.

MAIL_BACKEND=console (dev/CI) records messages in SENT and logs them; smtp sends
via smtplib. Verification/reset flows call send() and don't care which backend runs.
"""
import logging
import smtplib
from email.message import EmailMessage
from config import Config

# Test/dev inspection: console backend appends {"to","subject","body"} here.
SENT = []


def _send_console(to, subject, body):
    SENT.append({"to": to, "subject": subject, "body": body})
    logging.info("EMAIL (console) to=%s subject=%s", to, subject)


def _send_smtp(to, subject, body):
    msg = EmailMessage()
    msg["From"] = Config.MAIL_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    # Without a timeout, a blocked/unreachable SMTP port hangs the request until
    # gunicorn's own worker timeout kills the process (verified live: this took
    # down the worker handling the request, forcing a restart). Fail fast instead.
    with smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=10) as s:
        s.starttls()
        if Config.SMTP_USER:
            s.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
        s.send_message(msg)


def send(to, subject, body):
    """Send an email via the configured backend."""
    if Config.MAIL_BACKEND == "smtp":
        _send_smtp(to, subject, body)
    else:
        _send_console(to, subject, body)

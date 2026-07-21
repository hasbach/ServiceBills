"""Minimal email sending abstraction.

MAIL_BACKEND=console (dev/CI) records messages in SENT and logs them; smtp sends
via smtplib; sendgrid sends via SendGrid's HTTP API. Verification/reset flows
call send() and don't care which backend runs.

sendgrid is the recommended production backend: Render's free tier blocks
outbound SMTP (confirmed live -- smtplib hung until gunicorn's own worker
timeout killed the process, taking the app down for every user, not just the
request that triggered it). The HTTP API rides over normal HTTPS (443), which
is never blocked the way raw SMTP ports are.
"""
import socket
import logging
import smtplib
import requests
from email.message import EmailMessage
from config import Config

# Render's containers have an IPv6 interface but no working outbound IPv6 route:
# any host that publishes an AAAA record (api.sendgrid.com does; Cloudflare R2
# apparently doesn't, which is why that one worked) fails instantly with
# "[Errno 101] Network is unreachable" -- confirmed live in production logs --
# before even attempting IPv4. Force urllib3 (what `requests` uses under the
# hood) to resolve IPv4 only. This module is imported early in app.py, so it
# patches process-wide before any other requests-based call runs (WhatsApp's
# Graph API calls included, which likely hit the same failure).
import urllib3.util.connection as _urllib3_cn
_urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

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


def _send_sendgrid(to, subject, body):
    resp = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={
            "Authorization": f"Bearer {Config.SENDGRID_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "personalizations": [{"to": [{"email": to}]}],
            "from": {"email": Config.MAIL_FROM},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body}],
        },
        timeout=10,
    )
    if not resp.ok:
        raise RuntimeError(f"SendGrid send failed ({resp.status_code}): {resp.text}")


def send(to, subject, body):
    """Send an email via the configured backend."""
    if Config.MAIL_BACKEND == "sendgrid":
        _send_sendgrid(to, subject, body)
    elif Config.MAIL_BACKEND == "smtp":
        _send_smtp(to, subject, body)
    else:
        _send_console(to, subject, body)

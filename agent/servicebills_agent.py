"""ServiceBills on-prem network agent (Layer 2).

Runs on a tenant's own LAN and performs read-only device checks that the cloud
cannot: the CCR and OLT sit on private addresses with no inbound route.

Shape of the thing:
  * The agent polls OUTBOUND over HTTPS. Nothing connects in, so no firewall
    rule, port forward or static IP is needed.
  * It short-polls (default 2s) and never long-polls. The cloud runs a single
    synchronous gunicorn worker; a held connection would freeze the whole app.
  * Device credentials live HERE, in agent.toml, and never reach the cloud.
  * It reuses the application's own mikrotik.py and vsol_olt.py unchanged, so
    there is exactly one implementation of each connector.

Trust boundary: the agent trusts the cloud to say WHICH of its own devices to
check. It does not trust the cloud to say WHERE to connect -- every job's host
is verified against local config first. Without that, a compromised cloud
could point the agent at an attacker's host and collect the router password on
the first connection.

See docs/superpowers/specs/2026-09-04-network-agent-layer-2-design.md.
"""
import argparse
import logging
import logging.handlers
import os
import sys
import time
import types

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    raise SystemExit("This agent needs Python 3.11 or newer.")

import requests

# The connectors are expected one directory up from agent/ -- see README.md's
# Install section for the deployed layout (mikrotik.py and vsol_olt.py sit
# beside the agent/ directory, not inside it). Guarded explicitly because a
# bare ModuleNotFoundError here happens before _configure_logging() runs, so
# nothing would otherwise reach agent.log -- this turns that into a clear,
# actionable SystemExit instead of an unexplained crash on a box nobody is
# watching interactively.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    import mikrotik      # noqa: E402
    import vsol_olt      # noqa: E402
except ImportError as exc:
    raise SystemExit(
        "Cannot start: {}. This agent expects mikrotik.py and vsol_olt.py "
        "in the parent directory of agent/ (e.g. C:\\ServiceBills\\mikrotik.py, "
        "C:\\ServiceBills\\vsol_olt.py, C:\\ServiceBills\\agent\\servicebills_agent.py) "
        "-- see README.md's Install section. Copying only the agent/ "
        "directory on its own is not enough.".format(exc))

AGENT_VERSION = "1.0.0"

# Read-only, and deliberately so. mikrotik.set_secret_enabled disables a
# customer's PPPoE secret; it is absent here so that even a compromised cloud
# cannot disconnect anyone through this agent.
ALLOWED_OPERATIONS = (
    "test_connection", "device_health", "secret_status",
    "active_session", "olt_status",
)

DEFAULT_CONFIG_PATH = r"C:\ProgramData\ServiceBillsAgent\agent.toml"
DEFAULT_LOG_PATH = r"C:\ProgramData\ServiceBillsAgent\agent.log"
DEFAULT_POLL_SECONDS = 2
UNAUTHORIZED_BACKOFF_SECONDS = 30
HTTP_TIMEOUT_SECONDS = 30

logger = logging.getLogger("servicebills_agent")


class AgentConfigError(Exception):
    """agent.toml is missing or unusable. The agent refuses to start."""


def parse_config(raw):
    """Validate a parsed TOML mapping and index its devices by id."""
    cloud_url = (raw.get("cloud_url") or "").rstrip("/")
    token = raw.get("token") or ""
    if not cloud_url:
        raise AgentConfigError("cloud_url is required")
    if not token:
        raise AgentConfigError("token is required")

    devices = {}
    for entry in raw.get("device") or []:
        if "id" not in entry or "host" not in entry:
            raise AgentConfigError("every [[device]] needs an id and a host")
        devices[int(entry["id"])] = {
            "id": int(entry["id"]),
            "host": entry["host"],
            "type": entry.get("type", ""),
            "api_port": int(entry.get("api_port", 0)) or None,
            "use_tls": bool(entry.get("use_tls", False)),
            "username": entry.get("username", ""),
            "password": entry.get("password", ""),
            "service_name": entry.get("service_name"),
        }
    if not devices:
        raise AgentConfigError("at least one [[device]] is required")

    return {
        "cloud_url": cloud_url,
        "token": token,
        "poll_seconds": int(raw.get("poll_seconds", DEFAULT_POLL_SECONDS)),
        "devices": devices,
    }


def load_config(path):
    if not os.path.exists(path):
        raise AgentConfigError("config not found: {}".format(path))
    with open(path, "rb") as handle:
        return parse_config(tomllib.load(handle))


def _resolve_device_id(job):
    """Return (device_id, None) on success, or (None, refusal_reason).

    A job is untrusted input from the cloud: `device_id` may be missing,
    null, or a non-numeric string. int() raises TypeError/ValueError for
    those instead of returning a sentinel, so this is the one place that
    conversion happens -- both validate_job and execute_job call it instead
    of indexing/converting the job dict directly, so the malformed cases
    become an ordinary refusal instead of an uncaught exception.
    """
    raw = job.get("device_id", -1)
    try:
        return int(raw), None
    except (TypeError, ValueError):
        return None, "Device id {!r} is not a valid integer".format(raw)


def validate_job(job, config):
    """Return a refusal reason, or None if the job may run.

    Checks run in the order that fails cheapest first. Each one exists
    because the cloud is not fully trusted with this decision.
    """
    device_id, reason = _resolve_device_id(job)
    if reason:
        return reason
    device = config["devices"].get(device_id)
    if device is None:
        return "Device {} is not configured on this agent".format(job.get("device_id"))
    if job.get("operation") not in ALLOWED_OPERATIONS:
        return "Operation {!r} is not permitted by this agent".format(job.get("operation"))
    # A missing/None/empty host is treated as a mismatch, not skipped: the
    # whole point of this check is that every job's host must be verified
    # against local config, so "the job didn't say" cannot be a free pass.
    if not job.get("host") or job["host"] != device["host"]:
        return ("Refusing job: host {!r} does not match the configured host {!r} "
                "for device {}".format(job.get("host"), device["host"], device["id"]))
    return None


def build_server(device_cfg):
    """The duck-typed object mikrotik.py and vsol_olt.py expect.

    Only id/host/api_port are load-bearing for dispatch and the host check;
    use_tls/username/password/service_name are read with defaults so a
    hand-built config (as in tests, or a minimal [[device]] block) doesn't
    have to spell out every optional field. service_name in particular
    defaults to None -- mikrotik._secret_where() treats a falsy service_name
    as "match by /ppp/secret name only", which is the correct behaviour for
    a router that isn't shared between more than one ISP.
    """
    return types.SimpleNamespace(
        id=device_cfg["id"],
        host=device_cfg["host"],
        api_port=device_cfg["api_port"],
        use_tls=device_cfg.get("use_tls", False),
        username=device_cfg.get("username", ""),
        password=device_cfg.get("password", ""),
        service_name=device_cfg.get("service_name"),
        last_checked_at=None,
        last_status=None,
    )


def execute_job(job, config):
    """Run one job. Returns (ok, result, error, status). Never raises.

    status is the connector's own device classification -- 'online',
    'unreachable', or 'auth_failed' -- read back from server.last_status
    after the connector runs (mikrotik.py/vsol_olt.py's _mark_checked sets
    it on both their success and failure paths). It is None when the
    operation doesn't classify a device at all (secret_status, active_session
    never touch last_status), when the job was refused before any connector
    ran, or when the connector raised something its own try/except didn't
    already turn into a classified failure. The cloud stamps
    NetworkDevice.last_status from this so the tree's status chip reflects
    agent-mode checks -- see agent_post_result.
    """
    reason = validate_job(job, config)
    if reason:
        logger.warning("Refused job %s: %s", job.get("job_id"), reason)
        return False, None, reason, None

    # validate_job (via _resolve_device_id) already proved this device_id
    # parses and resolves to a configured device, so this cannot KeyError or
    # raise on the int() conversion the way indexing job["device_id"] could.
    device_id, _ = _resolve_device_id(job)
    device = config["devices"][device_id]
    server = build_server(device)
    params = job.get("params") or {}
    operation = job["operation"]

    try:
        if operation == "olt_status":
            ok, value = vsol_olt.get_olt_status(server)
        elif operation == "device_health":
            ok, value = mikrotik.get_device_health(server)
        elif operation == "test_connection":
            ok, value = mikrotik.test_connection(server)
        elif operation == "secret_status":
            ok, value = mikrotik.get_secret_status(server, params.get("pppoe_username"))
        else:  # active_session -- the only remaining allowed operation
            ok, value = mikrotik.get_active_session(server, params.get("pppoe_username"))
    except Exception as exc:  # noqa: BLE001 -- a bad job must not kill the loop
        # Never logger.exception here: the frame locals hold the device
        # credential, and a traceback in the log file would expose it.
        logger.warning("Job %s raised: %s", job.get("job_id"), exc)
        return False, None, "{}: {}".format(exc.__class__.__name__, exc), server.last_status

    return ((True, value, None, server.last_status) if ok
            else (False, None, value, server.last_status))


def _headers(config):
    return {
        "Authorization": "Bearer " + config["token"],
        "X-Agent-Version": AGENT_VERSION,
    }


def run_once(session, config):
    """Poll once. Returns True if a job was handled, False if there was none."""
    url = config["cloud_url"] + "/api/agent/jobs"
    response = session.get(url, timeout=HTTP_TIMEOUT_SECONDS, headers=_headers(config))
    if response.status_code == 204:
        return False
    if response.status_code == 401:
        logger.error("Agent token rejected. Backing off %ss.", UNAUTHORIZED_BACKOFF_SECONDS)
        time.sleep(UNAUTHORIZED_BACKOFF_SECONDS)
        return False
    if response.status_code != 200:
        logger.warning("Unexpected poll status %s", response.status_code)
        return False

    job = response.json()
    job_id = job.get("job_id")
    if job_id is None:
        # No usable job_id means there is nowhere to POST a result to -- the
        # URL itself needs it. This is unlike every other malformed-job case
        # (which still gets refused with an ok:false result the cloud can
        # see): here the cloud never learns anything happened, so make sure
        # it is at least loud in the local log.
        logger.error("Received a job with no job_id; cannot post a result. "
                     "operation=%r device_id=%r",
                     job.get("operation"), job.get("device_id"))
        return False

    ok, result, error, status = execute_job(job, config)
    payload = {"ok": ok, "result": result, "error": error}
    if status is not None:
        payload["status"] = status
    post_response = session.post(
        "{}/api/agent/jobs/{}/result".format(config["cloud_url"], job_id),
        json=payload,
        timeout=HTTP_TIMEOUT_SECONDS, headers=_headers(config),
    )
    if post_response.status_code >= 400:
        # The cloud returns 4xx when it rejects a result outright -- a
        # malformed shape (400, see agent_post_result/_validate_agent_result)
        # or a job that's no longer claimed (409) -- specifically so a broken
        # agent build or a race surfaces in its own logs instead of failing
        # silently. Logging the connector's own ok/error unconditionally, as
        # before, defeated that: a rejected POST looked identical in this log
        # to a clean success. The body is the cloud's own short JSON error
        # message, not attacker-controlled, but it's still truncated here --
        # there's no reason to let an oversized response bloat the log.
        body = getattr(post_response, "text", "") or ""
        logger.warning("Cloud rejected result for job %s: HTTP %s %s",
                       job_id, post_response.status_code, body[:200])
    else:
        logger.info("Job %s (%s) -> %s", job_id, job.get("operation"),
                    "ok" if ok else "error: {}".format(error))
    return True


def _configure_logging(log_path):
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler(sys.stdout))
    logger.setLevel(logging.INFO)


def _warn_if_world_readable(path):
    """The config holds device credentials. On Windows the install procedure
    restricts it to SYSTEM and Administrators; warn loudly if that wasn't done,
    but don't refuse to start -- a permissions slip should not become an outage.
    """
    try:
        import subprocess
        out = subprocess.run(["icacls", path], capture_output=True, text=True,
                             timeout=10).stdout
        if "Users:" in out or "Everyone:" in out:
            logger.warning("SECURITY: %s is readable beyond Administrators/SYSTEM. "
                           "It contains device credentials -- tighten its ACL.", path)
    except Exception:  # noqa: BLE001 -- advisory only
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="ServiceBills network agent")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--log", default=DEFAULT_LOG_PATH)
    parser.add_argument("--once", action="store_true",
                        help="handle at most one job then exit (for testing)")
    args = parser.parse_args(argv)

    _configure_logging(args.log)
    config = load_config(args.config)
    _warn_if_world_readable(args.config)
    logger.info("Agent %s starting: cloud=%s devices=%s poll=%ss",
                AGENT_VERSION, config["cloud_url"],
                sorted(config["devices"]), config["poll_seconds"])

    session = requests.Session()
    while True:
        try:
            handled = run_once(session, config)
        except requests.RequestException as exc:
            logger.warning("Cloud unreachable: %s", exc)
            handled = False
        except Exception as exc:  # noqa: BLE001 -- a bug anywhere in the poll
            # cycle must degrade to a logged, retried cycle, never a dead
            # agent (this is what actually keeps an unattended box running).
            # Deliberately not logger.exception/exc_info=True: this frame
            # wraps run_once, whose local `config` dict holds every device's
            # plaintext password, so a traceback dumped from here (or from
            # anywhere run_once calls) would put those in the log file the
            # same way execute_job's own except-block avoids doing. Logging
            # only the exception's type and message -- never its traceback or
            # this frame's locals -- carries the same guarantee: Python's
            # built-in exceptions don't embed unrelated local variables into
            # their str(), only a short, generic description.
            logger.error("Unexpected error in poll loop: %s: %s",
                         exc.__class__.__name__, exc)
            handled = False
        if args.once:
            return 0
        if not handled:
            time.sleep(config["poll_seconds"])


if __name__ == "__main__":
    raise SystemExit(main())

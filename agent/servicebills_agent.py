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

# The connectors live at the repository root, one level up from agent/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mikrotik      # noqa: E402
import vsol_olt      # noqa: E402

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


def validate_job(job, config):
    """Return a refusal reason, or None if the job may run.

    Three checks, in the order that fails cheapest first. Each one exists
    because the cloud is not fully trusted with this decision.
    """
    device = config["devices"].get(int(job.get("device_id", -1)))
    if device is None:
        return "Device {} is not configured on this agent".format(job.get("device_id"))
    if job.get("operation") not in ALLOWED_OPERATIONS:
        return "Operation {!r} is not permitted by this agent".format(job.get("operation"))
    if job.get("host") and job["host"] != device["host"]:
        return ("Refusing job: host {!r} does not match the configured host {!r} "
                "for device {}".format(job["host"], device["host"], device["id"]))
    return None


def build_server(device_cfg):
    """The duck-typed object mikrotik.py and vsol_olt.py expect.

    Only id/host/api_port are load-bearing for dispatch and the host check;
    use_tls/username/password are read with defaults so a hand-built config
    (as in tests, or a minimal [[device]] block) doesn't have to spell out
    every optional field.
    """
    return types.SimpleNamespace(
        id=device_cfg["id"],
        host=device_cfg["host"],
        api_port=device_cfg["api_port"],
        use_tls=device_cfg.get("use_tls", False),
        username=device_cfg.get("username", ""),
        password=device_cfg.get("password", ""),
        last_checked_at=None,
        last_status=None,
    )


def execute_job(job, config):
    """Run one job. Returns (ok, result, error). Never raises."""
    reason = validate_job(job, config)
    if reason:
        logger.warning("Refused job %s: %s", job.get("job_id"), reason)
        return False, None, reason

    device = config["devices"][int(job["device_id"])]
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
        return False, None, "{}: {}".format(exc.__class__.__name__, exc)

    return (True, value, None) if ok else (False, None, value)


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
    ok, result, error = execute_job(job, config)
    session.post(
        "{}/api/agent/jobs/{}/result".format(config["cloud_url"], job["job_id"]),
        json={"ok": ok, "result": result, "error": error},
        timeout=HTTP_TIMEOUT_SECONDS, headers=_headers(config),
    )
    logger.info("Job %s (%s) -> %s", job["job_id"], job["operation"],
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
        if args.once:
            return 0
        if not handled:
            time.sleep(config["poll_seconds"])


if __name__ == "__main__":
    raise SystemExit(main())

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
import hashlib
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

AGENT_VERSION = "1.2.0"

# How many hex characters of each file's sha256 are reported. This is a
# diagnostic, not an attestation -- see connector_fingerprint() -- so 48 bits
# is far more than enough to notice that two files differ, and short enough
# that the whole header stays readable in a log line.
FINGERPRINT_LENGTH = 12

# Read-only, and deliberately so. mikrotik.set_secret_enabled disables a
# customer's PPPoE secret; it is absent here so that even a compromised cloud
# cannot disconnect anyone through this agent.
ALLOWED_OPERATIONS = (
    "test_connection", "device_health", "secret_status",
    "active_session", "olt_status", "cpe_locations",
)

DEFAULT_CONFIG_PATH = r"C:\ProgramData\ServiceBillsAgent\agent.toml"
DEFAULT_LOG_PATH = r"C:\ProgramData\ServiceBillsAgent\agent.log"
DEFAULT_POLL_SECONDS = 2
UNAUTHORIZED_BACKOFF_SECONDS = 30
HTTP_TIMEOUT_SECONDS = 30

logger = logging.getLogger("servicebills_agent")


def _file_fingerprint(path):
    """A short content hash of one source file, or None if it can't be read.

    Line endings are normalised before hashing. These files reach the on-prem
    box by hand -- a git checkout with core.autocrlf, a zip, an editor that
    rewrites newlines -- and a CRLF/LF difference is a transport artefact, not
    a stale file. Without this every correctly-updated Windows box would
    report as stale, which is worse than having no check at all.
    """
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(data).hexdigest()[:FINGERPRINT_LENGTH]


def _fingerprint_sources():
    """The source files this agent is actually running, by short name.

    The connectors are resolved through their imported modules rather than by
    rebuilding the expected path, so this reports the file Python really
    loaded -- including the case where sys.path resolved it somewhere other
    than the directory above this one.
    """
    return {
        "agent": os.path.abspath(__file__),
        "mikrotik": getattr(mikrotik, "__file__", "") or "",
        "vsol_olt": getattr(vsol_olt, "__file__", "") or "",
    }


def compute_connector_fingerprint():
    """`name=hash` pairs for the files this agent loaded, for the cloud.

    The cloud hashes its own copies the same way, so Settings can say
    "agent 1.2.0, vsol_olt.py out of date" instead of showing a green version
    number beside a connector from three deploys ago. That exact trap cost two
    round trips of debugging in a single day: AGENT_VERSION lives in THIS
    file, so copying only this file over bumps the version the cloud displays
    while leaving the connector that does the work untouched.

    A file that cannot be read is simply left out. The cloud only compares
    names present on both sides, so a missing entry reads as "not known",
    never as "stale".

    Diagnostic, not attestation: a compromised agent could report whatever it
    liked. There is nothing to gain by doing so -- it already holds every
    device credential in agent.toml.
    """
    parts = []
    for name, path in sorted(_fingerprint_sources().items()):
        digest = _file_fingerprint(path)
        if digest:
            parts.append("{}={}".format(name, digest))
    return ",".join(parts)


# Computed once at import: these files cannot change under a running process,
# and _headers() would otherwise re-read all three on every poll.
CONNECTOR_FINGERPRINT = compute_connector_fingerprint()


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
        elif operation == "cpe_locations":
            ok, value = vsol_olt.get_cpe_locations(server)
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
    headers = {
        "Authorization": "Bearer " + config["token"],
        "X-Agent-Version": AGENT_VERSION,
    }
    # Omitted rather than sent empty when not a single file could be hashed:
    # the cloud only overwrites what it has stored when the header is present,
    # so a transient read failure leaves the last good reading in place
    # instead of blanking it.
    if CONNECTOR_FINGERPRINT:
        headers["X-Agent-Connectors"] = CONNECTOR_FINGERPRINT
    return headers


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
    """Log to a rotating file, and to stdout for interactive runs.

    The log's directory is created if it is missing. The install procedure
    happens to create C:\\ProgramData\\ServiceBillsAgent\\ as a side effect of
    copying agent.toml into it (README Install step 4), so a missing directory
    only bit someone who pointed --log or --config elsewhere -- but it bit
    hard: this handler is opened BEFORE any logging exists, so the
    FileNotFoundError surfaced as a raw traceback with nothing in agent.log to
    explain it. Exactly the failure shape the connector-import guard at the
    top of this file exists to prevent.

    If the file still cannot be opened -- a denied ACL, a read-only volume, a
    directory sitting where the file should be -- the agent logs to stdout
    only and keeps running. An agent that checks devices but cannot write its
    log is much better than one that refuses to start, and the cloud's
    Settings page still shows it as online; this is the same "degrade, don't
    outage" call _warn_if_world_readable makes about the config's ACL.

    The stdout handler goes on FIRST so that the warning below has somewhere
    to go even when the file handler is what failed.
    """
    logger.addHandler(logging.StreamHandler(sys.stdout))
    logger.setLevel(logging.INFO)
    try:
        # abspath first: a bare relative --log (e.g. "agent.log") has an empty
        # dirname, and os.makedirs("") raises FileNotFoundError even with
        # exist_ok -- resolving it to the working directory makes that a no-op.
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
    except OSError as exc:
        logger.warning(
            "Cannot open the log file %s (%s). Continuing with console "
            "logging only -- under Task Scheduler that means no persistent "
            "log at all, so fix the path or its permissions.", log_path, exc)
        return
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)


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
    logger.info("Agent %s starting: cloud=%s devices=%s poll=%ss connectors=%s",
                AGENT_VERSION, config["cloud_url"],
                sorted(config["devices"]), config["poll_seconds"],
                CONNECTOR_FINGERPRINT or "unavailable")

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

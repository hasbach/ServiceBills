# ServiceBills network agent

A small standalone program that runs on a tenant's own LAN and performs
device checks the cloud cannot: the CCR and OLT sit on private addresses
(`192.168.8.x`) with no inbound route from Render. The agent polls the cloud
**outbound** over HTTPS, so no firewall rule, port forward, or static IP is
needed. Device credentials live only in this box's `agent.toml` and are never
sent to the cloud.

See `docs/superpowers/specs/2026-09-04-network-agent-layer-2-design.md` for
the full design.

## Requirements

- Windows, always on.
- Python 3.11 or newer (for `tomllib`).

## Install

1. Install Python 3.11+ if it isn't already present.

2. Copy **both** `mikrotik.py` and `vsol_olt.py` (from the repository root)
   and this `agent/` directory to the box, keeping them siblings, e.g.:

   ```
   C:\ServiceBills\mikrotik.py
   C:\ServiceBills\vsol_olt.py
   C:\ServiceBills\agent\servicebills_agent.py
   ```

   The agent imports `mikrotik` and `vsol_olt` from the directory one level
   above `agent\`, so it needs both files there -- copying only the `agent\`
   directory on its own leaves those imports unresolvable and the agent will
   refuse to start (see Troubleshooting below). Nothing else from the
   repository is needed: `mikrotik.py` depends only on `librouteros`,
   `vsol_olt.py` only on `pysnmp`, both installed in the next step.

3. Install dependencies (`requirements-agent.txt` lives in `agent\`):

   ```
   pip install -r C:\ServiceBills\agent\requirements-agent.txt
   ```

4. Copy `agent.example.toml` to `C:\ProgramData\ServiceBillsAgent\agent.toml`
   and fill in:
   - `cloud_url` — the ServiceBills URL for this tenant.
   - `token` — the agent token, shown once when the agent was created in
     ServiceBills' Settings. It cannot be retrieved again; if lost, regenerate
     it there and update this file.
   - one `[[device]]` block per device, matching each device's `id`, `host`,
     `api_port`, and credentials as configured in ServiceBills. `host` must
     match exactly — the agent refuses any job whose host doesn't agree with
     this file, even if the cloud sends one, because that check is what stops
     a compromised cloud from pointing the agent at an attacker's host and
     harvesting the credential.

5. **Lock down the config file.** It contains device credentials in plain
   text. Restrict it to `SYSTEM` and `Administrators` only:

   ```
   icacls C:\ProgramData\ServiceBillsAgent\agent.toml /inheritance:r /grant:r SYSTEM:R Administrators:R
   ```

   The agent checks this ACL at startup and logs a loud warning if the file
   is readable by `Users` or `Everyone`, but it will still start — a
   permissions mistake should degrade to a warning, not an outage. Fix the
   ACL as soon as you see that warning.

6. Smoke-test before registering the scheduled task:

   ```
   python C:\ServiceBills\agent\servicebills_agent.py --config C:\ProgramData\ServiceBillsAgent\agent.toml --once
   ```

   `--once` handles at most one poll and exits, so you can confirm the config
   loads and the cloud is reachable without leaving anything running.

7. Register the scheduled task so the agent starts on boot and runs as
   `SYSTEM`:

   ```
   schtasks /Create /TN ServiceBillsAgent /SC ONSTART /RU SYSTEM /RL HIGHEST ^
     /TR "\"C:\Program Files\Python311\python.exe\" C:\ServiceBills\agent\servicebills_agent.py"
   ```

   Adjust the `python.exe` and script paths to match where you installed
   them in step 2.

8. **`schtasks` cannot set two settings that matter — open Task Scheduler
   (`taskschd.msc`) and edit the `ServiceBillsAgent` task by hand:**
   - On the **Settings** tab, check *If the task fails, restart every* and
     set it to **1 minute** (with enough restart attempts that a transient
     crash doesn't leave the agent down for good).
   - On the same tab, **clear** *Stop the task if it runs longer than*. The
     agent runs forever by design (it polls in a loop); this setting would
     otherwise kill it.

9. Start the task (or reboot) and confirm it's running:

   ```
   schtasks /Run /TN ServiceBillsAgent
   ```

## Logs

The agent logs to `C:\ProgramData\ServiceBillsAgent\agent.log`, rotated at
2 MB with 5 backups kept (`agent.log`, `agent.log.1`, ... `agent.log.5`).
Task Scheduler does not usefully capture stdout/stderr for a background task,
so this file is the source of truth for whether the agent is polling,
what jobs it's handling, and any refusals or connector failures.

The directory is created if it doesn't exist, so `--log` can point anywhere.
If the file itself cannot be opened (a denied ACL, a read-only volume), the
agent says so on the console and keeps running with console logging only —
it will still check devices, it just won't leave a record.

Never expect a device password to appear in this log, even in a traceback:
the agent deliberately avoids `logger.exception` (and any traceback dump) on
paths whose frame locals hold a device credential, logging only the
exception's type and message instead.

## Updating

There is no auto-update. To update the agent, stop the scheduled task, pull
the new `agent/` contents **and** `mikrotik.py` / `vsol_olt.py` into the same
layout as Install step 2, and start it again.

Copy all three files, not just the ones you think changed. The version number
Settings displays comes from `servicebills_agent.py` alone, so copying only
that one shows a freshly bumped version beside a connector that is several
deploys old -- and a stale connector usually fails by quietly returning
nothing rather than by erroring, which is a slow thing to notice.

Settings checks this for you. Under **On-prem Agent** it reports the connector
files as one of:

- **match this server** -- the agent is running exactly the files this
  deployment ships.
- **a named file is out of date** -- copy that file across again and restart
  the agent. The message names the file.
- **not reported** -- the agent predates this check (before 1.2.0) or could
  not read its own source files. Restart it on a current build to get an
  answer.

The agent computes a short content hash of each file it loads and sends it
with every poll; line endings are normalised first, so copying through a
Windows editor or a git checkout with `core.autocrlf` does not make a
correctly-updated file look stale. The same hashes are written to `agent.log`
on startup, next to the version.

## Troubleshooting

**The scheduled task starts and immediately stops, or `agent.log` is empty
even though the task shows as run.** This almost always means `mikrotik.py`
and `vsol_olt.py` were not copied next to `agent\` (see Install step 2): the
agent imports them before logging is configured, so a missing connector
fails before anything reaches `agent.log`. Run the agent directly from a
console to see the actual error:

```
python C:\ServiceBills\agent\servicebills_agent.py --config C:\ProgramData\ServiceBillsAgent\agent.toml --once
```

A missing-connector failure prints a message naming the expected layout
(`C:\ServiceBills\mikrotik.py`, `C:\ServiceBills\vsol_olt.py`,
`C:\ServiceBills\agent\servicebills_agent.py`) and exits instead of showing a
bare traceback.

**The agent is clearly running — Settings shows it online — but `agent.log`
never appears.** The log file could not be opened, so the agent fell back to
console logging, which Task Scheduler discards. Run the same `--once` command
above and look for a `Cannot open the log file` line naming the reason.

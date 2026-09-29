# On-Prem Installer & Updater — Design (ServiceBills sub-project 3 of 3)

Date: 2026-09-30
Status: approved in brainstorming, pending spec review
Depends on: `2026-09-30-onprem-runtime-license-design.md` (sub-project 2)

## Goal

A non-technical business owner downloads one `ServiceBills-Setup.exe`, clicks
Next → Finish, and ends up with ServiceBills running on their Windows PC,
reachable from other office PCs, backed up nightly, and updating itself.

## Release pipeline (GitHub Actions, on tag `v*`)

1. Build the image from the existing `Dockerfile` with build args
   `APP_VERSION` (tag) and `APP_RELEASE_DATE`; push
   `ghcr.io/<owner>/servicebills:<version>` and `:latest` (private package).
2. Build the installer on `windows-latest` with Inno Setup from
   `onprem/installer/ServiceBills.iss`; upload `ServiceBills-Setup.exe` to R2.
3. Publish release metadata to the SaaS: `POST /api/internal/releases`
   (protected by `CRON_TRIGGER_SECRET`-style secret) → stored in a `Release`
   table (`version`, `release_date`, `notes`, `min_upgrade_from`).
   `GET /api/updates/latest` (public) returns the newest.
4. `GET /download` on the SaaS redirects to the installer on R2.

Image pulls use a dedicated read-only GHCR token (`read:packages`, bot
account) embedded in the installer. Leaking it exposes only the image, which
every customer already has; licensing is enforced by the app. Layered pulls
make updates small.

## Repository layout (new `onprem/` folder in this repo — no fork)

```
onprem/
  docker-compose.yml        # image: ghcr.io/..., services web, db, scheduler
  installer/ServiceBills.iss
  scripts/
    common.ps1              # paths, logging, docker helpers
    install.ps1             # called by Setup.exe
    start.ps1  stop.ps1
    backup.ps1 restore.ps1
    update.ps1              # check, backup, pull, restart, health, rollback
    uninstall.ps1
  tests/*.Tests.ps1         # Pester
  docs/whatsapp-public-url.md
```

Install root: `C:\ProgramData\ServiceBills` (compose, `.env`, `logs\`,
`state.json` with current/previous version). Data lives in Docker named
volumes (`pgdata`, `uploads`). Backups go to the owner-chosen folder.

## docker-compose (on-prem)

- `db`: `postgres:16`, password from `.env`, no host port published.
- `web`: the image, `restart: unless-stopped`, port `8000:8000`,
  `DEPLOYMENT_MODE=onprem`, `WEB_CONCURRENCY=3`, `MACHINE_ID`, secrets from
  `.env`, healthcheck on `/api/health`. Command = the Dockerfile CMD
  (migrations run on start).
- `scheduler`: same image, `command: flask run-scheduler`,
  `restart: unless-stopped`, depends on web healthy.
- Backups bind-mount the chosen backup folder into `scheduler` at `/backups`.
  The `Dockerfile` adds `postgresql-client` (major version 16, matching `db`)
  so `pg_dump`/`pg_restore` exist in the app image.
- All services get `TZ` from `.env`, which the installer sets from the Windows
  time zone (mapped to IANA, e.g. `Asia/Beirut`), so 02:00 jobs run at local
  02:00.

## Setup.exe flow

1. **Pre-checks**: Windows 10 22H2+/11 64-bit, ≥ 8 GB RAM (warn under), ≥ 20 GB
   free disk, virtualization enabled
   (`Win32_Processor.VirtualizationFirmwareEnabled` or Hyper-V present). If
   virtualization is off: plain-language page with BIOS instructions and a
   "Check again" button; cannot continue.
2. **Docker Desktop**: if absent, show Docker's terms (free for businesses
   < 250 staff and < $10M revenue), download the official installer, run
   `install --quiet --accept-license --backend=wsl-2`. If WSL/reboot is
   required, register a RunOnce entry to resume Setup after restart and ask to
   reboot. Set Docker Desktop to start on login.
3. **Options page**: backup folder (suggest a detected OneDrive / Google Drive
   folder, else `Documents\ServiceBills Backups`), auto-update on (default).
4. **Configure**: write compose + `.env` (random `JWT_SECRET_KEY`,
   `FERNET_KEY`, DB password; `MACHINE_ID` = SHA-256 of MachineGuid;
   `APP_BASE_URL=http://<lan-ip>:8000`). Never overwrite an existing `.env`
   on reinstall/upgrade (keys must survive or encrypted data is lost).
5. **Start**: `docker login ghcr.io` with the embedded token, `compose pull`,
   `compose up -d`, wait up to 5 min for health with a progress bar.
6. **Firewall**: inbound rule for TCP 8000, Private profile only.
7. **Scheduled task** "ServiceBills Updater" at 03:30 daily (if auto-update on).
8. **Finish page**: "Open ServiceBills" (opens `http://localhost:8000` → setup
   wizard) and the LAN address for other PCs.

Every step logs to `C:\ProgramData\ServiceBills\logs\install-<date>.log`; on
failure the page shows a human message plus "Open log folder".

## Shortcuts

Desktop **ServiceBills** (runs `start.ps1`: ensures Docker Desktop and
containers are up, then opens the browser). Start menu folder: Backup now,
Restore backup, Check for updates, Stop ServiceBills, Uninstall. Scripts run
hidden (via a small launcher) and show Windows toast / message boxes, never a
raw console.

## Backups

- Nightly at 01:30, triggered by the scheduler container: `pg_dump -Fc` plus a
  tar of the uploads volume → `/backups/servicebills-YYYYMMDD-HHMM.zip`; keep
  the newest 14.
- **Backup now** runs the same via `docker compose exec`.
- **Restore backup** lists backups by date; restore stops web+scheduler,
  restores DB and uploads, starts again, health-checks. Confirms first
  ("This replaces current data with the backup from …").

## Updates (`update.ps1`)

1. `GET {LICENSE_SERVER_URL}/api/updates/latest`; compare with
   `state.json.current`. Skip if not newer, or if `min_upgrade_from` > current
   (then show "please reinstall with the latest Setup").
2. Take a backup (abort update if backup fails).
3. `compose pull` the new tag, record previous tag, `compose up -d`.
4. Wait for health (5 min). Success → update `state.json`, the app shows
   "Updated to vX" on next login.
5. Failure → re-tag previous version, restore the pre-update backup (the
   migration may have run), `compose up -d`, write a failure record the app
   shows as "Update to vX failed, still on vY".

Runs nightly via the scheduled task, or on demand from the shortcut. The
release-date guard from sub-project 2 means an install whose base license has
expired will refuse a newer version; `update.ps1` checks the license summary
from `/api/system/info` first and skips with a message instead of pulling.

Releases must upgrade cleanly from any version ≥ `min_upgrade_from`
(migrations are append-only and idempotent-safe).

## Uninstall

Stops and removes containers, scheduled task, firewall rule, shortcuts. Keeps
Docker volumes and backups unless the owner ticks "Delete all my ServiceBills
data" (second confirmation). Docker Desktop is left installed.

## Known limits (documented to the owner)

- Docker Desktop runs only while a Windows user is logged in on that PC.
- Without a code-signing certificate, SmartScreen shows "unknown publisher";
  the build has an optional signing step to enable later.
- Python source is readable inside the image; obfuscation is out of scope.
- WhatsApp needs the owner's own public URL, router and Meta setup (in-app help
  page from sub-project 2, source in `onprem/docs/`).

## Testing

- PSScriptAnalyzer clean; Pester unit tests for pure functions (version
  compare, backup rotation, MachineGuid hashing, `.env` preservation,
  state.json handling) with Docker calls mocked.
- CI builds the image and installer on every tag; a compose smoke test on the
  Linux runner (`up`, health, setup status 200, `down`).
- Manual release checklist on a clean Windows 11 VM with nested
  virtualization: fresh install incl. Docker Desktop, trial start, LAN access
  from a second machine, backup/restore, update, forced-failure rollback,
  uninstall keep/delete data.

## Out of scope

Hosting webhooks/tunnels for owners; macOS/Linux installers; native (non-Docker)
Windows service build; code signing purchase.

# On-Prem Installer & Updater Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A non-technical owner runs one `ServiceBills-Setup.exe` and gets ServiceBills running on their Windows PC (Docker Desktop + compose), reachable on the LAN, backed up nightly, auto-updating with rollback; the SaaS publishes releases and the installer download.

**Architecture:** Everything lives in a new `onprem/` folder (compose file, PowerShell scripts, Inno Setup script, Pester tests) plus small backend additions: a cross-worker license cache fix, an update-status read from a host-mounted `state.json`, and a SaaS `Release` API + `/download`. A GitHub Actions release workflow on `v*` tags builds/pushes the image, builds the installer, uploads it to R2 and publishes release metadata.

**Tech Stack:** PowerShell 5.1 (Windows built-in; scripts must not need PowerShell 7), Pester 5 (tests), PSScriptAnalyzer, Inno Setup 6, Docker Desktop / Compose v2, GitHub Actions, Flask/SQLAlchemy/Alembic, React.

**Spec:** `docs/superpowers/specs/2026-09-30-onprem-installer-updater-design.md`. Sub-projects 1–2 are live on main.

## Global Constraints

- Brand everywhere: **ServiceBills**. Install root `C:\ProgramData\ServiceBills` (`compose.yml`, `.env`, `state.json`, `logs\`, `scripts\`). Compose project name `servicebills`.
- Image: `ghcr.io/hasbach/servicebills:<version>` and `:latest`. Versions are SemVer `MAJOR.MINOR.PATCH` from tags `vMAJOR.MINOR.PATCH`.
- On-prem web: `DEPLOYMENT_MODE=onprem`, `WEB_CONCURRENCY=3`, port `8000:8000`, healthcheck `GET /api/health`. Scheduler service: same image, `command: flask run-scheduler`.
- `.env` keys (installer-generated, **never overwritten** once present): `JWT_SECRET_KEY`, `FERNET_KEY`, `POSTGRES_PASSWORD`, `MACHINE_ID` (lower-case hex SHA-256 of `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid`), `TZ` (IANA), `APP_BASE_URL=http://<lan-ip>:8000`, `IMAGE_TAG`. `FERNET_KEY` = urlsafe base64 of 32 random bytes (Fernet format).
- Scheduled tasks: `ServiceBills Backup` daily 01:30; `ServiceBills Updater` daily 03:30 (only if auto-update chosen). Both run as SYSTEM? **No** — Docker Desktop runs per-user; tasks run as the installing user, "run only when user is logged on", highest privileges.
- Backups: `<BackupDir>\servicebills-YYYYMMDD-HHMM.zip` containing `db.dump` (`pg_dump -Fc`) and `uploads\`; keep newest 14. Made via `docker compose exec`/`cp` (host side) — **deviation from spec**: not from the scheduler container, to avoid a pg client/server version mismatch.
- `state.json` shape: `{"current": "1.2.3", "previous": "1.2.2"|null, "last_update": {"status": "ok"|"failed"|"skipped", "target": "1.2.4", "at": "<ISO UTC>", "message": "..."}|null}`. Mounted read-only into `web` at `/onprem/state.json`; env `ONPREM_STATE_FILE=/onprem/state.json`.
- Script UX: never show a raw console to the owner. Shortcuts launch `wscript.exe "<root>\scripts\run-hidden.vbs" <script>.ps1`; scripts report via message boxes (`[System.Windows.Forms.MessageBox]`) or toast; all log to `logs\<script>-YYYYMMDD.log`.
- PowerShell: `Set-StrictMode -Version Latest`, `$ErrorActionPreference = 'Stop'`, PS 5.1 compatible (no `??`, `?.`, ternary, `-Parallel`), UTF-8 without BOM for files other tools read (use `[IO.File]::WriteAllText($p, $s, (New-Object Text.UTF8Encoding $false))`). Binary data never goes through PowerShell pipes/redirection (use `docker compose cp`).
- Pure logic lives in `onprem/scripts/common.ps1` as functions with no side effects; side-effecting scripts dot-source it. Pester tests mock `docker`, registry, filesystem where needed.
- Pester locally: `Save-Module Pester -RequiredVersion 5.6.1 -Path <scratch>` then `Import-Module <scratch>\Pester`; run `Invoke-Pester onprem/tests -Output Detailed`. Do **not** install modules globally. PSScriptAnalyzer the same way (`Save-Module PSScriptAnalyzer`), run `Invoke-ScriptAnalyzer -Path onprem/scripts -Recurse -Severity Warning,Error` → no findings (suppress only with a justified `[Diagnostics.CodeAnalysis.SuppressMessageAttribute]`).
- Backend tests `python -m pytest -q`; frontend `cd frontend && CI=true npx react-scripts test --watchAll=false`, `npm run build` then `git checkout -- frontend/build && git clean -fd frontend/build`.
- Secrets are never committed: GHCR read token, R2 creds, release-publish secret come from GitHub Actions secrets at build time.
- Commit messages end with a blank line + `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Don't touch `.claude/worktrees/`, `.worktrees/`.

---

### Task 1: Backend — cross-worker license cache + update status in `/api/system/info`

**Files:** Modify `app.py` (InstalledLicense), `onprem.py`; Create `migrations/versions/e1f4a5b6c7d8_add_installed_license_revision.py`; Test `tests/test_onprem_runtime_extras.py`.

**Interfaces:**
- `InstalledLicense.revision` Integer not null default 0 (guarded migration, down_revision = current head `d9e3f4a5b6c7` — verify).
- Every write that changes license meaning (`store_license`, refresh outcomes that set `revoked`, `last_refresh_error` does NOT count) increments `revision`.
- `current_state()` cache entry becomes `(timestamp, revision, state)`; a cached state is reused only if `< 60 s` old **and** the row's current `revision` equals the cached one. Reading the revision is one cheap query (`db.session.query(InstalledLicense.revision).filter_by(id=1).scalar()`), done under `no_autoflush`. No row → revision `-1`.
- `onprem.update_status() -> dict|None`: if `ONPREM_STATE_FILE` (env or app.config) points to a readable JSON file, return `{"current", "previous", "last_update"}` from it (missing keys → None); unreadable/invalid → None (log at debug). `system_info()` adds `"update": update_status()` when onprem (saas: key absent or None).
- Onprem `system_info()["app_version"]` stays `APP_VERSION`.

- [ ] **Step 1: Failing tests** (`tests/test_onprem_runtime_extras.py`, copy the `onprem` fixture + `_text` helper pattern from `tests/test_onprem_license.py`):
  1. Store a valid license → `current_state().state == "valid"`; then simulate **another worker** changing the row (update `license_text` to a license for another machine and `revision = revision + 1` via a raw `db.session.execute(update(...))` + commit, without calling `invalidate()`) → next `current_state()` (still within 60 s) reports `machine_mismatch`.
  2. Same as 1 but without bumping revision → cached `valid` is still returned (proves the cache still works).
  3. `store_license` increments revision (0 → 1 → 2 on two stores).
  4. `update_status()` with `ONPREM_STATE_FILE` pointing to a tmp file `{"current":"1.2.3","previous":"1.2.2","last_update":{"status":"ok","target":"1.2.3","at":"2026-10-01T03:31:00Z","message":""}}` → returned as-is; `/api/system/info` in onprem includes it under `update`.
  5. Missing file / invalid JSON → `update` is None, no exception.
- [ ] **Step 2** run → FAIL. **Step 3** implement. **Step 4** focused + full suite → PASS.
- [ ] **Step 5 commit** `feat(onprem): cross-worker license cache and update status`

---

### Task 2: SaaS — releases API and installer download

**Files:** Modify `app.py` (model), `license_server_routes.py` (or new `release_routes.py` registered the same way — prefer new file); Create migration `f2a5b6c7d8e9_add_release.py`; Modify `config.py`; Test `tests/test_releases.py`.

**Interfaces:**
- Model `Release` (`__tablename__="release"`): `id` Integer PK, `version` String(20) unique not null, `release_date` String(10) not null (ISO date), `notes` Text nullable, `min_upgrade_from` String(20) nullable, `created_at` DateTime. Guarded migration, down_revision `e1f4a5b6c7d8`.
- `Config.RELEASE_PUBLISH_SECRET = os.environ.get("RELEASE_PUBLISH_SECRET")`, `Config.INSTALLER_URL = os.environ.get("INSTALLER_URL")` (read at call time via app.config/env so tests can set them).
- `POST /api/internal/releases` header `X-Release-Secret` (hmac.compare_digest; unset secret → 503; wrong → 401). Body `{version, release_date, notes?, min_upgrade_from?}`; `version` and `min_upgrade_from` must match `^\d+\.\d+\.\d+$`; `release_date` ISO date; upsert by version → 201 (new) / 200 (updated) with the row dict.
- `GET /api/updates/latest` (public, `@limiter.limit("60 per minute")`) → 200 `{version, release_date, notes, min_upgrade_from}` of the **highest SemVer** (compare numerically, not by string or created_at); 404 `{"error":"no_release"}` when none.
- `GET /download` (public) → 302 to `INSTALLER_URL`; 503 `{"error":"installer not available"}` if unset. Must not be swallowed by the SPA catch-all route — register before it or verify ordering with a test.
- These are SaaS-only: `/api/internal/` is already 404 on-prem (Task 2 of sub-project 2). `/api/updates/latest` and `/download` are also meaningless on-prem → add `"/api/updates/"` and exact `"/download"` to the on-prem blocked list in `onprem.py` (`ONPREM_BLOCKED`); update the deployment-mode tests accordingly.

- [ ] **Step 1: Failing tests**: publish without secret configured → 503; wrong secret → 401; bad version `1.2` → 400; publish `1.2.0`, `1.10.0`, `1.9.3` → latest is `1.10.0`; republish `1.10.0` with new notes → 200 and notes updated; `/download` with and without `INSTALLER_URL`; on-prem mode `/api/updates/latest` → 404.
- [ ] **Step 2** RED. **Step 3** implement. **Step 4** focused + full suite GREEN.
- [ ] **Step 5 commit** `feat(releases): release metadata API and installer download redirect`

---

### Task 3: Frontend — update status banner (on-prem)

**Files:** Modify `frontend/src/components/LicenseBanner.js` (or create `UpdateBanner.js` rendered next to it), `frontend/src/utils/licenseStatus.js` (+ test).

**Interfaces:** consumes `systemInfo.update` from Task 1.
- Pure helper `updateNotice(update, dismissedKey) -> {severity: 'success'|'error', text, key} | null`:
  - `last_update.status === 'ok'` and `target === current` → success "ServiceBills was updated to v{current}."
  - `status === 'failed'` → error "Update to v{target} failed — still running v{current}. {message}" (message omitted if empty).
  - `skipped` / null → null.
  - `key` = `${status}:${target}:${at}`; if equal to `dismissedKey` → null.
- Banner (admins only, onprem only) shows the notice with a close button; dismissal stored in `localStorage['sb:update-notice-dismissed']` (wrap in try/catch).
- [ ] **Step 1** failing Jest tests for `updateNotice` (ok, failed with/without message, skipped, null, dismissed). **Step 2** RED. **Step 3** implement. **Step 4** full frontend suite + build GREEN; restore build dir.
- [ ] **Step 5 commit** `feat(onprem): update status banner`

---

### Task 4: `onprem/docker-compose.yml`, env template, compose smoke test in CI

**Files:** Create `onprem/docker-compose.yml`, `onprem/env.template`, `.github/workflows/onprem-smoke.yml`, `onprem/README.md` (developer notes: how the pieces fit).

- `onprem/docker-compose.yml` (project name via `name: servicebills`):

```yaml
name: servicebills
services:
  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_USER: servicebills
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: servicebills
      TZ: ${TZ:-UTC}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U servicebills"]
      interval: 5s
      timeout: 3s
      retries: 20
  web:
    image: ghcr.io/hasbach/servicebills:${IMAGE_TAG:-latest}
    restart: unless-stopped
    env_file: .env
    environment:
      DATABASE_URL: postgresql+psycopg2://servicebills:${POSTGRES_PASSWORD}@db:5432/servicebills
      DEPLOYMENT_MODE: onprem
      DISABLE_AUTO_CREATE_ALL: "1"
      WEB_CONCURRENCY: "3"
      STORAGE_BACKEND: local
      UPLOAD_FOLDER: /app/uploads
      ONPREM_STATE_FILE: /onprem/state.json
      PORT: "8000"
    ports:
      - "8000:8000"
    volumes:
      - uploads:/app/uploads
      - ./state.json:/onprem/state.json:ro
    depends_on:
      db:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5).status == 200 else 1)\""]
      interval: 10s
      timeout: 6s
      retries: 30
      start_period: 60s
  scheduler:
    image: ghcr.io/hasbach/servicebills:${IMAGE_TAG:-latest}
    restart: unless-stopped
    env_file: .env
    environment:
      DATABASE_URL: postgresql+psycopg2://servicebills:${POSTGRES_PASSWORD}@db:5432/servicebills
      DEPLOYMENT_MODE: onprem
      DISABLE_AUTO_CREATE_ALL: "1"
      STORAGE_BACKEND: local
      UPLOAD_FOLDER: /app/uploads
    command: ["flask", "run-scheduler"]
    volumes:
      - uploads:/app/uploads
    depends_on:
      web:
        condition: service_healthy
volumes:
  pgdata:
  uploads:
```

  Verify the image's CMD honours `PORT` and `WEB_CONCURRENCY` (Dockerfile: `gunicorn -w ${WEB_CONCURRENCY:-1} -k gevent -b 0.0.0.0:${PORT:-8000}`) and that `flask run-scheduler` works with `FLASK_APP=app.py` already set in the image.
- `onprem/env.template`: every `.env` key from Global Constraints with a placeholder and a one-line comment (used by docs and by the installer as the key list).
- `.github/workflows/onprem-smoke.yml` (on PRs touching `onprem/**`, `Dockerfile`, `onprem.py`, `license*.py`, and `workflow_dispatch`): build the image locally tagged `ghcr.io/hasbach/servicebills:smoke`; write a `.env` with random secrets, `MACHINE_ID=ci-machine`, `TZ=UTC`, `IMAGE_TAG=smoke`, `APP_BASE_URL=http://localhost:8000`; `echo '{}' > onprem/state.json`; `docker compose -f onprem/docker-compose.yml --env-file onprem/.env up -d` (copy `.env` next to the compose file); poll `http://localhost:8000/api/health` up to 5 min; assert `GET /api/setup/status` → `setup_required: true` and `GET /api/system/info` → `deployment_mode: onprem`; assert the scheduler container is running (`docker compose ps --status running scheduler`); print logs on failure; `down -v`.
- No local run possible without Docker; validate YAML with `python -c "import yaml,sys; yaml.safe_load(open('onprem/docker-compose.yml'))"` and the workflow with the same.

- [ ] **Step 1** write files. **Step 2** YAML validation passes. **Step 3 commit** `feat(onprem): compose stack and CI smoke test`

---

### Task 5: `common.ps1` pure functions + Pester tests

**Files:** Create `onprem/scripts/common.ps1`, `onprem/tests/common.Tests.ps1`, `onprem/scripts/timezones.json`.

**Functions (exact names; all pure unless noted):**
- `Get-SBRoot` → `C:\ProgramData\ServiceBills` (overridable by env `SERVICEBILLS_ROOT` for tests).
- `Compare-SBVersion([string]$a, [string]$b)` → `-1|0|1` numeric SemVer compare; throws on non-`\d+.\d+.\d+`.
- `Get-SBMachineId([string]$machineGuid)` → lower-case hex SHA-256 of the UTF-8 bytes of the GUID string **as read** (no normalisation); `Read-SBMachineGuid` (side effect: registry read of `HKLM:\SOFTWARE\Microsoft\Cryptography` `MachineGuid`).
- `New-SBSecret([int]$bytes=32)` → urlsafe base64 (no padding) of random bytes; `New-SBFernetKey` → standard urlsafe base64 **with** padding of 32 random bytes (44 chars, Fernet-compatible).
- `ConvertTo-SBIanaTimeZone([string]$windowsId)` → lookup in `timezones.json` (map Windows IDs → IANA; include at least: Middle East Standard Time→Asia/Beirut, GTB Standard Time→Europe/Bucharest, E. Europe Standard Time→Europe/Chisinau, Arab Standard Time→Asia/Riyadh, Arabian Standard Time→Asia/Dubai, Egypt Standard Time→Africa/Cairo, Turkey Standard Time→Europe/Istanbul, Jordan Standard Time→Asia/Amman, Syria Standard Time→Asia/Damascus, Israel Standard Time→Asia/Jerusalem, W. Europe Standard Time→Europe/Berlin, GMT Standard Time→Europe/London, Romance Standard Time→Europe/Paris, Central European Standard Time→Europe/Warsaw, Russian Standard Time→Europe/Moscow, Arabic Standard Time→Asia/Baghdad, Iran Standard Time→Asia/Tehran, Pakistan Standard Time→Asia/Karachi, India Standard Time→Asia/Kolkata, China Standard Time→Asia/Shanghai, Tokyo Standard Time→Asia/Tokyo, AUS Eastern Standard Time→Australia/Sydney, Eastern Standard Time→America/New_York, Central Standard Time→America/Chicago, Mountain Standard Time→America/Denver, Pacific Standard Time→America/Los_Angeles, UTC→UTC, plus any others you know exactly); unknown → `UTC` (caller logs a warning).
- `Merge-SBEnv([hashtable]$existing, [hashtable]$generated)` → ordered hashtable: every existing key/value kept verbatim; generated keys added only if missing; **exception**: `APP_BASE_URL` and `TZ` and `IMAGE_TAG` may be refreshed from `$generated` when provided (LAN IP / timezone can change) — secrets and `MACHINE_ID` never change.
- `ConvertFrom-SBEnvText([string]$text)` / `ConvertTo-SBEnvText([hashtable]$env)` — `KEY=VALUE` lines, `#` comments and blanks ignored on read, values not quoted; round-trip stable; keys sorted on write except preserve insertion order of an ordered hashtable.
- `Get-SBLanIp([object[]]$addresses)` → given objects with `IPAddress`, `InterfaceAlias`, `PrefixOrigin`, pick the first private IPv4 (10/8, 172.16/12, 192.168/16) not on aliases matching `vEthernet|WSL|Docker|Loopback|VirtualBox|VMware`; else `localhost`. `Get-SBLanIpLive` wraps `Get-NetIPAddress -AddressFamily IPv4`.
- `Get-SBBackupsToDelete([string[]]$names, [int]$keep=14)` → names matching `^servicebills-\d{8}-\d{4}\.zip$` sorted newest first by the timestamp in the name; returns those beyond `$keep`. Non-matching names are never returned.
- `Read-SBState([string]$path)` / `Write-SBState([string]$path, [hashtable]$state)` — JSON per Global Constraints; missing file → `@{current=$null; previous=$null; last_update=$null}`; write UTF-8 no BOM, atomic (write `.tmp` then `Move-Item -Force`).
- `New-SBUpdateResult([string]$status, [string]$target, [string]$message)` → hashtable with `at` = current UTC ISO `yyyy-MM-ddTHH:mm:ssZ`.
- `Write-SBLog([string]$name, [string]$message)` (side effect: append `[ISO] message` to `<root>\logs\<name>-yyyyMMdd.log`, creating the folder).
- `Test-SBVirtualization` (side effect) → `$true` if any `Win32_Processor.VirtualizationFirmwareEnabled` is true or `(Get-CimInstance Win32_ComputerSystem).HypervisorPresent` is true.

- [ ] **Step 1: Pester tests** (`onprem/tests/common.Tests.ps1`, `BeforeAll { . $PSScriptRoot/../scripts/common.ps1 }`): version compare (1.10.0 > 1.9.9, equal, throws on `1.2`); machine id of a known GUID equals a hard-coded expected hash (compute it once with Python `hashlib.sha256('<guid>'.encode()).hexdigest()` and paste); Fernet key length 44 and decodes to 32 bytes; secret has no `+/=`; tz map known + unknown→UTC; env merge keeps existing secrets, adds missing, refreshes APP_BASE_URL/TZ/IMAGE_TAG; env text round-trip ignoring comments; LAN IP selection skips vEthernet/WSL and public IPs, falls back to localhost; backup rotation with 16 names + one foreign file → returns the 2 oldest only; state read of missing file defaults, write+read round-trip, no BOM (first bytes not EF BB BF), `.tmp` not left behind.
- [ ] **Step 2** run (portable Pester 5 per Global Constraints) → FAIL. **Step 3** implement. **Step 4** Pester GREEN + PSScriptAnalyzer clean.
- [ ] **Step 5 commit** `feat(onprem): PowerShell common library with Pester tests`

---

### Task 6: `install.ps1`, `start.ps1`, `stop.ps1`, `run-hidden.vbs`

**Files:** Create `onprem/scripts/install.ps1`, `start.ps1`, `stop.ps1`, `run-hidden.vbs`, `onprem/tests/install.Tests.ps1`.

**install.ps1** — parameters: `-BackupDir <path>`, `-AutoUpdate <bool>`, `-GhcrUser <string>`, `-GhcrToken <string>`, `-ImageTag <string>` (default `latest`), `-SourceDir <path>` (folder with compose/env.template/scripts shipped by Setup), `-NoStart` (switch, for tests). Steps, each logged via `Write-SBLog install`:
1. Create root, `logs\`, copy `docker-compose.yml` → `<root>\compose.yml`, copy `scripts\*` → `<root>\scripts\`.
2. Build `.env`: read existing (if any) → `Merge-SBEnv` with generated `{JWT_SECRET_KEY=New-SBSecret; FERNET_KEY=New-SBFernetKey; POSTGRES_PASSWORD=New-SBSecret 24; MACHINE_ID=Get-SBMachineId (Read-SBMachineGuid); TZ=ConvertTo-SBIanaTimeZone (Get-TimeZone).Id; APP_BASE_URL="http://$(Get-SBLanIpLive):8000"; IMAGE_TAG=$ImageTag}` → write.
3. `state.json`: create with `current=$ImageTag` if missing (`latest` resolved later by update.ps1; store the literal).
4. Save backup dir + auto-update choice in `<root>\settings.json` (`{"backup_dir": ..., "auto_update": true}`).
5. Unless `-NoStart`: wait for Docker engine (`docker info` succeeds; start `"$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"` if not running; poll up to 3 min); `docker login ghcr.io -u $GhcrUser --password-stdin` (token via stdin, never on the command line/log); `docker compose -f compose.yml --env-file .env pull`; `up -d`; poll `http://localhost:8000/api/health` up to 5 min.
6. Firewall: `New-NetFirewallRule -DisplayName "ServiceBills (TCP 8000)" -Direction Inbound -Protocol TCP -LocalPort 8000 -Profile Private -Action Allow` if a rule with that DisplayName doesn't exist.
7. Scheduled tasks via `Register-ScheduledTask` (current user, RunLevel Highest, `-LogonType Interactive`): `ServiceBills Backup` 01:30 daily → `wscript.exe "<root>\scripts\run-hidden.vbs" backup.ps1 -Quiet`; `ServiceBills Updater` 03:30 daily → `update.ps1 -Quiet` only if AutoUpdate (unregister if false). Replace existing tasks of the same name.
8. Exit code 0 on success; on failure write the error to the log and `exit 1` with a one-line human message on stderr (Setup shows it).

**start.ps1**: ensure Docker engine up (as above, with a small "Starting ServiceBills…" message box that closes itself is optional — simplest: no UI until done), `docker compose ... up -d`, wait for health (2 min), open `http://localhost:8000` via `Start-Process`. On failure: message box "ServiceBills could not start. Open the log folder?" Yes → `explorer.exe <root>\logs`.

**stop.ps1**: `docker compose ... stop`; message box "ServiceBills stopped."

**run-hidden.vbs**: runs `powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "<root>\scripts\<arg0>" <remaining args>` with window style 0 and waits; exit code passthrough.

Put all docker/registry/network calls behind small wrapper functions in `common.ps1` (e.g. `Invoke-SBDocker [string[]]$args`, `Wait-SBHealth [int]$seconds`, `Test-SBDockerEngine`) so Pester can mock them.

- [ ] **Step 1: Pester** `install.Tests.ps1` with `SERVICEBILLS_ROOT` = TestDrive, all docker/firewall/scheduled-task/registry cmdlets mocked: fresh install writes `.env` with all keys and correct MACHINE_ID for a mocked GUID; reinstall keeps existing JWT_SECRET_KEY/FERNET_KEY/POSTGRES_PASSWORD/MACHINE_ID but refreshes APP_BASE_URL; `-AutoUpdate $false` unregisters the updater task and registers backup; docker login receives the token via stdin (assert the mocked call's arguments don't contain the token); failing health wait → exit code 1 and log contains the error.
- [ ] **Step 2** RED. **Step 3** implement. **Step 4** Pester GREEN + ScriptAnalyzer clean.
- [ ] **Step 5 commit** `feat(onprem): install, start and stop scripts`

---

### Task 7: `backup.ps1`, `restore.ps1`, `update.ps1`, `uninstall.ps1`

**Files:** Create those four scripts + `onprem/tests/ops.Tests.ps1`.

**backup.ps1** `[-Quiet] [-Destination <dir>]` (default from `settings.json.backup_dir`; create if missing). Returns the zip path (and writes it to the log). Steps: temp folder; `docker compose exec -T db sh -c "pg_dump -U servicebills -Fc -f /tmp/sb.dump servicebills"`; `docker compose cp db:/tmp/sb.dump <tmp>\db.dump`; `docker compose exec -T db rm -f /tmp/sb.dump`; `docker compose cp web:/app/uploads <tmp>\uploads`; `Compress-Archive <tmp>\* <dest>\servicebills-yyyyMMdd-HHmm.zip`; delete temp; rotation via `Get-SBBackupsToDelete`. Non-quiet → message box "Backup saved: <path>". Failure → exit 1 (+ message box unless quiet).

**restore.ps1** (interactive only): list zips in the backup dir newest first in a simple WinForms `ListBox` dialog (date formatted from the name); confirm "This replaces all current ServiceBills data with the backup from <date>. Continue?"; take a safety backup first (`backup.ps1 -Quiet`); `Expand-Archive` to temp; `docker compose stop web scheduler`; `docker compose cp <tmp>\db.dump db:/tmp/sb.dump`; `docker compose exec -T db sh -c "pg_restore -U servicebills -d servicebills --clean --if-exists --no-owner /tmp/sb.dump"`; replace uploads: `docker compose run --rm --no-deps -v servicebills_uploads:/data web sh -c "rm -rf /data/* && true"` then `docker compose cp <tmp>\uploads\. web:/app/uploads` after `docker compose start web` (cp needs a container; alternatively start web first then cp then restart) — pick an order that works and document it in comments; `docker compose up -d`; wait health; message box result.

**update.ps1** `[-Quiet] [-Force]`:
1. Read `settings.json`, `state.json`, `.env` (`IMAGE_TAG`).
2. `GET http://localhost:8000/api/system/info` → if `license.state -eq 'readonly'` and `license.reason -in 'expired','revoked','version_not_covered'` → record `skipped` ("License not valid for updates"), exit 0.
3. `GET {LICENSE_SERVER_URL or https://servicebills.onrender.com}/api/updates/latest` (timeout 30 s; failure → `skipped` "Could not reach update server", exit 0).
4. Determine current version: `state.current`; if it is `latest` or null, use `system_info.app_version` if SemVer else treat as `0.0.0`.
5. If `Compare-SBVersion latest current` ≤ 0 and not `-Force` → nothing (don't overwrite last_update), exit 0. If `min_upgrade_from` and current < it → `skipped` "Please reinstall with the latest ServiceBills Setup", exit 0.
6. `backup.ps1 -Quiet`; failure → `failed` "Backup failed, update not attempted", exit 1.
7. `IMAGE_TAG=<latest>` in `.env` (via Merge/Write, only this key changes); `docker compose pull web scheduler`; `docker compose up -d`; `Wait-SBHealth 300`.
8. Success → state `{current=<latest>, previous=<old>, last_update=ok}`.
9. Failure → set `IMAGE_TAG=<old>`, restore the pre-update backup with the same steps as restore.ps1 (factor the non-interactive core into a function `Restore-SBBackup <zip>` in common.ps1 or a shared `ops.ps1` that both scripts dot-source), `up -d`, wait health, state `{current=<old>, previous=<old previous>, last_update=failed "<reason>"}`, exit 1.
Non-quiet runs show a message box with the outcome.

**uninstall.ps1** `[-DeleteData]` (called by Inno uninstaller): `docker compose down` (with `-v` only if `-DeleteData`); unregister both scheduled tasks; remove firewall rule; remove `<root>` except `logs\` and the backup folder (backups always kept unless `-DeleteData`, in which case also delete the backup folder **only if** it is the default `Documents\ServiceBills Backups`; never delete a user-chosen folder like OneDrive — just leave it). Docker Desktop untouched.

- [ ] **Step 1: Pester** `ops.Tests.ps1` (mock `Invoke-SBDocker`, `Invoke-RestMethod`, `Wait-SBHealth`, message boxes, `Compress-Archive`/`Expand-Archive` where needed):
  - backup: builds the expected docker call sequence; names the zip by timestamp; rotation deletes only beyond 14.
  - update: no newer version → state unchanged; newer → backup → pull → up → ok state with previous; health failure → IMAGE_TAG reverted, restore invoked with the pre-update zip, failed state, exit 1; license readonly/expired → skipped without calling the update server; min_upgrade_from > current → skipped with reinstall message; update server unreachable → skipped.
  - uninstall: default keeps volumes and backups; `-DeleteData` passes `-v` and deletes default backup dir but not a custom one.
- [ ] **Step 2** RED. **Step 3** implement. **Step 4** Pester GREEN + ScriptAnalyzer clean.
- [ ] **Step 5 commit** `feat(onprem): backup, restore, update with rollback, uninstall`

---

### Task 8: Inno Setup installer

**Files:** Create `onprem/installer/ServiceBills.iss`, `onprem/installer/README.md` (how to build locally), `onprem/installer/assets/` (reuse `frontend/public/serviceBillsLogo.png` → convert to `.ico` at build time is optional; if no .ico, omit `SetupIconFile`).

Requirements:
- `AppName=ServiceBills`, `AppVersion={#AppVersion}` (passed by `/DAppVersion=`), `AppPublisher=ServiceBills`, `DefaultDirName={commonappdata}\ServiceBills` (fixed; `DisableDirPage=yes`), `PrivilegesRequired=admin`, `ArchitecturesAllowed=x64compatible`, `ArchitecturesInstallIn64BitMode=x64compatible`, `MinVersion=10.0.19045`, `OutputBaseFilename=ServiceBills-Setup`, `WizardStyle=modern`, `UninstallDisplayName=ServiceBills`.
- `[Files]`: `..\docker-compose.yml`, `..\env.template`, `..\scripts\*` → `{app}\setup-src\...`.
- Defines `{#GhcrUser}` and `{#GhcrToken}` from `/D` build parameters (CI secrets); they are passed to install.ps1 as arguments — never written to disk by the installer itself.
- `[Code]` (Pascal Script):
  1. **InitializeSetup**: RAM check via `GlobalMemoryStatusEx` (warn < 8 GB, continue allowed); free disk ≥ 20 GB on the system drive (block); virtualization check by running `powershell -NoProfile -Command "(Get-CimInstance Win32_Processor).VirtualizationFirmwareEnabled -contains $true -or (Get-CimInstance Win32_ComputerSystem).HypervisorPresent"` and reading its exit/output → if false: message with plain-language BIOS instructions ("Restart the PC, open BIOS/UEFI setup (usually F2, F10, Del or Esc at startup), enable 'Intel VT-x' / 'AMD-V' / 'SVM' / 'Virtualization Technology', save and restart, then run this Setup again.") and abort.
  2. **Docker page**: detect Docker Desktop (`{commonpf64}\Docker\Docker\Docker Desktop.exe` exists). If missing: a wizard page explaining Docker Desktop and linking its license terms (https://www.docker.com/legal/docker-subscription-service-agreement/) with a required "I accept Docker's terms" checkbox; on Next, download `https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe` with `DownloadTemporaryFile` (Inno 6.1+) showing progress, run it with `install --quiet --accept-license --backend=wsl-2`, and if its exit code is 3010 or WSL requires reboot: register `HKLM\...\RunOnce` `ServiceBillsSetup` = `"{srcexe}" /RESUME` and ask the user to restart (Inno `NeedRestart` → true). On `/RESUME`, skip straight to the options page.
  3. **Options page**: backup folder (default: first existing of `{userdocs}\..\OneDrive\ServiceBills Backups` parent `OneDrive`, `G:\My Drive` (Google Drive), else `{userdocs}\ServiceBills Backups`) with Browse; checkbox "Keep ServiceBills up to date automatically (recommended)" checked.
  4. **Install step** (`CurStepChanged(ssPostInstall)`): run `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{app}\setup-src\scripts\install.ps1" -SourceDir "{app}\setup-src" -BackupDir "<chosen>" -AutoUpdate <bool> -GhcrUser {#GhcrUser} -GhcrToken {#GhcrToken} -ImageTag {#AppVersion}` hidden, with a status label "Starting ServiceBills — this can take several minutes the first time…"; on non-zero exit show the stderr line + "Open log folder" (runs `explorer {commonappdata}\ServiceBills\logs`).
  5. **Finish page**: checkbox "Open ServiceBills now" (runs `start.ps1` via run-hidden.vbs) and a label with the LAN address read from `{commonappdata}\ServiceBills\.env` `APP_BASE_URL`.
- `[Icons]`: Desktop "ServiceBills" → `wscript.exe` `"{app}\scripts\run-hidden.vbs" start.ps1`; Start menu group "ServiceBills": Backup now (`backup.ps1`), Restore backup (`restore.ps1`), Check for updates (`update.ps1`), Stop ServiceBills (`stop.ps1`), Uninstall ServiceBills (`{uninstallexe}`).
- `[UninstallRun]` / `CurUninstallStepChanged`: ask "Also delete all ServiceBills data (customers, payments, backups)? This cannot be undone." (default No; if Yes ask a second confirmation) → run `uninstall.ps1` with or without `-DeleteData`.
- Local build: no Inno on this PC is fine — the script is compiled in CI (Task 9). If you can, install nothing; instead validate by careful review and keep the Pascal code minimal.

- [ ] **Step 1** write the `.iss` + README. **Step 2** self-review against the list above (no compiler available locally; CI compiles it). **Step 3 commit** `feat(onprem): Inno Setup installer`

---

### Task 9: Release workflow, PowerShell CI, docs

**Files:** Create `.github/workflows/release.yml`, `.github/workflows/onprem-scripts.yml`, `onprem/docs/whatsapp-public-url.md`, `onprem/docs/release-checklist.md`, `onprem/docs/owner-guide.md`.

- `onprem-scripts.yml` (PRs touching `onprem/**`): `windows-latest`; `Install-Module Pester -RequiredVersion 5.6.1 -Force -Scope CurrentUser`, `Install-Module PSScriptAnalyzer -Force -Scope CurrentUser`; run Pester (fail on failures) and ScriptAnalyzer (fail on Warning/Error); install Inno Setup (`choco install innosetup -y`) and compile `onprem/installer/ServiceBills.iss` with dummy `/DAppVersion=0.0.0 /DGhcrUser=x /DGhcrToken=x` to prove it compiles; upload nothing.
- `release.yml` (on push tags `v*.*.*`):
  1. `version` = tag without `v`; `release_date` = UTC date.
  2. Job `image` (ubuntu): `docker/login-action` to ghcr with `GITHUB_TOKEN` (`packages: write`); `docker/build-push-action` with `build-args: APP_VERSION=${version} APP_RELEASE_DATE=${release_date}`, tags `ghcr.io/hasbach/servicebills:${version}` and `:latest`.
  3. Job `installer` (windows, needs image): checkout; `choco install innosetup -y`; `ISCC.exe /DAppVersion=${version} /DGhcrUser=${{ secrets.GHCR_PULL_USER }} /DGhcrToken=${{ secrets.GHCR_PULL_TOKEN }} onprem\installer\ServiceBills.iss`; upload `ServiceBills-Setup.exe` to R2 with the AWS CLI (`aws s3 cp ... s3://${{ secrets.R2_BUCKET }}/installer/ServiceBills-Setup-${version}.exe --endpoint-url ${{ secrets.R2_ENDPOINT }}`, and also as `installer/ServiceBills-Setup.exe` for the stable link) using `R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY` secrets; attach the exe to the GitHub Release too.
  4. Job `publish` (needs installer): `curl -fsS -X POST https://servicebills.onrender.com/api/internal/releases -H "X-Release-Secret: ${{ secrets.RELEASE_PUBLISH_SECRET }}" -H "Content-Type: application/json" -d '{"version":"...","release_date":"...","notes":"<tag message or empty>"}'`.
  5. Required secrets documented at the top of the file in a comment: `GHCR_PULL_USER`, `GHCR_PULL_TOKEN` (classic PAT, `read:packages` only, from a bot account), `R2_BUCKET`, `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `RELEASE_PUBLISH_SECRET` (same value as the Render env var). Render needs `RELEASE_PUBLISH_SECRET` and `INSTALLER_URL` (= public URL of `installer/ServiceBills-Setup.exe`).
- `onprem/docs/whatsapp-public-url.md`: the same plain-language steps as the in-app help dialog.
- `onprem/docs/release-checklist.md`: the manual clean-Windows-11-VM checklist from the spec (fresh install incl. Docker Desktop, trial, LAN access, backup/restore, update, forced-failure rollback by publishing a deliberately broken tag to a test channel, uninstall keep/delete) plus "how to cut a release" (`git tag v1.0.0 && git push origin v1.0.0`).
- `onprem/docs/owner-guide.md`: one page for the business owner — requirements, install, first-run setup, where backups go, how updates happen, known limits (Docker Desktop needs a logged-in user; SmartScreen "unknown publisher" → More info → Run anyway; WhatsApp needs own public URL).
- Validate both workflows parse (`python -c "import yaml; yaml.safe_load(open(...))"`).

- [ ] **Step 1** write files. **Step 2** YAML validation. **Step 3 commit** `ci(onprem): release pipeline, script CI and docs`

---

## Final verification (orchestrator)

- Backend + frontend suites green; Pester + ScriptAnalyzer green (portable modules).
- Push the branch and let `onprem-scripts.yml` and `onprem-smoke.yml` run on the PR — both must pass (real Windows Pester/Inno compile; real Linux compose boot of the on-prem stack).
- Owner actions before the first real release: create the GHCR pull token + bot account, add the GitHub secrets, set `RELEASE_PUBLISH_SECRET` and `INSTALLER_URL` on Render, make the R2 installer object publicly readable, then run the manual VM checklist.

# ServiceBills installer (Inno Setup)

`ServiceBills.iss` builds `ServiceBills-Setup.exe`. It needs Inno Setup 6.3 or newer
(`ArchitecturesAllowed=x64compatible`, `CreateDownloadPage`). CI builds it in the release workflow.

## Build locally

```
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" ^
  /DAppVersion=1.2.3 /DGhcrUser=<github-user> /DGhcrToken=<read-only-packages-token> ^
  onprem\installer\ServiceBills.iss
```

Output: `onprem\installer\Output\ServiceBills-Setup.exe`.

- `AppVersion` must be a SemVer `MAJOR.MINOR.PATCH`; it is also the Docker image tag.
- `GhcrUser` / `GhcrToken` are a read-only GHCR pull credential. They are compiled into the setup binary
  and passed to `install.ps1` as arguments; the installer never writes them to disk. Use a token with
  `read:packages` only.
- The script ships `onprem\docker-compose.yml`, `onprem\env.template` and `onprem\scripts\*` to
  `{commonappdata}\ServiceBills\setup-src`, then runs `setup-src\scripts\install.ps1`.

## Install flow

1. Checks: RAM (warns under 8 GB), 20 GB free on the system drive, CPU virtualization enabled.
2. Docker Desktop page (only if not installed): license acceptance, download, silent install.
   If Windows needs a restart, a `RunOnce` entry re-launches Setup with `/RESUME` after sign-in.
3. Backup folder and auto-update options.
4. `install.ps1` runs hidden; a failure shows its one-line error and offers to open the log folder
   (`C:\ProgramData\ServiceBills\logs`).
5. Finish page shows the LAN address from `.env` and offers to open ServiceBills.

Uninstall asks whether to also delete all data (with a second confirmation) and runs `uninstall.ps1`
with or without `-DeleteData`.

There is no `SetupIconFile`; add `assets\ServiceBills.ico` and a `SetupIconFile=` line to brand it.

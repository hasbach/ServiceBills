# ServiceBills release checklist

## How to cut a release

1. Make sure everything is merged to `main` and the "Auto Build Frontend Bundle" workflow has finished (it commits `frontend/build`; the image is built from it). Pull the latest `main`.
2. Tag and push (use an annotated tag so its message becomes the release notes):

   ```
   git tag -a v1.0.0 -m "Release notes shown to customers"
   git push origin v1.0.0
   ```

3. Watch the **Release** workflow: image pushed to `ghcr.io/hasbach/servicebills:1.0.0` and `:latest`, installer built and uploaded to R2 (`installer/ServiceBills-Setup-1.0.0.exe` and the stable `installer/ServiceBills-Setup.exe`), release metadata published to servicebills.onrender.com.
4. Confirm `INSTALLER_URL` on Render still points at the stable installer object.

One-time owner setup before the first release: create the bot GitHub account and a classic PAT with `read:packages` only; add the GitHub secrets listed at the top of `.github/workflows/release.yml`; set `RELEASE_PUBLISH_SECRET` and `INSTALLER_URL` on Render; make the R2 installer object publicly readable. **Grant the GHCR bot account read access to the `servicebills` package** (package settings > Manage access), otherwise customers' `docker pull` fails with "denied".

## Manual verification on a clean Windows 11 VM

Run before announcing any release that changes the installer or scripts. Use a fresh VM snapshot with virtualization enabled and at least 8 GB RAM, no Docker Desktop.

- [ ] **Fresh install incl. Docker Desktop**: run `ServiceBills-Setup.exe` (SmartScreen: More info, Run anyway). Docker Desktop is downloaded and installed; if a restart is requested, the installer resumes after reboot. Installation finishes with the LAN address.
- [ ] **Trial**: open the address, complete first-run setup, confirm the trial license is active and the app works.
- [ ] **LAN access**: from another device on the network open `http://<lan-ip>:8000`; login works.
- [ ] **Backup**: run "ServiceBills Backup" from the Start menu; `servicebills-YYYYMMDD-HHMM.zip` appears in the backup folder containing `db.dump` and `uploads\`.
- [ ] **Restore**: change some data, restore that zip via the Restore shortcut, confirm the data returns.
- [ ] **Update**: publish a newer tag (e.g. `v1.0.1`), run "ServiceBills Update"; app comes back on the new version, `state/state.json` shows `current`/`previous`, and the Settings page shows the new version.
- [ ] **Forced-failure rollback**: publish a deliberately broken tag (image that fails its healthcheck) to a test channel / test release record; run the update; confirm it rolls back to the previous version automatically, data is intact, and `last_update.status` is `failed`.
- [ ] **Uninstall, keep data**: uninstall answering "keep my data"; containers removed, `C:\ProgramData\ServiceBills` data and backups remain; reinstalling picks the data up.
- [ ] **Uninstall, delete data**: uninstall with the delete option and second confirmation; volumes and `C:\ProgramData\ServiceBills` are gone.

## Only a real Windows / Docker run can confirm these

None of the following can be proven by unit tests or CI; check them on the clean VM (and, for the laptop items, on a real laptop) before trusting a release.

- [ ] **Docker installer exit code**: the Docker Desktop installer returns `3010` (restart needed) vs `0` (done). Confirm Setup handles both: 3010 shows the restart notice and resumes; 0 continues straight on.
- [ ] **RunOnce launches an elevated exe**: after the 3010 restart, confirm `HKLM\...\RunOnce\ServiceBillsSetup` really starts Setup with `/RESUME` and it gets elevation. If it does not fire, the fallback is an at-logon scheduled task that launches Setup.
- [ ] **First Docker Desktop start time**: measure how long the first start takes on the VM; the 180 s engine wait in `install.ps1` must be enough (otherwise raise it).
- [ ] **docker-users group / sign-out-in**: confirm the installing user can talk to Docker straight after install, or whether Windows needs a sign-out/in (membership of the `docker-users` group); document what the customer must do.
- [ ] **`docker compose cp` semantics**: Windows host paths, copying `uploads\.` into a **stopped** container, and copying out of `web:/app/uploads`; check file ownership/permissions inside the container after a restore (the app must still be able to write uploads).
- [ ] **Update banner after an update**: after an update the Settings page shows the new version and the update status (read from the mounted `state` folder, which the updater replaces atomically).
- [ ] **Non-elevated shortcuts after the nightly tasks ran**: after the 01:30 backup and 03:30 update have run (elevated, as the user), the Start menu shortcuts (Start, Backup now, Restore, Check for updates) still work without elevation: log files, `state\state.json` and `.env` remain writable/readable for the interactive user.
- [ ] **Forced-failure rollback with a foreign key**: publish a broken release whose migration adds a table with a foreign key, then fails health; confirm the rollback drops and recreates the database, restores the pre-update data cleanly (`pg_restore --exit-on-error`), and the old version comes back.
- [ ] **Uninstall keep-data, then reinstall**: uninstall answering "keep my data", reinstall the same or a newer Setup; login still works and saved passwords (e.g. stored ISP credentials) still decrypt, because `.env` (with the keys) survived.
- [ ] **Laptop on battery / sleeping through 01:30**: with the PC on battery and asleep at 01:30, the backup runs when it wakes (StartWhenAvailable), is not skipped on battery, and Docker Desktop is started by the task if it was not running.

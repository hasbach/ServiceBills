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

One-time owner setup before the first release: create the bot GitHub account and a classic PAT with `read:packages` only; add the GitHub secrets listed at the top of `.github/workflows/release.yml`; set `RELEASE_PUBLISH_SECRET` and `INSTALLER_URL` on Render; make the R2 installer object publicly readable.

## Manual verification on a clean Windows 11 VM

Run before announcing any release that changes the installer or scripts. Use a fresh VM snapshot with virtualization enabled and at least 8 GB RAM, no Docker Desktop.

- [ ] **Fresh install incl. Docker Desktop**: run `ServiceBills-Setup.exe` (SmartScreen: More info, Run anyway). Docker Desktop is downloaded and installed; if a restart is requested, the installer resumes after reboot. Installation finishes with the LAN address.
- [ ] **Trial**: open the address, complete first-run setup, confirm the trial license is active and the app works.
- [ ] **LAN access**: from another device on the network open `http://<lan-ip>:8000`; login works.
- [ ] **Backup**: run "ServiceBills Backup" from the Start menu; `servicebills-YYYYMMDD-HHMM.zip` appears in the backup folder containing `db.dump` and `uploads\`.
- [ ] **Restore**: change some data, restore that zip via the Restore shortcut, confirm the data returns.
- [ ] **Update**: publish a newer tag (e.g. `v1.0.1`), run "ServiceBills Update"; app comes back on the new version, `state.json` shows `current`/`previous`, and the Settings page shows the new version.
- [ ] **Forced-failure rollback**: publish a deliberately broken tag (image that fails its healthcheck) to a test channel / test release record; run the update; confirm it rolls back to the previous version automatically, data is intact, and `last_update.status` is `failed`.
- [ ] **Uninstall, keep data**: uninstall answering "keep my data"; containers removed, `C:\ProgramData\ServiceBills` data and backups remain; reinstalling picks the data up.
- [ ] **Uninstall, delete data**: uninstall with the delete option and second confirmation; volumes and `C:\ProgramData\ServiceBills` are gone.

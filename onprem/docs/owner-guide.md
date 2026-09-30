# ServiceBills on your own PC: owner guide

## What you need

- Windows 11 (or Windows 10 64-bit), 8 GB RAM or more, at least 20 GB free disk space.
- Virtualization enabled in the BIOS (the installer checks this and tells you if it is off).
- Internet access during installation and updates.
- A PC that stays on and logged in (see known limits).

## Install

1. Download `ServiceBills-Setup.exe` and double-click it.
2. Windows may show "Windows protected your PC / unknown publisher". Click **More info**, then **Run anyway**.
3. If Docker Desktop is not installed, the installer downloads and installs it for you. Accept its terms when asked. A restart may be needed; the installer continues by itself afterwards.
4. Choose where backups go (default: your OneDrive / Google Drive folder if found, otherwise Documents\ServiceBills Backups) and whether to install updates automatically.
5. When it finishes, it shows the address to use, for example `http://192.168.1.20:8000`.

## First run

Open the address in a browser, create your admin account and complete the setup screens. Other computers and phones on the same network can use the same address.

## Backups

A backup runs every night at 01:30 and is saved as `servicebills-YYYYMMDD-HHMM.zip` in the backup folder you chose. The newest 14 are kept. Use the "ServiceBills Backup" shortcut to back up right now. Because the folder is normally inside OneDrive or Google Drive, your backups are also copied off this PC.

**Backups contain your business data and keys - keep the backup folder private.** Each backup also holds the two keys that protect saved passwords, so anyone who can open the backup can read that data. When you restore a backup that came from a different installation, the Restore shortcut asks whether to restore its keys as well (needed to read the saved passwords in that backup).

## Updates

If you chose automatic updates, the PC checks every night at 03:30. Before updating it makes a backup; if the new version does not start correctly it goes back to the previous version by itself. You can also run "ServiceBills Update" from the Start menu at any time.

## Known limits

- Docker Desktop only runs while a user is logged in to Windows. Keep the PC on and logged in (you can lock the screen); the scheduled backup and update tasks also need that.
- The installer is not code-signed, hence the "unknown publisher" warning (More info, Run anyway).
- WhatsApp needs a public https address for this PC. Follow `whatsapp-public-url.md` (or the help button on the WhatsApp settings page) to set it up.
- ServiceBills is reachable on your local network only unless you set up internet access yourself.

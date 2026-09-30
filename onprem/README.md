# ServiceBills on-prem stack (developer notes)

- `docker-compose.yml` - project `servicebills`: `db` (Postgres 16), `web` (gunicorn on :8000,
  runs `flask db upgrade` at start via the image CMD), `scheduler` (`flask run-scheduler`, same image).
- `env.template` - the `.env` keys. The installer generates `.env` once and never overwrites it.
- `state/state.json` - written by the updater. The `state/` **directory** is mounted read-only into `web`
  at `/onprem` (`ONPREM_STATE_FILE=/onprem/state.json`); the app shows update status from it. A directory
  (not a single file) is mounted so the updater's atomic replace of the file is visible in the container.
  The folder must exist before `up`.
- Installed layout: `C:\ProgramData\ServiceBills\` (`compose.yml`, `.env`, `settings.json`, `state\state.json`, `logs\`, `scripts\`).
- Backups (`servicebills-*.zip`) contain `db.dump`, `uploads\` and `env.keys` (FERNET_KEY and JWT_SECRET_KEY only).
  **Backups contain your business data and keys - keep the backup folder private.**
- Image: `ghcr.io/hasbach/servicebills:${IMAGE_TAG}`; the web healthcheck polls `/api/health`.
- CI: `.github/workflows/onprem-smoke.yml` builds the image and boots this stack on Linux.

Local smoke (needs Docker): copy `env.template` to `.env` with real values, create `state/state.json`
containing `{}`, then `docker compose up -d`.

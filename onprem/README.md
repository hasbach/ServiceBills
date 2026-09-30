# ServiceBills on-prem stack (developer notes)

- `docker-compose.yml` - project `servicebills`: `db` (Postgres 16), `web` (gunicorn on :8000,
  runs `flask db upgrade` at start via the image CMD), `scheduler` (`flask run-scheduler`, same image).
- `env.template` - the `.env` keys. The installer generates `.env` once and never overwrites it.
- `state.json` - written by the updater, mounted read-only into `web` at `/onprem/state.json`
  (`ONPREM_STATE_FILE`); the app shows update status from it. Must exist before `up`.
- Installed layout: `C:\ProgramData\ServiceBills\` (`compose.yml`, `.env`, `state.json`, `logs\`, `scripts\`).
- Image: `ghcr.io/hasbach/servicebills:${IMAGE_TAG}`; the web healthcheck polls `/api/health`.
- CI: `.github/workflows/onprem-smoke.yml` builds the image and boots this stack on Linux.

Local smoke (needs Docker): copy `env.template` to `.env` with real values, create `state.json`
containing `{}`, then `docker compose up -d`.

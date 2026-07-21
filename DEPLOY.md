# servicesBills — Deployment Guide

Two parts: (A) a local production-like **dry-run** to validate everything before
spending on cloud, then (B) **go-live** on Render (Railway/Fly notes included).

---

## A. Local dry-run (Docker Desktop required)

Validates the Postgres migration chain, the full app boot, and the SaaS flows.

1. **Secrets** are already in `.env` (gitignored, dry-run values). Optionally paste
   your Stripe **test-mode** keys into `.env` to exercise billing.
2. **Start it:**
   ```
   docker compose up --build
   ```
   The web container runs `flask db upgrade` (builds the schema on Postgres) then
   gunicorn. Open **http://localhost:8000**.
3. **Verify the flows:**
   - Landing page at `/`, then **Create account** (business + email + user/pass).
   - The verification email prints to the compose logs (MAIL_BACKEND=console) — copy
     the `/verify?token=...` link and open it, then log in.
   - Create a subscription plan + a customer; check payments/receipts.
   - **Billing & Plan** page shows Free; if you set Stripe test keys, "Upgrade to Pro"
     opens Stripe checkout (use test card `4242 4242 4242 4242`).
   - Hit the free customer cap to see the upgrade prompt (temporarily lower it in
     `plans.py` if you don't want to add 50 customers).
4. **(Optional) import your existing SQLite data** into the dry-run Postgres:
   ```
   docker compose exec web sh -c \
     "SQLITE_PATH=/app/instance/database.db DATABASE_URL=$DATABASE_URL python scripts/migrate_sqlite_to_postgres.py"
   ```
   (Only if you copied `instance/database.db` into the image/volume.)
5. **Super-admin console:**
   ```
   docker compose exec -e SA_USERNAME=root -e SA_PASSWORD=changeme -e SA_EMAIL=you@x.com web flask create-superadmin
   ```
   Log in as `root` → the platform admin dashboard.
6. Tear down: `docker compose down` (add `-v` to wipe the Postgres volume).

---

## B. Go-live on Render (Blueprint)

**Prerequisites you create (secrets never go in git):**

1. **Postgres** — use Render's managed Postgres (in the blueprint) OR Supabase.
   For Supabase: copy the connection string into `DATABASE_URL` (the app auto-normalizes
   `postgres://` → `postgresql+psycopg2://`).
2. **Cloudflare R2** — create a bucket; create an R2 API token (access key + secret).
   Note your account's S3 endpoint `https://<accountid>.r2.cloudflarestorage.com`.
3. **Stripe** — create the Pro **Product + recurring Price** (copy the `price_...` id).
   Create a **webhook endpoint** → `https://<your-app>/api/stripe/webhook`, subscribe to
   `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`
   (copy the `whsec_...` signing secret).
4. **Email — SendGrid** (recommended; Render blocks outbound SMTP, confirmed live): create a
   SendGrid account, verify a sender/domain, create an API key (Mail Send scope) →
   `SENDGRID_API_KEY`. Direct SMTP (`MAIL_BACKEND=smtp`) is still available for hosts that
   allow it, but won't work on Render's free tier.
5. **Secrets** — generate:
   ```
   python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"   # FERNET_KEY
   python -c "import secrets;print(secrets.token_hex(32))"                                     # JWT_SECRET_KEY (or let Render generate)
   ```

**Deploy:**

1. Push this repo to GitHub, then in Render: **New → Blueprint**, point at the repo
   (`render.yaml`). It creates the Postgres DB + the web service.
2. Fill the `sync:false` env vars in the dashboard: `FERNET_KEY`, `APP_BASE_URL`
   (your Render URL, e.g. `https://servicesbills-web.onrender.com`), `CORS_ORIGINS`
   (same), `STORAGE_*`/`AWS_*`/`S3_ENDPOINT_URL`, `STRIPE_*`, `SENDGRID_API_KEY`, `MAIL_FROM`.
3. First deploy runs `flask db upgrade` automatically (in `dockerCommand`).
4. **Create the super-admin** — Render **Shell** on the web service:
   ```
   SA_USERNAME=root SA_PASSWORD='...' SA_EMAIL=you@x.com flask create-superadmin
   ```
5. **(Optional) import existing data** — Render Shell:
   ```
   SQLITE_PATH=/app/instance/database.db python scripts/migrate_sqlite_to_postgres.py
   ```
6. **Smoke test:** open `APP_BASE_URL` → sign up, verify (check email), log in,
   create a plan/customer, try an upgrade with a Stripe test card, confirm the
   webhook flips the plan (Stripe dashboard → webhook deliveries should be 200).

**Railway / Fly.io:** same image + env vars. Railway: add a Postgres plugin, deploy
the Dockerfile, set the env vars, override the start command to run
`flask db upgrade` first. Fly: `fly launch` (detects Dockerfile), `fly postgres create`
+ `fly postgres attach`, `fly secrets set ...`, and a release command
`flask db upgrade`.

---

## Scheduler at scale

APScheduler runs **in-process**, so with multiple web workers it would fire the daily
jobs once per worker. The starter blueprint uses **one** web worker with
`RUN_SCHEDULER=1` (fires once). To scale the web tier:
- Set the web service `RUN_SCHEDULER=0` and raise `-w`.
- Add a **1-instance worker** service from the same image with `RUN_SCHEDULER=1`
  (command `gunicorn -w 1 app:app` — it just needs to stay alive).

## Notes
- HTTPS/custom domain: configure on the host; then set `APP_BASE_URL`/`CORS_ORIGINS`
  to the custom domain.
- Rotate the dry-run secrets — do NOT reuse `.env` values in production.
- Uploads before go-live were local files; only new uploads land in R2. Migrate any
  existing logos manually if needed.

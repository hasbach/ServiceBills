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
4. **Whish (Lebanon self-serve Pro plan)** — Set `WHISH_CHANNEL` and `WHISH_SECRET` once
   Whish support issues them for this business's merchant account (not Stripe -- Stripe is
   not used for this market, see docs/superpowers/specs/2026-08-26-whish-self-serve-billing-design.md).
   Until both are set, the self-serve Whish checkout button stays hidden and
   `/api/billing/whish/checkout` is simply unused -- no code change needed when credentials
   do arrive, just set the two env vars and redeploy.
   
   **`APP_BASE_URL` must be the real, working public domain** -- `https://servicebills.salloumservices.com`,
   not the `onrender.com` hostname (confirmed broken/unreachable as this app's primary URL -- see the
   `project-security-hotfix-roadmap` memory's note on this). Whish's payment callback is a browser
   redirect to `{APP_BASE_URL}/api/billing/whish/success`; a wrong `APP_BASE_URL` means paying customers
   land on a broken URL after paying. Verify this in the Render dashboard's environment settings before
   Whish credentials are added -- do not assume `RENDER_EXTERNAL_URL`'s fallback value is correct.
5. **Email — SendGrid** (recommended; Render blocks outbound SMTP, confirmed live): create a
   SendGrid account, verify a sender/domain, create an API key (Mail Send scope) →
   `SENDGRID_API_KEY`. Direct SMTP (`MAIL_BACKEND=smtp`) is still available for hosts that
   allow it, but won't work on Render's free tier.
6. **Secrets** — generate:
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

## Scheduled jobs

These used to run on an in-process APScheduler that fired every job immediately on
import, in **every** process that imported `app.py` with `RUN_SCHEDULER=1` -- not
just one web worker among several (with multiple gunicorn workers and no
`--preload`, each forked worker got its own scheduler), but also `flask db upgrade`
and `flask create-superadmin` (both import `app.py` too, ahead of gunicorn on every
deploy), plus Render's zero-downtime deploy keeping the outgoing instance alive
briefly alongside the incoming one. This isn't hypothetical: this exact combination
once produced a deploy that hung for minutes with no log output (stuck on a Postgres
row lock held by an overlapping fire) and, on the retry, the same job running four
times in ~100 seconds.

The fix: there is no in-process scheduler anymore, and no dedicated Render service
either -- each job is triggered externally, over HTTP, against the same web service
that's already running:

```
POST /api/internal/scheduled-jobs/<name>
X-Cron-Secret: <CRON_TRIGGER_SECRET>
```

-- where `<name>` is one of the keys in `_SCHEDULED_JOBS` (app.py): `generate_missing_payments`,
`generate_missing_salary_charges`, `recalculate_all_estimated_profits`,
`send_daily_whatsapp_keepalive`, `auto_sync_upstream_status`,
`check_pro_plan_expirations`, `refresh_agent_mode_network_status`.
`flask run-scheduled-job <name>` (a small Flask CLI command, app.py) runs the same
thing locally/manually without the HTTP layer.

`.github/workflows/scheduled-jobs.yml` calls this route on GitHub Actions' free
`schedule:` cron (six daily, one every 15 minutes) -- a one-shot job that makes one
HTTP request and exits, no Render service involved at all. An earlier version of
this fix ran each job as its own Render Cron Job instead -- correct, but each Cron
Job service carries its own $1/month minimum regardless of actual usage, which
stopped paying for itself once it was seven of them (and before that, a dedicated
always-on worker service -- a full second paid instance -- was tried first). This
way nothing new needs to be created or paid for on Render: `render.yaml` is back to
a single web service.

The route spawns the job onto a background gevent greenlet and returns immediately
(202), rather than holding the HTTP request open for however long the job takes --
some of these (`auto_sync_upstream_status`'s Playwright scraping especially) can run
well past gunicorn's `--timeout 120` for a tenant with many customers. It also fails
closed: `CRON_TRIGGER_SECRET` unset means the trigger returns 503, not silently
allows an unauthenticated call.

`_run_scheduled_job` (app.py) still wraps every invocation in a Postgres advisory
lock, and that still matters here: a retried or manually-repeated GitHub Actions run
for the *same* job can genuinely overlap a still-running previous trigger, so the
lock is what stops that overlap from double-processing the same customer/payment
rows, not anything about the trigger mechanism itself.

**Setup:** generate a value for `CRON_TRIGGER_SECRET` (Render dashboard ->
servicesbills-web -> Environment; `render.yaml` has it as `generateValue: true`), then
copy that same value into the GitHub repo's Settings -> Secrets and variables ->
Actions, as a secret named `CRON_TRIGGER_SECRET`.

## Notes
- HTTPS/custom domain: configure on the host; then set `APP_BASE_URL`/`CORS_ORIGINS`
  to the custom domain.
- Rotate the dry-run secrets — do NOT reuse `.env` values in production.
- Uploads before go-live were local files; only new uploads land in R2. Migrate any
  existing logos manually if needed.

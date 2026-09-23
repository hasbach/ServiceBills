# syntax=docker/dockerfile:1

# --- Stage 1: build the React SPA ---
FROM node:20-slim AS frontend
WORKDIR /fe
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Python runtime (Flask API + compiled SPA) ---
FROM python:3.13-slim
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps chromium

COPY . .
# Flask serves the SPA from static_folder='build'.
COPY --from=frontend /fe/build ./build

EXPOSE 8000

# Required env at runtime: JWT_SECRET_KEY, DATABASE_URL (Supabase Postgres),
# FERNET_KEY, CORS_ORIGINS, and for prod uploads: STORAGE_BACKEND=s3,
# STORAGE_BUCKET, S3_ENDPOINT_URL (Cloudflare R2), AWS_* creds.
ENV FLASK_APP=app.py
# Startup: apply DB migrations, then serve. Binds Render's $PORT (falls back 8000)
# and uses WEB_CONCURRENCY workers (Render sets this; falls back 1).
# Startup: migrate; create the super-admin if SA_USERNAME/SA_PASSWORD are set
# (self-guarded + idempotent — no-op if unset or already exists); then serve.
#
# render.yaml's Cron Job services (the scheduled jobs -- see DEPLOY.md's
# "Scheduled jobs") override this CMD entirely with a one-shot
# `flask run-scheduled-job <name>`, deliberately skipping the migrate/
# create-superadmin steps below: the web service's own deploy already runs
# both against the same DATABASE_URL, so a cron container doing it again on
# every 15-minute tick would be redundant work, not a safety net.
CMD ["sh", "-c", "flask db upgrade && (flask create-superadmin || true) && exec gunicorn -w ${WEB_CONCURRENCY:-1} -k gevent -b 0.0.0.0:${PORT:-8000} --timeout 120 app:app"]

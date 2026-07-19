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

COPY . .
# Flask serves the SPA from static_folder='build'.
COPY --from=frontend /fe/build ./build

EXPOSE 8000

# Required env at runtime: JWT_SECRET_KEY, DATABASE_URL (Supabase Postgres),
# FERNET_KEY, CORS_ORIGINS, and for prod uploads: STORAGE_BACKEND=s3,
# STORAGE_BUCKET, S3_ENDPOINT_URL (Cloudflare R2), AWS_* creds.
ENV FLASK_APP=app.py
# Single-runner scheduler by default: with WEB_CONCURRENCY=1 (Render free) the one
# worker runs the scheduler. If you scale to multiple workers, set RUN_SCHEDULER=0
# here and run the scheduler in a separate 1-instance service.
ENV RUN_SCHEDULER=1
# Startup: apply DB migrations, then serve. Binds Render's $PORT (falls back 8000)
# and uses WEB_CONCURRENCY workers (Render sets this; falls back 1). This runs
# regardless of any platform command override, so the schema is always built.
# Startup: migrate; create the super-admin if SA_USERNAME/SA_PASSWORD are set
# (self-guarded + idempotent — no-op if unset or already exists); then serve.
CMD ["sh", "-c", "flask db upgrade && (flask create-superadmin || true) && exec gunicorn -w ${WEB_CONCURRENCY:-1} -b 0.0.0.0:${PORT:-8000} --timeout 120 app:app"]

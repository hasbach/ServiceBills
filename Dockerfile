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
#
# SCHEDULER: run exactly ONE process with RUN_SCHEDULER=1 (e.g. a 1-replica
# service using `gunicorn -w 1`), and run the web tier with RUN_SCHEDULER=0 so
# the daily jobs don't fire once per worker.
ENV RUN_SCHEDULER=0
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8000", "--timeout", "120", "app:app"]

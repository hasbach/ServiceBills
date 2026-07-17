import os
from datetime import timedelta


class Config:
    # Fail fast if unset: the secret must never be hardcoded or defaulted in production.
    JWT_SECRET_KEY = os.environ["JWT_SECRET_KEY"]
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=8)
    # Prefer a full DATABASE_URL (e.g. Postgres in prod); fall back to a local SQLite file.
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        f"sqlite:///{os.environ.get('DATABASE_PATH', 'database.db')}",
    )
    # Comma-separated allowlist; defaults to the local React dev server.
    CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")

    # --- Phase 3: secrets encryption + object storage ---
    # Fernet key for encrypting per-tenant WhatsApp credentials (required in prod).
    FERNET_KEY = os.environ.get("FERNET_KEY")
    # Uploads: "local" (dev, disk) or "s3" (prod). For Cloudflare R2 set S3_ENDPOINT_URL.
    STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")
    STORAGE_BUCKET = os.environ.get("STORAGE_BUCKET")
    STORAGE_PREFIX = os.environ.get("STORAGE_PREFIX", "uploads")
    AWS_REGION = os.environ.get("AWS_REGION")
    S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL")  # Cloudflare R2 / MinIO endpoint

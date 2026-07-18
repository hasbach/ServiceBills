import os
from datetime import timedelta


class Config:
    # Fail fast if unset: the secret must never be hardcoded or defaulted in production.
    JWT_SECRET_KEY = os.environ["JWT_SECRET_KEY"]
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=8)
    # Prefer a full DATABASE_URL (e.g. Postgres in prod); fall back to a local SQLite file.
    # Managed hosts (Render/Railway/Heroku) emit "postgres://" — normalize to the
    # "postgresql+psycopg2://" form SQLAlchemy 2.x requires.
    _db_url = os.environ.get("DATABASE_URL", f"sqlite:///{os.environ.get('DATABASE_PATH', 'database.db')}")
    if _db_url.startswith("postgres://"):
        _db_url = _db_url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif _db_url.startswith("postgresql://"):
        _db_url = _db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    SQLALCHEMY_DATABASE_URI = _db_url
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

    # --- Phase 4: billing, signup emails, app links ---
    # On Render, RENDER_EXTERNAL_URL is injected automatically, so APP_BASE_URL
    # (used in verification/reset/billing links) resolves with no manual setup.
    APP_BASE_URL = os.environ.get("APP_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "http://localhost:3000"
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
    # Email: "console" (dev/CI, records in-memory) or "smtp".
    MAIL_BACKEND = os.environ.get("MAIL_BACKEND", "console")
    SMTP_HOST = os.environ.get("SMTP_HOST")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
    MAIL_FROM = os.environ.get("MAIL_FROM", "noreply@servicesbills.net")

"""Upload storage abstraction.

Keys are tenant-namespaced ("{tenant_id}/{uuid}-{name}") so one tenant can never
reach another's uploads by guessing a filename. Local disk backend for dev; S3
compatible backend (AWS S3 / Cloudflare R2 via S3_ENDPOINT_URL) for prod.
Stored key in the DB is backend-independent; each backend maps it to a location.
"""
import os
import uuid
from werkzeug.utils import secure_filename
from config import Config

UPLOAD_ROOT = os.environ.get("UPLOAD_FOLDER", "uploads")


def _key(tenant_id, filename):
    return f"{tenant_id}/{uuid.uuid4().hex}-{secure_filename(filename or 'file')}"


class LocalBackend:
    def save(self, file_storage, tenant_id):
        key = _key(tenant_id, file_storage.filename)
        path = os.path.join(UPLOAD_ROOT, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        file_storage.save(path)
        return key

    def url(self, key):
        return f"/uploads/{key}"


class S3Backend:
    def __init__(self):
        import boto3
        self._c = boto3.client("s3", region_name=Config.AWS_REGION,
                               endpoint_url=Config.S3_ENDPOINT_URL)
        self._bucket = Config.STORAGE_BUCKET
        self._prefix = Config.STORAGE_PREFIX

    def _full(self, key):
        return f"{self._prefix}/{key}" if self._prefix else key

    def save(self, file_storage, tenant_id):
        key = _key(tenant_id, file_storage.filename)
        self._c.upload_fileobj(file_storage, self._bucket, self._full(key))
        return key

    def url(self, key):
        return self._c.generate_presigned_url(
            "get_object", Params={"Bucket": self._bucket, "Key": self._full(key)},
            ExpiresIn=3600)


_backend = None


def _get():
    global _backend
    if _backend is None:
        _backend = S3Backend() if Config.STORAGE_BACKEND == "s3" else LocalBackend()
    return _backend


def save(file_storage, tenant_id):
    """Persist an uploaded file under the tenant's namespace; return its storage key."""
    return _get().save(file_storage, tenant_id)


def url(key):
    """Return a servable URL for a stored key (None-safe)."""
    if not key:
        return None
    return _get().url(key)

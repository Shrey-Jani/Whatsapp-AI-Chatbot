"""File storage — local disk (default) or Cloudflare R2, chosen by STORAGE_BACKEND.

Same interface for both backends:
    upload(tenant_id, path, data, content_type) -> key
    load(key) -> bytes
    exists(key) -> bool

local  — zero setup, but needs a persistent disk (VPS / mounted volume). On ephemeral
         hosts (Render/Railway free) the disk is wiped on restart.
r2     — Cloudflare R2 object storage, survives anywhere. Set STORAGE_BACKEND=r2 and the
         R2_* keys. Files are served only through the authenticated admin API, never a
         public URL.
"""
from pathlib import Path

from .config import settings


def _key(tenant_id: int, path: str) -> str:
    return f"{tenant_id}/{path}"


# ---- local disk ----
def _root() -> Path:
    p = Path(settings.storage_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _local_upload(key: str, data: bytes, content_type: str | None) -> str:
    dest = _root() / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return key


def _local_load(key: str) -> bytes:
    return (_root() / key).read_bytes()


def _local_exists(key: str) -> bool:
    return bool(key) and (_root() / key).is_file()


# ---- Cloudflare R2 (S3-compatible via boto3) ----
_r2 = None


def _client():
    global _r2
    if _r2 is None:
        import boto3  # lazy — only needed when STORAGE_BACKEND=r2
        _r2 = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",
        )
    return _r2


def _r2_upload(key: str, data: bytes, content_type: str | None) -> str:
    _client().put_object(Bucket=settings.r2_bucket, Key=key, Body=data,
                         ContentType=content_type or "application/octet-stream")
    return key


def _r2_load(key: str) -> bytes:
    return _client().get_object(Bucket=settings.r2_bucket, Key=key)["Body"].read()


def _r2_exists(key: str) -> bool:
    if not key:
        return False
    from botocore.exceptions import ClientError
    try:
        _client().head_object(Bucket=settings.r2_bucket, Key=key)
        return True
    except ClientError:
        return False


# ---- dispatch on STORAGE_BACKEND ----
def upload(tenant_id: int, path: str, data: bytes, content_type: str | None = None) -> str:
    key = _key(tenant_id, path)
    if settings.storage_backend == "r2":
        return _r2_upload(key, data, content_type)
    return _local_upload(key, data, content_type)


def load(key: str) -> bytes:
    return _r2_load(key) if settings.storage_backend == "r2" else _local_load(key)


def exists(key: str) -> bool:
    return _r2_exists(key) if settings.storage_backend == "r2" else _local_exists(key)

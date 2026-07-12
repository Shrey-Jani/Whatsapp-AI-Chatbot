"""Document + PDF storage on Supabase Storage. Tenant-scoped paths keep firms isolated."""
from functools import lru_cache

from supabase import Client, create_client

from .config import settings

BUCKET = "taxbot-docs"


@lru_cache(maxsize=1)
def _client() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_key)


def upload(tenant_id: int, path: str, data: bytes, content_type: str) -> str:
    """Upload bytes under a per-tenant prefix; return the storage key."""
    key = f"{tenant_id}/{path}"
    _client().storage.from_(BUCKET).upload(
        key, data, {"content-type": content_type, "upsert": "true"})
    return key


def signed_url(key: str, expires_in: int = 3600) -> str:
    return _client().storage.from_(BUCKET).create_signed_url(key, expires_in)["signedURL"]

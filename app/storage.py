"""Local-disk file storage — uploaded slips are kept under STORAGE_DIR on the host.

Zero setup: no account, no keys. Works on your machine or any VPS with a persistent disk.
NOTE: on ephemeral PaaS hosts (Render/Railway free tiers) the disk is wiped on restart, so
files would not survive there — use object storage (e.g. Cloudflare R2) in that case.

Files are served back only through the authenticated admin API (see admin_routes.download_doc),
never a public URL.
"""
from pathlib import Path

from .config import settings


def _root() -> Path:
    p = Path(settings.storage_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def upload(tenant_id: int, path: str, data: bytes, content_type: str | None = None) -> str:
    """Write bytes under a per-tenant key; return the storage key."""
    key = f"{tenant_id}/{path}"
    dest = _root() / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return key


def load(key: str) -> bytes:
    return (_root() / key).read_bytes()


def exists(key: str) -> bool:
    return bool(key) and (_root() / key).is_file()

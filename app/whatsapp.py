import hashlib
import hmac

import httpx

from .config import settings
from .models import Tenant


def verify_signature(raw_body: bytes, header: str | None) -> bool:
    """Validate Meta's X-Hub-Signature-256 (sha256=<hex>) over the raw request body."""
    if not header or not header.startswith("sha256="):
        return False
    if not settings.app_secret:
        return False
    expected = hmac.new(settings.app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


async def send_text(tenant: Tenant, to: str, body: str) -> None:
    url = f"https://graph.facebook.com/{settings.graph_api_version}/{tenant.phone_number_id}/messages"
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body}}
    headers = {"Authorization": f"Bearer {tenant.access_token}"}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(url, json=payload, headers=headers)
        r.raise_for_status()


async def download_media(tenant: Tenant, media_id: str) -> tuple[bytes, str]:
    """Meta media is a two-step fetch: resolve the ID to a URL, then download it (both need the token)."""
    headers = {"Authorization": f"Bearer {tenant.access_token}"}
    base = f"https://graph.facebook.com/{settings.graph_api_version}"
    async with httpx.AsyncClient(timeout=30) as client:
        meta = (await client.get(f"{base}/{media_id}", headers=headers)).json()
        r = await client.get(meta["url"], headers=headers)
        r.raise_for_status()
        return r.content, meta.get("mime_type", "application/octet-stream")


async def upload_media(tenant: Tenant, data: bytes, mime: str, filename: str) -> str:
    """Upload bytes to Meta and get a media_id we can then send as a document."""
    url = f"https://graph.facebook.com/{settings.graph_api_version}/{tenant.phone_number_id}/media"
    headers = {"Authorization": f"Bearer {tenant.access_token}"}
    files = {"file": (filename, data, mime)}
    payload = {"messaging_product": "whatsapp", "type": mime}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, data=payload, files=files, headers=headers)
        r.raise_for_status()
        return r.json()["id"]


async def send_document(tenant: Tenant, to: str, media_id: str, filename: str,
                        caption: str | None = None) -> None:
    url = f"https://graph.facebook.com/{settings.graph_api_version}/{tenant.phone_number_id}/messages"
    doc = {"id": media_id, "filename": filename}
    if caption:
        doc["caption"] = caption
    payload = {"messaging_product": "whatsapp", "to": to, "type": "document", "document": doc}
    headers = {"Authorization": f"Bearer {tenant.access_token}"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, json=payload, headers=headers)
        r.raise_for_status()

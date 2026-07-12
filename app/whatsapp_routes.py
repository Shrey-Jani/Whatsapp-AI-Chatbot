from fastapi import APIRouter, BackgroundTasks, Request, Response
from sqlalchemy import select

from . import chat_engine, storage
from .config import settings
from .database import Session
from .models import ChatSession, Document, Escalation, Tenant
from .whatsapp import download_media, send_text, verify_signature

router = APIRouter()

MEDIA_TYPES = ("image", "document", "audio", "video")
_EXT = {"image/jpeg": "jpg", "image/png": "png", "application/pdf": "pdf"}


@router.get("/webhook")
async def verify(request: Request):
    """Meta subscription handshake: echo hub.challenge if the verify token matches."""
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == settings.verify_token:
        return Response(content=p.get("hub.challenge", ""), media_type="text/plain")
    return Response(status_code=403)


@router.post("/webhook")
async def receive(request: Request, bg: BackgroundTasks):
    raw = await request.body()
    if not verify_signature(raw, request.headers.get("X-Hub-Signature-256")):
        return Response(status_code=403)
    bg.add_task(_handle, await request.json())  # 200 fast; work off the request path
    return Response(status_code=200)


async def _handle(payload: dict):
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            pnid = value.get("metadata", {}).get("phone_number_id")
            for msg in value.get("messages", []):  # absent for status receipts
                await _process(pnid, msg)


async def _process(phone_number_id: str, msg: dict):
    wa_number = msg["from"]
    async with Session() as db:
        tenant = (await db.scalars(
            select(Tenant).where(Tenant.phone_number_id == phone_number_id))).first()
        if tenant is None:
            return

        sess = (await db.scalars(select(ChatSession).where(
            ChatSession.tenant_id == tenant.id, ChatSession.wa_number == wa_number))).first()
        first_touch = sess is None
        if first_touch:
            sess = ChatSession(tenant_id=tenant.id, wa_number=wa_number, channel="whatsapp")
            db.add(sess)
            await db.flush()  # need sess.id for documents/escalations

        mtype = msg.get("type")
        if mtype in MEDIA_TYPES:
            reply = await _save_media(db, tenant, sess, msg, mtype)
        elif mtype == "text":
            reply = _advance_text(db, tenant, sess, first_touch, msg["text"]["body"])
        else:
            reply = None

        await db.commit()
        if reply:
            await send_text(tenant, wa_number, reply)


async def _save_media(db, tenant, sess, msg, mtype) -> str:
    media = msg[mtype]
    filename = media.get("filename") or f"{media['id']}.{_EXT.get(media.get('mime_type'), 'bin')}"
    data, mime = await download_media(tenant, media["id"])
    # ponytail: supabase-py upload is sync (blocks briefly); wrap in a threadpool if volume grows.
    key = storage.upload(tenant.id, f"{sess.id}/{filename}", data, mime)
    db.add(Document(tenant_id=tenant.id, session_id=sess.id,
                    filename=filename, storage_path=key, file_type=mime))
    return f"Document received. {filename} saved."


def _advance_text(db, tenant, sess, first_touch, text) -> str:
    state = dict(sess.conversation_state_json or {})
    reply, _done = chat_engine.advance(state, None if first_touch else text)

    if state.get("_escalate") and not state.get("_escalated_recorded"):
        db.add(Escalation(tenant_id=tenant.id, session_id=sess.id,
                          reason="user requested agent", context_json={"last_message": text}))
        state["_escalated_recorded"] = True
        phone = (tenant.config or {}).get("support_phone")
        reply = ("Connecting you to our team. "
                 + (f"Please call {phone} or wait for a response." if phone
                    else "Someone from our team will follow up with you shortly."))

    sess.conversation_state_json = state
    return reply

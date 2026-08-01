import logging

from fastapi import APIRouter, BackgroundTasks, Request, Response
from sqlalchemy import select

from . import chat_engine, documents, pdf_generator, submission
from .config import settings
from .database import Session
from .models import ChatSession, Escalation, Tenant
from .whatsapp import download_media, send_document, send_text, upload_media, verify_signature

log = logging.getLogger("taxbot")
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
                try:
                    await _process(pnid, msg)
                except Exception:
                    # One bad message must not sink the rest of the webhook batch.
                    log.exception("WhatsApp message processing failed (from %s)", msg.get("from"))


def _operator(tenant) -> str | None:
    return (tenant.config or {}).get("operator_number")


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
            reply = await _advance_text(db, tenant, sess, first_touch, msg["text"]["body"])
        else:
            reply = None

        await db.commit()
        if reply:
            await send_text(tenant, wa_number, reply)


async def _save_media(db, tenant, sess, msg, mtype) -> str:
    media = msg[mtype]
    filename = media.get("filename") or f"{media['id']}.{_EXT.get(media.get('mime_type'), 'bin')}"
    data, mime = await download_media(tenant, media["id"])
    reply = documents.handle_file_upload(db, tenant, sess, data, filename, mime)

    op = _operator(tenant)                       # forward the slip to the operator's WhatsApp
    if op:
        try:
            mid = await upload_media(tenant, data, mime, filename)
            await send_document(tenant, op, mid, filename, caption=f"Slip from {sess.wa_number}")
        except Exception as e:
            print(f"[whatsapp] slip forward failed: {e}")
    return reply


async def _advance_text(db, tenant, sess, first_touch, text) -> str:
    state = dict(sess.conversation_state_json or {})
    reply, done = (chat_engine.advance(state, None, greeting=text) if first_touch
                   else chat_engine.advance(state, text))

    if await submission.prefill_existing(db, tenant, state):    # existing customer matched by SIN
        reply, done = chat_engine.advance(state, None)
        reply = ("Welcome back! Here's your profile on file:\n"
                 + submission.profile_summary(state) + "\n\n" + reply)
    elif state.get("details_ok") == "No, update my details" and not state.get("_reasked"):
        state["_reasked"] = True
        for f in submission.PREFILL_FIELDS:
            state.pop(f, None)
        reply, done = chat_engine.advance(state, None)
        reply = "No problem — let's update your details.\n\n" + reply

    if state.get("_escalate") and not state.get("_escalate_logged"):
        db.add(Escalation(tenant_id=tenant.id, session_id=sess.id,
                          reason=state.get("_escalate_reason", "escalation"),
                          context_json={k: v for k, v in state.items() if not k.startswith("_") and k not in ("sin", "spouse_sin")}))
        state["_escalate_logged"] = True
        op = _operator(tenant)
        if op:
            try:
                await send_text(tenant, op,
                                f"⚠️ Escalation ({state.get('_escalate_reason')}) from {sess.wa_number}")
            except Exception as e:
                print(f"[whatsapp] escalation notify failed: {e}")

    if done and state.get("_done") and sess.client_id is None:   # first true completion
        sess.conversation_state_json = state
        client, _sub = await submission.materialize(db, tenant, sess)
        op = _operator(tenant)
        if op:                                   # deliver the summary PDF to the operator
            try:
                pdf = await pdf_generator.generate_tax_summary_pdf(db, client.id)
                mid = await upload_media(tenant, pdf, "application/pdf", f"summary_{client.id}.pdf")
                await send_document(tenant, op, mid, f"summary_{client.id}.pdf",
                                    caption=f"New submission: {client.full_name}")
            except Exception as e:
                print(f"[whatsapp] operator delivery failed: {e}")

    sess.conversation_state_json = state
    return reply

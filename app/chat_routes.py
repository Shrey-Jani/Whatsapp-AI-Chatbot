from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import chat_engine, ocr, storage
from .database import get_db
from .models import ChatSession, Document, Tenant
from .schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/api")


async def _get_or_create_web_session(db, tenant, session_id):
    sess = (await db.scalars(select(ChatSession).where(
        ChatSession.tenant_id == tenant.id, ChatSession.wa_number == session_id))).first()
    if sess is None:
        sess = ChatSession(tenant_id=tenant.id, wa_number=session_id, channel="web")
        db.add(sess)
        await db.flush()  # need sess.id for storage paths / documents
    return sess


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, db: AsyncSession = Depends(get_db)):
    # ponytail: web channel maps to the first tenant (single-firm demo page).
    # Multi-tenant web = per-firm subdomain/page → resolve tenant from that in Phase 7.
    tenant = (await db.scalars(select(Tenant).order_by(Tenant.id))).first()
    if tenant is None:
        return ChatResponse(reply="No firm is configured yet.", done=True)

    sess = (await db.scalars(select(ChatSession).where(
        ChatSession.tenant_id == tenant.id,
        ChatSession.wa_number == body.session_id))).first()
    first_touch = sess is None
    if first_touch:
        sess = ChatSession(tenant_id=tenant.id, wa_number=body.session_id, channel="web")
        db.add(sess)

    state = dict(sess.conversation_state_json or {})
    reply, done = chat_engine.advance(state, None if first_touch else body.message)
    sess.conversation_state_json = state
    await db.commit()
    return ChatResponse(reply=reply, done=done)


@router.post("/upload", response_model=ChatResponse)
async def upload(session_id: str = Form(...), file: UploadFile = File(...),
                 db: AsyncSession = Depends(get_db)):
    tenant = (await db.scalars(select(Tenant).order_by(Tenant.id))).first()
    if tenant is None:
        return ChatResponse(reply="No firm is configured yet.", done=True)

    sess = await _get_or_create_web_session(db, tenant, session_id)
    data = await file.read()
    mime = file.content_type or "application/octet-stream"

    try:
        key = storage.upload(tenant.id, f"{sess.id}/{file.filename}", data, mime)
    except Exception as e:                     # storage not configured yet → keep OCR working
        print(f"[upload] storage failed: {e}")
        key = ""

    extracted = ocr.extract_slip(data, mime)
    db.add(Document(tenant_id=tenant.id, session_id=sess.id, filename=file.filename,
                    storage_path=key, file_type=mime))

    state = dict(sess.conversation_state_json or {})
    slips = state.get("slips", [])
    slips.append({"filename": file.filename, "slip_type": extracted.get("slip_type"),
                  "tax_year": extracted.get("tax_year"), "boxes": extracted.get("boxes", {})})
    state["slips"] = slips
    sess.conversation_state_json = state
    await db.commit()

    st = extracted.get("slip_type", "document")
    n = len(extracted.get("boxes") or {})
    detail = f" I read {n} amount(s) off it." if n else ""
    return ChatResponse(reply=f"Received your {st}.{detail} "
                              "Upload more slips, or keep answering to continue.", done=False)

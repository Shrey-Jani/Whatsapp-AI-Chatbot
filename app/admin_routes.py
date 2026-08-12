"""Admin dashboard API. Every route requires the X-Admin-Key header == settings.admin_password."""
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import chat_engine, pdf_generator, storage
from .config import settings
from .database import get_db
from .models import ChatSession, Client, Document, Escalation, Submission, Tenant
from .security import reveal_sin
from .whatsapp import send_text


def require_admin(x_admin_key: str = Header(default="")):
    if x_admin_key != settings.admin_password:
        raise HTTPException(status_code=401, detail="unauthorized")


router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


class StatusUpdate(BaseModel):
    status: str | None = None
    admin_notes: str | None = None


@router.get("/submissions")
async def list_submissions(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Submission, Client).join(Client, Submission.client_id == Client.id)
        .order_by(Submission.created_at.desc()))).all()
    return [{"id": s.id, "client_name": c.full_name, "phone": c.phone, "email": c.email,
             "date": s.created_at.isoformat(), "status": s.status} for s, c in rows]


@router.get("/submissions/{submission_id}")
async def submission_detail(submission_id: int, db: AsyncSession = Depends(get_db)):
    sub = await db.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(404, "not found")
    client = await db.get(Client, sub.client_id)
    docs = (await db.scalars(select(Document).where(Document.client_id == sub.client_id))).all()
    return {
        "id": sub.id, "status": sub.status, "admin_notes": sub.admin_notes,
        "client": dict(client.raw_answers or {}) | {
            "full_name": client.full_name, "phone": client.phone, "email": client.email,
            "sin": reveal_sin(client.sin), "dob": client.dob, "address": client.address,
            "marital_status": client.marital_status},
        "documents": [{"id": d.id, "filename": d.filename, "slip_type": d.slip_type,
                       "employer": d.employer_name, "income": d.income_amount,
                       "has_file": storage.exists(d.storage_path)} for d in docs],
    }


@router.get("/documents/{doc_id}/download")
async def download_doc(doc_id: int, db: AsyncSession = Depends(get_db)):
    d = await db.get(Document, doc_id)
    if d is None or not storage.exists(d.storage_path):
        raise HTTPException(404, "not found")
    return Response(content=storage.load(d.storage_path),
                    media_type=d.file_type or "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{d.filename}"'})


@router.put("/submissions/{submission_id}")
async def update_submission(submission_id: int, body: StatusUpdate,
                            db: AsyncSession = Depends(get_db)):
    sub = await db.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(404, "not found")
    if body.status is not None:
        sub.status = body.status
    if body.admin_notes is not None:
        sub.admin_notes = body.admin_notes
    await db.commit()
    return {"status": sub.status, "admin_notes": sub.admin_notes}


@router.get("/submissions/{submission_id}/download-pdf")
async def download_pdf(submission_id: int, db: AsyncSession = Depends(get_db)):
    sub = await db.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(404, "not found")
    pdf = await pdf_generator.generate_tax_summary_pdf(db, sub.client_id)   # on demand, no storage
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="summary_{submission_id}.pdf"'})


@router.get("/escalations")
async def escalations(db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(Escalation).where(Escalation.resolved.is_(False))
                             .order_by(Escalation.created_at.desc()))).all()
    return [{"id": e.id, "session_id": e.session_id, "reason": e.reason,
             "created_at": e.created_at.isoformat()} for e in rows]


@router.post("/escalations/{esc_id}/resolve")
async def resolve_escalation(esc_id: int, db: AsyncSession = Depends(get_db)):
    """Staff resolved it - clear the hand-off and pick the client's chat back up where it stopped.

    On WhatsApp the bot proactively re-sends the next question so the conversation continues.
    """
    esc = await db.get(Escalation, esc_id)
    if esc is None:
        raise HTTPException(404, "not found")
    esc.resolved = True
    sess = await db.get(ChatSession, esc.session_id)
    if sess is not None:
        state = dict(sess.conversation_state_json or {})
        resume = chat_engine.resume_message(state)     # clears escalation, builds the continue message
        sess.conversation_state_json = state
        if sess.channel == "whatsapp" and sess.wa_number:   # push the next question to the client
            tenant = await db.get(Tenant, sess.tenant_id)
            if tenant is not None:
                try:
                    await send_text(tenant, sess.wa_number, resume)
                except Exception as e:
                    print(f"[admin] resume send failed: {e}")
    await db.commit()
    return {"resolved": True}

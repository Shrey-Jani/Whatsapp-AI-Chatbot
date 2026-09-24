"""Admin dashboard API. Every route requires the X-Admin-Key header to match the admin password."""
import hmac
import json
import time
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import chat_engine, pdf_generator, storage
from .config import settings
from .database import get_db
from .models import ChatSession, Client, Document, Escalation, Setting, Submission, Tenant
from .security import hash_password, reveal_sin, verify_password
from .whatsapp import send_text


ADMIN_PW_KEY = "admin_password_hash"   # Setting row holding the hash, once one is set
RESET_KEY = "admin_reset_code"         # Setting row holding an in-progress reset
RESET_TTL = 10 * 60                    # seconds a reset code stays valid
RESET_MAX_TRIES = 5                    # wrong guesses before the code is burned


async def require_admin(x_admin_key: str = Header(default=""),
                        db: AsyncSession = Depends(get_db)):
    """Check the stored password hash; fall back to ADMIN_PASSWORD until one is set.

    Deleting the settings row is the forgot-password recovery: the env var works again.
    """
    stored = await db.get(Setting, ADMIN_PW_KEY)
    ok = (verify_password(x_admin_key, stored.value) if stored
          else hmac.compare_digest(x_admin_key, settings.admin_password))
    if not ok:
        raise HTTPException(status_code=401, detail="unauthorized")


router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


class StatusUpdate(BaseModel):
    status: str | None = None
    admin_notes: str | None = None


class PasswordChange(BaseModel):
    new_password: str


@router.post("/password")
async def change_password(body: PasswordChange, db: AsyncSession = Depends(get_db)):
    """Set a new admin password. The caller already proved the current one via require_admin."""
    pw = (body.new_password or "").strip()
    if len(pw) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    row = await db.get(Setting, ADMIN_PW_KEY)
    if row is None:
        db.add(Setting(key=ADMIN_PW_KEY, value=hash_password(pw)))
    else:
        row.value = hash_password(pw)
    await db.commit()
    return {"ok": True}


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


# ---- Forgot password ----------------------------------------------------------------
# The staff member messages the bot on WhatsApp to get a code (see whatsapp_routes), then
# enters it here with a new password. This router is deliberately NOT behind require_admin -
# the code is the proof of identity, since a locked-out user has no password to present.

reset_router = APIRouter(prefix="/api/admin")


class PasswordReset(BaseModel):
    code: str
    new_password: str


def issue_reset_code() -> tuple[str, str]:
    """A fresh 6-digit code and the Setting value that records it. Returns (code, stored)."""
    import secrets
    code = f"{secrets.randbelow(1_000_000):06d}"
    return code, json.dumps({"code": code, "exp": int(time.time()) + RESET_TTL, "tries": 0})


@reset_router.post("/reset-password")
async def reset_password(body: PasswordReset, db: AsyncSession = Depends(get_db)):
    row = await db.get(Setting, RESET_KEY)
    if row is None:
        raise HTTPException(400, "No reset in progress. Message the bot on WhatsApp first.")

    data = json.loads(row.value)
    if time.time() > data["exp"]:
        await db.delete(row); await db.commit()
        raise HTTPException(400, "That code has expired. Please request a new one.")
    if data["tries"] >= RESET_MAX_TRIES:
        await db.delete(row); await db.commit()
        raise HTTPException(400, "Too many incorrect attempts. Please request a new code.")
    if not hmac.compare_digest(body.code.strip(), data["code"]):
        data["tries"] += 1                      # burn an attempt so the code can't be brute-forced
        row.value = json.dumps(data)
        await db.commit()
        raise HTTPException(400, "Incorrect code.")

    pw = (body.new_password or "").strip()
    if len(pw) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")

    stored = await db.get(Setting, ADMIN_PW_KEY)
    if stored is None:
        db.add(Setting(key=ADMIN_PW_KEY, value=hash_password(pw)))
    else:
        stored.value = hash_password(pw)
    await db.delete(row)                        # single use
    await db.commit()
    return {"ok": True}

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import Escalation, Submission
from .schemas import SubmissionOut

# ponytail: no auth yet — add a staff login/API key before this is exposed publicly (Phase 7).
router = APIRouter(prefix="/api/admin")


@router.get("/submissions", response_model=list[SubmissionOut])
async def submissions(db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(Submission).order_by(Submission.created_at.desc()))).all()
    return list(rows)


@router.get("/escalations")
async def escalations(db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(
        select(Escalation).where(Escalation.resolved.is_(False))
        .order_by(Escalation.created_at.desc()))).all()
    return [{"id": e.id, "session_id": e.session_id, "reason": e.reason,
             "created_at": e.created_at.isoformat()} for e in rows]

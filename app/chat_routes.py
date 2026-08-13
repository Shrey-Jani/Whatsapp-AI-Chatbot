from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import chat_engine, checklists, documents, pdf_generator, pricing, submission
from .database import get_db
from .models import ChatSession, Client, Escalation, Tenant
from .question_flow import FILING_SERVICES
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
    # On first contact pass the message as `greeting` so the engine can detect their language.
    reply, done = (chat_engine.advance(state, None, greeting=body.message) if first_touch
                   else chat_engine.advance(state, body.message))

    if await submission.prefill_existing(db, tenant, state):   # existing customer matched by SIN
        reply, done = chat_engine.advance(state, None)         # -> "is this still correct?"
        reply = ("Welcome back! Here's your profile on file:\n"
                 + submission.profile_summary(state) + "\n\n" + reply)
    elif state.get("details_ok") == "No, update my details" and not state.get("_reasked"):
        state["_reasked"] = True                                # they want to change something
        for f in submission.PREFILL_FIELDS:
            state.pop(f, None)                                 # clear so the flow re-asks them
        reply, done = chat_engine.advance(state, None)
        reply = "No problem - let's update your details.\n\n" + reply

    sess.conversation_state_json = state

    if sess.id is None:
        await db.flush()                       # ensure sess.id for escalation FK

    if state.get("_escalate") and not state.get("_escalate_logged"):
        db.add(Escalation(tenant_id=tenant.id, session_id=sess.id,
                          reason=state.get("_escalate_reason", "escalation"),
                          context_json={k: v for k, v in state.items() if not k.startswith("_") and k not in ("sin", "spouse_sin")}))
        state["_escalate_logged"] = True
        sess.conversation_state_json = state

    if done and state.get("_done"):                              # first true completion
        if state.get("_escalate"):                               # handed off to staff (e.g. Quebec) - not a filing
            pass                                                 # escalation already logged above
        elif state.get("service_type") == "Others":              # an enquiry, not a tax filing
            if not state.get("_enquiry_logged"):                 # capture it for staff, keep it light
                db.add(Escalation(tenant_id=tenant.id, session_id=sess.id, reason="general enquiry",
                                  context_json={"enquiry": state.get("others_enquiry")}))
                state["_enquiry_logged"] = True
                sess.conversation_state_json = state
        # Only question-driven filings produce a submission; checklist-only services never do.
        elif state.get("service_type") in FILING_SERVICES and sess.client_id is None:
            _c, sub = await submission.materialize(db, tenant, sess)
            # When Meta is wired, deliver the PDF + slips to the operator's WhatsApp here.
            reply += (f"\n\nYour tax summary has been prepared for our team.\n"
                      f"Your reference number: {sub.reference_number}")
            if state.get("third_party_payer") == "Yes":          # shared token (spec §7)
                reply += "\nThis same reference applies to your payer's file."

    if state.get("shared_info") and not state.get("_shared_logged"):   # checklist-only: details in chat
        db.add(Escalation(tenant_id=tenant.id, session_id=sess.id,
                          reason=f"{state.get('service_type')} - details shared in chat",
                          context_json={"shared_info": state["shared_info"]}))
        state["_shared_logged"] = True

    images = [f"{checklists.URL_PREFIX}/{n}" for n in (state.pop("_images", None) or [])]
    sess.conversation_state_json = state          # persist with _images consumed
    await db.commit()
    return ChatResponse(reply=reply, done=done, images=images)


@router.post("/upload", response_model=ChatResponse)
async def upload(session_id: str = Form(...), file: UploadFile = File(...),
                 db: AsyncSession = Depends(get_db)):
    tenant = (await db.scalars(select(Tenant).order_by(Tenant.id))).first()
    if tenant is None:
        return ChatResponse(reply="No firm is configured yet.", done=True)

    sess = await _get_or_create_web_session(db, tenant, session_id)
    data = await file.read()
    reply = documents.handle_file_upload(
        db, tenant, sess, data, file.filename, file.content_type or "")

    state = dict(sess.conversation_state_json or {})
    if state.get("_done"):            # spec §7 - post-generation slip additions are chargeable
        reply += (f"\n\nNote: your file was already completed and sent for review. Adding slips "
                  f"now incurs a ${pricing.PRICING['post_slip_charge']} handling charge "
                  f"(covers up to 3 slips).")

    await db.commit()
    return ChatResponse(reply=reply, done=False)


@router.get("/generate-pdf/{client_id}")
async def generate_pdf(client_id: int, db: AsyncSession = Depends(get_db)):
    """Generate the summary PDF on demand and return it directly - nothing is stored."""
    client = await db.get(Client, client_id)
    if client is None:
        return Response(status_code=404)
    pdf = await pdf_generator.generate_tax_summary_pdf(db, client_id)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="summary_{client_id}.pdf"'})

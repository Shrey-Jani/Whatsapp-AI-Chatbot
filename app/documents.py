"""Orchestrates an uploaded file: validate → parse → Document row → confirmation.

Lives here (not in chat_engine) so the engine stays pure logic. Parsing: Gemini vision is the
primary reader (accurate on real slip layouts); pdfplumber is a cheap fallback for text PDFs
when vision can't identify the slip. Full parsed data is saved to the Document for the firm;
the user only sees a short "<slip> received." confirmation.
"""
from . import llm, ocr, pdf_parser, storage, submission
from .config import settings
from .models import Document

MAX_BYTES = 5 * 1024 * 1024


def _text_is_tax_doc(text: str) -> bool | None:
    """Ask the (text) LLM whether extracted PDF text is a tax/supporting document. None = unsure."""
    if not llm.configured() or not (text or "").strip():
        return None
    try:
        ans = llm.complete(
            "Is the document below a Canadian tax slip, receipt, invoice, bank/financial statement, "
            "Notice of Assessment/Tax Summary, or other tax-return supporting document? "
            "Answer with only YES or NO.\n\n" + text[:3000]).strip().upper()
        return True if ans.startswith("YES") else False if ans.startswith("NO") else None
    except Exception as e:
        print(f"[documents] text relevance check failed: {e}")
        return None
ALLOWED = {"application/pdf": "pdf", "image/jpeg": "img",
           "image/jpg": "img", "image/png": "img"}


def _parse(data: bytes, content_type: str, kind: str) -> dict:
    meta = ocr.extract_slip(data, content_type)          # Gemini vision - primary reader
    relevant = meta.get("is_relevant")                   # vision relevance flag (absent if OCR off)
    unknown = str(meta.get("slip_type", "unknown")).lower() in ("unknown", "", "document")
    if unknown and kind == "pdf":                         # cheap fallback for text PDFs
        text = pdf_parser.extract_text_from_pdf(data)
        if text.strip():
            st = pdf_parser.identify_slip_type(text)
            meta = {"slip_type": st, **pdf_parser.extract_key_fields(text, st)}
            if relevant is None and str(st).lower() in ("unknown", "", "document", "other"):
                relevant = _text_is_tax_doc(text)         # vision off -> classify the text instead
    if relevant is not None:
        meta["is_relevant"] = relevant
    if str(meta.get("slip_type", "")).lower() not in ("unknown", "", "document", "other"):
        meta["is_relevant"] = True                        # a recognised slip is always relevant
    return meta


def handle_file_upload(db, tenant, sess, data: bytes, filename: str, content_type: str) -> str:
    """Returns the message to send back to the user. Caller commits the session."""
    if len(data) > MAX_BYTES:
        return "That file is over 5 MB - please upload a smaller one."
    kind = ALLOWED.get((content_type or "").lower())
    if kind is None:
        return "Unsupported file type. Please upload a PDF, JPG, or PNG."

    meta = _parse(data, content_type, kind)
    if meta.get("is_relevant") is False:                 # clearly not a tax/supporting document
        return ("This doesn't look like a required document. Please upload a valid tax slip, "
                "receipt, or supporting document - or type 'skip' if you don't have one.")
    slip_type = meta.get("slip_type") or "unknown"
    employer = meta.get("employer_name") or meta.get("issuer")
    income = meta.get("income_amount")

    # Store under the client's "<Name>_<phone>" folder (falls back to unnamed until we have those).
    state0 = {k: v for k, v in (sess.conversation_state_json or {}).items() if not k.startswith("_")}
    folder = submission.client_folder(state0)
    try:                                       # keep the actual file so the firm can retrieve it
        key = storage.upload(tenant.id, f"{folder}/{filename}", data, content_type)
    except Exception as e:
        print(f"[documents] file storage failed: {e}")
        key = ""

    db.add(Document(
        tenant_id=tenant.id, session_id=sess.id, filename=filename, storage_path=key,
        file_type=content_type, slip_type=slip_type, employer_name=employer,
        income_amount=str(income) if income else None, parsed_metadata=meta))

    state = dict(sess.conversation_state_json or {})
    state.setdefault("slips", []).append(
        {"filename": filename, "slip_type": slip_type, "employer": employer})
    sess.conversation_state_json = state

    # Auto-match the slip against the active filing year (spec §1) and flag a mismatch.
    year = str(meta.get("tax_year") or "").strip()
    mismatch = (f" (note: this looks like a {year} slip, not {settings.tax_year} - "
                "we'll confirm with you)") if year and year != str(settings.tax_year) else ""

    # User sees only the slip type; the firm gets the full parsed data on the Document.
    # Not every upload is a tax slip (SIN docs, bank statements) - say "Document" when unsure.
    label = slip_type if slip_type.lower() != "unknown" else "Document"
    return f"{label} received.{mismatch}"

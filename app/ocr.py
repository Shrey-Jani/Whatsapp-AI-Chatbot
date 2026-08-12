"""Extract structured data from an uploaded Canadian tax slip using Gemini vision.

Accepts images (JPG/PNG) and PDFs. Returns the slip type + key monetary boxes so the firm
gets the numbers typed for them and we can count slips for pricing. Never raises - a failed
read returns an 'Unknown' result so the flow keeps moving.
"""
from .chat_engine import _safe_json
from .config import settings

PROMPT = (
    "A client uploaded this document for their Canadian personal tax return. "
    "First decide if it is a RELEVANT tax document: a tax slip (T4, T5, T4A, T2202, etc.), a "
    "receipt or invoice, a bank/financial statement, a Notice of Assessment/Tax Summary, a SIN "
    "document, or an e-transfer/payment screenshot. A selfie, a photo of a person, a landscape, a "
    "meme, a chat screenshot, or any unrelated image is NOT relevant.\n"
    "If it is a tax slip, also identify the slip type, issuer, tax year, and key monetary boxes.\n"
    "Respond with ONLY JSON:\n"
    '{"is_relevant": true or false, "slip_type": "...", "issuer": "...", "tax_year": "...", '
    '"boxes": {"<box>": "<amount>"}, "confidence": 0.0-1.0}'
)


def extract_slip(data: bytes, mime: str) -> dict:
    # Vision is Gemini-only for now; on other providers we skip straight to the pdfplumber path.
    if settings.llm_provider != "gemini" or not settings.gemini_api_key:
        return {"slip_type": "Unknown", "boxes": {}, "confidence": 0.0}
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=settings.gemini_api_key)
        resp = client.models.generate_content(
            model=settings.gemini_model,
            contents=[types.Part.from_bytes(data=data, mime_type=mime), PROMPT],
        )
        out = _safe_json(resp.text)
        out.setdefault("slip_type", "Unknown")
        out.setdefault("boxes", {})
        return out
    except Exception as e:
        print(f"[ocr] extract failed: {e}")
        return {"slip_type": "Unknown", "boxes": {}, "confidence": 0.0}

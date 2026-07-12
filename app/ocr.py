"""Extract structured data from an uploaded Canadian tax slip using Gemini vision.

Accepts images (JPG/PNG) and PDFs. Returns the slip type + key monetary boxes so the firm
gets the numbers typed for them and we can count slips for pricing. Never raises — a failed
read returns an 'Unknown' result so the flow keeps moving.
"""
from .chat_engine import _safe_json
from .config import settings

PROMPT = (
    "You are reading a Canadian tax slip. Identify the slip type "
    "(T4, T5, T4A, T2202, or Other), the issuer, and the tax year, and extract the key "
    "monetary boxes with their box numbers. Respond with ONLY JSON:\n"
    '{"slip_type": "...", "issuer": "...", "tax_year": "...", '
    '"boxes": {"<box>": "<amount>"}, "confidence": 0.0-1.0}'
)


def extract_slip(data: bytes, mime: str) -> dict:
    if not settings.gemini_api_key:
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

"""Deterministic tax-slip parsing with pdfplumber - fast, free, offline.

Only works on TEXT-based PDFs. Scanned/photo PDFs yield no text, and the caller
(documents.py) falls back to Gemini vision (ocr.py) for those and for images.
"""
import io
import re

# (slip type, keywords that identify it) - checked in order.
SIGNATURES = [
    ("T4", ["statement of remuneration", "t4 "]),
    ("T4A", ["statement of pension, retirement", "t4a"]),
    ("T5", ["statement of investment income", "t5 "]),
    ("T2202", ["tuition and enrolment", "t2202"]),
]

_AMOUNT = re.compile(r"\$?\s?(\d{1,3}(?:[,\s]\d{3})+(?:\.\d{2})?|\d+\.\d{2})")


def extract_text_from_pdf(file_bytes: bytes) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            return "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception as e:
        print(f"[pdf_parser] text extraction failed: {e}")
        return ""


def identify_slip_type(text: str) -> str:
    low = text.lower()
    for slip, keys in SIGNATURES:
        if any(k in low for k in keys):
            return slip
    return "unknown"


def extract_key_fields(text: str, slip_type: str) -> dict:
    out: dict = {}
    for line in text.splitlines():
        s = line.strip()
        if s and not s.lower().startswith(("t4", "t5", "t2202", "protected", "year", "box")):
            out["employer_name"] = s          # issuer name is usually the first real line
            break
    nums = []
    for m in _AMOUNT.findall(text):
        try:
            nums.append(float(m.replace(",", "").replace(" ", "")))
        except ValueError:
            pass
    if nums:
        out["income_amount"] = f"{max(nums):.2f}"   # largest figure ≈ the income box
    return out

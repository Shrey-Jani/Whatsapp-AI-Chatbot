"""Generate the Information Sheet (I.S.) PDF the spec requires at quote/checkout."""
import io

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas


def build_information_sheet(firm_name: str, reference_number: str, answers: dict) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    _, height = letter
    y = height - inch

    c.setFont("Helvetica-Bold", 16)
    c.drawString(inch, y, f"{firm_name} — Information Sheet")
    y -= 0.4 * inch
    c.setFont("Helvetica", 10)
    c.drawString(inch, y, f"Reference: {reference_number}")
    y -= 0.4 * inch

    c.setFont("Helvetica", 11)
    for key, value in answers.items():
        if key.startswith("_"):  # skip engine bookkeeping (_index, _escalate)
            continue
        c.drawString(inch, y, f"{key.replace('_', ' ').title()}: {value}")
        y -= 0.28 * inch
        if y < inch:
            c.showPage()
            y = height - inch
            c.setFont("Helvetica", 11)

    c.showPage()
    c.save()
    return buf.getvalue()

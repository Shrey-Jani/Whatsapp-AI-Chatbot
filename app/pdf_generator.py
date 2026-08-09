"""6-page client tax summary PDF (ReportLab Platypus).

Branding comes from the tenant's config (multi-tenant), defaulting to the values below so the
demo firm renders correctly. build_summary_pdf() is a pure function (easy to test);
generate_tax_summary_pdf() fetches the Client + Documents and calls it.
"""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)
from sqlalchemy import select

from .models import Client, Document, Submission, Tenant
from .security import reveal_sin

BRAND = "#075e54"
BRANDING_DEFAULT = {
    "firm_name": "Ravi's Accurate Tax Services",
    "tagline": "THE NAME YOU CAN TRUST",
    "footer": "Ravi's Accurate Tax Services | 647-300-1516 | 905-798-3785 | www.raviaccuratetax.ca",
}


def _decorate(canvas, doc):
    """Green header bar + footer on every page."""
    w, h = letter
    canvas.saveState()
    canvas.setFillColor(colors.HexColor(BRAND))
    canvas.rect(0, h - 50, w, 50, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 12)
    canvas.drawString(0.75 * inch, h - 33, doc._firm_name)
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.setFont("Helvetica", 8)
    canvas.drawCentredString(w / 2, 25, doc._footer)
    canvas.restoreState()


def _kv(pairs):
    rows = [[k, str(v) if v not in (None, "") else "-"] for k, v in pairs]
    t = Table(rows, colWidths=[2.2 * inch, 4.1 * inch])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 10),
        ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 10),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#e0e0e0")),
    ]))
    return t


def build_summary_pdf(data: dict, documents: list, branding: dict, submission_id: str = "-") -> bytes:
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], textColor=colors.HexColor(BRAND))
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=70, bottomMargin=55)
    doc._firm_name = branding["firm_name"]
    doc._footer = branding["footer"]
    s = []

    # Page 1 - Cover
    s += [Spacer(1, 1.6 * inch),
          Paragraph(branding["firm_name"], h1),
          Paragraph("Client Tax Summary", styles["Heading2"]),
          Spacer(1, 0.4 * inch),
          _kv([("Client", data.get("full_name")), ("Reference", submission_id)]),
          Spacer(1, 0.7 * inch),
          Paragraph(f"<b>{branding['tagline']}</b>", styles["Title"]),
          PageBreak()]

    # Page 2 - Personal Info
    s += [Paragraph("Personal Information", h1),
          _kv([("Full name", data.get("full_name")), ("Phone", data.get("phone")),
               ("Email", data.get("email")), ("SIN", data.get("sin")),
               ("Date of birth", data.get("dob")), ("Address", data.get("address")),
               ("Marital status", data.get("marital_status")),
               ("Landing date", data.get("landing_date"))]),
          PageBreak()]

    # Page 3 - Family
    fam = []
    if data.get("marital_status") in ("Married", "Common-Law"):
        fam += [("Spouse name", data.get("spouse_name")), ("Spouse DOB", data.get("spouse_dob")),
                ("Spouse SIN", data.get("spouse_sin")), ("Spouse income", data.get("spouse_income")),
                ("Spouse address", data.get("spouse_address")),
                ("Spouse in Canada", data.get("spouse_in_canada"))]
    for label, key in [("Marriage date", "marriage_date"), ("Cohabitation date", "cohabitation_date"),
                       ("Divorce date", "divorce_date"), ("Separation date", "separation_date"),
                       ("Spouse date of death", "date_of_death")]:
        if data.get(key):
            fam.append((label, data.get(key)))
    if data.get("has_children") == "Yes":
        fam += [("Children / dependents", data.get("children_details"))]
    s += [Paragraph("Family", h1),
          _kv(fam) if fam else Paragraph("No spouse or dependents on file.", styles["Normal"]),
          PageBreak()]

    # Page 4 - Income & Documents
    s += [Paragraph("Income & Schedules", h1),
          _kv([("Filed last year", data.get("filed_last_year")),
               ("Tuition (T2202A)", data.get("has_tuition")),
               ("Graduated 2025", data.get("graduation_2025")),
               ("Graduation date", data.get("graduation_date")),
               ("Gig / rideshare", data.get("is_gig")),
               ("Gig platforms", data.get("gig_platforms")),
               ("Gig cash estimate", data.get("gig_cash")),
               ("Owns rental property", data.get("owns_rental")),
               ("Rental address", data.get("rental_address")),
               ("Rental gross income", data.get("rental_gross_income")),
               ("Rental mortgage interest", data.get("rental_mortgage_interest")),
               ("Rental property tax", data.get("rental_property_tax")),
               ("Rental expenses", data.get("rental_expenses")),
               ("Rental ownership", data.get("rental_ownership")),
               ("Rental partners", data.get("rental_partners")),
               ("First-time home buyer", data.get("first_home")),
               ("First home details", data.get("first_home_details")),
               ("Medical expenses", data.get("has_medical")),
               ("Medical details", data.get("medical_details")),
               ("Charitable donations", data.get("has_donations")),
               ("Donations total", data.get("donations_note"))]),
          Spacer(1, 0.25 * inch),
          Paragraph("Uploaded Documents", styles["Heading3"])]
    if documents:
        rows = [["Slip Type", "Employer / Institution", "Filename"]] + \
               [[d.get("slip_type") or "-", d.get("employer") or "-", d.get("filename")]
                for d in documents]
        table = Table(rows, colWidths=[1.3 * inch, 2.8 * inch, 2.2 * inch])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(BRAND)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        s += [table]
    else:
        s += [Paragraph("No documents uploaded.", styles["Normal"])]
    s += [PageBreak()]

    # Page 5 - Housing & Life Changes
    s += [Paragraph("Housing & Life Changes", h1),
          _kv([("Rent paid (2025)", data.get("rent_paid_2025")),
               ("Proof of rent", data.get("rent_proof")),
               ("Changed province", data.get("province_changed")),
               ("Province move details", data.get("province_move_info")),
               ("Graduation date", data.get("graduation_date")),
               ("Left Canada", data.get("left_canada_date")),
               ("Spouse left Canada", data.get("spouse_left_canada_date"))]),
          PageBreak()]

    # Page 6 - Notes & Policies
    s += [Paragraph("Notes & Policies", h1),
          _kv([("Additional notes", data.get("additional_notes"))]),
          Spacer(1, 0.3 * inch),
          Paragraph("<b>Fee policy:</b> All fees are non-refundable once processing begins. "
                    "Initial fee of $45 was charged. Remaining balance will be communicated "
                    "after review.", styles["Normal"])]

    doc.build(s, onFirstPage=_decorate, onLaterPages=_decorate)
    return buf.getvalue()


async def generate_tax_summary_pdf(db, client_id) -> bytes:
    client = await db.get(Client, client_id)
    if client is None:
        raise ValueError(f"client {client_id} not found")
    docs = (await db.scalars(select(Document).where(Document.client_id == client_id))).all()
    tenant = await db.get(Tenant, client.tenant_id)
    sub = (await db.scalars(select(Submission).where(Submission.client_id == client_id))).first()

    branding = {**BRANDING_DEFAULT, **((tenant.config or {}).get("branding", {}) if tenant else {})}
    documents = [{"slip_type": d.slip_type, "employer": d.employer_name, "filename": d.filename}
                 for d in docs]
    ref = (sub.reference_number or str(sub.id)) if sub else "-"
    data = dict(client.raw_answers or {})
    data["sin"] = reveal_sin(client.sin)      # decrypted for the firm's working copy
    if client.spouse_json and client.spouse_json.get("sin"):
        data["spouse_sin"] = reveal_sin(client.spouse_json["sin"])
    return build_summary_pdf(data, documents, branding, ref)

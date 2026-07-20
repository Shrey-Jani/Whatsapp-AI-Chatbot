from app.pdf_generator import BRANDING_DEFAULT, build_summary_pdf


def test_build_summary_pdf_is_valid_multipage():
    data = {
        "full_name": "Jane Doe", "phone": "4160001111", "email": "jane@example.com",
        "sin": "046454286", "dob": "01/01/1990", "address": "1 King St, Toronto ON",
        "marital_status": "Married", "spouse_name": "John Doe", "spouse_dob": "02/02/1988",
        "rent_paid_2025": "12000", "province_changed": "No", "additional_notes": "none",
    }
    docs = [{"slip_type": "T4", "employer": "ACME CORP", "filename": "t4.pdf"},
            {"slip_type": "T5", "employer": "RBC", "filename": "t5.pdf"}]
    pdf = build_summary_pdf(data, docs, BRANDING_DEFAULT, submission_id="123")
    assert pdf[:4] == b"%PDF"          # a real PDF
    assert len(pdf) > 2500             # non-trivial (6 pages of content)

from app.pdf_parser import extract_key_fields, identify_slip_type


def test_identify_t4():
    assert identify_slip_type("... T4 Statement of Remuneration Paid ...") == "T4"


def test_identify_t5():
    assert identify_slip_type("RBC - Statement of Investment Income (T5)") == "T5"


def test_identify_unknown():
    assert identify_slip_type("just some random document text") == "unknown"


def test_extract_income_picks_largest_amount():
    text = ("ACME CORP LTD\n"
            "T4 Statement of Remuneration Paid\n"
            "Box 22 Income tax deducted 8,400.00\n"
            "Box 14 Employment income 52,000.00")
    fields = extract_key_fields(text, "T4")
    assert fields["income_amount"] == "52000.00"
    assert fields["employer_name"] == "ACME CORP LTD"

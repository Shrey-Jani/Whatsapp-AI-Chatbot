from app.chat_engine import _done_message, _payment_terms, get_next_question, validate_answer
from app.question_flow import QUESTIONS


def _q(field):
    return next(x for x in QUESTIONS if x["field"] == field)


def test_address_requires_postal_code():
    assert not validate_answer("21 purebooke ave", _q("address"))[0]      # client bug: no postal
    assert validate_answer("21 Purebrook Ave, Toronto M5V 3L9", _q("address"))[0]


def test_gst_reporting_period_requires_year():
    assert not validate_answer("some period", _q("corp_gst_reporting"))[0]
    assert validate_answer("Jan 2025 to Dec 2025", _q("corp_gst_reporting"))[0]


def test_kb_link_removed_everywhere():
    # Client removed the "refund lower than friends" KB link + message from the closing (PDF p11).
    kb = "benefits-discrepancies"
    assert kb not in _done_message({"service_type": "Personal Tax"})
    assert kb not in _done_message({"service_type": "Business Registration",
                                    "reg_type": "New Incorporation"})
    assert kb not in _done_message({"service_type": "Corporate Tax"})


def test_etransfer_email_shown_at_completion():
    assert "raviaccuratetax@gmail.com" in _payment_terms({"service_type": "Personal Tax"})


def test_cra_rep_auth_guidance_shown():
    from app.chat_engine import advance
    seed = {"customer_status": "New Customer", "service_type": "Personal Tax", "full_name": "A B",
            "phone": "4160001234", "email": "a@b.com", "sin": "046454286", "sin_document": "skip",
            "dob": "01/01/1990", "address": "1 St, Toronto M5V 3L9", "age": "36", "landed_2024": "No"}
    reply, _ = advance(dict(seed), "Yes")     # answering has_mycra = Yes
    assert "active CRA My Account" in reply and "6. Grant us Level 2" in reply
    assert "2026 tuition credits" in reply and "2025 Notice of Assessment" in reply


def test_client_folder_and_categorized_form():
    from app.submission import client_folder, categorized_form
    a = {"full_name": "Vijay Sahi", "phone": "647 402 1615", "sin": "046454286",
         "marital_status": "Married", "spouse_name": "Kamal", "has_mycra": "Yes"}
    assert client_folder(a) == "Vijay_Sahi_6474021615"          # <Name>_<phone>, sanitised
    form = categorized_form(a, [{"slip_type": "T4", "filename": "t4.pdf"}])
    for section in ("Basic info", "Income info", "Spouse info", "Dependent info",
                    "NOA shared or not", "CRA access given or not"):
        assert section in form
    assert "046454286" not in form and "•••-•••-286" in form    # SIN masked, not plaintext


def test_policies_shown_on_every_completion():
    # The old one-line no-refund policy is retired; the full Policies & Procedures closes EVERY
    # completion, for all services.
    for svc in ({"service_type": "Personal Tax"}, {"service_type": "GST/HST"},
                {"service_type": "Corporate Tax"},
                {"service_type": "Business Registration", "reg_type": "Annual Renewal"}):
        msg = _payment_terms(svc)
        assert "Policies & Procedures" in msg
        assert "partially or fully non-refundable" in msg
    assert "final no refunds once processing" not in _payment_terms({"service_type": "Corporate Tax"})


def test_personal_closing_is_client_payment_message():
    msg = _payment_terms({"service_type": "Personal Tax"})
    assert "initial payment of $45" in msg and "raviaccuratetax@gmail.com" in msg
    assert "Personal tax return (up to 3 slips): $60" in msg


def test_batch_additions():
    from app.chat_engine import _done_message
    from app.question_flow import QUESTIONS, SERVICE_Q
    fields = {q["field"] for q in QUESTIONS}
    # new deduction question sets present
    for f in ("has_gym", "gym_province", "has_childcare", "childcare_details",
              "has_northern_travel", "northern_zone"):
        assert f in fields
    # tuition question removed
    assert "has_tuition" not in fields and "graduation_2025" not in fields
    # Others service option + its enquiry, and its own hand-off completion
    assert "Others" in SERVICE_Q["options"] and "others_enquiry" in fields
    assert "contact you" in _done_message({"service_type": "Others"})
    # NOA "have us obtain" is now $80
    noa = next(q for q in QUESTIONS if q["field"] == "noa_method")
    assert any("$80" in o for o in noa["options"])


def test_address_geocode_soft_check(monkeypatch):
    import app.chat_engine as ce
    monkeypatch.setattr(ce.llm, "configured", lambda: False)        # deterministic parse
    monkeypatch.setattr(ce.geocode, "configured", lambda: True)     # verification on
    seed = {"customer_status": "New Customer", "service_type": "Personal Tax", "full_name": "A B",
            "phone": "4160001234", "email": "a@b.com", "sin": "046454286", "sin_document": "skip",
            "dob": "01/01/1990"}
    assert ce.get_next_question(seed)["field"] == "address"

    # 1) unverifiable address → rejected once, with the suggestion; NOT stored
    monkeypatch.setattr(ce.geocode, "verify",
                        lambda a: {"ok": False, "suggestion": "10 King St W, Toronto ON M5H 1A1"})
    s = dict(seed)
    reply, done = ce.advance(s, "999 Fakeplace Rd, Nowhere M5V 3L9")
    assert "couldn't verify" in reply and "Did you mean" in reply and not done
    assert "address" not in s                                        # rejected, not saved

    # 2) user re-sends the SAME address → accepted as typed, flow advances
    reply2, _ = ce.advance(s, "999 Fakeplace Rd, Nowhere M5V 3L9")
    assert s.get("address") == "999 Fakeplace Rd, Nowhere M5V 3L9"

    # 3) a verifiable address is accepted on the first try
    monkeypatch.setattr(ce.geocode, "verify", lambda a: {"ok": True, "suggestion": a})
    s2 = dict(seed)
    ce.advance(s2, "10 King St W, Toronto ON M5H 1A1")
    assert s2.get("address") == "10 King St W, Toronto ON M5H 1A1"


def test_completion_authorization_and_payment_flow():
    import app.chat_engine as ce
    from app.question_flow import QUESTIONS
    fields = {q["field"] for q in QUESTIONS}
    # new moving sub-flow + gig GST questions exist
    for f in ("move_date", "province_from", "move_reason", "move_40km", "move_expenses",
              "gig_has_gst", "gig_netfile", "authorization_agreed", "payment_reference"):
        assert f in fields
    st = {"customer_status": "New Customer", "service_type": "Personal Tax", "full_name": "A B",
          "phone": "4160001234", "email": "a@b.com", "sin": "046454286", "sin_document": "skip",
          "dob": "01/01/1990", "address": "1 St, Toronto M5V 3L9", "age": "36", "landed_2024": "No",
          "has_mycra": "Yes", "marital_status": "Single", "filed_last_year": "Yes",
          "income_slips": "skip", "is_gig": "No", "owns_rental": "No", "first_home": "No",
          "has_medical": "No", "has_donations": "No", "has_gym": "No", "has_childcare": "No",
          "has_northern_travel": "No", "is_student": "No", "rent_paid_2025": "0",
          "province_changed": "No", "left_canada_date": "No", "additional_notes": "none",
          "third_party_payer": "No"}
    s = dict(st)
    r1, d1 = ce.advance(s, "YES")                 # confirm details → fee/payment terms + ask reference
    assert "$45" in r1 and "Interac" in r1 and not d1
    r2, d2 = ce.advance(s, "CA1234ABCD")          # pay (reference) → authorization step
    assert "Authorization" in r2 and not d2
    r3, d3 = ce.advance(s, "Yes")                 # agree → done
    assert d3 and s["payment_reference"] == "CA1234ABCD" and s["authorization_agreed"] == "Yes"


def test_gst_registration_closing():
    reg = _payment_terms({"service_type": "GST/HST", "gst_service": "Register for a GST Number"})
    assert "Registration Fee: $85" in reg and "GST Program Account Registration" in reg
    assert "raviaccuratetax@gmail.com" in reg and "Policies & Procedures" in reg
    # a GST *return* still uses the $45 pricing message, not the $85 registration one
    ret = _payment_terms({"service_type": "GST/HST", "gst_service": "File a GST Return"})
    assert "Registration Fee: $85" not in ret and "initial payment of $45" in ret


def test_benefits_explainer_personal_tax_only_at_end():
    personal = _payment_terms({"service_type": "Personal Tax"})
    assert "Canada Child Benefit (CCB)" in personal
    assert personal.rstrip().endswith("claim all applicable ones on your tax return.")  # very end
    for svc in ({"service_type": "GST/HST"}, {"service_type": "Corporate Tax"}):
        assert "Canada Child Benefit" not in _payment_terms(svc)


def test_gst_number_format_validation():
    assert validate_answer("719404519RT0001", _q("corp_gst_number"))[0]     # p14: valid BN
    assert validate_answer("719404519", _q("corp_gst_number"))[0]
    assert validate_answer("none", _q("corp_gst_number"))[0]                # allowed for corp
    assert not validate_answer("2345", _q("corp_gst_number"))[0]            # p15: too short


def test_future_dates_rejected():
    assert not validate_answer("01/01/2050", _q("dob"))[0]
    assert validate_answer("01/01/1990", _q("dob"))[0]


def test_age_dob_cross_check():
    from app.chat_engine import _age_vs_dob_error
    assert _age_vs_dob_error("20", "01/01/1990")      # contradicts DOB → error string
    assert not _age_vs_dob_error("36", "01/01/1990")  # matches → ""


def test_named_company_fee_note():
    from app.pricing import estimate
    named = estimate("Business Registration", {"reg_type": "New Incorporation", "company_type": "Named"})
    numbered = estimate("Business Registration", {"reg_type": "New Incorporation", "company_type": "Numbered"})
    assert "NUANS" in named and "NUANS" not in numbered

# customer_status (New/Existing) is asked first, then service_type - include both in fixtures.
CORE = {"customer_status": "New Customer", "service_type": "Personal Tax",
        "full_name": "Jane Doe", "phone": "4161234567", "email": "jane@example.com",
        "sin": "046454286", "sin_document": "skip", "dob": "01/01/1990"}


def test_customer_status_asked_first():
    assert get_next_question({})["field"] == "customer_status"


def test_service_router_asked_second():
    assert get_next_question({"customer_status": "New Customer"})["field"] == "service_type"


def test_router_directs_to_corporate():
    q = get_next_question({"customer_status": "New Customer", "service_type": "Corporate Tax"})
    assert q["workflow"] == "Corporate Tax"
    assert q["field"] == "corporation_name"


def test_router_directs_to_gst():
    answers = {"customer_status": "New Customer", "service_type": "GST/HST"}
    assert get_next_question(answers)["field"] == "gst_service"


def test_flow_reaches_address_after_core():
    q = get_next_question(dict(CORE))
    assert q["field"] == "address"


def test_single_filer_skips_spouse_and_children():
    answers = dict(CORE, address="x", age="35", landed_2024="No", has_mycra="Yes", marital_status="Single")
    # spouse + marital-date + children questions are all conditional → skipped for Single
    assert get_next_question(answers)["field"] == "filed_last_year"


def test_existing_customer_asked_sin_first():
    answers = {"service_type": "Personal Tax", "customer_status": "Existing Customer"}
    assert get_next_question(answers)["field"] == "sin"


def test_new_customer_asked_name_first():
    answers = {"service_type": "Personal Tax", "customer_status": "New Customer"}
    assert get_next_question(answers)["field"] == "full_name"


def test_prefilled_existing_asked_to_confirm():
    answers = {"service_type": "Personal Tax", "customer_status": "Existing Customer",
               "sin": "046454286", "profile_prefilled": "yes", "full_name": "A B",
               "phone": "4160001234", "email": "a@b.com", "dob": "01/01/1990",
               "address": "1 St", "marital_status": "Single"}
    assert get_next_question(answers)["field"] == "details_ok"


def test_age_asked_before_marital_status():
    answers = dict(CORE, address="1 King St")
    assert get_next_question(answers)["field"] == "age"


def test_age_bounds():
    q = {"type": "number", "min": 0, "max": 120}
    assert validate_answer("35", q)[0]
    assert not validate_answer("500", q)[0]      # over max
    assert not validate_answer("-2", q)[0]       # under min


def test_married_flow_asks_marriage_date():
    answers = dict(CORE, address="x", age="35", landed_2024="No", has_mycra="Yes", marital_status="Married")
    assert get_next_question(answers)["field"] == "marriage_date"


def test_widowed_flow_asks_date_of_death():
    answers = dict(CORE, address="x", age="35", landed_2024="No", has_mycra="Yes", marital_status="Widowed")
    assert get_next_question(answers)["field"] == "date_of_death"


def test_full_name_requires_two_words():
    q = {"type": "text", "check": "fullname"}
    assert validate_answer("Shrey Jani", q)[0]
    assert validate_answer("Shrey MiteshBhai Jani", q)[0]
    assert validate_answer("Shrey Shrey", q)[0]
    assert not validate_answer("Shrey", q)[0]
    assert not validate_answer("Neil", q)[0]


def test_code_field_rejects_non_answer():
    q = {"type": "text", "check": "code"}
    assert not validate_answer("I do not have", q)[0]   # spaces / words → rejected
    assert not validate_answer("no", q)[0]               # too short
    assert validate_answer("RT0001", q)[0]               # real code
    assert validate_answer("none", q)[0]                 # sentinel for "not set up"


def test_allowed_literal_preserved_over_llm():
    # A literal the ai_parse quotes (No / unknown / none) must be returned verbatim,
    # never sent to the LLM - which was turning "No" into an empty value and looping.
    from app.chat_engine import parse_answer
    q_no = {"type": "text", "check": "date_or_no", "ai_parse": "Departure date DD/MM/YYYY, or 'No'."}
    assert parse_answer("No", q_no)["value"] == "No"
    assert parse_answer("no", q_no)["value"].lower() == "no"
    q_unknown = {"type": "text", "ai_parse": "Extract the figure, or 'unknown'."}
    assert parse_answer("unknown", q_unknown)["value"] == "unknown"
    q_none = {"type": "text", "check": "code", "ai_parse": "Extract the GST number or 'none'."}
    assert parse_answer("none", q_none)["value"] == "none"


def test_date_or_no_check():
    q = {"type": "text", "check": "date_or_no"}
    assert validate_answer("No", q)[0]                     # legit 'No'
    assert validate_answer("15/06/2025", q)[0]             # legit date
    assert not validate_answer("I left last summer", q)[0]  # vague → rejected


def test_sin_luhn_validation():
    assert validate_answer("046454286", {"type": "text", "check": "sin"})[0]
    assert not validate_answer("123456789", {"type": "text", "check": "sin"})[0]


def test_date_accepts_any_year_when_unconstrained():
    assert validate_answer("31/08/2022", {"type": "date"})[0]      # DOB etc. - any year
    assert validate_answer("01-01-1999", {"type": "date"})[0]      # dashes ok
    assert not validate_answer("2022/31/08", {"type": "date"})[0]  # month 31 → real-date check


def test_date_year_constraint():
    q = {"type": "date", "year": 2024}
    assert validate_answer("15/06/2024", q)[0]
    assert not validate_answer("15/06/2022", q)[0]
    assert not validate_answer("15/06/2025", q)[0]


def test_edit_field_at_review(monkeypatch):
    import app.chat_engine as ce
    monkeypatch.setattr(ce, "parse_answer", lambda t, q, lang="English": {"value": t, "confidence": 1.0})
    # a completed-except-confirmation state (confirmation is the current question)
    state = {"customer_status": "New Customer", "service_type": "Personal Tax", "full_name": "A B",
             "phone": "4160001234", "email": "old@x.com", "sin": "046454286", "sin_document": "skip",
             "dob": "01/01/1990", "address": "1 St", "age": "35", "landed_2024": "No",
             "has_mycra": "Yes", "marital_status": "Single", "filed_last_year": "Yes",
             "income_slips": "skip", "is_gig": "No", "owns_rental": "No",
             "first_home": "No", "has_medical": "No", "has_donations": "No",
             "has_gym": "No", "has_childcare": "No", "has_northern_travel": "No", "is_student": "No",
             "rent_paid_2025": "0", "province_changed": "No", "left_canada_date": "No",
             "additional_notes": "none", "third_party_payer": "No"}
    assert ce.get_next_question(state)["field"] == "confirmation"
    reply, done = ce.advance(state, "change my email")
    assert "email" not in state          # email was cleared for re-entry
    assert not done
    assert "email" in reply.lower()      # the email question is re-asked


def test_go_back_re_asks_previous_question(monkeypatch):
    import app.chat_engine as ce
    monkeypatch.setattr(ce, "parse_answer", lambda t, q, lang="English": {"value": t, "confidence": 1.0})
    # answer New/Existing, then service type; then "go back" should undo service_type and re-ask it
    state = {}
    ce.advance(state, None)                       # asks customer_status
    ce.advance(state, "New Customer")             # answered → asks service_type
    ce.advance(state, "GST/HST")                  # answered service_type
    assert state.get("service_type") == "GST/HST"
    reply, done = ce.advance(state, "sorry wrong choice")
    assert "service_type" not in state            # the last answer was undone
    assert "type of tax" in reply.lower()         # service_type question re-asked
    assert not done


def test_manual_escalation():
    import app.chat_engine as ce
    state = {"service_type": "Personal Tax"}
    reply, done = ce.advance(state, "agent")
    assert done and state.get("_escalate")
    assert "staff" in reply.lower()


def test_repeated_errors_auto_escalate(monkeypatch):
    import app.chat_engine as ce
    monkeypatch.setattr(ce, "parse_answer",
                        lambda t, q, lang=None: {"value": t, "confidence": 1.0})
    state = {"service_type": "Personal Tax", "customer_status": "New Customer",
             "full_name": "Jane Doe", "phone": "4160001111", "email": "jane@example.com"}
    # next question is SIN - feed an invalid one repeatedly
    assert not ce.advance(state, "123456789")[1]      # error 1
    assert not ce.advance(state, "123456789")[1]      # error 2
    reply, done = ce.advance(state, "123456789")      # error 3 → escalate
    assert done and state.get("_escalate")


def test_multi_platform_triggers_mandatory_gst():
    import app.chat_engine as ce
    assert ce._multi_platform("I drive for Uber and Lyft")      # 2 platforms → mandatory
    assert ce._multi_platform("uber, doordash, instacart")
    assert not ce._multi_platform("just Uber")                  # single platform → not mandatory
    assert not ce._multi_platform("")


def test_new_resident_asked_mycra_and_gets_repid(monkeypatch):
    import app.chat_engine as ce
    monkeypatch.setattr(ce, "parse_answer", lambda t, q, lang="English": {"value": t, "confidence": 1.0})
    # New customer, resides in Canada (landed_2024=No) → gets the myCRA question
    answers = dict(CORE, address="1 St", age="35", landed_2024="No")
    assert ce.get_next_question(answers)["field"] == "has_mycra"
    # answering Yes surfaces the Rep-ID instruction
    state = dict(answers)
    reply, _ = ce.advance(state, "Yes")
    assert "Representative ID" in reply


def test_newcomer_gets_world_income_notice(monkeypatch):
    import app.chat_engine as ce
    monkeypatch.setattr(ce, "parse_answer", lambda t, q, lang="English": {"value": t, "confidence": 1.0})
    state = dict(CORE, address="1 St", age="35")   # landed_2024 is the next question
    reply, _ = ce.advance(state, "Yes")            # landed in Canada in 2024
    assert "worldwide income" in reply.lower()     # guidance stays; links removed per client
    assert "canada.ca" not in reply                # all resource links removed


def test_incorporation_shows_etransfer_directive(monkeypatch):
    import app.chat_engine as ce
    monkeypatch.setattr(ce, "parse_answer", lambda t, q, lang="English": {"value": t, "confidence": 1.0})
    state = {"customer_status": "New Customer", "service_type": "Business Registration"}
    reply, _ = ce.advance(state, "New Incorporation")     # reg_type answered
    assert "e-Transfer" in reply


def test_gst_register_shows_procurement_sla(monkeypatch):
    import app.chat_engine as ce
    monkeypatch.setattr(ce, "parse_answer", lambda t, q, lang="English": {"value": t, "confidence": 1.0})
    state = {"customer_status": "New Customer", "service_type": "GST/HST"}
    reply, _ = ce.advance(state, "Register for a GST Number")   # gst_service answered
    assert "2 to 3 weeks" in reply


def test_sin_encryption_round_trip():
    from cryptography.fernet import Fernet
    from app import security
    from app.config import settings
    settings.sin_encryption_key = Fernet.generate_key().decode()
    security._fernet.cache_clear()
    enc = security.protect_sin("046 454 286")
    assert enc.startswith("enc:") and enc != "046 454 286"      # stored value is ciphertext
    assert security.reveal_sin(enc) == "046 454 286"            # round-trips
    assert security.reveal_sin("plaintext-passthrough") == "plaintext-passthrough"
    assert security.digits("046 454 286") == "046454286"        # normalised for lookup


def test_pricing_estimates():
    from app.pricing import estimate
    assert "45" in estimate("Personal Tax", {})                                   # standard
    assert "70" in estimate("Personal Tax", {"is_gig": "Yes"})                    # gig
    assert "85" in estimate("GST/HST", {"gst_service": "Register for a GST Number"})
    assert "50" in estimate("GST/HST", {"gst_service": "File a GST Return"})
    assert "275" in estimate("Corporate Tax", {})
    assert "350" in estimate("Business Registration", {"reg_type": "New Incorporation"})
    assert "62" in estimate("Business Registration", {"reg_type": "Annual Renewal"})


def test_closing_message_after_completion():
    import app.chat_engine as ce
    state = {"_done": True}
    reply, done = ce.advance(state, "okay, thank you!")
    assert reply == "Thank you, Have a nice day."
    assert done


def test_name_answer_advances_to_phone(monkeypatch):
    import app.chat_engine as ce
    monkeypatch.setattr(ce, "parse_answer",
                        lambda text, q, lang=None: {"value": "John Doe", "confidence": 1.0})
    state = {"service_type": "Personal Tax", "customer_status": "New Customer"}  # name is next
    ce.advance(state, None)
    reply, done = ce.advance(state, "My name is John Doe")
    assert state["full_name"] == "John Doe"
    assert not done
    assert "mobile number" in reply.lower()

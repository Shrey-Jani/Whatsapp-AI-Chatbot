from app.chat_engine import get_next_question, validate_answer

# customer_status (New/Existing) is asked first, then service_type — include both in fixtures.
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
    answers = dict(CORE, address="x", age="35", landed_2024="No", marital_status="Single")
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
    answers = dict(CORE, address="x", age="35", landed_2024="No", marital_status="Married")
    assert get_next_question(answers)["field"] == "marriage_date"


def test_widowed_flow_asks_date_of_death():
    answers = dict(CORE, address="x", age="35", landed_2024="No", marital_status="Widowed")
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


def test_date_or_no_check():
    q = {"type": "text", "check": "date_or_no"}
    assert validate_answer("No", q)[0]                     # legit 'No'
    assert validate_answer("15/06/2025", q)[0]             # legit date
    assert not validate_answer("I left last summer", q)[0]  # vague → rejected


def test_sin_luhn_validation():
    assert validate_answer("046454286", {"type": "text", "check": "sin"})[0]
    assert not validate_answer("123456789", {"type": "text", "check": "sin"})[0]


def test_date_accepts_any_year_when_unconstrained():
    assert validate_answer("31/08/2022", {"type": "date"})[0]      # DOB etc. — any year
    assert validate_answer("01-01-1999", {"type": "date"})[0]      # dashes ok
    assert not validate_answer("2022/31/08", {"type": "date"})[0]  # month 31 → real-date check


def test_date_year_constraint():
    q = {"type": "date", "year": 2024}
    assert validate_answer("15/06/2024", q)[0]
    assert not validate_answer("15/06/2022", q)[0]
    assert not validate_answer("15/06/2025", q)[0]


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
    # next question is SIN — feed an invalid one repeatedly
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

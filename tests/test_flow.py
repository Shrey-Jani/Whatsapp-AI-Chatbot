from app.chat_engine import get_next_question, validate_answer


def test_after_five_answers_next_is_sixth():
    answers = {
        "full_name": "Jane Doe",
        "phone": "4161234567",
        "email": "jane@example.com",
        "sin": "046454286",
        "dob": "01/01/1990",
    }
    q = get_next_question(answers)
    assert q["id"] == 6
    assert q["field"] == "address"


def test_single_filer_skips_spouse_questions():
    answers = {f: "x" for f in
               ["full_name", "phone", "email", "sin", "dob", "address"]}
    answers["marital_status"] = "Single"
    q = get_next_question(answers)
    # spouse questions (ids 8-12) are conditional on Married/Common-Law → skipped
    assert q["field"] == "newborn_2025"


def test_sin_luhn_validation():
    ok, _ = validate_answer("046454286", {"type": "text", "check": "sin"})
    assert ok
    bad, _ = validate_answer("123456789", {"type": "text", "check": "sin"})
    assert not bad


def test_name_answer_advances_to_phone(monkeypatch):
    import app.chat_engine as ce
    # Mock Gemini so the test is hermetic: "My name is John Doe" → "John Doe".
    monkeypatch.setattr(ce, "parse_answer_with_gemini",
                        lambda text, q: {"value": "John Doe", "confidence": 1.0})
    state = {}
    ce.advance(state, None)                       # first touch → asks the name question
    reply, done = ce.advance(state, "My name is John Doe")
    assert state["full_name"] == "John Doe"
    assert not done
    assert "mobile number" in reply.lower()       # advanced to the phone question

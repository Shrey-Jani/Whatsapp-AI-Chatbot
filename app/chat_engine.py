"""Channel-agnostic conversation engine. WhatsApp and web both call advance().

Deterministic flow: get_next_question() walks QUESTIONS honouring conditions. Gemini only
interprets messy free-text answers (parse_answer) — it never drives the flow.
Answers live in the session's conversation_state_json; a Client row + Information Sheet get
materialised at confirmation (Phase 5).
"""
import json
import re
from datetime import datetime

from . import i18n, llm
from .pricing import PRICING, estimate
from .question_flow import GST_WARNING, QUESTIONS, RIDESHARE_PLATFORMS

ESCALATE_WORDS = {"agent", "staff", "human", "representative", "help", "support"}
MAX_ERRORS = 3   # repeated validation failures on one question → auto-handoff to staff
DONE_MSG = ("Thank you — we have everything we need. Our team will review your details and "
            "send your Information Sheet and price estimate shortly.\n\n"
            "Payment is by Interac e-Transfer once you approve the estimate; work begins after "
            "payment is confirmed.")
# Mandated verbatim (spec §7) — never translated.
POLICY_MSG = "All transactions are final — no refunds once processing has started."
# ponytail: per-tenant prices + the generated Information Sheet PDF attach here in Phase 5.
CLOSING_MSG = "Thank you, Have a nice day."
# Spec §8 — permanent knowledge-base link, appended to the completion message.
KB_LINK = ("Why is my tax refund or benefit allocation lower, or structurally different, from my "
           "friends or other individuals?\nhttps://www.cra-arc.gc.ca/benefits-discrepancies-explained")
ESCALATE_MSG = "Connecting you with our staff — someone will follow up with you shortly."

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _answers(state: dict) -> dict:
    return {k: v for k, v in state.items() if not k.startswith("_")}


def get_next_question(answers: dict) -> dict | None:
    """First question that is unanswered, in the selected workflow, whose condition passes."""
    service = answers.get("service_type")
    for q in QUESTIONS:
        if q["field"] in answers:
            continue
        wf = q.get("workflow")
        if wf is not None and wf != service:   # question belongs to a different workflow
            continue
        cond = q.get("condition")
        if cond and not cond(answers):
            continue
        return q
    return None


# ---- validation --------------------------------------------------------------

def _luhn_ok(num: str) -> bool:
    """Canadian SIN check digit (Luhn)."""
    if not (len(num) == 9 and num.isdigit()):
        return False
    total = 0
    for i, ch in enumerate(num):
        d = int(ch)
        if i % 2 == 1:            # double every second digit
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _normalize_date(v: str) -> str | None:
    """Accept DD/MM/YYYY or ISO YYYY-MM-DD (/, -, or . separators), ANY year.

    Returns the canonical DD/MM/YYYY string, or None if it isn't a real calendar date.
    """
    v = (v or "").strip()
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$", v)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.match(r"^(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})$", v)   # ISO order
        if not m:
            return None
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        datetime(y, mo, d)
    except ValueError:
        return None
    return f"{d:02d}/{mo:02d}/{y:04d}"


def validate_answer(value: str, question: dict) -> tuple[bool, str]:
    t = question["type"]
    v = (value or "").strip()

    if question.get("check") == "sin":
        return (True, "") if _luhn_ok(v) else (False, "That SIN isn't valid — please enter the 9 digits again.")
    if question.get("check") == "fullname":
        parts = [p for p in v.split() if any(c.isalpha() for c in p)]
        return (True, "") if len(parts) >= 2 else \
            (False, "Please enter your full legal name — first and last name (e.g. Shrey Jani).")
    if question.get("check") == "code":
        return (True, "") if re.fullmatch(r"[A-Za-z0-9\-]{3,}", v) else \
            (False, "That doesn't look like a valid code — enter it exactly (letters/numbers, no spaces).")
    if question.get("check") == "date_or_no":
        if v.lower() == "no":
            return True, ""
        return (True, "") if _normalize_date(v) else (False, "Enter a date as DD/MM/YYYY, or reply 'No'.")
    if t == "textarea":
        return True, ""                       # notes may be short / 'none'
    if t == "file":
        return True, ""                       # presence handled by the upload path
    if not v:
        return False, "This can't be empty — please provide an answer."
    if t == "email":
        return (True, "") if EMAIL_RE.match(v) else (False, "That doesn't look like a valid email. Try again.")
    if t == "phone":
        digits = re.sub(r"\D", "", v)
        return (True, "") if len(digits) >= 10 else (False, "Please enter a valid phone number (at least 10 digits).")
    if t == "number":
        try:
            n = float(v.replace("$", "").replace(",", ""))
        except ValueError:
            return False, "Please enter a number."
        if n < question.get("min", float("-inf")):
            return False, f"Please enter a number of at least {question['min']}."
        if n > question.get("max", float("inf")):
            return False, f"Please enter a number no greater than {question['max']}."
        return True, ""
    if t == "date":
        nd = _normalize_date(v)
        if not nd:
            return False, "Please use the format DD/MM/YYYY."
        yr = question.get("year")                 # some dates must fall in a specific tax year
        if yr and int(nd[-4:]) != yr:
            return False, f"That date must be within {yr}. Please enter a {yr} date (DD/MM/YYYY)."
        return True, ""
    if t in ("select", "boolean"):
        return (True, "") if v in question["options"] else (False, "Please pick one of the listed options.")
    return True, ""


# ---- Gemini parsing ----------------------------------------------------------

def _choose(question: dict, user_text: str) -> str:
    """Deterministic select/boolean: accept the option number or its exact text."""
    t = user_text.strip()
    opts = question["options"]
    if t.isdigit() and 1 <= int(t) <= len(opts):
        return opts[int(t) - 1]
    for o in opts:
        if t.lower() == o.lower():
            return o
    return t  # let validation reject if it doesn't match


def _safe_json(text: str) -> dict:
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}


def parse_answer(user_text: str, question: dict, lang: str = i18n.DEFAULT) -> dict:
    """Return {"value": str, "confidence": float}. Falls back to raw text if the LLM is off."""
    if question["type"] in ("select", "boolean"):
        choice = _choose(question, user_text)
        if choice not in question["options"] and lang != i18n.DEFAULT:
            # e.g. they answered "haan" instead of "Yes" — map it back onto an option.
            choice = i18n.map_choice(user_text, tuple(question["options"]), lang) or choice
        return {"value": choice, "confidence": 1.0}
    if question["type"] == "file":
        return {"value": user_text.strip() or "[uploaded]", "confidence": 1.0}
    if not llm.configured():
        return {"value": user_text.strip(), "confidence": 1.0}
    try:
        prompt = (f'{question["ai_parse"]}\n\nUser message: "{user_text}"\n'
                  'If the message does not actually contain a valid answer (e.g. the user says '
                  'they do not have it, declines, or it is unrelated), return an empty string for '
                  'value.\nRespond with ONLY JSON: {"value": "<extracted>", "confidence": <0.0-1.0>}')
        data = _safe_json(llm.complete(prompt))
        return {"value": str(data.get("value", user_text)).strip(),
                "confidence": float(data.get("confidence", 0.5))}
    except Exception as e:
        # Never let an LLM hiccup break intake — fall back to the raw answer, but log why.
        print(f"[llm] fell back to raw text: {e}")
        return {"value": user_text.strip(), "confidence": 1.0}


# ---- main loop ---------------------------------------------------------------

def _multi_platform(text: str) -> bool:
    """2+ distinct rideshare/delivery platforms → a dedicated GST number is mandatory (spec §4)."""
    low = (text or "").lower()
    return sum(1 for p in RIDESHARE_PLATFORMS if p in low) >= 2


def _render(q: dict, lang: str = i18n.DEFAULT) -> str:
    body = q["prompt"]
    if q.get("options"):
        opts = "\n".join(f"{i + 1}. {o}" for i, o in enumerate(q["options"]))
        body = f"{body}\n{opts}"
    body = i18n.localize(body, lang)
    # Mandated verbatim legal text — deliberately NOT translated (needs human sign-off).
    pre = q.get("preamble")
    return f"{pre}\n\n{body}" if pre else body


def _done_message(answers: dict, lang: str = i18n.DEFAULT) -> str:
    est = estimate(answers.get("service_type"), answers)
    body = f"{est}\n\n{DONE_MSG}" if est else DONE_MSG
    return f"{i18n.localize(body, lang)}\n\n{POLICY_MSG}\n\n{KB_LINK}"


def advance(state: dict, user_text: str | None, greeting: str | None = None) -> tuple[str, bool]:
    """Given prior state + latest user message, return (reply, done). Mutates state in place.

    `greeting` is the user's very first message — used once to detect their language so the
    bot can acknowledge them and continue in the same language + script.
    """
    answers = _answers(state)
    q = get_next_question(answers)

    if user_text is None:                     # first contact — ask, don't record
        if greeting and "_lang" not in state:
            state["_lang"] = i18n.detect(greeting)
        lang = state.get("_lang", i18n.DEFAULT)
        if q is None:
            return _done_message(answers, lang), True
        return (i18n.greet_and_ask(greeting, _render(q, lang), lang) if greeting
                else _render(q, lang)), False

    lang = state.get("_lang", i18n.DEFAULT)

    if state.get("_escalate"):                # already handed off — stay with staff
        return i18n.localize(ESCALATE_MSG, lang), True

    if user_text.strip().lower() in ESCALATE_WORDS:
        state["_escalate"] = True
        state["_escalate_reason"] = "customer requested staff"
        return i18n.localize(ESCALATE_MSG, lang), True

    if state.get("_done") or q is None:       # intake already finished — just close politely
        state["_done"] = True
        return i18n.localize(CLOSING_MSG, lang), True

    parsed = parse_answer(user_text, q, lang)
    value = parsed["value"]
    if q["type"] == "date":                   # accept any year / separators, store DD/MM/YYYY
        value = _normalize_date(value) or value
    ok, err = validate_answer(value, q)
    if not ok:
        ekey = f"_err_{q['field']}"
        state[ekey] = state.get(ekey, 0) + 1
        if state[ekey] >= MAX_ERRORS:         # repeated validation errors → auto-handoff
            state["_escalate"] = True
            state["_escalate_reason"] = f"repeated validation errors on '{q['field']}'"
            return i18n.localize(
                "I'm having trouble understanding your answer — I'm connecting you with our "
                "staff, who will follow up with you shortly.", lang), True
        return f"{i18n.localize(err, lang)}\n\n{_render(q, lang)}", False

    state.pop(f"_err_{q['field']}", None)      # clear the error counter on success
    state[q["field"]] = value
    answers[q["field"]] = value

    extra = ""                                 # spec §4: 2+ platforms → GST number is mandatory
    if q["field"] in ("gig_platforms", "gst_platforms") and _multi_platform(value):
        state["gst_required"] = "Yes"
        extra = (i18n.localize(
            "Because you operate on more than one rideshare/delivery platform, a dedicated "
            f"GST number is MANDATORY (registration ${PRICING['gst_setup']['flat']}).", lang)
            + f"\n\n{GST_WARNING}")     # warning itself stays verbatim English

    nxt = get_next_question(answers)
    if nxt is None:                           # last answer → estimate + thank-you + KB link
        state["_done"] = True
        return _done_message(answers, lang), True
    return (f"{extra}\n\n{_render(nxt, lang)}" if extra else _render(nxt, lang)), False

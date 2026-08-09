"""Channel-agnostic conversation engine. WhatsApp and web both call advance().

Deterministic flow: get_next_question() walks QUESTIONS honouring conditions. Gemini only
interprets messy free-text answers (parse_answer) - it never drives the flow.
Answers live in the session's conversation_state_json; a Client row + Information Sheet get
materialised at confirmation (Phase 5).
"""
import json
import re
from datetime import datetime

from . import geocode, i18n, llm
from .config import settings
from .pricing import PRICING, estimate
from .question_flow import (AUTHORIZATION_MSG, CRA_HELPLINE, ETRANSFER_DIRECTIVE,
                            GIG_GST_NETFILE_HELP, GST_WARNING, PROCUREMENT_SLA, QUESTIONS,
                            RENT_NO_PROOF_GUIDANCE, REP_AUTH_GUIDANCE, GIG_SUMMARY_GUIDANCE,
                            RIDESHARE_PLATFORMS, WORLD_INCOME)

ESCALATE_WORDS = {"agent", "staff", "human", "representative", "help", "support"}
MAX_ERRORS = 3   # repeated validation failures on one question → auto-handoff to staff
# "Take me back to the previous question" - phrases that undo the last answer.
GO_BACK_PHRASES = ("go back", "wrong choice", "wrong answer", "wrong option", "previous question",
                   "made a mistake", "change my answer", "change the previous", "correct the previous",
                   "back to previous", "galat", "oops")
# At the review step a client can edit any field by naming it (e.g. "change my email").
EDIT_ALIASES = {"e-mail": "email", "email": "email", "name": "full_name", "mobile": "phone",
                "contact number": "phone", "phone": "phone", "address": "address",
                "insurance number": "sin", "sin": "sin", "date of birth": "dob", "birth": "dob",
                "dob": "dob", "age": "age", "marital": "marital_status", "marriage": "marital_status"}
# Fields shown in the review summary so the client can catch a mistake (incl. a mistyped SIN).
REVIEW_FIELDS = [("corporation_name", "Corporation"), ("full_name", "Name"),
                 ("business_activity", "Business activity"), ("phone", "Phone"), ("email", "Email"),
                 ("sin", "SIN"), ("age", "Age"), ("dob", "Date of birth"),
                 ("address", "Address"), ("marital_status", "Marital status")]
DONE_MSG = ("Thank you we have everything we need. Our team will review your details and "
            "send your Information Sheet and price estimate shortly.\n\n"
            "Payment is by Interac e-Transfer once you approve the estimate; work begins after "
            "payment is confirmed.")
# Consumer-filing closing (Personal Tax / GST) - the client's exact wording; {email} is the payee.
PAYMENT_CLOSING = ("Thank you for sharing your information.\n\n"
                   "To begin reviewing your file, please send an initial payment of $45 by "
                   "e-transfer to {email}.\n\n"
                   "Once we review your documents, we will advise you of the remaining balance. "
                   "Your $45 initial payment will be deducted from the total service fee.\n\n"
                   "Our pricing:\n"
                   "• Personal tax return (up to 3 slips): $60-$70\n"
                   "• Self-employed / Uber, Lyft, Skip, Instacart, Amazon Flex, etc.: "
                   "Starting from $75\n"
                   "• GST/HST return: $50\n\n"
                   "Thank you. We look forward to assisting you with your tax filing.")
# Firm engagement terms - shown at completion for every service. Mandated legal text: kept
# verbatim (not machine-translated), per the same convention as the old no-refund line.
POLICIES_MSG = (
    "Policies & Procedures\n\n"
    "Thank you for choosing Ravi's Accurate Tax Services.\n\n"
    "By providing your tax information and submitting the initial e-transfer payment, you "
    "acknowledge and agree that our engagement has commenced.\n\n"
    "Service Fees\n"
    "- All service fees are partially or fully non-refundable.\n"
    "- The initial payment is applied toward your total service fees.\n\n"
    "Review & Approval Process\n"
    "- Once your tax return has been prepared, we will provide you with a Tax Summary and any "
    "required authorization forms for your review.\n"
    "- Please review all information carefully. If you have any questions or if any information is "
    "missing or incorrect, notify us before signing. We will gladly answer your questions and make "
    "any necessary corrections at that stage.\n\n"
    "Changes After Approval\n"
    "- Your signature confirms that you have reviewed and approved the information provided.\n"
    "- Any changes requested after the authorization forms have been signed may be subject to "
    "additional fees, depending on the nature of the changes, additional tax slips, or the amount "
    "of work required.\n\n"
    "Client Responsibility\n"
    "To ensure the accuracy of your tax return and avoid delays or additional charges, please "
    "provide all relevant tax slips, supporting documents, and any other information that may "
    "affect your tax return before your file is finalized.\n\n"
    "Thank you for your cooperation. We look forward to assisting you with your tax filing.")
# GST-number registration closing ($85 program-account registration). {email} = payee.
GST_REGISTRATION_CLOSING = (
    "Checklist for Uber/Lyft GST Program Account Registration\n\n"
    "Please provide the following information:\n"
    "- Full Name\n"
    "- Date of Birth\n"
    "- Complete Residential Address\n"
    "- SIN (Social Insurance Number)\n"
    "- Email Address\n"
    "- Contact Number\n\n"
    "Registration Fee: $85\n\n"
    "Please send the payment by e-Transfer to:\n{email}\n\n"
    "Once we receive your information and payment, we will proceed with your Uber/Lyft GST "
    "program account registration.")
# Benefits explainer - Personal Tax only, at the very end of completion. Helpful (translated).
BENEFITS_INFO = (
    "Benefits you may qualify for\n\n"
    "The benefits you may qualify for depend on your personal situation, income, marital status, "
    "and family size.\n\n"
    "Some common benefits include:\n"
    "- Canada Grocery & Essential Benefit\n"
    "- Ontario Trillium Benefit (OTB)\n"
    "- Canada Child Benefit (CCB)\n"
    "- GST/HST Credit\n"
    "- Canada Workers Benefit (CWB)\n\n"
    "Once we review your tax information, we will determine which benefits and credits you are "
    "eligible for and claim all applicable ones on your tax return.")
# ponytail: per-tenant prices + the generated Information Sheet PDF attach here in Phase 5.
CLOSING_MSG = "Thank you, Have a nice day."
ESCALATE_MSG = "Connecting you with our staff - someone will follow up with you shortly."

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
    m = re.match(r"^(\d{1,2})[/\-.\s](\d{1,2})[/\-.\s](\d{4})$", v)   # DD MM YYYY, any separator
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.match(r"^(\d{4})[/\-.\s](\d{1,2})[/\-.\s](\d{1,2})$", v)   # ISO order
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
        return (True, "") if _luhn_ok(v) else (False, "That SIN isn't valid - please enter the 9 digits again.")
    if question.get("check") == "fullname":
        parts = [p for p in v.split() if any(c.isalpha() for c in p)]
        return (True, "") if len(parts) >= 2 else \
            (False, "Please enter your full legal name - first and last name (e.g. Shrey Jani).")
    if question.get("check") == "code":
        return (True, "") if re.fullmatch(r"[A-Za-z0-9\-]{3,}", v) else \
            (False, "That doesn't look like a valid code - enter it exactly (letters/numbers, no spaces).")
    if question.get("check") == "postal":     # a full address must carry a Canadian postal code
        return (True, "") if re.search(r"[A-Za-z]\d[A-Za-z]\s?\d[A-Za-z]\d", v) else \
            (False, "Please include your postal code in the address (e.g. M5V 3L9).")
    if question.get("check") == "period":     # a reporting period must name a year
        return (True, "") if re.search(r"\b(19|20)\d{2}\b", v) else \
            (False, "Please enter the reporting period including the year (e.g. Jan 2025 - Dec 2025).")
    if question.get("check") == "gst":        # Business Number: 9 digits, optional program suffix
        if v.lower() == "none":
            return True, ""                   # some flows allow "none" (not registered yet)
        return (True, "") if re.fullmatch(r"\d{9}([A-Za-z]{2}\d{4})?", v.replace(" ", "")) else \
            (False, "Enter a valid 9-digit GST/HST (Business) number, e.g. 719404519RT0001.")
    if question.get("check") == "date_or_no":
        if v.lower() == "no":
            return True, ""
        return (True, "") if _normalize_date(v) else (False, "Enter a date as DD/MM/YYYY, or reply 'No'.")
    if t == "textarea":
        return True, ""                       # notes may be short / 'none'
    if t == "file":
        return True, ""                       # presence handled by the upload path
    if not v:
        return False, "This can't be empty - please provide an answer."
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
        d, mo, y = int(nd[:2]), int(nd[3:5]), int(nd[6:])   # a DOB / life-event date can't be future
        if datetime(y, mo, d).date() > datetime.now().date():
            return False, "That date is in the future - please check it and re-enter."
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
            # e.g. they answered "haan" instead of "Yes" - map it back onto an option.
            choice = i18n.map_choice(user_text, tuple(question["options"]), lang) or choice
        return {"value": choice, "confidence": 1.0}
    if question["type"] == "file":
        return {"value": user_text.strip() or "[uploaded]", "confidence": 1.0}
    # A short literal the field explicitly allows (its ai_parse quotes it, e.g. 'No', 'none',
    # 'unknown') must be kept verbatim - otherwise the LLM's "return empty when the user
    # declines" rule turns it into an empty value and validation loops.
    raw = user_text.strip()
    if question.get("check") == "date_or_no" and raw.lower() in ("no", "none", "n", "na", "n/a", "nil", "nope"):
        return {"value": "No", "confidence": 1.0}
    if raw and f"'{raw.lower()}'" in question.get("ai_parse", "").lower():
        return {"value": raw, "confidence": 1.0}
    if not llm.configured():
        return {"value": raw, "confidence": 1.0}
    try:
        prompt = (f'{question["ai_parse"]}\n\nUser message: "{user_text}"\n'
                  'If the message does not actually contain a valid answer (e.g. the user says '
                  'they do not have it, declines, or it is unrelated), return an empty string for '
                  'value.\nRespond with ONLY JSON: {"value": "<extracted>", "confidence": <0.0-1.0>}')
        data = _safe_json(llm.complete(prompt))
        return {"value": str(data.get("value", user_text)).strip(),
                "confidence": float(data.get("confidence", 0.5))}
    except Exception as e:
        # Never let an LLM hiccup break intake - fall back to the raw answer, but log why.
        print(f"[llm] fell back to raw text: {e}")
        return {"value": user_text.strip(), "confidence": 1.0}


# ---- main loop ---------------------------------------------------------------

def _age_vs_dob_error(age_str: str, dob_str: str | None) -> str:
    """A stated age that contradicts the DOB by >1 year is a typo - ask them to re-enter."""
    nd = _normalize_date(dob_str or "")
    if not nd:
        return ""
    try:
        age = int(float(age_str))
    except (ValueError, TypeError):
        return ""
    d, mo, y = int(nd[:2]), int(nd[3:5]), int(nd[6:])
    today = datetime.now().date()
    computed = today.year - y - ((today.month, today.day) < (mo, d))
    if abs(age - computed) > 1:
        return f"That age doesn't match your date of birth ({dob_str} ≈ {computed}). Please re-enter your age."
    return ""


def _multi_platform(text: str) -> bool:
    """2+ distinct rideshare/delivery platforms → a dedicated GST number is mandatory (spec §4)."""
    low = (text or "").lower()
    return sum(1 for p in RIDESHARE_PLATFORMS if p in low) >= 2


def _review_summary(answers: dict, lang: str = i18n.DEFAULT) -> str:
    lines = [f"• {label}: {answers[f]}" for f, label in REVIEW_FIELDS if answers.get(f)]
    return i18n.localize("Here's a summary of your details:", lang) + "\n" + "\n".join(lines)


def _render(q: dict, lang: str = i18n.DEFAULT, answers: dict | None = None) -> str:
    body = q["prompt"].replace("{year}", str(settings.tax_year))   # keep prompt year in sync w/ guidance
    if q.get("options"):
        opts = "\n".join(f"{i + 1}. {o}" for i, o in enumerate(q["options"]))
        body = f"{body}\n{opts}"
    body = i18n.localize(body, lang)
    if q["field"] == "confirmation" and answers:      # show the details to review before confirming
        body = f"{_review_summary(answers, lang)}\n\n{body}"
    # Mandated verbatim legal text - deliberately NOT translated (needs human sign-off).
    pre = q.get("preamble")
    return f"{pre}\n\n{body}" if pre else body


def _payment_terms(answers: dict, lang: str = i18n.DEFAULT) -> str:
    """Fee + e-Transfer request + engagement terms. Shown at the authorization step (before the
    client sends payment), then they reply with their Interac reference."""
    service = answers.get("service_type")
    if service == "GST/HST" and answers.get("gst_service") == "Register for a GST Number":
        head = i18n.localize(GST_REGISTRATION_CLOSING.format(email=settings.etransfer_email), lang)
    elif service in ("Personal Tax", "GST/HST"):      # client's $45-initial closing + pricing list
        head = i18n.localize(PAYMENT_CLOSING.format(email=settings.etransfer_email), lang)
    else:
        est = estimate(service, answers)
        body = f"{est}\n\n{DONE_MSG}" if est else DONE_MSG
        head = (f"{i18n.localize(body, lang)}\n\nPlease send your e-Transfer to: "
                f"{settings.etransfer_email}")
    # Firm engagement terms - verbatim (legal text, not translated).
    msg = f"{head}\n\n{POLICIES_MSG}"
    if service == "Personal Tax":                     # benefits explainer (client)
        msg += f"\n\n{i18n.localize(BENEFITS_INFO, lang)}"
    return msg


def _done_message(answers: dict, lang: str = i18n.DEFAULT) -> str:
    """Final acknowledgement. The fee/terms were already shown at the authorization step."""
    service = answers.get("service_type")
    if service == "Others":                           # not a filing - just an enquiry hand-off
        return i18n.localize("Thank you - our team will review your enquiry and contact you "
                             "shortly.", lang)
    ref = (answers.get("payment_reference") or "").strip()
    note = (" We've recorded your payment reference and will confirm your payment shortly."
            if ref and ref.lower() != "skip"
            else " Once you've sent your e-Transfer, reply here with your Interac reference number.")
    return i18n.localize("Thank you - we have everything we need and our team will begin reviewing "
                         "your file." + note, lang)


def advance(state: dict, user_text: str | None, greeting: str | None = None) -> tuple[str, bool]:
    """Given prior state + latest user message, return (reply, done). Mutates state in place.

    `greeting` is the user's very first message - used once to detect their language so the
    bot can acknowledge them and continue in the same language + script.
    """
    answers = _answers(state)
    q = get_next_question(answers)

    if user_text is None:                     # first contact - ask, don't record
        if greeting and "_lang" not in state:
            state["_lang"] = i18n.detect(greeting)
        lang = state.get("_lang", i18n.DEFAULT)
        if q is None:
            return _done_message(answers, lang), True
        return (i18n.greet_and_ask(greeting, _render(q, lang, answers), lang) if greeting
                else _render(q, lang, answers)), False

    lang = state.get("_lang", i18n.DEFAULT)

    if state.get("_escalate"):                # already handed off - stay with staff
        return i18n.localize(ESCALATE_MSG, lang), True

    if user_text.strip().lower() in ESCALATE_WORDS:
        state["_escalate"] = True
        state["_escalate_reason"] = "customer requested staff"
        return i18n.localize(ESCALATE_MSG, lang), True

    low = user_text.strip().lower()
    if any(p in low for p in GO_BACK_PHRASES):   # undo the previous answer and re-ask it
        history = state.get("_history") or []
        if history:
            state.pop(history.pop(), None)       # remove the last answer → it becomes current again
            state["_history"] = history
            prev = get_next_question(_answers(state))
            back = i18n.localize("No problem - let's redo that.", lang)
            return (f"{back}\n\n{_render(prev, lang, _answers(state))}", False) if prev \
                else (_done_message(_answers(state), lang), True)
        return i18n.localize("There's nothing to go back to yet.", lang) + "\n\n" + _render(q, lang, answers), False

    # At the review step, "change <field>" edits that one detail and re-asks it.
    if q and q["field"] == "confirmation" and low not in ("yes", "y", "confirm", "yes all correct"):
        for kw, field in EDIT_ALIASES.items():
            if kw in low and field in answers:
                state.pop(field, None)
                state["_history"] = [h for h in state.get("_history", []) if h != field]
                nq = get_next_question(_answers(state))
                return (f"{i18n.localize('Sure - let us update that.', lang)}\n\n"
                        f"{_render(nq, lang, _answers(state))}", False)

    if state.get("_done") or q is None:       # intake already finished - just close politely
        state["_done"] = True
        return i18n.localize(CLOSING_MSG, lang), True

    parsed = parse_answer(user_text, q, lang)
    value = parsed["value"]
    if q["type"] == "date":                   # accept any year / separators, store DD/MM/YYYY
        value = _normalize_date(value) or value
    ok, err = validate_answer(value, q)
    if ok and q["field"] == "age":             # cross-check the stated age against the DOB
        cross = _age_vs_dob_error(value, answers.get("dob"))
        if cross:
            ok, err = False, cross
    if ok and q.get("check") == "postal" and geocode.configured():   # optional address verification
        if value == state.get("_addr_last"):   # user re-sent the same address → accept as typed
            state.pop("_addr_last", None)
        else:
            res = geocode.verify(value)        # None = key absent / API down → accept
            if res is not None and not res["ok"]:
                state["_addr_last"] = value
                hint = f" Did you mean: {res['suggestion']}?" if res.get("suggestion") else ""
                ok, err = False, ("I couldn't verify that address." + hint + " Please re-enter it "
                                  "- or send the same address again to use it as typed.")
    if not ok:
        ekey = f"_err_{q['field']}"
        state[ekey] = state.get(ekey, 0) + 1
        if state[ekey] >= MAX_ERRORS:         # repeated validation errors → auto-handoff
            state["_escalate"] = True
            state["_escalate_reason"] = f"repeated validation errors on '{q['field']}'"
            return i18n.localize(
                "I'm having trouble understanding your answer - I'm connecting you with our "
                "staff, who will follow up with you shortly.", lang), True
        return f"{i18n.localize(err, lang)}\n\n{_render(q, lang, answers)}", False

    state.pop(f"_err_{q['field']}", None)      # clear the error counter on success
    state[q["field"]] = value
    answers[q["field"]] = value
    state.setdefault("_history", []).append(q["field"])   # so "go back" can undo this answer

    notices = []                               # contextual guidance shown before the next question
    f = q["field"]
    if f == "is_gig" and value == "Yes":                                     # which platform reports to send
        notices.append(i18n.localize(GIG_SUMMARY_GUIDANCE, lang))
    if f in ("gig_platforms", "gst_platforms") and _multi_platform(value):   # §4 mandatory GST
        state["gst_required"] = "Yes"
        notices.append(i18n.localize(
            "Because you operate on more than one rideshare/delivery platform, a dedicated "
            f"GST number is MANDATORY (registration ${PRICING['gst_setup']['flat']}).", lang)
            + f"\n\n{GST_WARNING}")            # warning itself stays verbatim
    if f == "rent_proof" and value == "No":                                  # §3 rent, correct year
        notices.append(i18n.localize(RENT_NO_PROOF_GUIDANCE.format(year=settings.tax_year), lang))
    if f == "gig_has_gst" and value == "Yes":                                # how to get NetFile code
        notices.append(i18n.localize(GIG_GST_NETFILE_HELP, lang))
    if f == "confirmation":                                                  # fee + terms → then pay
        notices.append(_payment_terms(answers, lang))
    if f == "authorization_agreed" and value == "No":                        # declined to authorize
        notices.append(i18n.localize(
            "No problem - we won't submit your return until you're ready to authorize it. "
            "Our team will follow up with you.", lang))
    if f == "gst_service" and value == "Register for a GST Number":          # §4 procurement SLA
        notices.append(i18n.localize(PROCUREMENT_SLA, lang))
    if f == "corp_gst_number" and value.strip().lower() == "none":           # §5 CRA helpline
        notices.append(CRA_HELPLINE)           # mandated verbatim (+ phone)
    if f == "reg_type" and value == "New Incorporation":                     # §6 e-Transfer first
        notices.append(ETRANSFER_DIRECTIVE)    # mandated verbatim
    if f == "has_mycra":                                                     # §2A CRA rep authorization
        notices.append(i18n.localize(
            REP_AUTH_GUIDANCE.format(year=settings.tax_year, next_year=settings.tax_year + 1), lang))
    if f == "landed_2024" and value == "Yes":                                # §2B world income
        notices.append(i18n.localize(WORLD_INCOME, lang))

    extra = "\n\n".join(notices)
    nxt = get_next_question(answers)
    if nxt is None:                           # last answer → estimate + thank-you + KB link
        state["_done"] = True
        return _done_message(answers, lang), True
    return (f"{extra}\n\n{_render(nxt, lang, answers)}" if extra else _render(nxt, lang, answers)), False

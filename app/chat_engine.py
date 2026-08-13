"""Channel-agnostic conversation engine. WhatsApp and web both call advance().

Deterministic flow: get_next_question() walks QUESTIONS honouring conditions. Gemini only
interprets messy free-text answers (parse_answer) - it never drives the flow.
Answers live in the session's conversation_state_json; a Client row + Information Sheet get
materialised at confirmation (Phase 5).
"""
import json
import re
import time
from datetime import datetime

from . import geocode, i18n, llm
from .config import settings
from .pricing import PRICING, estimate
from .question_flow import (AUTHORIZATION_MSG, CORP_FILING_CHECKLIST, CRA_HELPLINE,
                            ETRANSFER_DIRECTIVE, GIG_GST_NETFILE_HELP, GST_REG_CHECKLIST, GST_WARNING,
                            INCORPORATION_CHECKLIST, PROCUREMENT_SLA, QUESTIONS,
                            RENT_NO_PROOF_GUIDANCE, REP_AUTH_NO, REP_AUTH_YES, GIG_SUMMARY_GUIDANCE,
                            NEWCOMER_FILE_BENEFITS, RIDESHARE_PLATFORMS, in_quebec)

SESSION_TIMEOUT = 5 * 60   # seconds of inactivity before an unfinished session restarts
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
                "dob": "dob", "marital": "marital_status", "marriage": "marital_status"}
# Friendly labels for the review summary. Any answered field not listed here falls back to a
# label auto-derived from its name. The summary shows EVERY answer so the client can verify all of it.
REVIEW_LABELS = {
    "service_type": "Service", "corporation_name": "Corporation", "full_name": "Name",
    "business_activity": "Business activity", "phone": "Phone", "email": "Email", "sin": "SIN",
    "dob": "Date of birth", "address": "Address", "marital_status": "Marital status",
    "marital_changed": "Marital status changed in year", "marital_change_date": "Date of change",
    "spouse_in_canada": "Spouse in Canada", "spouse_name": "Spouse name", "spouse_dob": "Spouse DOB",
    "spouse_income": "Spouse income", "spouse_sin": "Spouse SIN", "spouse_phone": "Spouse phone",
    "spouse_email": "Spouse email", "spouse_address": "Spouse address",
    "spouse_landing_date": "Spouse landing date", "has_children": "Has children/dependents",
    "children_details": "Children", "child_born_this_year": "Child born this year",
    "newborn_details": "Newborn", "landed_2024": "Newcomer (landed in year)",
    "landing_date": "Landing date", "has_mycra": "CRA My Account", "noa_method": "NOA method",
    "filed_last_year": "Filed last year", "is_student": "Student", "student_type": "Student type",
    "student_completion": "Program completion", "is_gig": "Gig/rideshare income",
    "gig_platforms": "Platforms", "gig_cash": "Cash income", "gig_has_gst": "GST/HST account",
    "gig_netfile": "NetFile access code", "owns_rental": "Rental income",
    "rental_address": "Rental address", "first_home": "First-time home buyer",
    "has_medical": "Medical expenses", "has_donations": "Donations", "has_gym": "Gym/fitness",
    "gym_province": "Gym province (Dec 31)", "gym_amount": "Gym amount",
    "has_childcare": "Child care", "has_child_fitness": "Child sports/fitness",
    "child_fitness_amount": "Child fitness amount", "lived_north": "Northern resident",
    "northern_zone": "Northern zone", "rent_paid_2025": "Rent/property tax paid",
    "province_changed": "Changed province", "province_from": "Moved from", "province_to": "Moved to",
    "move_date": "Move date", "move_reason": "Move reason", "left_canada_date": "Left Canada",
    "last_refund": "Last refund", "third_party_payer": "Third-party payer", "additional_notes": "Notes",
}
# Housekeeping fields that aren't client-facing "details" - kept out of the review.
_REVIEW_SKIP = {"customer_status", "confirmation", "payment_reference", "authorization_agreed",
                "rep_auth_ack", "netfile_help_ack", "details_ok", "profile_prefilled"}
DONE_MSG = ("Thank you we have everything we need. Our team will review your details and "
            "send your Information Sheet and price estimate shortly.\n\n"
            "Payment is by Interac e-Transfer once you approve the estimate; work begins after "
            "payment is confirmed.")
# Consumer-filing closing (Personal or Individual Tax / GST) - the client's exact wording; {email} is the payee.
PAYMENT_CLOSING = ("Thank you for sharing your information.\n\n"
                   "Please e-transfer your initial payment to {email} along with your information "
                   "for review.\n\n"
                   "Initial Payment:\n"
                   "- $45 for regular employment income\n"
                   "- $70 if you have Uber, Skip, DoorDash, Lyft, or other business/self-employment "
                   "income\n\n"
                   "Your initial payment ($45 or $70) will be adjusted toward your total tax filing "
                   "charges. We will only request the remaining balance (if any).\n\n"
                   "Tax Filing Fees:\n"
                   "- $65 for up to 3 T4 slips\n"
                   "- $75 and above for self-employment & delivery income\n"
                   "- $50 for GST return filing\n\n"
                   "Please share a screenshot of the e-transfer for confirmation.")
# Firm engagement terms - shown at completion for every service. Mandated legal text: kept
# verbatim (not machine-translated), per the same convention as the old no-refund line.
POLICIES_MSG = (
    "Service & Filing Policy\n\n"
    "At Ravi's Accurate Tax Services, we are committed to transparency, professionalism, and "
    "delivering accurate tax solutions.\n\n"
    "1. File Review & Delivery\n"
    "Once fees are paid in full, your completed tax file will be prepared and sent to you for "
    "review.\n\n"
    "2. Client Review & Authorization\n"
    "You will have the opportunity to:\n"
    "- Review your tax return\n"
    "- Discuss any questions or concerns with our team\n"
    "- Provide your authorization and signature\n\n"
    "3. Engagement Begins\n"
    "Your engagement with Ravi's Accurate Tax Services begins when you start sharing your personal, "
    "tax, or financial information with our team for review or preparation.\n\n"
    "4. Submission Policy\n"
    "Your tax return will not be submitted to CRA until we have received your signed approval.\n\n"
    "5. Payment Policy\n"
    "Once the engagement has begun (as defined above), all fees paid, whether partial or full, are "
    "partially or fully non-refundable.\n\n"
    "By proceeding with our services, you acknowledge, understand, and agree to the above policy.\n\n"
    "Thank you for choosing Ravi's Accurate Tax Services.")
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
# Benefits explainer - Personal or Individual Tax only, at the very end of completion. Helpful (translated).
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
# Quebec residence detected. We don't file Quebec provincial returns, but a prior year (when the
# client lived elsewhere) may still be fileable - so we hand off to staff instead of ending cold.
QUEBEC_NOTICE = (
    "We don't file Quebec (Revenu Quebec) provincial returns. If you were a Quebec resident on "
    "December 31 of the year you're filing, we're unable to help with that return.\n\n"
    "However, if you're filing an earlier year when you lived in another province, our team can "
    "still help - I'll connect you with them and someone will follow up shortly.")

# Strict-ish email: letters/digits/._%+- local part, real domain labels, 2+ letter TLD.
# (The old "anything but @/space" regex accepted junk like a trailing apostrophe.)
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")


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
    if question.get("check") == "sin_optional":       # e.g. spouse without a SIN / not working
        if v.lower() in ("skip", "no", "none", "n/a", "na", "-"):
            return True, ""
        return (True, "") if _luhn_ok(v) else \
            (False, "That SIN isn't valid - enter the 9 digits, or reply 'skip'.")
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
        if yr == "prev_year":                     # landing date -> the year before the filing year
            yr = settings.tax_year - 1
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

def age_from_dob(dob_str: str | None) -> int | None:
    """Compute age from a DOB (DD/MM/YYYY or ISO). Age is never asked - always derived."""
    nd = _normalize_date(dob_str or "")
    if not nd:
        return None
    d, mo, y = int(nd[:2]), int(nd[3:5]), int(nd[6:])
    today = datetime.now().date()
    return today.year - y - ((today.month, today.day) < (mo, d))


_PROVINCES = frozenset("ON BC AB SK MB QC NB NS PE NL YT NT NU".split())
_STREET_TYPES = frozenset(
    "avenue ave street st road rd drive dr boulevard blvd crescent cres court ct lane ln "
    "way circle cir trail terrace place pl gate row square sq parkway pkwy line highway hwy".split())


def _addr_missing(addr: str) -> list[str]:
    """Which required components are missing: street/house number, city, postal code.

    Deterministic checks (unit numbers like '- 1802' don't confuse them); the geocoder covers
    deeper problems. Per the client: ask ONLY for the missing information, never the whole address.
    """
    from .question_flow import _POSTAL_RE
    missing = []
    no_postal = _POSTAL_RE.sub("", addr or "")
    if not re.search(r"\d", no_postal):                       # digits besides the postal code
        missing.append("street/house number")
    # City: an alphabetic word after the street-type word (Avenue/St/...), before province/postal.
    tokens = [t.strip(",.-#") for t in no_postal.split()]
    low = [t.lower() for t in tokens]
    if any(t in _STREET_TYPES for t in low):                  # can only judge city if we see a street type
        last_st = max(i for i, t in enumerate(low) if t in _STREET_TYPES)
        after = [t for t in tokens[last_st + 1:] if t and t.upper() not in _PROVINCES]
        if not any(t.isalpha() for t in after):
            missing.append("city")
    if not _POSTAL_RE.search(addr or ""):
        missing.append("postal code (e.g. M5V 3L9)")
    return missing


def _merge_addr(pending: str, piece: str) -> str:
    """Combine the stored partial address with the just-supplied missing piece(s)."""
    from .question_flow import _POSTAL_RE
    p = piece.strip().strip(",")
    # A house/unit number in any common shape goes in front: '70', '1802 - 70', '70-1802', 'unit 1802 70'.
    if re.fullmatch(r"(?:(?:unit|apt|suite|#)\s*)?[\d][\d\s\-#/]*[A-Za-z]?", p, re.IGNORECASE):
        return f"{p} {pending}"
    if _POSTAL_RE.search(p) and len(p) <= 8:                  # a bare postal code goes at the end
        return f"{pending} {p}"
    if p.replace(" ", "").isalpha() and len(p.split()) <= 3:  # a city goes before province/postal
        toks = pending.split()
        for i, t in enumerate(toks):
            if t.strip(",").upper() in _PROVINCES or _POSTAL_RE.match(" ".join(toks[i:i + 2])):
                return " ".join(toks[:i] + [p] + toks[i:])
        return f"{pending} {p}"
    return p                                                  # anything else = a fresh full address


def _names_quebec(text: str) -> bool:
    """True if a free-text province answer names Quebec (any spelling / abbreviation)."""
    t = (text or "").strip().lower()
    return t in ("qc", "que", "pq", "qué") or "quebec" in t or "québec" in t


def _to_amount(v) -> float:
    try:
        return float(str(v or "0").replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _multi_platform(text: str) -> bool:
    """2+ distinct rideshare/delivery platforms → a dedicated GST number is mandatory (spec §4)."""
    low = (text or "").lower()
    return sum(1 for p in RIDESHARE_PLATFORMS if p in low) >= 2


def _review_summary(answers: dict, lang: str = i18n.DEFAULT) -> str:
    """Show EVERY answered detail (in flow order) so the client can verify all of it before confirming.

    Mirrors get_next_question's filtering (workflow + condition) and de-dupes by field, so the summary
    reflects exactly the questions this client's flow asked - once each.
    """
    service = answers.get("service_type")
    lines, seen = [], set()
    for q in QUESTIONS:
        f = q["field"]
        wf = q.get("workflow")
        if (wf is not None and wf != service) or f in seen or f in _REVIEW_SKIP or q["type"] == "file":
            continue
        cond = q.get("condition")
        if cond and not cond(answers):
            continue
        v = answers.get(f)
        if v is None or str(v).strip() == "":
            continue
        seen.add(f)
        label = REVIEW_LABELS.get(f) or f.replace("_", " ").strip().capitalize()
        lines.append(f"• {label}: {v}")
        if f == "dob":                         # age is derived from DOB, shown so a typo is caught
            age = age_from_dob(v)
            if age is not None:
                lines.append(f"• Age: {age}")
    return i18n.localize("Here's a summary of your details:", lang) + "\n" + "\n".join(lines)


def _render(q: dict, lang: str = i18n.DEFAULT, answers: dict | None = None) -> str:
    body = (q["prompt"].replace("{year}", str(settings.tax_year))   # keep prompt year in sync w/ guidance
                       .replace("{prev_year}", str(settings.tax_year - 1)))
    if q.get("options") and not q.get("hide_options"):   # hide_options: the prompt lists them itself
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
    elif service in ("Personal or Individual Tax", "GST/HST"):      # client's $45-initial closing + pricing list
        head = i18n.localize(PAYMENT_CLOSING.format(email=settings.etransfer_email), lang)
    else:
        est = estimate(service, answers)
        body = f"{est}\n\n{DONE_MSG}" if est else DONE_MSG
        head = (f"{i18n.localize(body, lang)}\n\nPlease send your e-Transfer to: "
                f"{settings.etransfer_email}")
    # Firm engagement terms - verbatim (legal text, not translated).
    msg = f"{head}\n\n{POLICIES_MSG}"
    if service == "Personal or Individual Tax":                     # benefits explainer (client)
        msg += f"\n\n{i18n.localize(BENEFITS_INFO, lang)}"
    return msg


# Shown upfront when the client picks Personal Tax (option 1): the four checklists + email fallback.
PERSONAL_TAX_CHECKLIST = (
    "Personal Tax - Checklist (what to have ready):\n"
    "- Income slips: T4, T4A, T5, RRSP, FHSA, T2202 (tuition), and any other slips\n"
    "- SIN, date of birth, and complete address\n"
    "- Marital status (and your spouse's details if married/common-law)\n"
    "- Rent or property tax paid, and receipts for any deductions (medical, donations, child care, moving)")
PRICING_INFO = (
    "Pricing:\n"
    "- Initial payment: $45 (regular employment income) or $70 (Uber/Skip/DoorDash/Lyft or self-employment)\n"
    "- Personal tax return (up to 3 T4 slips): $65\n"
    "- Self-employment / delivery income: $75 and above\n"
    "- GST return filing: $50\n"
    "Your initial payment is adjusted toward your total fee; we only request the remaining balance (if any).")
EMAIL_FALLBACK = (
    "If you'd prefer not to go through the chatbot, take a screenshot of this checklist and email "
    "your information to {email}, and our team will get back to you shortly.")
# Corporate / GST / Business Registration are NOT question-driven - just show the checklist + hand-off.
_SERVICE_CHECKLISTS = {"Corporate Tax": CORP_FILING_CHECKLIST, "GST/HST": GST_REG_CHECKLIST,
                       "Business Registration": INCORPORATION_CHECKLIST}
CHECKLIST_HANDOFF = ("Please review this checklist. If you have any questions, press 'Speak with "
                     "Staff' - or just share your information and e-Transfer screenshot by email to "
                     "{email}, and we'll get back to you shortly.")


def _done_message(answers: dict, lang: str = i18n.DEFAULT) -> str:
    """Final acknowledgement. The fee/terms were already shown at the authorization step."""
    service = answers.get("service_type")
    if service == "Others":                           # not a filing - just an enquiry hand-off
        return i18n.localize("Thank you - our team will review your enquiry and contact you "
                             "shortly.", lang)
    checklist = _SERVICE_CHECKLISTS.get(service)      # Corporate / GST / Business Reg: checklist only
    if checklist:
        return (i18n.localize(checklist.format(email=settings.etransfer_email), lang) + "\n\n"
                + i18n.localize(CHECKLIST_HANDOFF.format(email=settings.etransfer_email), lang))
    paid = (answers.get("payment_screenshot") or "").strip().lower() not in ("", "skip")
    note = (" We've received your payment confirmation and will verify it shortly." if paid
            else " Once you've sent your e-Transfer, share a screenshot here for confirmation.")
    return i18n.localize("Thank you - we have everything we need and our team will begin reviewing "
                         "your file." + note, lang)


def resume_message(state: dict) -> str:
    """Clear an active escalation and return a 'let's continue' message + the current question.

    Called when staff resolve an escalation so the client's chat picks up exactly where it stopped.
    Mutates state (drops the escalation flags).
    """
    for k in ("_escalate", "_escalate_reason", "_escalate_logged"):
        state.pop(k, None)
    lang = state.get("_lang", i18n.DEFAULT)
    answers = _answers(state)
    q = get_next_question(answers)
    if q is None:
        return i18n.localize(CLOSING_MSG, lang)
    prefix = i18n.localize("Thanks for waiting - let's continue where we left off.", lang)
    return f"{prefix}\n\n{_render(q, lang, answers)}"


def advance(state: dict, user_text: str | None, greeting: str | None = None) -> tuple[str, bool]:
    """Given prior state + latest user message, return (reply, done). Mutates state in place.

    `greeting` is the user's very first message - used once to detect their language so the
    bot can acknowledge them and continue in the same language + script.
    """
    # Lazy 5-minute inactivity timeout: every message stamps _last_at; if the NEXT message
    # arrives after the window, the unfinished session resets and starts fresh. No timers needed.
    now = time.time()
    last = state.get("_last_at")
    state["_last_at"] = now
    if (user_text is not None and last and now - last > SESSION_TIMEOUT
            and not state.get("_done") and not state.get("_escalate")):
        state.clear()
        state["_last_at"] = now
        first = get_next_question({})
        return ("Your session timed out due to inactivity, so let's start fresh.\n\n"
                + _render(first), False)

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

    low = user_text.strip().lower()

    if state.get("_escalate"):                # already handed off to staff
        if any(p in low for p in GO_BACK_PHRASES) and q is not None:   # clicked staff by mistake -> resume
            for k in ("_escalate", "_escalate_reason", "_escalate_logged", "_done"):
                state.pop(k, None)            # clear _done too - the flow is no longer finished
            resume = i18n.localize("No problem - let's continue where we left off.", lang)
            return f"{resume}\n\n{_render(q, lang, answers)}", False
        return i18n.localize(ESCALATE_MSG, lang), True

    if low in ESCALATE_WORDS:
        state["_escalate"] = True
        state["_escalate_reason"] = "customer requested staff"
        return i18n.localize(ESCALATE_MSG, lang), True
    if any(p in low for p in GO_BACK_PHRASES):   # undo the previous answer and re-ask it
        history = state.get("_history") or []
        if history:
            state.pop(history.pop(), None)       # remove the last answer → it becomes current again
            state["_history"] = history
            state.pop("_done", None)             # undoing means the flow is no longer complete
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
    if q["type"] == "email":                  # strip copy-paste junk: quotes, trailing punctuation
        value = value.strip().strip("'\"<>()[]{},;:").lower()
    if q.get("check") == "postal":            # partial address: merge the missing piece, ask only for it
        pending = state.pop("_addr_pending", None)
        if pending:
            value = _merge_addr(pending, value)
            state["_addr_merged"] = True      # confirm the assembled address before moving on
        miss = _addr_missing(value)
        if miss:
            state["_addr_pending"] = value
            return i18n.localize(f"Almost there - your address seems to be missing the "
                                 f"{' and '.join(miss)}. Please send just that.", lang), False
    ok, err = validate_answer(value, q)
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

    # Quebec detected - from the address postal code or a "moved to Quebec" answer. We don't file
    # Quebec provincial returns, but a prior non-Quebec year may still be fileable, so hand off to
    # staff. Checked BEFORE storing, so pressing 'Back' re-asks the address (not the next question).
    if (q["field"] == "address" and in_quebec({"address": value})) or \
       (q["field"] == "province_to" and _names_quebec(value)):
        # INVARIANT: an escalation sets _escalate ONLY, never _done. _done means the intake genuinely
        # completed (all questions answered) - see the single assignment at the end of advance().
        # Conflating the two caused stale-_done "goodbye" bugs on resume; keep them separate.
        state["_escalate"] = True
        state["_escalate_reason"] = "Quebec residence - confirm which filing year"
        return i18n.localize(QUEBEC_NOTICE, lang), True

    state.pop(f"_err_{q['field']}", None)      # clear the error counter on success
    state[q["field"]] = value
    answers[q["field"]] = value
    state.setdefault("_history", []).append(q["field"])   # so "go back" can undo this answer

    notices = []                               # contextual guidance shown before the next question
    f = q["field"]
    if state.pop("_addr_merged", None):        # assembled from pieces - confirm the full address
        notices.append(i18n.localize(f"Perfect - your full address is: {value}", lang))
    if f == "service_type" and value == "Personal or Individual Tax":        # four checklists upfront
        for msg in (PERSONAL_TAX_CHECKLIST, POLICIES_MSG, PRICING_INFO,
                    REP_AUTH_YES, EMAIL_FALLBACK.format(email=settings.etransfer_email)):
            notices.append(i18n.localize(msg, lang))
    if f == "is_gig" and value == "Yes":                                     # which platform reports to send
        notices.append(i18n.localize(GIG_SUMMARY_GUIDANCE, lang))
    if f in ("gig_platforms", "gst_platforms") and _multi_platform(value):   # §4 mandatory GST
        state["gst_required"] = "Yes"
        notices.append(i18n.localize(
            "Because you operate on more than one rideshare/delivery platform, a dedicated "
            f"GST number is MANDATORY (registration ${PRICING['gst_setup']['flat']}).", lang)
            + f"\n\n{GST_WARNING}")            # warning itself stays verbatim
    if f == "rent_paid_2025" and _to_amount(value) > 0:                      # rent/property tax proof note
        notices.append(i18n.localize(RENT_NO_PROOF_GUIDANCE, lang))
    # gig_has_gst == "No" -> the netfile_help_ack step shows the how-to-get info (Ok to continue).
    if f == "confirmation":                                                  # fee + terms → then pay
        notices.append(_payment_terms(answers, lang))
    if f == "authorization_agreed" and value == "No":                        # declined to authorize
        notices.append(i18n.localize(
            "No problem - we won't submit your return until you're ready to authorize it. "
            "Our team will follow up with you.", lang))
    if f == "service_type" and value == "Corporate Tax":                     # upfront checklist
        notices.append(i18n.localize(CORP_FILING_CHECKLIST.format(email=settings.etransfer_email), lang))
    if f == "gst_service" and value == "Register for a GST Number":          # upfront checklist + §4 SLA
        notices.append(i18n.localize(GST_REG_CHECKLIST.format(email=settings.etransfer_email), lang))
        notices.append(i18n.localize(PROCUREMENT_SLA, lang))
    if f == "corp_gst_number" and value.strip().lower() == "none":           # §5 CRA helpline
        notices.append(CRA_HELPLINE)           # mandated verbatim (+ phone)
    if f == "reg_type" and value == "New Incorporation":                     # upfront checklist + §6 e-Transfer
        notices.append(i18n.localize(INCORPORATION_CHECKLIST.format(email=settings.etransfer_email), lang))
        notices.append(ETRANSFER_DIRECTIVE)    # mandated verbatim
    # has_mycra == Yes is handled by the rep_auth_ack question (steps + "Ok" to continue).
    if f == "has_mycra" and value == "No":                                   # §2A no account -> send NOA
        notices.append(i18n.localize(REP_AUTH_NO.format(year=settings.tax_year), lang))
    # is_student == "No" -> the prior_noa question handles the previous-student NOA (upload or skip).
    # Landed in {prev_year} -> we ask whether they filed that year's return (no world-income message
    # here, per the client). Answering No gets the benefits guidance below.
    if f == "filed_last_year" and value == "No":
        notices.append(i18n.localize(
            NEWCOMER_FILE_BENEFITS.replace("{prev_year}", str(settings.tax_year - 1)), lang))

    extra = "\n\n".join(notices)
    nxt = get_next_question(answers)
    if nxt is None:                           # last answer → estimate + thank-you + KB link
        state["_done"] = True
        return _done_message(answers, lang), True
    return (f"{extra}\n\n{_render(nxt, lang, answers)}" if extra else _render(nxt, lang, answers)), False

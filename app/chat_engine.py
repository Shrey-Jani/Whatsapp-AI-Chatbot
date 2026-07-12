"""Channel-agnostic conversation engine. WhatsApp and web both call advance().

Deterministic flow: get_next_question() walks QUESTIONS honouring conditions. Gemini only
interprets messy free-text answers (parse_answer_with_gemini) — it never drives the flow.
Answers live in the session's conversation_state_json; a Client row + Information Sheet get
materialised at confirmation (Phase 5).
"""
import json
import re

from .config import settings
from .question_flow import QUESTIONS

ESCALATE_WORDS = {"agent", "staff", "human", "representative", "help"}
DONE_MSG = ("Thank you — we have everything we need. Our team will review your details and "
            "send your Information Sheet and price estimate shortly.\n\n"
            "Payment is by e-Transfer once you approve the estimate; work begins after payment "
            "is confirmed. All transactions are final — no refunds once processing has started.")
# ponytail: per-tenant prices + the generated Information Sheet PDF attach here in Phase 5.
ESCALATE_MSG = "Connecting you with our staff — someone will follow up with you shortly."

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _answers(state: dict) -> dict:
    return {k: v for k, v in state.items() if not k.startswith("_")}


def get_next_question(answers: dict) -> dict | None:
    """First question that is unanswered and whose condition passes; None when complete."""
    for q in QUESTIONS:
        if q["field"] in answers:
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


def validate_answer(value: str, question: dict) -> tuple[bool, str]:
    t = question["type"]
    v = (value or "").strip()

    if question.get("check") == "sin":
        return (True, "") if _luhn_ok(v) else (False, "That SIN isn't valid — please enter the 9 digits again.")
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
        return True, ""
    if t == "date":
        return (True, "") if DATE_RE.match(v) else (False, "Please use the format DD/MM/YYYY.")
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


def parse_answer_with_gemini(user_text: str, question: dict) -> dict:
    """Return {"value": str, "confidence": float}. Falls back to raw text if Gemini is off."""
    if question["type"] in ("select", "boolean"):
        return {"value": _choose(question, user_text), "confidence": 1.0}
    if question["type"] == "file":
        return {"value": user_text.strip() or "[uploaded]", "confidence": 1.0}
    if not settings.gemini_api_key:
        return {"value": user_text.strip(), "confidence": 1.0}
    try:
        from google import genai
        client = genai.Client(api_key=settings.gemini_api_key)
        prompt = (f'{question["ai_parse"]}\n\nUser message: "{user_text}"\n'
                  'Respond with ONLY JSON: {"value": "<extracted>", "confidence": <0.0-1.0>}')
        resp = client.models.generate_content(model=settings.gemini_model, contents=prompt)
        data = _safe_json(resp.text)
        return {"value": str(data.get("value", user_text)).strip(),
                "confidence": float(data.get("confidence", 0.5))}
    except Exception as e:
        # Never let an LLM hiccup break intake — fall back to the raw answer, but log why.
        print(f"[gemini] fell back to raw text: {e}")
        return {"value": user_text.strip(), "confidence": 1.0}


# ---- main loop ---------------------------------------------------------------

def _render(q: dict) -> str:
    if q.get("options"):
        opts = "\n".join(f"{i + 1}. {o}" for i, o in enumerate(q["options"]))
        return f"{q['prompt']}\n{opts}"
    return q["prompt"]


def advance(state: dict, user_text: str | None) -> tuple[str, bool]:
    """Given prior state + latest user message, return (reply, done). Mutates state in place."""
    answers = _answers(state)
    q = get_next_question(answers)

    if user_text is None:                     # first contact — ask, don't record
        return (_render(q), False) if q else (DONE_MSG, True)

    if user_text.strip().lower() in ESCALATE_WORDS:
        state["_escalate"] = True
        return ESCALATE_MSG, True

    if q is None:
        return DONE_MSG, True

    parsed = parse_answer_with_gemini(user_text, q)
    value = parsed["value"]
    ok, err = validate_answer(value, q)
    if not ok:
        return f"{err}\n\n{_render(q)}", False

    state[q["field"]] = value
    answers[q["field"]] = value
    nxt = get_next_question(answers)
    return (_render(nxt), False) if nxt else (DONE_MSG, True)

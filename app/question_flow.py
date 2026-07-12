"""Personal-tax intake flow as data.

Each question is a dict:
  id        int, stable order
  field     where the answer is stored (in conversation_state_json / later Client)
  prompt    what the user sees
  type      text | phone | email | date | number | select | boolean | file | textarea
  options   choices for select/boolean
  check     optional extra validator key (e.g. "sin" → 9 digits + Luhn)
  min/max   optional numeric bounds
  condition callable(answers)->bool : ask this only when it returns True (default: always)
  ai_parse  instruction for Gemini to pull clean structured value out of messy text

condition is a real Python callable, NOT an eval'd string — no code-injection surface.
This is the Personal (Type-1) tree only. Other workflows + language routing = Phase 3 / 6.
"""


def _num(a: dict, field: str) -> float:
    try:
        return float(str(a.get(field, "0")).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


MARRIED = lambda a: a.get("marital_status") in ("Married", "Common-Law")          # noqa: E731
SPOUSE_HERE = lambda a: MARRIED(a) and a.get("spouse_in_canada") == "Yes"          # noqa: E731

QUESTIONS = [
    {"id": 1, "field": "full_name", "type": "text",
     "prompt": "What is your full legal name?",
     "ai_parse": "Extract the person's full legal name from the message."},

    {"id": 2, "field": "phone", "type": "phone",
     "prompt": "Your contact mobile number?",
     "ai_parse": "Extract a phone number; return digits only, keep country code if present."},

    {"id": 3, "field": "email", "type": "email",
     "prompt": "Your email address?",
     "ai_parse": "Extract the email address, lowercased."},

    {"id": 4, "field": "sin", "type": "text", "check": "sin",
     "prompt": "Your Social Insurance Number (9 digits)?",
     "ai_parse": "Extract a 9-digit SIN; return digits only, no spaces or dashes."},

    {"id": 5, "field": "dob", "type": "date",
     "prompt": "Your date of birth (DD/MM/YYYY)?",
     "ai_parse": "Extract the date of birth and return it strictly as DD/MM/YYYY."},

    {"id": 6, "field": "address", "type": "textarea",
     "prompt": "Your complete residential address (including postal code)?",
     "ai_parse": "Extract the full mailing address as a single line."},

    {"id": 7, "field": "marital_status", "type": "select",
     "options": ["Single", "Married", "Common-Law", "Divorced", "Separated", "Widowed"],
     "prompt": "Your marital status?",
     "ai_parse": "Map the message to exactly one of: Single, Married, Common-Law, Divorced, Separated, Widowed."},

    {"id": 8, "field": "spouse_in_canada", "type": "boolean", "options": ["Yes", "No"],
     "condition": MARRIED, "prompt": "Is your spouse currently living in Canada?",
     "ai_parse": "Return Yes or No."},

    {"id": 9, "field": "spouse_name", "type": "text", "condition": MARRIED,
     "prompt": "Your spouse's full legal name?",
     "ai_parse": "Extract the spouse's full legal name."},

    {"id": 10, "field": "spouse_dob", "type": "date", "condition": MARRIED,
     "prompt": "Your spouse's date of birth (DD/MM/YYYY)?",
     "ai_parse": "Extract the spouse's DOB as DD/MM/YYYY."},

    {"id": 11, "field": "spouse_sin", "type": "text", "check": "sin", "condition": SPOUSE_HERE,
     "prompt": "Your spouse's SIN (9 digits)?",
     "ai_parse": "Extract a 9-digit SIN; digits only."},

    {"id": 12, "field": "spouse_income", "type": "text", "condition": SPOUSE_HERE,
     "prompt": "Your spouse's approximate annual income (or their income slips info)?",
     "ai_parse": "Extract the spouse's income info as free text."},

    {"id": 13, "field": "newborn_2025", "type": "boolean", "options": ["Yes", "No"],
     "prompt": "Did you have a newborn child in 2025?",
     "ai_parse": "Return Yes or No."},

    {"id": 14, "field": "newborn_info", "type": "text",
     "condition": lambda a: a.get("newborn_2025") == "Yes",
     "prompt": "Newborn's full name and date of birth (DD/MM/YYYY)?",
     "ai_parse": "Extract child's name and DOB (DD/MM/YYYY)."},

    {"id": 15, "field": "landed_2024", "type": "boolean", "options": ["Yes", "No"],
     "prompt": "Did you land / arrive in Canada in 2024?",
     "ai_parse": "Return Yes or No."},

    {"id": 16, "field": "landing_date", "type": "date",
     "condition": lambda a: a.get("landed_2024") == "Yes",
     "prompt": "Your exact landing date in Canada (DD/MM/YYYY)?",
     "ai_parse": "Extract the landing date as DD/MM/YYYY."},

    {"id": 17, "field": "filed_2024", "type": "boolean", "options": ["Yes", "No"],
     "condition": lambda a: a.get("landed_2024") == "Yes",
     "prompt": "Did you file a Canadian tax return for 2024?",
     "ai_parse": "Return Yes or No."},

    {"id": 18, "field": "rent_paid_2025", "type": "number", "min": 0,
     "prompt": "Total rent you paid in 2025 (enter 0 if none)?",
     "ai_parse": "Extract the total rent amount as a number, no currency symbols."},

    {"id": 19, "field": "rent_proof", "type": "boolean", "options": ["Yes", "No"],
     "condition": lambda a: _num(a, "rent_paid_2025") > 0,
     "prompt": "Do you have proof of rent (receipts / landlord details)?",
     "ai_parse": "Return Yes or No."},

    {"id": 20, "field": "income_slips", "type": "file",
     "prompt": "Please upload your income slips — T4, T5, T4A, etc. Tap 📎 to attach each "
               "(photo or PDF), then type 'done'. Type 'skip' if you have none.",
     "ai_parse": "Not parsed by AI — file upload handled separately."},

    {"id": 21, "field": "province_changed", "type": "boolean", "options": ["Yes", "No"],
     "prompt": "Did you change your province of residence during 2025?",
     "ai_parse": "Return Yes or No."},

    {"id": 22, "field": "province_move_info", "type": "text",
     "condition": lambda a: a.get("province_changed") == "Yes",
     "prompt": "Date of move (DD/MM/YYYY) and your new province?",
     "ai_parse": "Extract the move date (DD/MM/YYYY) and the new province."},

    {"id": 23, "field": "left_canada_date", "type": "text",
     "prompt": "If you left (or plan to leave) Canada in 2025, enter the date (DD/MM/YYYY); "
               "otherwise reply 'No'.",
     "ai_parse": "If a departure date is given, return DD/MM/YYYY; otherwise return 'No'."},

    {"id": 24, "field": "student_completion_date", "type": "text",
     "prompt": "If you completed post-secondary studies in 2025 and don't have a T2202A, "
               "enter your completion date (DD/MM/YYYY); otherwise reply 'No'.",
     "ai_parse": "If a completion date is given, return DD/MM/YYYY; otherwise return 'No'."},

    {"id": 25, "field": "additional_notes", "type": "textarea",
     "prompt": "Any other income, deductions, or notes you'd like to add? "
               "(Type 'none' if nothing.)",
     "ai_parse": "Return the note text as-is; if the user indicates nothing, return 'none'."},

    {"id": 26, "field": "confirmation", "type": "text",
     "prompt": "Please review your answers. Type YES to confirm everything is accurate.",
     "ai_parse": "Return YES if the user confirms/agrees, otherwise return NO."},
]

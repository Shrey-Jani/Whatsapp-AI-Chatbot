"""Intake flows as data - one router question + four workflows.

Each question dict may carry:
  id/field/prompt/type/options/check/year/min/max/condition/ai_parse  (see engine)
  workflow  which service this question belongs to ("personal" | "corporate" | "gst" |
            "registration"). Set in bulk by _tag(). The router question has no workflow, so
            it's always asked first; the engine then only shows questions whose workflow
            matches the chosen service_type.

condition is a real Python callable, NOT an eval'd string. Displayed pricing / legal warnings
are intentionally NOT here (collection only) - they land in the pricing phase.
"""


import re


def _num(a: dict, field: str) -> float:
    try:
        return float(str(a.get(field, "0")).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


# Province detection from the Canadian postal code (first letter -> province). Deterministic, no API.
_POSTAL_RE = re.compile(r"\b([A-Za-z])\d[A-Za-z]\s*\d[A-Za-z]\d\b")
QUEBEC_LETTERS = frozenset("GHJ")             # Quebec - not serviced
GYM_PROVINCE_LETTERS = frozenset("RAYBS")     # Manitoba, Newfoundland&Labrador, Yukon, Nova Scotia, Saskatchewan
NORTHERN_LETTERS = frozenset("XY")            # NWT/Nunavut, Yukon (territories)


def postal_letter(address: str) -> str:
    """First letter of the Canadian postal code in the address, uppercased ('' if none found)."""
    m = _POSTAL_RE.search(address or "")
    return m.group(1).upper() if m else ""


def in_quebec(a: dict) -> bool:
    return postal_letter(a.get("address", "")) in QUEBEC_LETTERS


GYM_ELIGIBLE = lambda a: postal_letter(a.get("address", "")) in GYM_PROVINCE_LETTERS   # noqa: E731
IS_NORTHERN = lambda a: postal_letter(a.get("address", "")) in NORTHERN_LETTERS         # noqa: E731


def _left_canada(a: dict) -> bool:
    return (a.get("left_canada_date") or "").strip().lower() not in ("", "no")


def _is(status):
    return lambda a: a.get("marital_status") == status


def YES(field):
    return lambda a: a.get(field) == "Yes"


def EQ(field, value):
    return lambda a: a.get(field) == value


# Upfront checklists - shown as soon as the client picks the matching service, so they know what
# to have ready before entering details. {email} is filled in with the payee at render time.
GST_REG_CHECKLIST = (
    "Checklist for Uber/Lyft GST Program Account Registration\n\n"
    "Please have the following ready:\n"
    "- Full Name\n"
    "- Date of Birth\n"
    "- Complete Residential Address\n"
    "- SIN (Social Insurance Number)\n"
    "- Email Address\n"
    "- Contact Number\n\n"
    "Registration Fee: $85, payable by e-Transfer to {email}.\n"
    "Once we receive your information and payment, we'll proceed with your Uber/Lyft GST program "
    "account registration.")
INCORPORATION_CHECKLIST = (
    "Company Incorporation Checklist\n\n"
    "Please have the following ready:\n"
    "- Full Name\n"
    "- Complete Address\n"
    "- SIN Number\n"
    "- Date of Birth\n"
    "- Contact Number\n"
    "- Email ID\n"
    "- Business Activity / Nature of Business\n\n"
    "Fees: Company Incorporation $100 + GST/HST Registration $50 + Government Fees $200 "
    "(+ Named Company charges, which vary) = $350 total, by e-Transfer to {email}.")
CORP_FILING_CHECKLIST = (
    "Corporation Tax Filing Checklist\n\n"
    "Please have the following ready:\n"
    "- T4A Slip\n"
    "- December bank statement (to verify the closing balance)\n"
    "- Director's Full Name\n"
    "- SIN Number\n"
    "- Date of Birth\n"
    "- Email ID\n"
    "- Contact Number\n"
    "- Net File Access Code (to file the GST return) - ask us how to get it if you need help\n\n"
    "e-Transfer initial payment: $275 to {email}.")
MARRIED = lambda a: a.get("marital_status") in ("Married", "Common-Law")           # noqa: E731
SPOUSE_HERE = lambda a: MARRIED(a) and a.get("spouse_in_canada") == "Yes"          # noqa: E731
NOT_SINGLE = lambda a: a.get("marital_status") != "Single"                         # noqa: E731
SPOUSE_LEFT = lambda a: MARRIED(a) and _left_canada(a)                             # noqa: E731
FILED_Q = lambda a: a.get("landed_2024") == "Yes"   # newcomers: did they file that year's return?

# Newcomer who has NOT filed their landing-year return - why they should (client's wording).
NEWCOMER_FILE_BENEFITS = (
    "We recommend filing your {prev_year} Income Tax and Benefit Return, even if you had little or "
    "no income.\n\n"
    "Since you became a resident of Canada in {prev_year}, filing your {prev_year} tax return allows "
    "CRA to determine all the benefits and credits you may be eligible for, including:\n"
    "- Canada Groceries and Essentials Benefit (formerly GST/HST Credit)\n"
    "- Ontario Trillium Benefit (OTB)\n"
    "- Other applicable federal and provincial benefits and credits\n\n"
    "Even if you had no income in {prev_year}, filing your return may still allow you to receive "
    "eligible benefit payments.")

# Mandated verbatim legal text (spec §4) - shown before GST filing and on the mandatory-GST flag.
GST_WARNING = ("Warning: Failure to file your required GST returns will prompt the CRA to "
               "withhold your personal tax refunds. Additionally, the CRA reserves the right to "
               "arbitrarily calculate and establish a GST balance owing by estimating figures "
               "directly from your baseline bank account statement history.")

# Rideshare/delivery platforms - operating on 2+ makes a GST number mandatory (spec §4).
RIDESHARE_PLATFORMS = ("uber", "lyft", "doordash", "skip", "instacart")

# Shown when a client reports gig income - which platform reports to collect.
GIG_SUMMARY_GUIDANCE = (
    "If you worked with Uber, Skip, DoorDash, Instacart, Hopp, or any other ride-share or courier "
    "delivery platform, please send your Annual Tax Summary for each platform.\n\n"
    "Exception: For Lyft, please send your Quarterly Tax Summaries, as we require the quarterly "
    "reports instead of the annual summary.")

# Guidance messages (spec §3-6). Mandated/verbatim ones are NOT translated; helpful ones are.
CRA_HELPLINE = ("Please contact the CRA corporate helpline directly at 1-800-959-5525, "
                "press 4 to instantly instantiate corporate account access.")            # §5 verbatim
ETRANSFER_DIRECTIVE = ("You must successfully submit the complete fee amount via e-Transfer. "
                       "Processing and business formation work will only commence once payment "
                       "confirmation is received.")                                       # §6 verbatim
PROCUREMENT_SLA = ("Same-day acquisition of your GST number is targeted via automated routing. "
                   "If manual filing exceptions occur, processing via official form submissions "
                   "can take 2 to 3 weeks.")                                              # §4
RENT_NO_PROOF_GUIDANCE = (                                                       # rent/property tax proof
    "You don't need to upload proof now. However, the CRA may ask for proof later.\n\n"
    "Acceptable proof may include:\n"
    "- E-transfer / payment records to your landlord\n"
    "- Lease agreement in your name\n"
    "- A letter or rent receipt signed by your landlord confirming the total rent paid\n"
    "- Property tax bill / receipt, if applicable")
TUITION_CREDIT_GUIDANCE = (                                                      # student tuition credits
    "If you are or were a student, you may have unused tuition tax credits available.\n\n"
    "Please send us your {year} Notice of Assessment (NOA) or your {year} Tax Summary so we can "
    "check whether you have any tuition credits carried forward that may be available to use.")
MOVING_CHECKLIST = ("Since you changed provinces, you may be able to claim moving expenses. "
                    "Keep receipts for:\n\n"
                    "- Transportation & storage of household goods\n"
                    "- Travel (mileage, meals, lodging) during the move\n"
                    "- Temporary lodging near the new home\n"
                    "- Lease-cancellation costs\n"
                    "- Incidentals (address changes, utility hook-ups)\n\n"
                    "We'll help you claim the eligible amounts.")                         # §3

# How to get a GST/HST NetFile Access Code - shown to gig drivers who have a GST account.
GIG_GST_NETFILE_HELP = (
    "How to get Netfile Access Code:\n"
    "Send your previous GST/HST return,\n"
    "If you Cannot find it, call the CRA Business Enquiries line at 1-800-959-5525.")

# Tax Return Review & Authorization - shown at completion, before payment. Legal text (verbatim).
AUTHORIZATION_MSG = (
    "Tax Return Review & Authorization\n\n"
    "Once your tax return is prepared, we will send you:\n"
    "- Your Tax Summary for review.\n"
    "- An electronic signature request.\n\n"
    "Please review your Tax Summary carefully before signing.\n\n"
    "By signing, you confirm that:\n"
    "- You have reviewed your Tax Summary.\n"
    "- The information provided is complete and accurate to the best of your knowledge.\n"
    "- You authorize us to submit your tax return to the CRA.\n\n"
    "Please note: We will not submit your tax return until we receive your signed authorization.")

# §2 CRA Access Authorization guidance. {year} = filing year, {next_year} = year credits carry to.
# Shown only when the client HAS a CRA My Account (has_mycra == Yes).
REP_AUTH_YES = (
    "Please add our Rep ID by following these steps:\n\n"
    "- Open your CRA account\n"
    "- Go to Profile\n"
    "- Find 'Add Authorized Representative'\n"
    "- Add our Rep ID: 64HN5M7\n"
    "- Select Access Level 2\n"
    "- Select an expiry date, or choose No Expiry\n\n"
    "This will allow us to:\n"
    "- Review tax slips submitted by your employer, bank, college, or other institutions.\n"
    "- Help ensure all available information is included in your tax return, reducing the chances "
    "of a future CRA reassessment.")                                                     # §2 A
# Shown only when the client does NOT have a CRA My Account (has_mycra == No).
REP_AUTH_NO = (
    "No problem. Please send us your {year} Notice of Assessment (NOA) or your {year} Tax Summary "
    "so we can review your prior assessment and any carry-forward amounts.")
WORLD_INCOME = ("As you're new to Canada, we've noted your calendar year of landing. Please note: "
                "you are legally required to report your preceding worldwide income in Canadian "
                "dollars (CAD) - please call the CRA directly to report it.")            # §2 B

#  router
# Asked first, for everyone - before the tax-type router.
CUSTOMER_Q = {"id": -1, "field": "customer_status", "type": "select",
              "options": ["New Customer", "Existing Customer"],
              "prompt": "Are you a New Customer or an Existing Customer?",
              "ai_parse": "Map to exactly 'New Customer' or 'Existing Customer'."}

SERVICE_Q = {"id": 0, "field": "service_type", "type": "select",
             "options": ["Personal or Individual Tax", "Corporate Tax", "GST/HST", "Business Registration", "Others"],
             "hide_options": True,               # the prompt lists the numbered menu itself
             "prompt": "Thank you for contacting Accurate Tax Services. How can we help you today?\n"
                       "Please type\n"
                       "1 for Personal or Individual Tax\n"
                       "2 for Corporate Tax\n"
                       "3 for GST/HST\n"
                       "4 for Business Registration\n"
                       "5 for Others",
             "ai_parse": ("Map to one of: Personal or Individual Tax, Corporate Tax, GST/HST, "
                          "Business Registration, Others.")}

# Personal (Type-1)
_PERSONAL_RAW = [
    # Existing customers are matched by SIN up front, so we can pre-fill their profile from
    # their last filing. New customers get the SIN at its normal spot below.
    {"id": 2, "field": "sin", "type": "text", "check": "sin",
     "condition": lambda a: a.get("customer_status") == "Existing Customer",
     "prompt": "Welcome back! Please enter your SIN (9 digits) so we can pull up your file.",
     "ai_parse": "Extract a 9-digit SIN; digits only, no spaces or dashes."},

    # Shown only to returning customers matched by SIN - confirm the pulled profile.
    {"id": 2, "field": "details_ok", "type": "boolean",
     "options": ["Yes, all correct", "No, update my details"],
     "condition": lambda a: a.get("profile_prefilled") == "yes",
     "prompt": "Is everything above still correct?",
     "ai_parse": "Return 'Yes, all correct' or 'No, update my details'."},

    {"id": 3, "field": "full_name", "type": "text", "check": "fullname",
     "prompt": "What is your full legal name (first and last name)?",
     "ai_parse": "Extract the person's full legal name."},

    {"id": 3, "field": "phone", "type": "phone", "prompt": "Your contact mobile number?",
     "ai_parse": "Extract a phone number; digits only, keep country code if present."},

    {"id": 4, "field": "email", "type": "email", "prompt": "Your email address?",
     "ai_parse": "Extract the email address, lowercased."},

    {"id": 5, "field": "sin", "type": "text", "check": "sin",
     "prompt": "Your Social Insurance Number (9 digits)?",
     "ai_parse": "Extract a 9-digit SIN; digits only."},

    {"id": 5.5, "field": "sin_document", "type": "file",
     "prompt": "Please upload a photo or PDF of your SIN document with 📎, "
               "then type 'done' (or type 'skip' if you don't have it handy).",
     "ai_parse": "File upload - handled separately."},

    {"id": 6, "field": "dob", "type": "date", "prompt": "Your date of birth (DD/MM/YYYY)?",
     "ai_parse": "Extract the DOB strictly as DD/MM/YYYY."},

    {"id": 7, "field": "address", "type": "textarea", "check": "postal",
     "prompt": "Your complete residential address (including postal code)?",
     "ai_parse": "Extract the full mailing address as a single line."},
    # Age is NOT asked - it's computed from the date of birth (see chat_engine.age_from_dob).

    # Landing in Canada - asked before marital status.
    {"id": 22, "field": "landed_2024", "type": "boolean", "options": ["Yes", "No"],
     "prompt": "Did you land / arrive in Canada in {prev_year}?", "ai_parse": "Return Yes or No."},
    {"id": 23, "field": "landing_date", "type": "date", "year": "prev_year", "condition": YES("landed_2024"),
     "prompt": "Your exact landing date in Canada in {prev_year} (DD/MM/YYYY)?",
     "ai_parse": "Landing date DD/MM/YYYY."},

    # §2 CRA Access Authorization - new-to-firm clients residing in Canada (not brand-new arrivals).
    {"id": 23.3, "field": "has_mycra", "type": "boolean", "options": ["Yes", "No"],
     "condition": lambda a: a.get("landed_2024") == "No",
     "prompt": "Do you have an active myCRA (CRA My Account) online account?",
     "ai_parse": "Return Yes or No."},

    # Yes -> show the rep-authorization steps and wait for an "Ok" before continuing.
    {"id": 23.35, "field": "rep_auth_ack", "type": "text", "condition": YES("has_mycra"),
     "prompt": REP_AUTH_YES + "\n\nReply 'Ok' to continue.",
     "ai_parse": "Return 'Ok'."},

    {"id": 23.6, "field": "noa_method", "type": "select",
     "condition": lambda a: a.get("has_mycra") == "No",
     "options": ["CRA My Account (free)", "Call CRA (free)", "Have us obtain it ($80)"],
     "prompt": "We'll need your Notice of Assessment (NOA). There are 3 ways to get it - how would "
               "you like to proceed?\n"
               "• CRA My Account - if you have one, log in and download your NOA instantly (free)\n"
               "• Call CRA - call 1-800-959-8281 and request a copy; CRA will mail it to your "
               "address on file (free)\n"
               "• Have us obtain it - we can obtain your NOA on your behalf ($80 per NOA)",
     "ai_parse": "Map to one of: CRA My Account (free), Call CRA (free), Have us obtain it ($80)."},

    {"id": 8, "field": "marital_status", "type": "select",
     "options": ["Single", "Married", "Common-Law", "Divorced", "Separated", "Widowed"],
     "prompt": "Your marital status?",
     "ai_parse": "Map to one of: Single, Married, Common-Law, Divorced, Separated, Widowed."},

    {"id": 8.5, "field": "marital_changed", "type": "boolean", "options": ["Yes", "No"],
     "condition": NOT_SINGLE,                    # a Single filer's status didn't change - skip only Single
     "prompt": "Did your marital status change in {year}?", "ai_parse": "Return Yes or No."},
    {"id": 8.6, "field": "marital_change_date", "type": "date", "condition": YES("marital_changed"),
     "prompt": "Date of change (DD/MM/YYYY)?", "ai_parse": "Date of change as DD/MM/YYYY."},

    {"id": 14, "field": "spouse_in_canada", "type": "boolean", "options": ["Yes", "No"],
     "condition": MARRIED, "prompt": "Is your spouse currently living in Canada?",
     "ai_parse": "Return Yes or No."},
    {"id": 15, "field": "spouse_name", "type": "text", "check": "fullname", "condition": MARRIED,
     "prompt": "Your spouse's full legal name?", "ai_parse": "Extract the spouse's full legal name."},
    {"id": 16, "field": "spouse_dob", "type": "date", "condition": MARRIED,
     "prompt": "Your spouse's date of birth (DD/MM/YYYY)?", "ai_parse": "Spouse DOB DD/MM/YYYY."},
    {"id": 16.5, "field": "spouse_income", "type": "text", "condition": MARRIED,   # form: income both in/out of Canada
     "prompt": "Your spouse's approximate {year} income?", "ai_parse": "Spouse income as free text."},
    {"id": 17, "field": "spouse_sin", "type": "text", "check": "sin_optional", "condition": SPOUSE_HERE,
     "prompt": "Your spouse's SIN (9 digits), or reply 'skip' if they don't have one / aren't working.",
     "ai_parse": "Extract a 9-digit SIN (digits only), or 'skip'."},
    {"id": 17.3, "field": "spouse_phone", "type": "phone", "condition": SPOUSE_HERE,
     "prompt": "Your spouse's phone number?", "ai_parse": "Extract a phone number; digits only."},
    {"id": 17.4, "field": "spouse_email", "type": "email", "condition": SPOUSE_HERE,
     "prompt": "Your spouse's email address?", "ai_parse": "Extract the email address, lowercased."},
    {"id": 19, "field": "spouse_address", "type": "text", "condition": SPOUSE_HERE,
     "prompt": "Your spouse's address (if different; else 'same')?", "ai_parse": "Spouse address or 'same'."},
    {"id": 19.5, "field": "spouse_landing_date", "type": "text", "check": "date_or_no", "condition": SPOUSE_HERE,
     "prompt": "Your spouse's landing date in Canada (DD/MM/YYYY), if a newcomer; else 'No'.",
     "ai_parse": "Landing date DD/MM/YYYY, or 'No'."},

    {"id": 20, "field": "has_children", "type": "boolean", "options": ["Yes", "No"],
     "condition": NOT_SINGLE, "prompt": "Do you have any children or dependents?",
     "ai_parse": "Return Yes or No."},
    {"id": 21.5, "field": "child_born_this_year", "type": "boolean", "options": ["Yes", "No"],
     "condition": YES("has_children"), "prompt": "Was a child born in {year}?",
     "ai_parse": "Return Yes or No."},
    {"id": 21.6, "field": "newborn_details", "type": "text", "condition": YES("child_born_this_year"),
     "prompt": "The newborn's full name and date of birth (DD/MM/YYYY)?",
     "ai_parse": "Extract the child's full name and date of birth."},
    {"id": 21, "field": "children_details", "type": "textarea", "condition": YES("has_children"),
     "prompt": "List EACH child/dependent - full name, DOB (DD/MM/YYYY), and SIN if available "
               "(SIN optional). Include children whether or not they live with you.",
     "ai_parse": "Return the child/dependent details as given."},

    # Newcomers only (landed in {prev_year}) - No triggers the benefits guidance in advance().
    {"id": 24, "field": "filed_last_year", "type": "boolean", "options": ["Yes", "No"],
     "condition": FILED_Q, "prompt": "Did you file your {prev_year} tax return?",
     "ai_parse": "Return Yes or No."},

    {"id": 25, "field": "income_slips", "type": "file",
     "preamble": "Don't have all of your tax slips yet? That's okay.\n\n"
                 "You can still file your tax return even if you don't have every tax slip "
                 "available at the time of filing.\n\n"
                 "However, if any missing slips are later reported to the CRA by your employer, "
                 "bank, college, or another institution, the CRA may issue a Notice of "
                 "Reassessment. This could result in:\n\n"
                 "- An increase or decrease in your tax refund.\n"
                 "- An amount owing if additional income is reported.\n"
                 "- Changes to your government benefits and credits.\n\n"
                 "We recommend sharing all available tax slips with us before filing whenever "
                 "possible, but you can still proceed with your tax return if some slips are not "
                 "yet available.",
     "prompt": "Please upload ALL your slips - several at once is fine. Include income slips "
               "(T4, T4A, T5, RRSP, FHSA, T2202) and any province-specific slips. Tap 📎, select every file "
               "(photo or PDF), then type 'done'. Type 'skip' if you have none.\n\n"
               "If you have any documents to share other than your income slips, please send them "
               "as well. These may include documents related to medical expenses, rent, childcare, "
               "RRSP contributions, moving expenses, or any other information that may be relevant "
               "to your tax return.",
     "ai_parse": "Not parsed by AI - file upload handled separately."},

    {"id": 30, "field": "is_gig", "type": "boolean", "options": ["Yes", "No"],
     "prompt": "Do you earn rideshare/delivery income (Uber, Lyft, DoorDash, SkipTheDishes, "
               "Instacart, etc.)?", "ai_parse": "Return Yes or No."},
    {"id": 31, "field": "gig_platforms", "type": "text", "condition": YES("is_gig"),
     "prompt": "Which platforms do you drive/deliver for? (list all)",
     "ai_parse": "Extract the list of platforms."},
    {"id": 32, "field": "gig_cash", "type": "text", "condition": YES("is_gig"),
     "prompt": "If you earn cash directly or lack structured reports, enter your estimated total "
               "from annual bank statements (else 'No').", "ai_parse": "Cash total, or 'No'."},
    {"id": 32.5, "field": "gig_has_gst", "type": "boolean", "options": ["Yes", "No"],
     "condition": YES("is_gig"),
     "prompt": "Do you have a GST/HST account registered with CRA?", "ai_parse": "Return Yes or No."},
    {"id": 32.6, "field": "gig_netfile", "type": "text", "condition": YES("gig_has_gst"),
     "prompt": "Please share your GST/HST Netfile Access code (If available).",
     "ai_parse": "Extract the NetFile access code, or 'skip'."},
    # No account -> show how to get the access code; user confirms with 'Ok' before continuing.
    {"id": 32.7, "field": "netfile_help_ack", "type": "text", "condition": EQ("gig_has_gst", "No"),
     "prompt": GIG_GST_NETFILE_HELP + "\n\nReply 'Ok' to continue.",
     "ai_parse": "Return 'Ok'."},

    {"id": 33, "field": "owns_rental", "type": "boolean", "options": ["Yes", "No"],
     "prompt": "Do you own any real estate that generates RENTAL income?", "ai_parse": "Return Yes or No."},
    {"id": 34, "field": "rental_address", "type": "text", "check": "postal", "condition": YES("owns_rental"),
     "prompt": "Rental property - complete physical address (including postal code)?",
     "ai_parse": "Extract the rental address."},
    {"id": 35, "field": "rental_gross_income", "type": "number", "min": 0, "condition": YES("owns_rental"),
     "prompt": "Gross annual rental revenue received?", "ai_parse": "Gross rental revenue as a number."},
    {"id": 36, "field": "rental_mortgage_interest", "type": "number", "min": 0, "condition": YES("owns_rental"),
     "prompt": "Total annual mortgage interest paid?", "ai_parse": "Mortgage interest as a number."},
    {"id": 37, "field": "rental_property_tax", "type": "number", "min": 0, "condition": YES("owns_rental"),
     "prompt": "Total municipal property taxes paid?", "ai_parse": "Property tax as a number."},
    {"id": 38, "field": "rental_expenses", "type": "text", "condition": YES("owns_rental"),
     "prompt": "Aggregate repair expenses and structural insurance premiums paid?",
     "ai_parse": "Repairs + insurance figures/details."},
    {"id": 39, "field": "rental_ownership", "type": "select", "options": ["Alone", "Partnership"],
     "condition": YES("owns_rental"), "prompt": "Is the property held Alone or as a Partnership?",
     "ai_parse": "Map to 'Alone' or 'Partnership'."},
    {"id": 40, "field": "rental_partners", "type": "textarea",
     "condition": lambda a: a.get("owns_rental") == "Yes" and a.get("rental_ownership") == "Partnership",
     "prompt": "For EACH partner - name, address, SIN, contact info, and ownership percentage?",
     "ai_parse": "Return the partner details as given."},

    {"id": 41, "field": "first_home", "type": "boolean", "options": ["Yes", "No"],
     "prompt": "Did you buy a home for the FIRST time in the previous calendar year?",
     "ai_parse": "Return Yes or No."},
    {"id": 42, "field": "first_home_details", "type": "text", "condition": YES("first_home"),
     "prompt": "Total purchase price and exact date of purchase (DD/MM/YYYY)?",
     "ai_parse": "Extract purchase price and date."},

    {"id": 43, "field": "has_medical", "type": "boolean", "options": ["Yes", "No"],
     "prompt": "Do you have prescribed medical expenses to claim?", "ai_parse": "Return Yes or No."},
    {"id": 44, "field": "medical_details", "type": "textarea", "condition": YES("has_medical"),
     "prompt": "For each medical expense - total amount paid, date, and the doctor's/clinic's name?",
     "ai_parse": "Return the medical expense details."},

    {"id": 45, "field": "has_donations", "type": "boolean", "options": ["Yes", "No"],
     "prompt": "Do you have official charitable donation receipts?", "ai_parse": "Return Yes or No."},
    {"id": 46, "field": "donations_note", "type": "text", "condition": YES("has_donations"),
     "prompt": "Please upload your donation receipts with 📎, and enter the total donated here.",
     "ai_parse": "Extract the total donation amount."},

    # Gym / physical-activity expenses - only in provinces that offer the credit (from postal code).
    {"id": 45.3, "field": "has_gym", "type": "boolean", "options": ["Yes", "No"],
     "condition": GYM_ELIGIBLE,
     "prompt": "Did you pay for any gym membership, fitness program, or other physical activity "
               "expenses during {year}?", "ai_parse": "Return Yes or No."},
    {"id": 45.31, "field": "gym_amount", "type": "number", "min": 0, "condition": YES("has_gym"),
     "prompt": "Total amount paid for gym/fitness/physical activity?", "ai_parse": "Amount as a number."},
    {"id": 45.32, "field": "gym_receipt", "type": "file", "condition": YES("has_gym"),
     "prompt": "Please upload your gym/fitness receipt(s) or invoice(s) with 📎, then 'done' "
               "(or 'skip' if you don't have them).", "ai_parse": "File upload - handled separately."},
    {"id": 45.33, "field": "gym_province", "type": "text", "condition": YES("has_gym"),
     "prompt": "Which province or territory did you live in on December 31, {year}?",
     "ai_parse": "Extract the province/territory."},

    # Child care expenses.
    {"id": 45.4, "field": "has_childcare", "type": "boolean", "options": ["Yes", "No"],
     "condition": YES("has_children"),           # only clients who have children/dependents
     "prompt": "Did you pay any child care expenses during {year}? (daycare, nursery, preschool, "
               "babysitting, day camp, before/after-school care)", "ai_parse": "Return Yes or No."},
    {"id": 45.41, "field": "childcare_details", "type": "textarea", "condition": YES("has_childcare"),
     "prompt": "For child care, please provide: the child's full name and date of birth; the total "
               "amount paid; the provider's name; and the provider's SIN (if an individual) or "
               "Business Number (if a business), if available.",
     "ai_parse": "Return the child care details as given."},
    {"id": 45.42, "field": "childcare_receipt", "type": "file", "condition": YES("has_childcare"),
     "prompt": "Please upload the child care receipt(s) or annual statement with 📎, then 'done' "
               "(or 'skip').", "ai_parse": "File upload - handled separately."},

    # Children's sports / fitness / physical-activity expenses (only clients who have children).
    {"id": 45.45, "field": "has_child_fitness", "type": "boolean", "options": ["Yes", "No"],
     "condition": YES("has_children"),
     "prompt": "Did you pay any sports, fitness, or physical activity expenses for your child "
               "during {year}?", "ai_parse": "Return Yes or No."},
    {"id": 45.46, "field": "child_fitness_province", "type": "text", "condition": YES("has_child_fitness"),
     "prompt": "Which province or territory did you live in on December 31, {year}?",
     "ai_parse": "Extract the province/territory."},
    {"id": 45.47, "field": "child_fitness_amount", "type": "number", "min": 0,
     "condition": YES("has_child_fitness"),
     "prompt": "Total amount paid for your child's sports/fitness/physical activity?",
     "ai_parse": "Amount as a number."},
    {"id": 45.48, "field": "child_fitness_receipt", "type": "file", "condition": YES("has_child_fitness"),
     "prompt": "Please upload your child's sports/fitness receipt(s) or invoice(s) with 📎, then "
               "'done' (or 'skip' if you don't have them).", "ai_parse": "File upload - handled separately."},

    # Northern residents / travel benefits - only for territory postal codes (Yukon, NWT, Nunavut).
    {"id": 45.5, "field": "lived_north", "type": "boolean", "options": ["Yes", "No"],
     "condition": IS_NORTHERN,
     "prompt": "Did you live or work in a prescribed Northern or Intermediate Zone (Zone A or "
               "Zone B) during {year}? These are remote areas such as Yukon, the Northwest "
               "Territories, Nunavut, and the far north of some provinces.",
     "ai_parse": "Return Yes or No."},
    {"id": 45.51, "field": "northern_zone", "type": "select", "condition": YES("lived_north"),
     "options": ["Zone A (Northern)", "Zone B (Intermediate)", "Not sure"],
     "prompt": "Was that Zone A (Northern Zone) or Zone B (Intermediate Zone)?",
     "ai_parse": "Map to 'Zone A (Northern)', 'Zone B (Intermediate)', or 'Not sure'."},
    {"id": 45.52, "field": "has_northern_travel", "type": "boolean", "options": ["Yes", "No"],
     "condition": YES("lived_north"),
     "prompt": "Did your employer provide travel benefits or reimburse you for travel during {year}?",
     "ai_parse": "Return Yes or No."},
    {"id": 45.53, "field": "northern_t4", "type": "file", "condition": YES("has_northern_travel"),
     "prompt": "Please upload your T4 slip with 📎, then 'done'.",
     "ai_parse": "File upload - handled separately."},
    {"id": 45.54, "field": "northern_receipts", "type": "file", "condition": YES("has_northern_travel"),
     "prompt": "Upload any travel receipts (airfare, hotel, etc.) with 📎, then 'done' "
               "(or 'skip' if none).", "ai_parse": "File upload - handled separately."},

    # Student - upload the T2202 tuition slip; if not available yet, capture full/part-time + completion.
    {"id": 45.6, "field": "is_student", "type": "boolean", "options": ["Yes", "No"],
     "prompt": "Were you a student at any time during {year} (full-time or part-time)?",
     "ai_parse": "Return Yes or No."},
    {"id": 45.61, "field": "tuition_t2202", "type": "file", "condition": YES("is_student"),
     "prompt": "Please upload your {year} tuition slip (T2202) with 📎, then 'done' "
               "(or 'skip' if it's not yet available).", "ai_parse": "File upload - handled separately."},
    {"id": 45.62, "field": "student_type", "type": "select", "options": ["Full-time", "Part-time"],
     "condition": YES("is_student"),
     "prompt": "Were you a full-time or part-time student?", "ai_parse": "Map to 'Full-time' or 'Part-time'."},
    {"id": 45.63, "field": "student_completion", "type": "text", "condition": YES("is_student"),
     "prompt": "Your program completion date, or expected completion date (DD/MM/YYYY)?",
     "ai_parse": "Extract the completion date."},
    # Not a student this year - but a previous student may have tuition credits carried forward.
    {"id": 45.64, "field": "prior_noa", "type": "file", "condition": EQ("is_student", "No"),
     "prompt": "If you were a student previously, please share your {prev_year} Notice of Assessment "
               "(NOA) or {prev_year} Tax Summary with 📎, then 'done' - so we can use any tuition "
               "credits carried forward. Type 'skip' if you were never a student.",
     "ai_parse": "File upload - handled separately."},

    {"id": 47, "field": "rent_paid_2025", "type": "number", "min": 0,
     "prompt": "Total rent or property tax you paid in {year} (enter 0 if none)?",
     "ai_parse": "Rent or property tax amount as a number."},

    {"id": 49, "field": "province_changed", "type": "boolean", "options": ["Yes", "No"],
     "prompt": "Did you change your province of residence during {year}?", "ai_parse": "Return Yes or No."},
    {"id": 50.1, "field": "move_date", "type": "date", "condition": YES("province_changed"),
     "prompt": "What was your date of move? (DD/MM/YYYY)", "ai_parse": "Move date DD/MM/YYYY."},
    {"id": 50.2, "field": "province_from", "type": "text", "condition": YES("province_changed"),
     "prompt": "Which province did you move from?", "ai_parse": "Extract the province."},
    {"id": 50.3, "field": "province_to", "type": "text", "condition": YES("province_changed"),
     "prompt": "Which province did you move to?", "ai_parse": "Extract the province."},
    {"id": 50.4, "field": "move_reason", "type": "select", "condition": YES("province_changed"),
     "options": ["Work / New Job", "Business", "Full-time Studies", "Other"],
     "prompt": "What was the main reason for your move?",
     "ai_parse": "Map to one of: Work / New Job, Business, Full-time Studies, Other."},
    {"id": 50.5, "field": "move_40km", "type": "select", "options": ["Yes", "No", "Not Sure"],
     "condition": lambda a: a.get("province_changed") == "Yes" and a.get("move_reason") in (
         "Work / New Job", "Business", "Full-time Studies"),
     "prompt": "Was your new home at least 40 km closer to your new work location, business, or school?",
     "ai_parse": "Map to Yes, No, or Not Sure."},
    {"id": 50.6, "field": "move_expenses", "type": "textarea",
     "condition": lambda a: a.get("move_40km") in ("Yes", "Not Sure"),
     "prompt": "Did you have any moving expenses? Please reply with all that apply in one message - "
               "e.g. moving company / transportation; travel (fuel, meals, hotels); temporary "
               "accommodation; storage; lease-cancellation fees; utility connection/disconnection "
               "fees; other. Type 'None' if you had none.",
     "ai_parse": "Return the list of moving expenses as given, or 'None'."},
    {"id": 50.7, "field": "move_receipts", "type": "file",
     "condition": lambda a: a.get("move_40km") in ("Yes", "Not Sure"),
     "preamble": ("Please keep all receipts related to your moving expenses. If you have them, "
                  "upload them now - we'll review your eligibility and claim all allowable moving "
                  "expenses on your tax return."),
     "prompt": "Upload your moving-expense receipt(s) with 📎, then 'done' (or 'skip').",
     "ai_parse": "File upload - handled separately."},
    {"id": 51, "field": "left_canada_date", "type": "text", "check": "date_or_no",
     "prompt": "If you left/plan to leave Canada in {year}, enter the date (DD/MM/YYYY); else 'No'.",
     "ai_parse": "Departure date DD/MM/YYYY, or 'No'."},
    {"id": 52, "field": "spouse_left_canada_date", "type": "text", "check": "date_or_no", "condition": SPOUSE_LEFT,
     "prompt": "Your spouse's date of leaving Canada (DD/MM/YYYY), if applicable; else 'No'.",
     "ai_parse": "Departure date DD/MM/YYYY, or 'No'."},


    {"id": 52.6, "field": "third_party_payer", "type": "boolean", "options": ["Yes", "No"],
     "prompt": "Will someone else be paying your fee on your behalf?",
     "ai_parse": "Return Yes or No."},

    {"id": 52.7, "field": "payer_details", "type": "text", "condition": YES("third_party_payer"),
     "prompt": "The payer's full name and contact number?",
     "ai_parse": "Extract the payer's name and contact number."},

    {"id": 53, "field": "additional_notes", "type": "textarea",
     "prompt": "Any other income, deductions, or notes? (Type 'none' if nothing.)",
     "ai_parse": "Return the note, or 'none'."},
    {"id": 54, "field": "confirmation", "type": "text",
     "prompt": "Type YES to confirm everything above is accurate - or tell me what to change (e.g. 'change email').",
     "ai_parse": "Return YES if confirmed, else NO."},
]


# The firm's Client Information Form dictates the order. The questions are authored above (grouped
# as written); here we resequence them into the form's top-to-bottom order - so conditions and
# content stay put and only the order changes. Any field not listed falls to the end (safety).
def _reorder(raw, sections):
    remaining = list(raw)
    out = []
    for sec in sections:
        for fld in sec:
            for i, q in enumerate(remaining):
                if q["field"] == fld:
                    out.append(remaining.pop(i))
                    break
    out.extend(remaining)
    return out


_PERSONAL_SECTIONS = [
    # Personal information (+ marital, per the form's Personal section)
    ["sin", "details_ok", "full_name", "phone", "email", "sin", "sin_document", "dob", "address"],
    ["marital_status", "marital_changed", "marital_change_date"],
    ["is_student", "tuition_t2202", "student_type", "student_completion"],                      # Student
    ["landed_2024", "landing_date", "has_mycra", "rep_auth_ack", "noa_method", "filed_last_year"],  # Newcomer/CRA
    ["rent_paid_2025"],                                                                          # Rent
    ["income_slips"],                                                                            # Income/slips
    ["is_gig", "gig_platforms", "gig_cash", "gig_has_gst", "gig_netfile",                        # extra deductions
     "owns_rental", "rental_address", "rental_gross_income", "rental_mortgage_interest",
     "rental_property_tax", "rental_expenses", "rental_ownership", "rental_partners",
     "first_home", "first_home_details", "has_medical", "medical_details",
     "has_donations", "donations_note", "has_gym", "gym_amount", "gym_receipt", "gym_province",
     "lived_north", "northern_zone", "has_northern_travel", "northern_t4", "northern_receipts"],
    ["has_children", "child_born_this_year", "newborn_details", "children_details",              # Children
     "has_childcare", "childcare_details", "childcare_receipt",
     "has_child_fitness", "child_fitness_province", "child_fitness_amount", "child_fitness_receipt"],
    ["spouse_in_canada", "spouse_name", "spouse_dob", "spouse_income", "spouse_sin",             # Spouse
     "spouse_phone", "spouse_email", "spouse_address", "spouse_landing_date"],
    ["province_changed", "move_date", "province_from", "province_to", "move_reason",             # Province change
     "move_40km", "move_expenses", "move_receipts"],
    ["left_canada_date", "spouse_left_canada_date"],                                             # Leaving Canada
    ["third_party_payer", "payer_details", "additional_notes", "confirmation"],                  # Wrap-up
]
PERSONAL = _reorder(_PERSONAL_RAW, _PERSONAL_SECTIONS)


# ---------------------------------------------------------------- Corporate Tax (Section 5)
CORPORATE = [
    {"id": 100, "field": "corporation_name", "type": "text",
     "prompt": "Corporation's legal name?", "ai_parse": "Extract the corporation name."},
    {"id": 101, "field": "full_name", "type": "text", "check": "fullname",
     "prompt": "Primary director's full legal name?", "ai_parse": "Extract the director's full name."},
    {"id": 102, "field": "dob", "type": "date",
     "prompt": "Primary director's date of birth (DD/MM/YYYY)?", "ai_parse": "DOB DD/MM/YYYY."},
    {"id": 103, "field": "sin", "type": "text", "check": "sin",
     "prompt": "Primary director's SIN (9 digits)?", "ai_parse": "9-digit SIN, digits only."},
    {"id": 103.5, "field": "sin_document", "type": "file",
     "prompt": "Please upload a photo or PDF of the director's SIN document with 📎, "
               "then type 'done' (or type 'skip' if you don't have it handy).",
     "ai_parse": "File upload - handled separately."},
    {"id": 104, "field": "address", "type": "textarea", "check": "postal",
     "prompt": "Primary director's residential address (including postal code)?",
     "ai_parse": "Extract the address."},
    {"id": 105, "field": "phone", "type": "phone",
     "prompt": "Primary director's mobile number?", "ai_parse": "Phone digits."},
    {"id": 106, "field": "email", "type": "email",
     "prompt": "Corporate email ID?", "ai_parse": "Email lowercased."},
    {"id": 107, "field": "has_other_directors", "type": "boolean", "options": ["Yes", "No"],
     "prompt": "Are there other directors or business partners?", "ai_parse": "Return Yes or No."},
    {"id": 108, "field": "other_directors", "type": "textarea", "condition": YES("has_other_directors"),
     "prompt": "For EACH other director/partner - full legal name, SIN, contact number, address, "
               "and email?", "ai_parse": "Return the directors' details as given."},
    {"id": 109, "field": "corp_gst_number", "type": "text", "check": "gst",
     "prompt": "Corporation's 9-digit GST number? (reply 'none' if not set up)",
     "ai_parse": "Extract the GST number or 'none'."},
    {"id": 110, "field": "corp_gst_reporting", "type": "text", "check": "period",
     "condition": lambda a: (a.get("corp_gst_number") or "").lower() != "none",
     "prompt": "Corporate GST reporting period? (e.g. Jan {year} - Dec {year})",
     "ai_parse": "Extract the reporting period."},
    {"id": 111, "field": "corp_gst_access", "type": "text", "check": "code",
     "condition": lambda a: (a.get("corp_gst_number") or "").lower() != "none",
     "prompt": "Corporate GST Access Code / Net File Code?", "ai_parse": "Extract the access code."},
    {"id": 112, "field": "issued_t4a", "type": "boolean", "options": ["Yes", "No"],
     "prompt": "Did the corporation issue or receive any T4A slips this year?",
     "ai_parse": "Return Yes or No."},
    {"id": 112.5, "field": "t4a_slip", "type": "file", "condition": YES("issued_t4a"),
     "prompt": "Please upload the T4A slip(s) with 📎, then type 'done' (or 'skip' if not handy).",
     "ai_parse": "File upload - handled separately."},
    {"id": 113, "field": "bank_statement_full", "type": "file", "condition": YES("issued_t4a"),
     "prompt": "Please upload the FULL annual bank statement (Jan 1 - Dec 31) with 📎, then 'done'.",
     "ai_parse": "File upload - handled separately."},
    {"id": 114, "field": "bank_statement_dec", "type": "file",
     "prompt": "Please upload the DECEMBER bank statement (Dec 31 closing balance) with 📎, then 'done'.",
     "ai_parse": "File upload - handled separately."},
    {"id": 115, "field": "additional_notes", "type": "textarea",
     "prompt": "Any other notes for the corporate filing? (Type 'none' if nothing.)",
     "ai_parse": "Return the note, or 'none'."},
    {"id": 116, "field": "confirmation", "type": "text",
     "prompt": "Type YES to confirm everything above is accurate - or tell me what to change (e.g. 'change email').",
     "ai_parse": "Return YES if confirmed, else NO."},
]

# ---------------------------------------------------------------- GST/HST (Section 4)
GST = [
    {"id": 200, "field": "gst_service", "type": "select",
     "options": ["Register for a GST Number", "File a GST Return"],
     "prompt": "Do you want to register for a GST number, or file a GST return?",
     "ai_parse": "Map to 'Register for a GST Number' or 'File a GST Return'."},
    {"id": 201, "field": "full_name", "type": "text", "check": "fullname",
     "prompt": "Your full legal name?", "ai_parse": "Extract full legal name."},
    {"id": 202, "field": "phone", "type": "phone", "prompt": "Your mobile number?", "ai_parse": "Phone digits."},
    {"id": 203, "field": "email", "type": "email", "prompt": "Your email address?", "ai_parse": "Email lowercased."},
    {"id": 204, "field": "sin", "type": "text", "check": "sin",
     "prompt": "Your SIN (9 digits)?", "ai_parse": "9-digit SIN."},
    {"id": 204.5, "field": "sin_document", "type": "file",
     "prompt": "Please upload a photo or PDF of your SIN document with 📎, "
               "then type 'done' (or type 'skip' if you don't have it handy).",
     "ai_parse": "File upload - handled separately."},
    {"id": 205, "field": "dob", "type": "date", "prompt": "Your date of birth (DD/MM/YYYY)?",
     "ai_parse": "DOB DD/MM/YYYY."},
    {"id": 206, "field": "address", "type": "textarea", "check": "postal",
     "prompt": "Your complete residential address (including postal code)?",
     "ai_parse": "Extract the address."},
    {"id": 207, "field": "gst_platforms", "type": "text",
     "prompt": "Which rideshare/delivery platforms do you operate on? (list all)",
     "ai_parse": "Extract the list of platforms."},
    {"id": 208, "field": "gst_number", "type": "text", "check": "gst",
     "condition": EQ("gst_service", "File a GST Return"), "preamble": GST_WARNING,
     "prompt": "Your 9-digit GST number?", "ai_parse": "Extract the GST number."},
    {"id": 209, "field": "gst_reporting_period", "type": "text", "check": "period",
     "condition": EQ("gst_service", "File a GST Return"),
     "prompt": "Your GST reporting period? (e.g. Jan {year} - Dec {year})",
     "ai_parse": "Extract the reporting period."},
    {"id": 210, "field": "gst_access_code", "type": "text", "check": "code",
     "condition": EQ("gst_service", "File a GST Return"),
     "prompt": "Your CRA Access Code / Net File Code?", "ai_parse": "Extract the access code."},
    {"id": 211, "field": "additional_notes", "type": "textarea",
     "prompt": "Any other notes? (Type 'none' if nothing.)", "ai_parse": "Return the note, or 'none'."},
    {"id": 212, "field": "confirmation", "type": "text",
     "prompt": "Type YES to confirm everything above is accurate - or tell me what to change (e.g. 'change email').",
     "ai_parse": "Return YES if confirmed, else NO."},
]

# ---------------------------------------------------------------- Business Registration (Section 6)
REGISTRATION = [
    {"id": 300, "field": "reg_type", "type": "select",
     "options": ["New Incorporation", "Annual Renewal"],
     "prompt": "Do you need a new incorporation, or an annual renewal?",
     "ai_parse": "Map to 'New Incorporation' or 'Annual Renewal'."},
    {"id": 301, "field": "full_name", "type": "text", "check": "fullname",
     "prompt": "Contact person's full legal name?", "ai_parse": "Extract full legal name."},
    {"id": 302, "field": "phone", "type": "phone", "prompt": "Your mobile number?", "ai_parse": "Phone digits."},
    {"id": 303, "field": "email", "type": "email", "prompt": "Your email address?", "ai_parse": "Email lowercased."},
    # incorporation
    {"id": 304, "field": "address", "type": "textarea", "check": "postal",
     "condition": EQ("reg_type", "New Incorporation"),
     "prompt": "Your complete residential address (including postal code)?",
     "ai_parse": "Extract the address."},
    {"id": 305, "field": "sin", "type": "text", "check": "sin", "condition": EQ("reg_type", "New Incorporation"),
     "prompt": "Your SIN (9 digits)?", "ai_parse": "9-digit SIN."},
    {"id": 305.5, "field": "sin_document", "type": "file",
     "condition": EQ("reg_type", "New Incorporation"),
     "prompt": "Please upload a photo or PDF of your SIN document with 📎, "
               "then type 'done' (or type 'skip' if you don't have it handy).",
     "ai_parse": "File upload - handled separately."},
    {"id": 306, "field": "dob", "type": "date", "condition": EQ("reg_type", "New Incorporation"),
     "prompt": "Your date of birth (DD/MM/YYYY)?", "ai_parse": "DOB DD/MM/YYYY."},
    {"id": 307, "field": "business_activity", "type": "text", "condition": EQ("reg_type", "New Incorporation"),
     "prompt": "Your business activity / vertical? (e.g., Trucking, Restaurant, Construction)",
     "ai_parse": "Extract the business activity."},
    {"id": 307.5, "field": "company_type", "type": "select", "options": ["Numbered", "Named"],
     "condition": EQ("reg_type", "New Incorporation"),
     "prompt": "Do you want a Numbered company or a Named company? "
               "(a Named company has an extra name-search / NUANS fee)",
     "ai_parse": "Map to 'Numbered' or 'Named'."},
    # renewal
    {"id": 308, "field": "registered_with_us", "type": "boolean", "options": ["Yes", "No"],
     "condition": EQ("reg_type", "Annual Renewal"),
     "prompt": "Was your corporation's initial registration done by our firm?",
     "ai_parse": "Return Yes or No."},
    {"id": 309, "field": "company_key", "type": "text", "check": "code",
     "condition": lambda a: a.get("reg_type") == "Annual Renewal" and a.get("registered_with_us") == "No",
     "prompt": "Your alpha-numeric Company Key / Corporation Key?", "ai_parse": "Extract the company key."},
    {"id": 310, "field": "corp_info_sheet", "type": "textarea",
     "condition": lambda a: a.get("reg_type") == "Annual Renewal" and a.get("registered_with_us") == "No",
     "prompt": "Please provide your complete Corporate Information Sheet details.",
     "ai_parse": "Return the corporate info sheet details."},
    {"id": 311, "field": "additional_notes", "type": "textarea",
     "prompt": "Any other notes? (Type 'none' if nothing.)", "ai_parse": "Return the note, or 'none'."},
    {"id": 312, "field": "confirmation", "type": "text",
     "prompt": "Type YES to confirm everything above is accurate - or tell me what to change (e.g. 'change email').",
     "ai_parse": "Return YES if confirmed, else NO."},
]


# Others - not a filing; just capture the enquiry and hand off to staff.
OTHERS = [
    {"id": 400, "field": "others_enquiry", "type": "textarea",
     "prompt": "Please mention your enquiry and our team will contact you about it.",
     "ai_parse": "Return the enquiry text as given."},
]

# Services that end with authorization + payment (everything except an "Others" enquiry).
# Only Personal Tax is question-driven (payment/auth steps). Per the client, options 2-4 show
# their checklist and collect nothing in chat - staff pick those up from the shared details.
FILING_SERVICES = ("Personal or Individual Tax",)

# Shared completion steps - asked after each filing workflow's own confirmation (untagged =
# always considered; gated to filing services by condition).
COMPLETION = [
    # Initial payment comes first (the fee + terms are shown right after the details are confirmed).
    {"id": 600, "field": "payment_screenshot", "type": "file",
     "condition": lambda a: a.get("service_type") in FILING_SERVICES,
     "prompt": "Once you've sent the e-Transfer, please share a screenshot of it for confirmation - "
               "upload it with 📎, then 'done' (or 'skip' if you haven't paid yet).",
     "ai_parse": "File upload - handled separately."},
    # Authorization to submit to the CRA comes AFTER the initial payment.
    {"id": 601, "field": "authorization_agreed", "type": "boolean", "options": ["Yes", "No"],
     "condition": lambda a: a.get("service_type") in FILING_SERVICES,
     "preamble": AUTHORIZATION_MSG, "prompt": "Do you understand and agree?",
     "ai_parse": "Return Yes or No."},
]


def _tag(questions, workflow):
    for q in questions:
        q["workflow"] = workflow
    return questions


# Workflow tag == the router's option label, so get_next_question matches service_type directly.
# Personal-flow sequence, matching the client's 2026 Client Information Form. The two
# existing-customer prefill questions (SIN + "still correct?") stay at the very front; everything
# else is ordered by this list. Reordering only - no question is added, removed, or changed.
_PERSONAL_ORDER = [
    # Personal information
    "full_name", "phone", "email", "sin", "sin_document", "dob", "address",
    "marital_status", "marital_changed", "marital_change_date",
    # Student
    "is_student", "tuition_t2202", "student_type", "student_completion", "prior_noa",
    # Newcomer + CRA access
    "landed_2024", "landing_date", "filed_last_year", "has_mycra", "rep_auth_ack", "noa_method",
    # Rent / property tax
    "rent_paid_2025",
    # Income slips + gig
    "income_slips", "is_gig", "gig_platforms", "gig_cash", "gig_has_gst", "gig_netfile",
    "netfile_help_ack",
    # Other deductions & credits
    "owns_rental", "rental_address", "rental_gross_income", "rental_mortgage_interest",
    "rental_property_tax", "rental_expenses", "rental_ownership", "rental_partners",
    "first_home", "first_home_details", "has_medical", "medical_details",
    "has_donations", "donations_note", "has_gym", "gym_amount", "gym_receipt", "gym_province",
    "lived_north", "northern_zone", "has_northern_travel", "northern_t4", "northern_receipts",
    # Children
    "has_children", "child_born_this_year", "newborn_details", "children_details",
    "has_childcare", "childcare_details", "childcare_receipt",
    "has_child_fitness", "child_fitness_province", "child_fitness_amount", "child_fitness_receipt",
    # Spouse
    "spouse_in_canada", "spouse_name", "spouse_dob", "spouse_income", "spouse_sin",
    "spouse_phone", "spouse_email", "spouse_address", "spouse_landing_date",
    # Province change
    "province_changed", "move_date", "province_from", "province_to", "move_reason",
    "move_40km", "move_expenses", "move_receipts",
    # Leaving Canada
    "left_canada_date", "spouse_left_canada_date",
    # Completion
    "third_party_payer", "payer_details", "additional_notes", "confirmation",
]
_ORD = {f: i for i, f in enumerate(_PERSONAL_ORDER)}
PERSONAL = PERSONAL[:2] + sorted(PERSONAL[2:], key=lambda q: _ORD.get(q["field"], len(_ORD)))

# SERVICE_Q first so the greeting opens with the service menu (Amendment); New/Existing follows.
# Only Personal Tax asks questions. Corporate / GST / Business Registration show their checklist
# and take the details however the client sends them (chat or e-mail) - see chat_engine.
# CORPORATE, GST and REGISTRATION stay defined for when a flow is wanted again; deliberately untagged.
QUESTIONS = ([SERVICE_Q]
             + _tag(PERSONAL, "Personal or Individual Tax")
             + _tag(OTHERS, "Others")
             + COMPLETION)                # untagged: asked after filing workflows complete

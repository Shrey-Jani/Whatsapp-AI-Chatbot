"""Materialise a finished intake session into a Client (+ Submission), linking its Documents.

Runs once when the flow completes (confirmation = YES). Until then everything lives in the
session's conversation_state_json; this is where it becomes a durable client record.
"""
import re

from sqlalchemy import select, update

from . import storage
from .config import settings
from .models import Client, Document, Submission
from .security import digits, protect_sin, reveal_sin

# Stable profile fields we pre-fill for a returning (existing) customer matched by SIN.
# Year-specific answers (income, rent, this year's changes) are always re-asked.
STABLE_FIELDS = {
    "full_name", "phone", "email", "dob", "address", "marital_status",
    "marriage_date", "cohabitation_date", "divorce_date", "separation_date", "date_of_death",
    "spouse_in_canada", "spouse_name", "spouse_dob", "spouse_sin", "spouse_income",
    "spouse_address", "has_children", "children_details", "sin_document",
}   # note: spouse_sin is NOT prefilled - it's encrypted and kept out of the JSON blob
# Recurring tax details also carried over (last year's values - the confirmation step lets the
# customer update anything that changed).
TAX_FIELDS = {
    "owns_rental", "rental_address", "rental_gross_income", "rental_mortgage_interest",
    "rental_property_tax", "rental_expenses", "rental_ownership", "rental_partners",
    "has_tuition", "is_gig", "gig_platforms", "gig_cash",
    "has_medical", "medical_details", "has_donations", "donations_note",
    "rent_paid_2025", "rent_proof",
}
PREFILL_FIELDS = STABLE_FIELDS | TAX_FIELDS


def _to_float(v) -> float:
    try:
        return float(str(v or "0").replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def client_folder(a: dict) -> str:
    """Storage folder for a client's files: '<Name>_<phone>' (sanitised)."""
    name = re.sub(r"[^A-Za-z0-9 ]", "", (a.get("full_name") or "")).strip()
    phone = digits(a.get("phone") or "")
    return "_".join(p for p in (name.replace(" ", "_"), phone) if p) or "unnamed_client"


def _mask_sin(sin: str | None) -> str:
    d = digits(sin or "")
    return f"•••-•••-{d[-3:]}" if len(d) >= 3 else ""


def categorized_form(a: dict, slips: list | None = None) -> str:
    """A human-readable form of everything the client provided, grouped into sections."""
    def sect(title, rows):
        lines = [f"- {k}: {v}" for k, v in rows if v not in (None, "", [])]
        return f"== {title} ==\n" + ("\n".join(lines) or "- (none)")

    slip_txt = ", ".join(f"{s.get('slip_type', 'slip')} ({s.get('filename')})"
                         for s in (slips or [])) or "(none uploaded)"
    rep = "Requested (client asked to add our Level 2 rep)" if a.get("has_mycra") == "Yes" else "N/A"
    return "\n\n".join([
        f"CLIENT FORM - {a.get('full_name', '')} ({a.get('phone', '')})",
        sect("Basic info", [("Name", a.get("full_name")), ("Phone", a.get("phone")),
                            ("Email", a.get("email")), ("SIN", _mask_sin(a.get("sin"))),
                            ("Date of birth", a.get("dob")), ("Age", a.get("age")),
                            ("Address", a.get("address")), ("Marital status", a.get("marital_status"))]),
        sect("Income info", [("Filed last year", a.get("filed_last_year")),
                             ("Slips uploaded", slip_txt), ("Tuition (T2202A)", a.get("has_tuition")),
                             ("Gig/rideshare", a.get("is_gig")), ("Gig platforms", a.get("gig_platforms")),
                             ("Owns rental", a.get("owns_rental")),
                             ("Rental income", a.get("rental_gross_income")),
                             ("Rent paid", a.get("rent_paid_2025"))]),
        sect("Spouse info", [("Name", a.get("spouse_name")), ("DOB", a.get("spouse_dob")),
                             ("SIN", _mask_sin(a.get("spouse_sin"))), ("Income", a.get("spouse_income")),
                             ("In Canada", a.get("spouse_in_canada")), ("Address", a.get("spouse_address"))]),
        sect("Dependent info", [("Has dependents", a.get("has_children")),
                                ("Details", a.get("children_details"))]),
        sect("NOA shared or not", [("Has myCRA account", a.get("has_mycra")),
                                   ("NOA method", a.get("noa_method"))]),
        sect("CRA access given or not", [("Level 2 rep access", rep)]),
    ]) + "\n"


async def materialize(db, tenant, sess):
    a = {k: v for k, v in (sess.conversation_state_json or {}).items() if not k.startswith("_")}
    married = a.get("marital_status") in ("Married", "Common-Law")
    left = a.get("left_canada_date")
    # Keep plaintext SINs out of the JSON blobs - the encrypted values live on typed columns.
    raw = {k: v for k, v in a.items() if k not in ("sin", "spouse_sin")}

    client = Client(
        tenant_id=tenant.id,
        full_name=a.get("full_name"), phone=a.get("phone"), email=a.get("email"),
        sin=protect_sin(a.get("sin")), dob=a.get("dob"), address=a.get("address"),
        marital_status=a.get("marital_status"),
        spouse_json={"name": a.get("spouse_name"), "dob": a.get("spouse_dob"),
                     "sin": protect_sin(a.get("spouse_sin")), "income": a.get("spouse_income"),
                     "address": a.get("spouse_address"),
                     "in_canada": a.get("spouse_in_canada")} if married else None,
        children_json=[a["children_details"]] if a.get("has_children") == "Yes" and a.get("children_details") else None,
        rent_paid=_to_float(a.get("rent_paid_2025")),
        rent_proof=a.get("rent_proof"),
        province_changed=a.get("province_changed") == "Yes",
        new_province=a.get("province_move_info"),
        left_canada_date=left if (left or "").lower() != "no" else None,
        landing_date=a.get("landing_date"),
        is_newcomer=a.get("landed_2024") == "Yes",
        additional_notes=a.get("additional_notes"),
        status="submitted",
        raw_answers=raw,
    )
    db.add(client)
    await db.flush()                          # need client.id

    # Scrub the plaintext SINs from the transient session record too.
    sess.conversation_state_json = {k: v for k, v in (sess.conversation_state_json or {}).items()
                                    if k not in ("sin", "spouse_sin")}
    sess.client_id = client.id
    await db.execute(update(Document).where(Document.session_id == sess.id)
                     .values(client_id=client.id))       # link uploaded slips to the client

    sub = Submission(tenant_id=tenant.id, client_id=client.id, status="pending")
    db.add(sub)
    await db.flush()
    # Unique transaction reference (spec §7) - shared with a third-party payer when there is one.
    sub.reference_number = f"REF-{settings.tax_year}-{sub.id:05d}"

    # Categorised form saved alongside the client's slips, in the "<Name>_<phone>" folder.
    try:
        form = categorized_form(a, (sess.conversation_state_json or {}).get("slips"))
        storage.upload(tenant.id, f"{client_folder(a)}/CLIENT_FORM.txt",
                       form.encode("utf-8"), "text/plain")
    except Exception as e:
        print(f"[submission] client form save failed: {e}")
    return client, sub


async def prefill_existing(db, tenant, state) -> bool:
    """Existing customer matched by SIN → merge stable profile fields from their latest filing.

    Runs once per session (guarded by _prefilled). Returns True if a prior record was found
    and merged, so the caller can re-render the next question and greet them.
    """
    if state.get("customer_status") != "Existing Customer":
        return False
    if not state.get("sin") or state.get("_prefilled"):
        return False
    state["_prefilled"] = True
    # SINs are encrypted (non-deterministic), so we can't match on the ciphertext - decrypt each
    # tenant client and compare. Fine at single-operator scale; add a blind index if it ever grows.
    target = digits(state["sin"])
    prior = None
    for c in (await db.scalars(select(Client).where(Client.tenant_id == tenant.id)
                               .order_by(Client.created_at.desc()))).all():
        if digits(reveal_sin(c.sin)) == target:
            prior = c
            break
    if prior is None:
        return False
    src = dict(prior.raw_answers or {})
    for f in PREFILL_FIELDS:
        if src.get(f) and f not in state:
            state[f] = src[f]
    state["profile_prefilled"] = "yes"        # triggers the "is this still correct?" question
    return True


_SUMMARY_LABELS = {"full_name": "Name", "phone": "Phone", "email": "Email",
                   "dob": "DOB", "address": "Address", "marital_status": "Marital status",
                   "owns_rental": "Owns rental", "is_gig": "Gig/rideshare",
                   "has_tuition": "Tuition", "has_medical": "Medical expenses",
                   "has_donations": "Donations", "rent_paid_2025": "Rent paid"}


def profile_summary(state) -> str:
    return "\n".join(f"• {label}: {state[f]}"
                     for f, label in _SUMMARY_LABELS.items() if state.get(f))

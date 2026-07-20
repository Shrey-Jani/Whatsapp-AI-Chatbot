"""Materialise a finished intake session into a Client (+ Submission), linking its Documents.

Runs once when the flow completes (confirmation = YES). Until then everything lives in the
session's conversation_state_json; this is where it becomes a durable client record.
"""
from sqlalchemy import select, update

from .config import settings
from .models import Client, Document, Submission

# Stable profile fields we pre-fill for a returning (existing) customer matched by SIN.
# Year-specific answers (income, rent, this year's changes) are always re-asked.
STABLE_FIELDS = {
    "full_name", "phone", "email", "dob", "address", "marital_status",
    "marriage_date", "cohabitation_date", "divorce_date", "separation_date", "date_of_death",
    "spouse_in_canada", "spouse_name", "spouse_dob", "spouse_sin", "spouse_income",
    "spouse_address", "has_children", "children_details", "sin_document",
}
# Recurring tax details also carried over (last year's values — the confirmation step lets the
# customer update anything that changed).
TAX_FIELDS = {
    "owns_rental", "rental_address", "rental_gross_income", "rental_mortgage_interest",
    "rental_property_tax", "rental_expenses", "rental_ownership", "rental_partners",
    "has_tuition", "tuition_osap", "is_gig", "gig_platforms", "gig_cash",
    "has_medical", "medical_details", "has_donations", "donations_note",
    "rent_paid_2025", "rent_proof", "last_refund",
}
PREFILL_FIELDS = STABLE_FIELDS | TAX_FIELDS


def _to_float(v) -> float:
    try:
        return float(str(v or "0").replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


async def materialize(db, tenant, sess):
    a = {k: v for k, v in (sess.conversation_state_json or {}).items() if not k.startswith("_")}
    married = a.get("marital_status") in ("Married", "Common-Law")
    left = a.get("left_canada_date")

    client = Client(
        tenant_id=tenant.id,
        full_name=a.get("full_name"), phone=a.get("phone"), email=a.get("email"),
        sin=a.get("sin"), dob=a.get("dob"), address=a.get("address"),
        marital_status=a.get("marital_status"),
        spouse_json={"name": a.get("spouse_name"), "dob": a.get("spouse_dob"),
                     "sin": a.get("spouse_sin"), "income": a.get("spouse_income"),
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
        raw_answers=a,
    )
    db.add(client)
    await db.flush()                          # need client.id

    sess.client_id = client.id
    await db.execute(update(Document).where(Document.session_id == sess.id)
                     .values(client_id=client.id))       # link uploaded slips to the client

    sub = Submission(tenant_id=tenant.id, client_id=client.id, status="pending")
    db.add(sub)
    await db.flush()
    # Unique transaction reference (spec §7) — shared with a third-party payer when there is one.
    sub.reference_number = f"REF-{settings.tax_year}-{sub.id:05d}"
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
    prior = (await db.scalars(
        select(Client).where(Client.tenant_id == tenant.id, Client.sin == state["sin"])
        .order_by(Client.created_at.desc()))).first()
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
                   "has_donations": "Donations", "rent_paid_2025": "Rent paid",
                   "last_refund": "Last refund / owing"}


def profile_summary(state) -> str:
    return "\n".join(f"• {label}: {state[f]}"
                     for f, label in _SUMMARY_LABELS.items() if state.get(f))

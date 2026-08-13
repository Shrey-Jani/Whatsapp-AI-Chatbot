"""Checklist images shown when a client picks a service.

The files live in app/static/checklists/ and are served at /static/checklists/<name> on the web;
WhatsApp uploads the same bytes to Meta. A missing file is simply skipped, so the text version
in chat_engine still carries the information.
"""
from pathlib import Path

DIR = Path(__file__).parent / "static" / "checklists"
URL_PREFIX = "/static/checklists"

# Sent after every service's own checklist - same three cards for all of them.
COMMON = ["policy.png", "pricing.png", "cra_rep_id.png"]

# service_type -> the cards to send, in order.
IMAGES = {
    "Personal or Individual Tax": ["personal_tax.png", *COMMON],
    "Corporate Tax": ["corporate_tax.png", *COMMON],
    "GST/HST": ["uber_lyft_gst.png", *COMMON],
    "Business Registration": ["incorporation.png", *COMMON],
}


def names_for(service: str) -> list[str]:
    """Filenames that actually exist on disk for this service (missing ones are skipped)."""
    return [n for n in IMAGES.get(service, []) if (DIR / n).is_file()]


def urls_for(service: str) -> list[str]:
    return [f"{URL_PREFIX}/{n}" for n in names_for(service)]


def load(name: str) -> bytes:
    return (DIR / name).read_bytes()

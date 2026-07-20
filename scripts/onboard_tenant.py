"""Manually onboard one firm (the 'service' GTM model — no self-serve UI).

Usage:
    python -m scripts.onboard_tenant "Firm Name" <phone_number_id> <access_token> [operator_number]

operator_number = the firm operator's WhatsApp number (e.g. 14165551234) that receives the
finished summary PDF + forwarded slips + escalation alerts.
"""
import asyncio
import sys

from app.database import Session, init_db
from app.models import Tenant


async def main(name: str, phone_number_id: str, access_token: str, operator_number: str = ""):
    await init_db()
    config = {"operator_number": operator_number} if operator_number else {}
    async with Session() as db:
        db.add(Tenant(name=name, phone_number_id=phone_number_id,
                      access_token=access_token, config=config))
        await db.commit()
    print(f"onboarded: {name} ({phone_number_id})"
          + (f" → operator {operator_number}" if operator_number else ""))


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(*sys.argv[1:]))

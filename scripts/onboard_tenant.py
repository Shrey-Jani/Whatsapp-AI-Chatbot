"""Manually onboard one firm (the 'service' GTM model — no self-serve UI).

Usage:
    python -m scripts.onboard_tenant "Firm Name" <phone_number_id> <access_token>
"""
import asyncio
import sys

from app.database import Session, init_db
from app.models import Tenant


async def main(name: str, phone_number_id: str, access_token: str):
    await init_db()
    async with Session() as db:
        db.add(Tenant(name=name, phone_number_id=phone_number_id, access_token=access_token))
        await db.commit()
    print(f"onboarded: {name} ({phone_number_id})")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(*sys.argv[1:]))

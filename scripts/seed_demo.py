"""Seed 3 fake submissions so the admin dashboard has data to display.

Usage:  python -m scripts.seed_demo
"""
import asyncio

from sqlalchemy import select

from app.database import Session, init_db
from app.models import Client, Submission, Tenant

FAKE = [
    ("Amrit Singh", "4160001111", "amrit@example.com", "Married", "pending"),
    ("Priya Sharma", "6470002222", "priya@example.com", "Single", "in_review"),
    ("Neil Patel", "9050003333", "neil@example.com", "Common-Law", "completed"),
]


async def main():
    await init_db()
    async with Session() as db:
        tenant = (await db.scalars(select(Tenant).order_by(Tenant.id))).first()
        if tenant is None:
            print("No tenant — run scripts.onboard_tenant first.")
            return
        for name, phone, email, marital, status in FAKE:
            answers = {"full_name": name, "phone": phone, "email": email,
                       "marital_status": marital, "sin": "046454286", "dob": "01/01/1990",
                       "address": "123 Demo St, Toronto ON"}
            client = Client(tenant_id=tenant.id, full_name=name, phone=phone, email=email,
                            marital_status=marital, status="submitted", raw_answers=answers)
            db.add(client)
            await db.flush()
            db.add(Submission(tenant_id=tenant.id, client_id=client.id, status=status))
        await db.commit()
        print(f"seeded {len(FAKE)} submissions")


if __name__ == "__main__":
    asyncio.run(main())

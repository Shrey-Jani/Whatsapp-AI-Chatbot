"""One-time: encrypt SINs already stored in plaintext, and scrub them from JSON blobs.

Run AFTER setting SIN_ENCRYPTION_KEY in .env:
    python -m scripts.encrypt_existing_sins
Safe to re-run — already-encrypted values (prefixed 'enc:') are skipped.
"""
import asyncio

from sqlalchemy import select

from app.config import settings
from app.database import Session, init_db
from app.models import Client
from app.security import protect_sin


async def main():
    if not settings.sin_encryption_key:
        print("SIN_ENCRYPTION_KEY is not set — set it in .env first. Nothing done.")
        return
    await init_db()
    async with Session() as db:
        clients = (await db.scalars(select(Client))).all()
        n = 0
        for c in clients:
            changed = False
            if c.sin and not str(c.sin).startswith("enc:"):
                c.sin = protect_sin(c.sin)
                changed = True
            if c.spouse_json and c.spouse_json.get("sin") and not str(c.spouse_json["sin"]).startswith("enc:"):
                sj = dict(c.spouse_json)
                sj["sin"] = protect_sin(sj["sin"])
                c.spouse_json = sj
                changed = True
            if c.raw_answers and ("sin" in c.raw_answers or "spouse_sin" in c.raw_answers):
                c.raw_answers = {k: v for k, v in c.raw_answers.items()
                                 if k not in ("sin", "spouse_sin")}
                changed = True
            n += changed
        await db.commit()
        print(f"encrypted / scrubbed {n} client record(s)")


if __name__ == "__main__":
    asyncio.run(main())

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings

# statement_cache_size=0: required so asyncpg works through Supabase's connection pooler.
engine = create_async_engine(settings.database_url, connect_args={"statement_cache_size": 0})
Session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    """FastAPI dependency: yields a request-scoped async session."""
    async with Session() as s:
        yield s


async def init_db():
    from . import models  # noqa: F401 - register tables on Base.metadata
    # ponytail: create_all for scaffold; switch to Alembic once the schema stops moving.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

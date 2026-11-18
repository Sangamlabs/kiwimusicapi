from collections.abc import AsyncGenerator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine
)
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

engine_kwargs = {"echo": False, "future": True}
if "sqlite" in settings.DATABASE_URL:
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs["pool_pre_ping"] = True
    engine_kwargs["pool_size"] = 10
    engine_kwargs["max_overflow"] = 20
    engine_kwargs["pool_recycle"] = 300
    # Optimizations for PgBouncer / Neon connection poolers
    engine_kwargs["connect_args"] = {
        "statement_cache_size": 0,
        "command_timeout": 30,
    }

engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

class Base(DeclarativeBase):
    pass

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Purge legacy mis-mapped tracks from early fuzzy matching cache
        await conn.execute(
            text(
                "DELETE FROM tracks WHERE track_id IN ("
                "'ZWCWfO0y26c', 'mbSp4gsKaU0', 'STLIkd7lgsw', '_dAkj5dxmEM'"
                ")"
            )
        )


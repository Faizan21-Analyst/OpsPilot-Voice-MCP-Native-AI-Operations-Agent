from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from src.core.config import DatabaseSettings


def build_engine(settings: DatabaseSettings):
    """Create the async SQLAlchemy engine from settings. Called once, at startup."""
    return create_async_engine(settings.url, echo=False)


def build_session_factory(engine):
    """Create a session factory bound to the given engine."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
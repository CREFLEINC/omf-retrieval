"""Explicit SQLAlchemy engine and session factory construction."""

from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def create_database_engine(
    database_url: str | URL,
    *,
    echo: bool = False,
) -> Engine:
    """Create a database engine without connecting at module import time."""
    return create_engine(database_url, echo=echo, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a non-expiring transaction-bound session factory."""
    return sessionmaker(bind=engine, expire_on_commit=False)

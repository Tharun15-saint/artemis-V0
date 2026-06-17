"""Backwards-compatible database entrypoint.

Historically this module defined its OWN engine, SessionLocal and Base, separate
from database/base.py. That created two distinct DeclarativeBase classes and two
engines — a latent correctness bug. This module now re-exports the single
canonical objects from database.base so that every import path
(`from database.database import ...` and `from database.base import ...`)
resolves to the exact same engine, session factory and metadata.
"""

from database.base import (  # noqa: F401
    Base,
    DATABASE_URL,
    IS_SQLITE,
    SessionLocal,
    engine,
    get_db,
)


def init_db() -> None:
    """Create all tables on the configured database from the ORM metadata."""
    import database.models  # noqa: F401 — registers every model on Base.metadata

    Base.metadata.create_all(bind=engine)


__all__ = [
    "Base",
    "DATABASE_URL",
    "IS_SQLITE",
    "SessionLocal",
    "engine",
    "get_db",
    "init_db",
]

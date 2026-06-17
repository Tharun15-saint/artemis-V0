"""
Artemis database base — SQLAlchemy 2.0
Every model inherits from Base.
Every session has foreign keys enforced.
"""

from decimal import Decimal

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./artemis.db")

# Single source of truth for the engine. The dialect is detected from the URL so
# the exact same code runs against SQLite (local/legacy) and PostgreSQL/Timescale
# (production) — see scripts/migrate_sqlite_to_postgres.py.
_url = make_url(DATABASE_URL)
IS_SQLITE = _url.get_backend_name() == "sqlite"

if IS_SQLITE:
    # SQLite: single-file DB, allow cross-thread use (FastAPI), no real pool.
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=False,  # Set True temporarily to debug SQL
    )
else:
    # PostgreSQL / TimescaleDB: real connection pool with liveness checks so a
    # dropped server connection is transparently recycled rather than erroring.
    engine = create_engine(
        DATABASE_URL,
        pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "20")),
        pool_timeout=30,
        pool_pre_ping=True,
        pool_recycle=1800,
        echo=False,
    )


# Enforce foreign key constraints on every connection.
# SQLite disables FK enforcement by default — this turns it on. PostgreSQL
# enforces foreign keys natively, so this listener is registered for SQLite only.
if IS_SQLITE:

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")  # Better concurrent reads
        cursor.close()


class Base(DeclarativeBase):
    pass


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency — yields a database session and closes it after."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _values_match(existing_value, new_value) -> bool:
    if existing_value is None and new_value is None:
        return True
    if existing_value is None or new_value is None:
        return False
    if isinstance(existing_value, Decimal) or isinstance(new_value, Decimal):
        return Decimal(str(existing_value)) == Decimal(str(new_value))
    return existing_value == new_value


def is_duplicate_row(
    db: Session,
    model_class,
    filter_kwargs: dict,
    value_kwargs: dict,
) -> bool:
    """
    Returns True if an identical row already exists.
    filter_kwargs: fields that identify the entity (e.g. origin_country)
    value_kwargs: fields that contain the value (e.g. spot_price_usd)
    A row is a duplicate if all filter_kwargs AND all value_kwargs match
    an existing row with is_latest = True.
    """
    query = db.query(model_class).filter_by(**filter_kwargs, is_latest=True)
    existing = query.first()
    if existing is None:
        return False
    for field, value in value_kwargs.items():
        if not _values_match(getattr(existing, field), value):
            return False
    return True


def assert_fk_exists(
    db: Session,
    model_class,
    pk_field: str,
    pk_value,
) -> bool:
    """
    Returns True if the referenced entity exists.
    Call this before writing any row that has a foreign key.
    If it returns False, reject the row with ctx.rejected().
    """
    return (
        db.query(model_class)
        .filter(getattr(model_class, pk_field) == pk_value)
        .count()
        > 0
    )


def mark_latest(db, model_class, filter_kwargs: dict) -> None:
    """
    Set is_latest = False on all existing rows matching filter_kwargs,
    then the caller inserts the new row with is_latest = True.
    Must be called inside the same transaction as the insert.
    Never call this outside a transaction.
    """
    db.query(model_class).filter_by(**filter_kwargs).update(
        {"is_latest": False},
        synchronize_session="fetch",
    )

"""
Alembic environment configuration for Artemis.
Uses batch mode for SQLite compatibility (handles ALTER TABLE limitations).
"""

import os
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url
from alembic import context

# Import all models so Alembic can detect schema changes
from database.base import Base
import database.models  # noqa: F401 — triggers all model imports

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Honor DATABASE_URL from the environment (.env) so migrations run against
# whatever the app is pointed at — SQLite locally, Postgres/Timescale in prod —
# instead of the hardcoded sqlalchemy.url in alembic.ini.
_env_url = os.getenv("DATABASE_URL")
if _env_url:
    config.set_main_option("sqlalchemy.url", _env_url)

# Batch mode rewrites ALTER TABLE as a table copy — required for SQLite, but it
# must be OFF for Postgres (which has real ALTER TABLE) or migrations misbehave.
_render_as_batch = (
    make_url(config.get_main_option("sqlalchemy.url")).get_backend_name() == "sqlite"
)

target_metadata = Base.metadata

# TimescaleDB auto-creates a descending index on each hypertable's time column,
# named "<table>_<timecol>_idx". These are managed by Timescale, not the ORM, so
# Alembic autogenerate must ignore them (and Timescale's internal schemas) rather
# than trying to drop them. Keep this in sync with scripts/timescale_setup.py.
_TIMESCALE_TIME_INDEXES = {
    "crude_oil_as_of_date_idx", "cotton_as_of_date_idx",
    "cotton_price_observation_as_of_date_idx", "fx_rates_as_of_date_idx",
    "fx_forward_curve_as_of_date_idx", "fx_volatility_as_of_date_idx",
    "fx_interest_rates_as_of_date_idx", "bunker_fuel_prices_as_of_date_idx",
    "retailer_stock_prices_price_date_idx", "cftc_cotton_cot_report_date_idx",
    "px_paraxylene_as_of_date_idx", "pta_as_of_date_idx",
    "polyester_pet_chips_as_of_date_idx", "cotton_region_weather_week_ending_idx",
}


def _include_object(obj, name, type_, reflected, compare_to):
    # Skip TimescaleDB-managed time-dimension indexes.
    if type_ == "index" and name in _TIMESCALE_TIME_INDEXES:
        return False
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_render_as_batch,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=_render_as_batch,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

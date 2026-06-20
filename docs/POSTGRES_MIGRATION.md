# SQLite → PostgreSQL + TimescaleDB Migration

This is the runbook for moving Artemis off the single-file SQLite database onto
PostgreSQL with the TimescaleDB extension. The application code is now
dialect-agnostic, so the same code runs on either backend; the only switch is
`DATABASE_URL`.

## What changed in the codebase

| File | Change |
|---|---|
| `database/base.py` | Engine is now dialect-aware: real connection pool + `pool_pre_ping` for Postgres; `check_same_thread` and the `PRAGMA` listener apply to SQLite only. |
| `database/database.py` | No longer defines its own engine/Base. Re-exports the canonical objects from `database/base.py` (fixes the previous dual-`Base` bug). |
| `alembic/env.py` | Honors `DATABASE_URL` from the environment; `render_as_batch` is enabled for SQLite only (off for Postgres). |
| `.env` | Fixed `itsDATABASE_URL` typo → `DATABASE_URL`. Added `SQLITE_URL` and `POSTGRES_URL`. |
| `docker-compose.yml` | Local TimescaleDB (`timescale/timescaledb-ha:pg16`). |
| `scripts/migrate_sqlite_to_postgres.py` | Schema + data migration with verification. |
| `scripts/timescale_setup.py` | Converts time-series tables to hypertables. |
| `requirements.txt` | Added `psycopg[binary]`. |

## Runtime: Colima (no Docker Desktop)

Docker Desktop needs a `sudo` password to install, so we use **Colima** instead —
a free, no-sudo, headless Docker engine installed via Homebrew. The Postgres+
Timescale container runs inside Colima's lightweight VM.

```bash
# One-time install (already done)
brew install colima docker docker-compose

# Start the engine + database (after a mac reboot, run these two)
colima start --cpu 2 --memory 4 --disk 20
docker-compose up -d            # uses docker-compose.yml

# Daily controls
docker-compose ps               # status
docker-compose stop             # stop DB (keeps data)
docker-compose up -d            # start DB
colima stop                     # stop the whole VM when not working
```

The container is set to `restart: unless-stopped`, so it comes back automatically
whenever Colima is running. To make Colima itself auto-start at login:
`brew services start colima`.

> Production alternative: a managed **Timescale Cloud** instance. Set
> `DATABASE_URL` in `.env` to their connection string — no other code changes.

## Migration steps

```bash
# 0. From the project root, with the venv active
cd /Users/tharunrajkumar/Projects/ArtemisV0
pip install -r requirements.txt          # picks up psycopg

# 1. Start Postgres + TimescaleDB locally
docker compose up -d
docker compose ps                        # wait until 'healthy'

# 2. Migrate schema + all data (reads SQLITE_URL/POSTGRES_URL from .env)
python scripts/migrate_sqlite_to_postgres.py
#    Re-run a clean migration with:  python scripts/migrate_sqlite_to_postgres.py --truncate

# 3. Convert time-series tables to TimescaleDB hypertables
python scripts/timescale_setup.py

# 4. Point the app at Postgres: in .env, comment the SQLite DATABASE_URL line and
#    uncomment the Postgres one:
#        # DATABASE_URL=sqlite:///./artemis.db
#        DATABASE_URL=postgresql+psycopg://artemis:artemis@localhost:5432/artemis

# 5. Smoke test
python scripts/health_check.py
alembic current                          # should print u5v6w7x8y9z0 (head)
```

The migration is **read-only against SQLite** — your `artemis.db` is never
modified, so this is fully reversible (just flip `DATABASE_URL` back).

## Models ↔ hypertables: DONE

The hypertable models already declare the composite `(id, time_col)` primary key
that Timescale requires, so `alembic revision --autogenerate` reports **no PK
drift**. `alembic/env.py` also excludes the descending time-column indexes that
Timescale auto-creates (`<table>_<timecol>_idx`) via `include_object`, so those
don't show up as spurious drops. The synced tables:

```
crude_oil, cotton, cotton_price_observation, fx_rates, fx_forward_curve,
fx_volatility, fx_interest_rates, bunker_fuel_prices, retailer_stock_prices,
cftc_cotton_cot, px_paraxylene, pta, polyester_pet_chips, cotton_region_weather
```

## Latent schema bugs fixed during migration

SQLite silently accepted data that violated the models' declared limits; Postgres
enforces them, which surfaced 11 columns to correct (now fixed in the models):

- **String length too small** (data was longer): `bunker_fuel_prices.proxy_basis`,
  `retailer_financials.sams_club_model_note`, `retailer_intelligence_extract`/
  `retailer_signal_evidence.{excluded_reason,number_mentioned}`,
  `yarn.global_cotton_benchmark_source`, `ingestion_log.script_version`,
  `retailer_demand_forecast.model_version`.
- **Numeric precision too small**: `retailer_financials.guidance_sales_range_{low,high}`
  (`Numeric(6,4)` → `Numeric(12,4)`).

## Pre-existing items (NOT caused by the migration)

- The health check reports the same 15 warnings / 69 critical on SQLite and
  Postgres — these are pre-existing `is_latest` data-quality issues (tracked
  separately).
- 9 DB-only tables (`cotton_price_observation`, `cotton_price_series`,
  `signal_category_taxonomy`, etc.) exist in the database but have no ORM model,
  so `alembic --autogenerate` wants to drop them. This predates the migration
  (they had no model in SQLite either). Either add models for them or add them to
  the Alembic ignore list.

## SQL views: DONE

Views are derived objects (stored queries, no data), so they are created by
neither Alembic nor the data migration. They live in `database/views/*.sql` and
are applied with:

```bash
python scripts/apply_views.py        # honors .env DATABASE_URL; re-runnable
```

`cotton_macro_features_v` (the cotton macro model training view, 783 rows × 66
columns) has been **fully translated to PostgreSQL-native SQL**:

- `strftime('%m'|'%Y', d)` → `EXTRACT(MONTH|YEAR FROM d)::int`
- `strftime('%W', d)` → Monday-anchored week number, replicated exactly
  (verified equal to Python `strftime('%W')` on all 783 rows + 253 year-boundary
  dates)
- `JULIANDAY(a) - JULIANDAY(b)` → `(a - b)` (DATE minus DATE = integer days)
- `is_latest = 1` → `is_latest = true` (real boolean now)
- Every "most-recent within N days" subquery got a primary-key tie-breaker
  (`ORDER BY <date> DESC, <pk> DESC`) so selection is **deterministic even if
  `is_latest` is ever over-set again**. On clean data this is a no-op (verified:
  identical content hash before/after adding it).

Cross-engine validation: the Postgres view was diffed cell-by-cell against the
original SQLite view. All differences reduce to (a) Postgres enforcing declared
`Numeric` scale, same class as the string-length fixes above, and (b) 27 month-end
weeks where crude columns differ **because the live Postgres `crude_oil` has had
its `is_latest` repaired** (0 dates with duplicate "latest" rows) while the frozen
SQLite snapshot still has the old over-set bug (68 such dates). The Postgres view
is therefore reading the *corrected* data — it is more correct than the old one,
not less.

## Production notes

- For production, use **Timescale Cloud** (managed) or RDS+self-managed
  Timescale; only `DATABASE_URL` changes.
- Keep secrets out of `.env` in production — use a secrets manager.
- Enable automated backups (managed Postgres does this; the old
  `artemis_backup_*.db` file-copy approach is retired).
```

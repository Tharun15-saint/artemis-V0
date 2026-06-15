"""
Populates cotton_price_observation from real sources.

Sources (in priority order for ICE_CT_FRONT):
  1. yfinance CT=F weekly closes (2010-present, most reliable)
  2. FRED PCOTTINDUSDM weekly fallback (1960-present, interpolated weekly)

Data discipline:
- Every row records its source, data_quality, and is_estimate flag
- Duplicate dates are skipped (existing is_latest rows for series+date)
- All prices stored in both original units and normalised USD/kg
- mark_latest scoped per (series_id, as_of_date) — not global
- Every backfill run logged to ingestion_log
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from fredapi import Fred
from sqlalchemy import text
from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from data.ingestion.cotton_futures_historical_backfill import fetch_ct_f_weekly_history
from data.ingestion.historical_macro_backfill import (
    ORIGIN_COTTON_START_DATE,
    fetch_fred_pcottindusdm_weekly,
)
from database.base import SessionLocal
from database.ingestion_context import IngestionContext

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

SCRIPT_VERSION = "1.0.0"

# Conversion: ICE CT=F quotes in US cents per pound
# 1 lb = 0.453592 kg → 1 cent/lb = 0.022046 USD/kg
CENTS_PER_LB_TO_USD_PER_KG = Decimal("0.022046")

ICE_START_DATE = date(2010, 1, 1)
YFINANCE_COTTON_URL = "https://finance.yahoo.com/quote/CT=F"
FRED_COTTON_URL = "https://fred.stlouisfed.org/series/PCOTTINDUSDM"


def get_series_id(db: Session, series_code: str) -> int:
    row = db.execute(
        text("SELECT series_id FROM cotton_price_series WHERE series_code=:code"),
        {"code": series_code},
    ).fetchone()
    if not row:
        raise ValueError(f"Series {series_code} not found in cotton_price_series table")
    return row[0]


def _normalize_date(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def get_existing_dates(db: Session, series_id: int) -> set[str]:
    rows = db.execute(
        text(
            "SELECT as_of_date FROM cotton_price_observation "
            "WHERE series_id=:sid AND is_latest=1"
        ),
        {"sid": series_id},
    ).fetchall()
    return {_normalize_date(row[0]) for row in rows}


def quantize(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def demote_series_date(db: Session, series_id: int, obs_date: date) -> None:
    db.execute(
        text(
            """
        UPDATE cotton_price_observation
        SET is_latest=0
        WHERE series_id=:sid AND as_of_date=:dt
    """
        ),
        {"sid": series_id, "dt": obs_date.isoformat()},
    )


def insert_observation(
    db: Session,
    series_id: int,
    series_code: str,
    obs_date: date,
    price_cents_per_lb: Decimal,
    source_document: str,
    source_url: str,
    data_quality: str,
    is_estimate: bool,
    pulled_at: datetime,
) -> None:
    price_usd_per_kg = quantize(price_cents_per_lb * CENTS_PER_LB_TO_USD_PER_KG)
    demote_series_date(db, series_id, obs_date)
    db.execute(
        text(
            """
        INSERT INTO cotton_price_observation
          (series_id, series_code, as_of_date, price_value, price_unit,
           price_in_usd_cents_per_lb, price_in_usd_per_kg,
           raw_value_original_unit, original_unit,
           source_document, source_url,
           data_quality, is_estimate, is_latest, pulled_at,
           created_at, updated_at)
        VALUES
          (:sid, :code, :dt, :price, 'cents_per_lb',
           :cents, :usd_kg,
           :raw_val, 'cents_per_lb',
           :src_doc, :src_url,
           :quality, :estimate, 1, :pulled,
           CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """
        ),
        {
            "sid": series_id,
            "code": series_code,
            "dt": obs_date.isoformat(),
            "price": float(price_cents_per_lb),
            "cents": float(price_cents_per_lb),
            "usd_kg": float(price_usd_per_kg),
            "raw_val": float(price_cents_per_lb),
            "src_doc": source_document,
            "src_url": source_url,
            "quality": data_quality,
            "estimate": 1 if is_estimate else 0,
            "pulled": pulled_at.isoformat(),
        },
    )


def _insert_price_observations(
    db: Session,
    ctx: IngestionContext,
    series_id: int,
    series_code: str,
    price_lookup: dict[date, Decimal],
    existing_dates: set[str],
    source_document: str,
    source_url: str,
    data_quality: str,
    is_estimate: bool,
    pulled_at: datetime,
    min_price: Decimal,
    max_price: Decimal,
) -> dict[str, int]:
    inserted = skipped = rejected = 0

    for obs_date, price in sorted(price_lookup.items()):
        obs_date_str = obs_date.isoformat()

        if price < min_price or price > max_price:
            rejected += 1
            ctx.increment_rejected(f"price_out_of_range_{obs_date_str}_{price}")
            logger.warning(
                f"{series_code} price {price}¢/lb on {obs_date_str} outside valid range — skipped"
            )
            continue

        if obs_date_str in existing_dates:
            skipped += 1
            ctx.stale()
            continue

        insert_observation(
            db,
            series_id,
            series_code,
            obs_date,
            price,
            source_document,
            source_url,
            data_quality,
            is_estimate,
            pulled_at,
        )
        existing_dates.add(obs_date_str)
        ctx.increment_inserted()
        inserted += 1

    return {"inserted": inserted, "skipped": skipped, "rejected": rejected}


def backfill_ice_ct_weekly(db: Session, ctx: IngestionContext) -> dict[str, int]:
    """Backfill ICE CT=F weekly closes from yfinance, FRED weekly fallback for gaps."""
    series_id = get_series_id(db, "ICE_CT_FRONT")
    existing_dates = get_existing_dates(db, series_id)
    pulled_at = datetime.now(timezone.utc)
    end = date.today()

    logger.info("Fetching CT=F weekly history from yfinance 2010-present...")
    ct_lookup = fetch_ct_f_weekly_history(ICE_START_DATE, end)

    stats = {"inserted": 0, "skipped": 0, "rejected": 0}
    if ct_lookup:
        yf_stats = _insert_price_observations(
            db,
            ctx,
            series_id,
            "ICE_CT_FRONT",
            ct_lookup,
            existing_dates,
            "ICE Cotton No.2 CT=F weekly close via yfinance",
            YFINANCE_COTTON_URL,
            "verified",
            False,
            pulled_at,
            Decimal("20"),
            Decimal("300"),
        )
        for key in stats:
            stats[key] += yf_stats[key]
        db.commit()
    else:
        logger.error("yfinance returned no CT=F history")

    logger.info("Fetching FRED PCOTTINDUSDM weekly fallback for ICE_CT_FRONT gaps...")
    fred_weekly = fetch_fred_pcottindusdm_weekly(ORIGIN_COTTON_START_DATE, end)
    if fred_weekly:
        fred_lookup = {obs_date: price for obs_date, price in fred_weekly}
        fred_stats = _insert_price_observations(
            db,
            ctx,
            series_id,
            "ICE_CT_FRONT",
            fred_lookup,
            existing_dates,
            "FRED PCOTTINDUSDM weekly interpolation fallback for ICE_CT_FRONT",
            FRED_COTTON_URL,
            "verified",
            True,
            pulled_at,
            Decimal("20"),
            Decimal("300"),
        )
        for key in stats:
            stats[key] += fred_stats[key]
        db.commit()
    else:
        logger.warning("FRED PCOTTINDUSDM weekly fallback returned no data")

    logger.info(
        "ICE CT=F backfill: inserted=%s skipped=%s rejected=%s",
        stats["inserted"],
        stats["skipped"],
        stats["rejected"],
    )
    return stats


def backfill_fred_cotton_monthly(db: Session, ctx: IngestionContext) -> dict[str, int]:
    """
    Backfill FRED PCOTTINDUSDM monthly Cotlook A Index as COTLOOK_A series.
    FRED series PCOTTINDUSDM = IMF Primary Commodity Prices: Cotton (Cotlook A).
    """
    api_key = os.getenv("FRED_API_KEY", "")
    if not api_key:
        logger.error("FRED_API_KEY not set — skipping FRED cotton monthly backfill")
        return {"inserted": 0, "skipped": 0, "rejected": 0}

    series_id = get_series_id(db, "COTLOOK_A")
    existing_dates = get_existing_dates(db, series_id)
    pulled_at = datetime.now(timezone.utc)

    logger.info("Fetching FRED PCOTTINDUSDM monthly cotton (Cotlook A) 1960-present...")
    fred = Fred(api_key=api_key)
    series = fred.get_series("PCOTTINDUSDM", observation_start="1960-01-01")

    if series is None or series.empty:
        logger.error("FRED PCOTTINDUSDM returned no data")
        return {"inserted": 0, "skipped": 0, "rejected": 0}

    inserted = skipped = rejected = 0
    series = series.dropna()

    for ts, value in series.items():
        obs_date = ts.date() if hasattr(ts, "date") else ts
        obs_date_str = obs_date.isoformat()

        try:
            price = quantize(Decimal(str(float(value))))
        except (TypeError, ValueError):
            rejected += 1
            ctx.increment_rejected(f"bad_value_{obs_date_str}")
            continue

        if price < Decimal("10") or price > Decimal("400"):
            rejected += 1
            ctx.increment_rejected(f"price_out_of_range_{obs_date_str}_{price}")
            continue

        if obs_date_str in existing_dates:
            skipped += 1
            ctx.stale()
            continue

        insert_observation(
            db,
            series_id,
            "COTLOOK_A",
            obs_date,
            price,
            "FRED PCOTTINDUSDM — IMF Primary Commodity Prices: Cotton (Cotlook A index)",
            FRED_COTTON_URL,
            "verified",
            False,
            pulled_at,
        )
        existing_dates.add(obs_date_str)
        ctx.increment_inserted()
        inserted += 1

    db.commit()
    logger.info(
        "FRED Cotlook A backfill: inserted=%s skipped=%s rejected=%s",
        inserted,
        skipped,
        rejected,
    )
    return {"inserted": inserted, "skipped": skipped, "rejected": rejected}


def backfill_wasde_su_ratio(db: Session, ctx: IngestionContext) -> dict[str, int]:
    """
    Backfill WASDE S/U ratio from the existing cotton table into cotton_price_observation.
    Uses US-origin rows (global WASDE S/U is replicated on every origin row per date).
    """
    series_id = get_series_id(db, "WASDE_SU_RATIO")
    existing_dates = get_existing_dates(db, series_id)
    pulled_at = datetime.now(timezone.utc)
    inserted = skipped = rejected = 0

    rows = db.execute(
        text(
            """
        SELECT as_of_date, wasde_su_ratio_pct, source, data_source_url
        FROM cotton
        WHERE wasde_su_ratio_pct IS NOT NULL
        AND origin_country = 'US'
        ORDER BY as_of_date
    """
        )
    ).fetchall()

    logger.info("Found %s cotton rows with WASDE S/U ratio data", len(rows))

    for row in rows:
        obs_date_str = _normalize_date(row[0])
        if obs_date_str in existing_dates:
            skipped += 1
            ctx.stale()
            continue

        su_ratio = quantize(Decimal(str(float(row[1]))))

        if su_ratio < Decimal("15") or su_ratio > Decimal("100"):
            rejected += 1
            ctx.increment_rejected(f"su_ratio_out_of_range_{obs_date_str}_{su_ratio}")
            continue

        demote_series_date(db, series_id, date.fromisoformat(obs_date_str))
        db.execute(
            text(
                """
            INSERT INTO cotton_price_observation
              (series_id, series_code, as_of_date,
               price_value, price_unit,
               price_in_usd_cents_per_lb, price_in_usd_per_kg,
               raw_value_original_unit, original_unit,
               source_document, source_url,
               data_quality, is_estimate, is_latest, pulled_at,
               created_at, updated_at)
            VALUES
              (:sid, 'WASDE_SU_RATIO', :dt,
               :su, 'pct_ratio',
               NULL, NULL,
               :su, 'pct_ratio',
               'USDA WASDE PSD via wasde_ingestion.py', :src_url,
               'verified', 0, 1, :pulled,
               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """
            ),
            {
                "sid": series_id,
                "dt": obs_date_str,
                "su": float(su_ratio),
                "src_url": str(row[3]) if row[3] else "https://apps.fas.usda.gov/psdonline/",
                "pulled": pulled_at.isoformat(),
            },
        )
        existing_dates.add(obs_date_str)
        ctx.increment_inserted()
        inserted += 1

    db.commit()
    logger.info("WASDE S/U ratio migration: inserted=%s skipped=%s rejected=%s", inserted, skipped, rejected)
    return {"inserted": inserted, "skipped": skipped, "rejected": rejected}


def run_full_backfill() -> bool:
    db = SessionLocal()
    try:
        with IngestionContext(
            source_name="cotton_ice_ct_backfill",
            script_version=SCRIPT_VERSION,
            data_source_url=YFINANCE_COTTON_URL,
            db=db,
        ) as ctx:
            ctx.set_as_of_date(date.today())
            stats1 = backfill_ice_ct_weekly(db, ctx)

        with IngestionContext(
            source_name="cotton_cotlook_a_backfill",
            script_version=SCRIPT_VERSION,
            data_source_url=FRED_COTTON_URL,
            db=db,
        ) as ctx:
            ctx.set_as_of_date(date.today())
            stats2 = backfill_fred_cotton_monthly(db, ctx)

        with IngestionContext(
            source_name="cotton_wasde_su_migration",
            script_version=SCRIPT_VERSION,
            data_source_url="https://apps.fas.usda.gov/psdonline/",
            db=db,
        ) as ctx:
            ctx.set_as_of_date(date.today())
            stats3 = backfill_wasde_su_ratio(db, ctx)

        logger.info(
            """
Cotton observation backfill complete:
  ICE CT=F weekly:     inserted=%s skipped=%s rejected=%s
  COTLOOK_A monthly:   inserted=%s skipped=%s rejected=%s
  WASDE S/U ratio:     inserted=%s skipped=%s rejected=%s
""",
            stats1["inserted"],
            stats1["skipped"],
            stats1["rejected"],
            stats2["inserted"],
            stats2["skipped"],
            stats2["rejected"],
            stats3["inserted"],
            stats3["skipped"],
            stats3["rejected"],
        )
        return True
    except Exception as exc:
        logger.critical("Cotton backfill failed: %s", exc, exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(0 if run_full_backfill() else 1)

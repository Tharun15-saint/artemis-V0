raise SystemExit(
    "\n"
    "PERMANENTLY DISABLED — cotton_curve_backfill.py\n"
    "This script generates synthetic S/U-calibrated cotton futures curves.\n"
    "Synthetic futures data corrupts model training.\n"
    "Real-data-only policy: use cotton_ice_historical_backfill.py instead.\n"
    "If you need to re-enable this, you are making a mistake.\n"
)

import logging
from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from sqlalchemy import delete

from data.ingestion.cotton_ingestion import (
    COTTON_TICKER,
    build_curve,
    fetch_real_ice_curve,
    validate_spot,
    write_cotton_row,
)
from database.database import SessionLocal
from database.models import CommodityFuturesCurve, Cotton

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

BACKFILL_YEARS = 10
START_DATE = date(date.today().year - BACKFILL_YEARS, 1, 1)
REAL_ICE_START = date(2025, 1, 1)


class MissingDataError(Exception):
    pass


def _normalize_close_df(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    close = df[["Close"]].dropna()
    close.index = pd.to_datetime(close.index)
    return close


def fetch_yfinance_history() -> pd.DataFrame:
    df = yf.download(
        COTTON_TICKER,
        start=START_DATE.strftime("%Y-%m-%d"),
        end=date.today().strftime("%Y-%m-%d"),
        interval="1wk",
        progress=False,
        auto_adjust=True,
    )
    close = _normalize_close_df(df)
    logger.info(f"yfinance history fetched: {len(close)} weeks")
    return close


def _to_record_date(idx: Any) -> date:
    return pd.Timestamp(idx).date()


def delete_fred_fallback_rows(db) -> int:
    cotton_deleted = db.execute(
        delete(Cotton).where(Cotton.source.like("%FRED_PCOTTINDUSDM%"))
    ).rowcount
    curve_deleted = db.execute(
        delete(CommodityFuturesCurve).where(
            CommodityFuturesCurve.source.like("%FRED_PCOTTINDUSDM%")
        )
    ).rowcount
    db.commit()
    return cotton_deleted + curve_deleted


def _build_backfill_curve(
    spot: Decimal,
    record_date: date,
    db,
) -> tuple[dict[str, Any], str]:
    """Return (curve dict, spot_source tag) for a backfill row."""
    use_real_ice = record_date >= REAL_ICE_START

    if use_real_ice:
        ice_curve = fetch_real_ice_curve(reference_date=record_date)
        if ice_curve:
            curve = build_curve(
                spot=spot,
                nasdaq_curve_data=None,
                ice_curve_data=ice_curve,
                db_session=db,
            )
            return curve, "yfinance_CT=F_historical"

    curve = build_curve(
        spot=spot,
        nasdaq_curve_data=None,
        ice_curve_data=None,
        db_session=db,
    )
    curve["source"] = "SYNTHETIC_HISTORICAL"
    curve["is_real"] = False
    spot_source = "yfinance_CT=F_spot_SYNTHETIC_HISTORICAL_curve"
    return curve, spot_source


def run_backfill() -> None:
    logger.info("Cotton curve backfill starting...")

    spot_df = fetch_yfinance_history()
    if spot_df.empty:
        raise MissingDataError("yfinance returned no cotton history for backfill.")

    db = SessionLocal()
    rows_written = 0
    real_count = 0
    synthetic_count = 0
    try:
        deleted = delete_fred_fallback_rows(db)
        logger.info(f"Deleted {deleted} FRED fallback rows")

        for idx, row in spot_df.iterrows():
            record_date = _to_record_date(idx)
            spot = Decimal(str(round(float(row["Close"]), 4)))
            if not validate_spot(spot):
                continue

            curve, spot_source = _build_backfill_curve(spot, record_date, db)
            if curve.get("is_real"):
                real_count += 1
            else:
                synthetic_count += 1

            if write_cotton_row(
                db, spot, curve, record_date, spot_source, upsert=True
            ):
                rows_written += 1
                if rows_written % 50 == 0:
                    logger.info(f"Written {rows_written} cotton rows...")

        logger.info(
            f"Backfill complete: {rows_written} rows | "
            f"real ICE curve: {real_count} | synthetic: {synthetic_count}"
        )
    except Exception as exc:
        logger.critical(f"Cotton curve backfill failed: {exc}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run_backfill()

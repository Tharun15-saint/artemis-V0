"""
Backfill weekly historical FX rates from yfinance (2004 → yesterday).

Supplements fx_ingestion.py which owns the live daily is_latest=True row.
Historical rows are stored with is_latest=False for grn_date-matched lookups.

Run:
  python data/ingestion/fx_historical_backfill.py
  python data/ingestion/fx_historical_backfill.py --from 2004-01-01
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

import yfinance as yf
from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from database.base import SessionLocal
from database.ingestion_context import IngestionContext
from database.models.market_data import FxRates
from database.validation.ingestion_validators import validate_fx_rate

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

SCRIPT_VERSION = "fx-historical-v1.0"
SOURCE_NAME = "fx_historical_yfinance"
HISTORICAL_SOURCE = "yfinance_historical_weekly"
DATA_SOURCE_URL = "https://finance.yahoo.com"
DEFAULT_START = date(2004, 1, 1)

TICKERS: dict[str, tuple[str, str, str]] = {
    "INR": ("INR=X", "usd_inr", "USD_INR"),
    "BDT": ("BDT=X", "usd_bdt", "USD_BDT"),
    "VND": ("VND=X", "usd_vnd", "USD_VND"),
    "CNY": ("CNY=X", "usd_cny", "USD_CNY"),
    "TRY": ("TRY=X", "usd_try", "USD_TRY"),
    "MAD": ("MAD=X", "usd_mad", "USD_MAD"),
    "PKR": ("PKR=X", "usd_pkr", "USD_PKR"),
}

FX_FIELDS = tuple(field for _, field, _ in TICKERS.values())


def _yesterday() -> date:
    return date.today() - timedelta(days=1)


def _normalize_week_date(ts: Any) -> date:
    if hasattr(ts, "date"):
        return ts.date()
    return date.fromisoformat(str(ts)[:10])


def fetch_weekly_series(
    symbol: str,
    start: date,
    end: date,
) -> dict[date, Decimal]:
    """Fetch weekly close prices keyed by week date."""
    ticker = yf.Ticker(symbol)
    hist = ticker.history(
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        interval="1wk",
        auto_adjust=True,
    )
    if hist is None or hist.empty:
        logger.warning("No weekly history returned for %s", symbol)
        return {}

    series: dict[date, Decimal] = {}
    for ts, row in hist.iterrows():
        close = row.get("Close")
        if close is None or (isinstance(close, float) and close != close):
            continue
        week_date = _normalize_week_date(ts)
        series[week_date] = Decimal(str(close))
    logger.info("Fetched %s weekly bars for %s", len(series), symbol)
    return series


def fetch_all_weekly_rates(start: date, end: date) -> dict[date, dict[str, Decimal]]:
    """Merge all currency weekly series into one dict per week date."""
    by_week: dict[date, dict[str, Decimal]] = {}
    for code, (symbol, field, _) in TICKERS.items():
        series = fetch_weekly_series(symbol, start, end)
        for week_date, rate in series.items():
            by_week.setdefault(week_date, {})[field] = rate
        if not series:
            logger.warning("No data for %s (%s)", code, symbol)
    return by_week


def _historical_week_exists(db: Session, as_of_date: date) -> bool:
    """
    Idempotency check keyed on as_of_date (per is_duplicate_row intent).

    is_duplicate_row() only queries is_latest=True live rows; historical
    backfill rows use is_latest=False, so we check as_of_date + source.
    """
    return (
        db.query(FxRates)
        .filter(FxRates.as_of_date == as_of_date)
        .filter(FxRates.source == HISTORICAL_SOURCE)
        .count()
        > 0
    )


def _validate_week_rates(
    raw_rates: dict[str, Decimal],
    ctx: IngestionContext,
) -> dict[str, Decimal]:
    """
    Range-only validation for historical backfill.

    Deliberately omits the 5% week-on-week check used in fx_ingestion.py —
    genuine currency movements over 20 years exceed that threshold regularly.
    """
    validated: dict[str, Decimal] = {}
    for _code, (_, field, pair) in TICKERS.items():
        rate = raw_rates.get(field)
        if rate is None:
            continue
        is_valid, reason = validate_fx_rate(float(rate), pair)
        if not is_valid:
            logger.warning("Skipping %s for week: %s", pair, reason)
            ctx.record_flag(f"{pair} {reason}")
            continue
        validated[field] = rate
    return validated


def backfill_fx_history(
    db: Session,
    start: date,
    end: date,
) -> dict[str, Any]:
    weekly_data = fetch_all_weekly_rates(start, end)
    week_dates = sorted(weekly_data.keys())

    min_date: Optional[date] = None
    max_date: Optional[date] = None

    with IngestionContext(
        source_name=SOURCE_NAME,
        script_version=SCRIPT_VERSION,
        data_source_url=DATA_SOURCE_URL,
        db=db,
    ) as ctx:
        for week_date in week_dates:
            if week_date > end:
                continue

            validated = _validate_week_rates(weekly_data[week_date], ctx)
            if not validated:
                ctx.rejected(f"No valid FX rates for week {week_date}")
                continue

            if _historical_week_exists(db, week_date):
                ctx.stale()
                continue

            record = FxRates(
                usd_inr=validated.get("usd_inr"),
                usd_bdt=validated.get("usd_bdt"),
                usd_vnd=validated.get("usd_vnd"),
                usd_cny=validated.get("usd_cny"),
                usd_try=validated.get("usd_try"),
                usd_mad=validated.get("usd_mad"),
                usd_pkr=validated.get("usd_pkr"),
                as_of_date=week_date,
                source=HISTORICAL_SOURCE,
                data_source_url=DATA_SOURCE_URL,
                refresh="weekly",
                status="historical",
                pulled_at=datetime.now(timezone.utc),
                is_latest=False,
            )
            db.add(record)
            ctx.inserted()

            if min_date is None or week_date < min_date:
                min_date = week_date
            if max_date is None or week_date > max_date:
                max_date = week_date

        if min_date:
            ctx.set_as_of_date(min_date)

        db.commit()

        return {
            "ctx": ctx,
            "weeks": len(week_dates),
            "min_date": min_date,
            "max_date": max_date,
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill weekly historical FX rates from yfinance"
    )
    parser.add_argument(
        "--from",
        dest="start_date",
        default=DEFAULT_START.isoformat(),
        help="Start date YYYY-MM-DD (default: 2004-01-01)",
    )
    args = parser.parse_args()

    start = date.fromisoformat(args.start_date)
    end = _yesterday()

    if start > end:
        raise ValueError(f"Start date {start} is after yesterday {end}")

    db = SessionLocal()
    try:
        summary = backfill_fx_history(db, start, end)
        ctx: IngestionContext = summary["ctx"]
        print("FX historical backfill complete")
        print(f"  Weeks processed: {summary['weeks']}")
        print(f"  Rows inserted:   {ctx.log.rows_inserted}")
        print(f"  Rows stale:      {ctx.log.rows_stale}")
        if summary["min_date"] and summary["max_date"]:
            print(f"  Date range:      {summary['min_date']} to {summary['max_date']}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

import argparse
import logging
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from database.base import SessionLocal, is_duplicate_row, mark_latest
from database.constants import CRUDE_OIL_DYEING_PRESSURE_THRESHOLD
from database.ingestion_context import IngestionContext
from database.models import CrudeOil, FxRates
from database.validation.ingestion_validators import (
    check_crude_price_change_flag,
    validate_and_log,
    validate_crude_price,
)

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

SCRIPT_VERSION = "4.0.0"
SOURCE_NAME = "crude_oil_fred"
SOURCE_SYSTEM = "fred_api"
FRED_DATA_URL = "https://api.stlouisfed.org/fred/series/observations"

FRED_API_KEY = os.getenv("FRED_API_KEY", "")
FRED_BASE = FRED_DATA_URL
BRENT_SERIES = "DCOILBRENTEU"
WTI_SERIES = "DCOILWTICO"

# Full FRED series start — DCOILBRENTEU begins 1987-05-20.
# Previously truncated to 2011-01-01; now using the full series.
BACKFILL_START = "1987-05-20"

SCHEDULE_HOURS = 24
REQUEST_TIMEOUT = 30
STALE_DAYS = 2          # days beyond which we widen the FRED lookback window
SLACK_STALE_DAYS = 7    # days beyond which we fire a Slack alert
FRED_MAX_LAG_DAYS = 10  # FRED EOP weekly data can lag up to ~10 calendar days

ANOMALY_WINDOW_DAYS = 30
ANOMALY_SIGMA_THRESHOLD = 3.0
ROLLING_AVG_WEEKS = 4


# ──────────────────────────────────────────────────────────────────────────────
# Slack alerting
# ──────────────────────────────────────────────────────────────────────────────

def _send_slack_alert(message: str, level: str = "warning") -> None:
    """Post to SLACK_WEBHOOK_URL. No-ops silently when env var is not set."""
    webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        logger.warning(f"[NO SLACK WEBHOOK] {message}")
        return
    prefix = "⚠ *ARTEMIS ALERT*" if level == "warning" else "🔴 *ARTEMIS CRITICAL*"
    try:
        resp = requests.post(
            webhook_url,
            json={"text": f"{prefix}\n{message}"},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.error(f"Slack webhook returned HTTP {resp.status_code}")
    except requests.RequestException as exc:
        logger.error(f"Slack alert failed: {exc}")


# ──────────────────────────────────────────────────────────────────────────────
# FRED fetch
# ──────────────────────────────────────────────────────────────────────────────

def fetch_fred_oil_series(
    series_id: str,
    start_date: str,
    end_date: str,
) -> list[tuple[date, Decimal]]:
    if not FRED_API_KEY:
        logger.error("FRED_API_KEY not set.")
        return []

    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "observation_start": start_date,
        "observation_end": end_date,
        "frequency": "w",
        "aggregation_method": "eop",
        "output_type": 1,
    }

    try:
        response = requests.get(FRED_BASE, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        observations = response.json().get("observations", [])
    except requests.RequestException as exc:
        logger.error(f"FRED oil fetch failed for {series_id}: {exc}")
        return []

    rows: list[tuple[date, Decimal]] = []
    for obs in observations:
        raw = obs.get("value")
        if raw in (None, ".", ""):
            continue
        try:
            rows.append((date.fromisoformat(obs["date"]), Decimal(str(raw))))
        except (ValueError, TypeError):
            continue
    return sorted(rows, key=lambda x: x[0])


# ──────────────────────────────────────────────────────────────────────────────
# DB helpers
# ──────────────────────────────────────────────────────────────────────────────

def _days_since_refresh(as_of: date) -> int:
    return (date.today() - as_of).days


def get_latest_crude_row(db: Session) -> Optional[CrudeOil]:
    return (
        db.query(CrudeOil)
        .filter(CrudeOil.is_latest.is_(True))
        .filter(CrudeOil.source == SOURCE_SYSTEM)
        .filter(CrudeOil.as_of_date.isnot(None))
        .order_by(CrudeOil.as_of_date.desc())
        .first()
    )


def _merge_weekly_series(
    brent_rows: list[tuple[date, Decimal]],
    wti_rows: list[tuple[date, Decimal]],
) -> dict[date, dict[str, Optional[Decimal]]]:
    brent_map = dict(brent_rows)
    wti_map = dict(wti_rows)
    all_dates = sorted(set(brent_map) | set(wti_map))
    return {
        d: {"brent": brent_map.get(d), "wti": wti_map.get(d)} for d in all_dates
    }


def _previous_spot(db: Session, as_of: date) -> tuple[Optional[Decimal], Optional[Decimal]]:
    prior = (
        db.query(CrudeOil)
        .filter(CrudeOil.is_latest.is_(True))
        .filter(CrudeOil.as_of_date < as_of)
        .order_by(CrudeOil.as_of_date.desc())
        .first()
    )
    if prior is None:
        return None, None
    return prior.brent_spot, prior.wti_spot


def _brent_n_days_ago(db: Session, as_of: date, days: int) -> Optional[Decimal]:
    """Return Brent spot closest to N days before as_of (±7 day window)."""
    target = as_of - timedelta(days=days)
    candidates = (
        db.query(CrudeOil)
        .filter(CrudeOil.is_latest.is_(True))
        .filter(CrudeOil.as_of_date >= target - timedelta(days=7))
        .filter(CrudeOil.as_of_date <= target + timedelta(days=7))
        .all()
    )
    if not candidates:
        return None
    closest = min(candidates, key=lambda r: abs((r.as_of_date - target).days))
    return closest.brent_spot


def _brent_rolling_4w_avg(db: Session, as_of: date) -> Optional[Decimal]:
    """
    4-week rolling average Brent: the mean of the N most recent prior weekly rows.
    Filters by source=fred_api to avoid contaminating with Pink Sheet monthly averages.
    Includes the current row's date if already written (autoflush catches it).
    """
    rows = (
        db.query(CrudeOil.brent_spot)
        .filter(CrudeOil.is_latest.is_(True))
        .filter(CrudeOil.source == SOURCE_SYSTEM)
        .filter(CrudeOil.brent_spot.isnot(None))
        .filter(CrudeOil.as_of_date < as_of)
        .order_by(CrudeOil.as_of_date.desc())
        .limit(ROLLING_AVG_WEEKS - 1)
        .all()
    )
    # Combine prior rows with the current price (passed separately by caller)
    # — caller appends after calling this, so we only have N-1 prior rows.
    # Return the raw prior mean; caller blends with current price.
    if not rows:
        return None
    prices = [r[0] for r in rows if r[0] is not None]
    if not prices:
        return None
    return sum(prices) / len(prices)


def _compute_anomaly(
    db: Session,
    as_of: date,
    new_price: Decimal,
) -> tuple[bool, Optional[float]]:
    """
    Z-score of new_price vs the 30-day rolling mean/stddev of prior is_latest rows.
    Returns (is_anomaly, sigma). sigma is None when history is insufficient (<5 obs).
    """
    cutoff = as_of - timedelta(days=ANOMALY_WINDOW_DAYS)
    rows = db.execute(text("""
        SELECT brent_spot FROM crude_oil
        WHERE is_latest = 1
          AND brent_spot IS NOT NULL
          AND as_of_date >= :cutoff
          AND as_of_date < :today
        ORDER BY as_of_date
    """), {"cutoff": cutoff, "today": as_of}).fetchall()

    prices = [float(r[0]) for r in rows if r[0] is not None]
    if len(prices) < 5:
        return False, None

    mean = sum(prices) / len(prices)
    variance = sum((p - mean) ** 2 for p in prices) / len(prices)
    stddev = math.sqrt(variance)
    if stddev < 0.01:
        return False, None

    z = (float(new_price) - mean) / stddev
    if abs(z) > ANOMALY_SIGMA_THRESHOLD:
        return True, round(z, 3)
    return False, None


def _latest_usd_inr(db: Session) -> Optional[Decimal]:
    row = (
        db.query(FxRates)
        .filter(FxRates.usd_inr.isnot(None))
        .order_by(FxRates.as_of_date.desc())
        .first()
    )
    return row.usd_inr if row else None


# ──────────────────────────────────────────────────────────────────────────────
# Core ingestion
# ──────────────────────────────────────────────────────────────────────────────

def append_crude_row(
    db: Session,
    ctx: IngestionContext,
    as_of: date,
    brent: Optional[Decimal],
    wti: Optional[Decimal],
    flag_large_moves: bool = True,
) -> bool:
    if brent is None or wti is None:
        ctx.increment_rejected("crude_oil: missing Brent or WTI for row")
        return False

    # Hard bounds — reject on failure
    brent_valid = validate_and_log(brent, validate_crude_price, ctx)
    wti_valid = validate_and_log(wti, validate_crude_price, ctx)
    if brent_valid is None or wti_valid is None:
        return False

    # Soft change guard — flag but ALWAYS write the row.
    # Crude oil regularly moves >20% in a week during market stress events.
    if flag_large_moves:
        prev_brent, prev_wti = _previous_spot(db, as_of)
        if flag := check_crude_price_change_flag(brent_valid, prev_brent, label="Brent"):
            ctx.record_flag(flag)
            logger.warning(f"Large move flagged (row still written): {flag}")
        if flag := check_crude_price_change_flag(wti_valid, prev_wti, label="WTI"):
            ctx.record_flag(flag)
            logger.warning(f"Large move flagged (row still written): {flag}")

    # Basic derived fields
    spread = brent_valid - wti_valid
    usd_inr = _latest_usd_inr(db)
    brent_inr = (brent_valid * usd_inr) if usd_inr else None
    wti_inr = (wti_valid * usd_inr) if usd_inr else None
    brent_30d = _brent_n_days_ago(db, as_of, days=30)
    trend = (
        (brent_valid - brent_30d) / brent_30d * Decimal("100")
        if brent_30d and brent_30d > 0
        else None
    )

    # Cost-engine derived fields (fred_api rows only)
    # Rolling 4w avg: blend the 3 most recent prior rows with the current price
    prior_avg = _brent_rolling_4w_avg(db, as_of)
    if prior_avg is not None:
        # We get back the avg of up to 3 prior rows; blend with current price
        # to get true 4-week average (current row is the 4th data point)
        rolling_4w_avg = (prior_avg * Decimal("3") + brent_valid) / Decimal("4")
    else:
        rolling_4w_avg = brent_valid  # first row — rolling avg = spot

    dyeing_premium_active = rolling_4w_avg > CRUDE_OIL_DYEING_PRESSURE_THRESHOLD

    # brent_t_minus_4w: the crude input price for programs manufactured TODAY
    # (accounts for ~4-week crude→dye chemical transmission lag)
    brent_t_minus_4w = _brent_n_days_ago(db, as_of, days=28)

    # Anomaly detection: flag if price >3σ from 30d mean
    anomaly_flag, anomaly_sigma = _compute_anomaly(db, as_of, brent_valid)
    if anomaly_flag:
        msg = (
            f"crude_oil ANOMALY: Brent ${brent_valid} on {as_of} is "
            f"{anomaly_sigma:+.2f}σ from 30d mean. "
            f"Row written with price_anomaly_flag=True — requires human review."
        )
        _send_slack_alert(msg, level="warning")
        logger.warning(msg)

    # Dedup: compare price values only — not metadata fields like source/url
    value_kwargs = {
        "brent_spot": brent_valid,
        "wti_spot": wti_valid,
    }
    if is_duplicate_row(db, CrudeOil, {"as_of_date": as_of}, value_kwargs):
        ctx.stale()
        logger.debug(f"Crude oil unchanged for as_of_date={as_of} — skipping insert")
        return True

    pulled_at = datetime.now(timezone.utc)
    # Scope demotion to fred_api rows only — Pink Sheet rows share some calendar
    # dates with FRED EOP dates (when month-start falls on a Friday) and must
    # not be demoted when writing weekly FRED observations.
    mark_latest(db, CrudeOil, {"as_of_date": as_of, "source": SOURCE_SYSTEM})
    db.add(
        CrudeOil(
            brent_spot=brent_valid,
            wti_spot=wti_valid,
            brent_wti_spread_usd=spread,
            trend_30d_pct=trend,
            brent_inr_per_barrel=brent_inr,
            wti_inr_per_barrel=wti_inr,
            fx_usd_inr_at_ingestion=usd_inr,
            as_of_date=as_of,
            days_since_refresh=_days_since_refresh(as_of),
            aggregation_period="weekly",
            source=SOURCE_SYSTEM,
            data_source_url=FRED_DATA_URL,
            refresh="weekly",
            pulled_at=pulled_at,
            is_latest=True,
            # Derived cost-engine fields
            brent_rolling_4w_avg=rolling_4w_avg,
            brent_dyeing_premium_active=dyeing_premium_active,
            brent_t_minus_4w=brent_t_minus_4w,
            price_anomaly_flag=anomaly_flag,
            price_anomaly_sigma=anomaly_sigma,
        )
    )
    # Flush to SQLite so derived-field queries in subsequent iterations
    # (e.g. during --backfill) see this row. SessionLocal has autoflush=False.
    db.flush()
    ctx.increment_inserted()
    return True


# ──────────────────────────────────────────────────────────────────────────────
# Backfill
# ──────────────────────────────────────────────────────────────────────────────

def run_historical_backfill() -> int:
    end_date = date.today().isoformat()
    brent_rows = fetch_fred_oil_series(BRENT_SERIES, BACKFILL_START, end_date)
    wti_rows = fetch_fred_oil_series(WTI_SERIES, BACKFILL_START, end_date)
    merged = _merge_weekly_series(brent_rows, wti_rows)

    db = SessionLocal()
    written = 0
    try:
        with IngestionContext(
            source_name=f"{SOURCE_NAME}_backfill",
            script_version=SCRIPT_VERSION,
            data_source_url=FRED_DATA_URL,
            db=db,
        ) as ctx:
            for week_date, prices in merged.items():
                # flag_large_moves=False: FRED is authoritative; historical shocks
                # (1990 Gulf War, 2008, 2020 COVID) are real events, not errors.
                if append_crude_row(
                    db,
                    ctx,
                    week_date,
                    prices["brent"],
                    prices["wti"],
                    flag_large_moves=False,
                ):
                    written += 1
                    if written % 100 == 0:
                        logger.info(f"Backfill progress: {written} rows written (at {week_date})...")
        logger.info(f"Crude oil backfill complete: {written} rows written from {BACKFILL_START}")
        return written
    except Exception as exc:
        logger.critical(f"Crude oil backfill failed: {exc}", exc_info=True)
        db.rollback()
        return written
    finally:
        db.close()


# ──────────────────────────────────────────────────────────────────────────────
# Scheduled run
# ──────────────────────────────────────────────────────────────────────────────

def run_once() -> bool:
    db = SessionLocal()
    try:
        latest_db = get_latest_crude_row(db)
        db_age_days = (
            _days_since_refresh(latest_db.as_of_date)
            if latest_db and latest_db.as_of_date
            else STALE_DAYS + 1
        )
        is_stale = db_age_days > STALE_DAYS

        lookback_days = 60 if is_stale else 14
        start = (date.today() - timedelta(days=lookback_days)).isoformat()
        end = date.today().isoformat()
        brent_rows = fetch_fred_oil_series(BRENT_SERIES, start, end)
        wti_rows = fetch_fred_oil_series(WTI_SERIES, start, end)
        merged = _merge_weekly_series(brent_rows, wti_rows)

        if not merged:
            logger.warning("No FRED crude oil observations returned.")
            return False

        # Write ALL dates newer than the last DB row — don't skip any missed weeks.
        new_dates = sorted(
            d for d in merged
            if latest_db is None
            or latest_db.as_of_date is None
            or d > latest_db.as_of_date
        )

        if not new_dates and not is_stale:
            logger.info(
                f"Crude oil data current | as_of_date={latest_db.as_of_date} | "
                f"days_since_refresh={latest_db.days_since_refresh}"
            )
            return True

        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=FRED_DATA_URL,
            db=db,
        ) as ctx:
            if new_dates:
                ctx.set_as_of_date(max(new_dates))
            written = 0
            for d in new_dates:
                if append_crude_row(
                    db,
                    ctx,
                    d,
                    merged[d]["brent"],
                    merged[d]["wti"],
                    flag_large_moves=True,
                ):
                    written += 1

            if written > 0:
                latest_d = max(new_dates)
                logger.info(
                    f"Crude oil: {written} new row(s) | latest as_of_date={latest_d} | "
                    f"Brent={merged[latest_d]['brent']} WTI={merged[latest_d]['wti']}"
                )
            elif db_age_days > SLACK_STALE_DAYS:
                # FRED hasn't published new data and we're past the expected lag window.
                # Could be a FRED outage, series discontinuation, or a genuine ingestion failure.
                level = "critical" if db_age_days > FRED_MAX_LAG_DAYS else "warning"
                msg = (
                    f"crude_oil: FRED data stale — latest as_of_date="
                    f"{latest_db.as_of_date if latest_db else 'NONE'} ({db_age_days}d old). "
                    f"FRED EOP weekly normally publishes within {FRED_MAX_LAG_DAYS} calendar days. "
                    f"Check FRED status or re-run --backfill."
                )
                _send_slack_alert(msg, level=level)
                logger.warning(msg)
            elif is_stale:
                logger.warning(
                    f"Crude oil stale ({db_age_days}d) but no new FRED data in lookback window."
                )
        return True
    except Exception as exc:
        logger.critical(f"Crude oil ingestion failed: {exc}", exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


def run_scheduled() -> None:
    logger.info(f"Crude oil scheduler started — every {SCHEDULE_HOURS} hours.")
    while True:
        run_once()
        time.sleep(SCHEDULE_HOURS * 3600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FRED Brent/WTI crude oil ingestion")
    parser.add_argument("--backfill", action="store_true",
                        help=f"Pull full FRED series from {BACKFILL_START} to today.")
    parser.add_argument("--schedule", action="store_true",
                        help="Run in loop every 24 hours (used by launchd).")
    parser.add_argument("--run-once", action="store_true",
                        help="Fetch latest Brent/WTI and write all new rows since last run.")
    args = parser.parse_args()

    if args.backfill:
        raise SystemExit(0 if run_historical_backfill() > 0 else 1)
    if args.schedule:
        run_scheduled()
    else:
        raise SystemExit(0 if run_once() else 1)

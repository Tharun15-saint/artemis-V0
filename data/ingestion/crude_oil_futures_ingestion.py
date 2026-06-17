"""Crude oil forward curve ingestion — EIA Short-Term Energy Outlook (STEO).

Fetches monthly price forecasts for Brent and WTI crude oil from the EIA STEO
API and computes 3m/6m/9m/12m forward curve signals relative to current spot.

DATA SOURCE — EIA STEO:
  Endpoint: https://api.eia.gov/v2/steo/data/
  Series:
    BREPUUS — Brent Crude Oil Spot Price (USD/barrel, EIA monthly forecast)
    WTIPUUS — West Texas Intermediate Crude Oil Price (USD/barrel, EIA monthly forecast)
  Coverage: Published monthly by EIA. Forecasts extend 18 months into the future.
  Quality: Official US government energy forecast. Gold standard for US energy data.
           Forecasts are EIA model projections, not exchange-traded settlement prices.
           Appropriate for cost-trend analysis and cost-pressure signals.
           Brent (BREPUUS): EIA's primary global crude benchmark forecast.
           WTI (WTIPUUS): EIA's US domestic crude benchmark forecast.
  Auth: EIA_API_KEY (free registration at eia.gov)

FORWARD TENOR CALCULATION:
  Run date = today (e.g., 2026-06-16)
  3m  = STEO month for (run_date + 3 months)  → 2026-09
  6m  = STEO month for (run_date + 6 months)  → 2026-12
  9m  = STEO month for (run_date + 9 months)  → 2027-03
  12m = STEO month for (run_date + 12 months) → 2027-06

SPOT REFERENCE:
  Latest Brent and WTI spot prices from crude_oil table (is_latest=True).
  contango_pct = (fwd - spot) / spot × 100
  curve_signal = 'contango' if brent_12m_contango_pct > 3%
               = 'backwardation' if < -3%
               = 'flat' otherwise

ARCHITECTURE:
  Appends one CommodityFutures row per run (crude-specific; cotton columns NULL).
  is_latest discipline: prior crude STEO rows demoted before new row inserted.
  Idempotent: same-day re-runs check for value changes before writing.

Usage:
  python -m data.ingestion.crude_oil_futures_ingestion
  python -m data.ingestion.crude_oil_futures_ingestion --dry-run
"""
import argparse
import logging
import os
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import requests
from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from database.base import SessionLocal, mark_latest
from database.constants import CRUDE_CURVE_SIGNAL_THRESHOLDS
from database.ingestion_context import IngestionContext
from database.models.commodities import CrudeOil
from database.models.market_data import CommodityFutures

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

SCRIPT_VERSION = "2.0.0"
SOURCE_NAME = "crude_oil_futures_eia_steo"
SOURCE_VALUE = "eia_steo"
STEO_BASE_URL = "https://api.eia.gov/v2/steo/data/"
DATA_SOURCE_URL = "https://www.eia.gov/forecasts/steo/"

BRENT_SERIES = "BREPUUS"
WTI_SERIES = "WTIPUUS"

TENORS = [3, 6, 9, 12]

Q2 = Decimal("0.01")
CONTANGO_THRESHOLD = CRUDE_CURVE_SIGNAL_THRESHOLDS["contango_threshold_pct"]
BACKWARDATION_THRESHOLD = CRUDE_CURVE_SIGNAL_THRESHOLDS["backwardation_threshold_pct"]


def _target_month(from_date: date, offset_months: int) -> str:
    """Return YYYY-MM string for the month that is offset_months ahead of from_date."""
    total_months = from_date.month + offset_months
    year = from_date.year + (total_months - 1) // 12
    month = ((total_months - 1) % 12) + 1
    return f"{year}-{month:02d}"


def _fetch_steo_series(api_key: str, series_id: str, months_needed: list[str]) -> dict[str, Decimal]:
    """Fetch STEO forecast values for requested months. Returns {YYYY-MM: Decimal}."""
    if not months_needed:
        return {}

    start = min(months_needed)
    end = max(months_needed)

    try:
        resp = requests.get(
            STEO_BASE_URL,
            params=[
                ("api_key", api_key),
                ("frequency", "monthly"),
                ("facets[seriesId][]", series_id),
                ("data[]", "value"),
                ("start", start),
                ("end", end),
                ("sort[0][column]", "period"),
                ("sort[0][direction]", "asc"),
            ],
            timeout=20,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"EIA STEO request failed for {series_id}: {e}") from e

    rows = resp.json().get("response", {}).get("data", [])
    result: dict[str, Decimal] = {}
    for row in rows:
        period = row.get("period")
        val = row.get("value")
        if period and val is not None:
            try:
                result[period] = Decimal(str(val)).quantize(Q2, rounding=ROUND_HALF_UP)
            except Exception:
                logger.warning(f"Skipping non-numeric STEO value: {series_id} {period} = {val}")
    return result


def _get_spot_prices(db: Session) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """Return (brent_spot, wti_spot) from the latest crude_oil row."""
    row = (
        db.query(CrudeOil)
        .filter(CrudeOil.is_latest.is_(True))
        .filter(CrudeOil.brent_spot.isnot(None))
        .order_by(CrudeOil.as_of_date.desc())
        .first()
    )
    if row is None:
        return None, None
    return row.brent_spot, row.wti_spot


def _contango_pct(fwd: Optional[Decimal], spot: Optional[Decimal]) -> Optional[Decimal]:
    """Return (fwd - spot) / spot × 100, or None if either value is missing."""
    if fwd is None or spot is None or spot == 0:
        return None
    return ((fwd - spot) / spot * Decimal("100")).quantize(Q2, rounding=ROUND_HALF_UP)


def _curve_signal(brent_12m_contango: Optional[Decimal]) -> Optional[str]:
    if brent_12m_contango is None:
        return None
    if brent_12m_contango > CONTANGO_THRESHOLD:
        return "contango"
    if brent_12m_contango < BACKWARDATION_THRESHOLD:
        return "backwardation"
    return "flat"


def _write_futures_row(
    db: Session,
    ctx: IngestionContext,
    as_of: date,
    brent_fwds: dict[int, Optional[Decimal]],
    wti_fwds: dict[int, Optional[Decimal]],
    brent_spot: Decimal,
    wti_spot: Optional[Decimal],
    pulled_at: datetime,
    dry_run: bool = False,
) -> bool:
    brent_12m_ct = _contango_pct(brent_fwds.get(12), brent_spot)
    signal = _curve_signal(brent_12m_ct)

    existing = (
        db.query(CommodityFutures)
        .filter(CommodityFutures.is_latest.is_(True))
        .filter(CommodityFutures.as_of_date == as_of)
        .filter(CommodityFutures.crude_source == SOURCE_VALUE)
        .first()
    )

    if existing:
        if (
            existing.brent_12m_fwd == brent_fwds.get(12)
            and existing.wti_12m_fwd == wti_fwds.get(12)
        ):
            ctx.stale()
            logger.info(f"Crude futures row for {as_of} unchanged — stale skip")
            return False
        mark_latest(db, CommodityFutures, {"as_of_date": as_of, "crude_source": SOURCE_VALUE})

    if dry_run:
        logger.info(
            f"[DRY-RUN] Would write crude futures for {as_of}: "
            f"Brent 3m=${brent_fwds.get(3)} 6m=${brent_fwds.get(6)} "
            f"9m=${brent_fwds.get(9)} 12m=${brent_fwds.get(12)} | signal={signal}"
        )
        return False

    row = CommodityFutures(
        as_of_date=as_of,
        brent_3m_fwd=brent_fwds.get(3),
        brent_6m_fwd=brent_fwds.get(6),
        brent_9m_fwd=brent_fwds.get(9),
        brent_12m_fwd=brent_fwds.get(12),
        wti_3m_fwd=wti_fwds.get(3),
        wti_6m_fwd=wti_fwds.get(6),
        wti_9m_fwd=wti_fwds.get(9),
        wti_12m_fwd=wti_fwds.get(12),
        brent_3m_contango_pct=_contango_pct(brent_fwds.get(3), brent_spot),
        brent_9m_contango_pct=_contango_pct(brent_fwds.get(9), brent_spot),
        brent_12m_contango_pct=brent_12m_ct,
        wti_3m_contango_pct=_contango_pct(wti_fwds.get(3), wti_spot),
        wti_12m_contango_pct=_contango_pct(wti_fwds.get(12), wti_spot),
        crude_curve_signal=signal,
        crude_source=SOURCE_VALUE,
        source=SOURCE_VALUE,
        data_source_url=DATA_SOURCE_URL,
        is_latest=True,
        pulled_at=pulled_at,
    )
    db.add(row)
    db.flush()
    ctx.increment_inserted()

    logger.info(
        f"Crude futures written for {as_of}: "
        f"Brent spot=${brent_spot} 3m=${brent_fwds.get(3)} 6m=${brent_fwds.get(6)} "
        f"9m=${brent_fwds.get(9)} 12m=${brent_fwds.get(12)} | signal={signal}"
    )
    return True


def run_once(dry_run: bool = False) -> bool:
    api_key = os.getenv("EIA_API_KEY")
    if not api_key:
        logger.error("EIA_API_KEY not set — cannot fetch STEO forecasts")
        return False

    db = SessionLocal()
    try:
        brent_spot, wti_spot = _get_spot_prices(db)
        if brent_spot is None:
            logger.error("No Brent spot price in crude_oil table — run crude_oil_ingestion first")
            return False

        as_of = date.today()
        target_months = {tenor: _target_month(as_of, tenor) for tenor in TENORS}
        months_needed = sorted(set(target_months.values()))

        logger.info(f"Fetching EIA STEO for {BRENT_SERIES}/{WTI_SERIES}: months {months_needed}")
        brent_raw = _fetch_steo_series(api_key, BRENT_SERIES, months_needed)
        wti_raw = _fetch_steo_series(api_key, WTI_SERIES, months_needed)

        if not brent_raw:
            logger.error("EIA STEO returned no Brent (BREPUUS) data")
            return False
        if not wti_raw:
            logger.warning("EIA STEO returned no WTI (WTIPUUS) data — Brent-only forward curve")

        brent_fwds = {tenor: brent_raw.get(month) for tenor, month in target_months.items()}
        wti_fwds = {tenor: wti_raw.get(month) for tenor, month in target_months.items()}

        missing = [f"{t}m" for t in TENORS if brent_fwds.get(t) is None]
        if missing:
            logger.warning(f"Missing STEO Brent forecasts for tenors: {missing}")

        pulled_at = datetime.now(timezone.utc)

        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=DATA_SOURCE_URL,
            db=db,
        ) as ctx:
            _write_futures_row(
                db, ctx, as_of, brent_fwds, wti_fwds,
                brent_spot, wti_spot, pulled_at, dry_run=dry_run,
            )

        logger.info("Crude forward curve ingestion complete")
        return True

    except Exception as exc:
        logger.critical(f"Crude futures ingestion failed: {exc}", exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crude oil forward curve ingestion (EIA STEO)")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be written without committing")
    args = parser.parse_args()
    raise SystemExit(0 if run_once(dry_run=args.dry_run) else 1)

"""Crude oil forward curve ingestion — EIA petroleum futures (RCLC) + EIA STEO (BREPUUS/WTIPUUS).

Populates the wti_futures_* / brent_futures_* / contango_signal / crude_market_structure
fields on crude_oil table. Creates one crude_oil row per calendar month
(source='eia_petroleum_futures', aggregation_period='monthly').

DATA SOURCES:
  WTI 1m:  EIA RCLC1 — NYMEX WTI 1st nearby contract (petroleum/pri/fut)
  WTI 3m:  EIA RCLC3 — NYMEX WTI 3rd nearby contract
  WTI 6m:  EIA RCLC4 — NYMEX WTI 4th nearby (longest available; 6m proxy)
  WTI 12m: STEO WTIPUUS — EIA forecast for month T+12 (forward-looking for current; realized for history)
  Brent:   STEO BREPUUS — for current month: T+1/3/6/12 ahead; for backfill history: same-period realized

MARKET STRUCTURE SIGNAL:
  wti_contango_signal = (RCLC4 - RCLC1) / RCLC1 × 100
    This is the only reliable historical forward signal: the NYMEX 4th-vs-1st nearby spread.
    >1.5%  → contango (supply glut, cheap near-term, market expects recovery)
    <-1.5% → backwardation (supply constrained, high near-term, market expects easing)
    Otherwise → flat

  brent_contango_signal = wti_contango_signal (WTI proxy — no ICE Brent in EIA petroleum/pri/fut)
  crude_market_structure from brent_contango_signal

NOTE ON ECONOMICS (validate before assuming prompt is right):
  Supply-glut periods (2014-2016 crash): CONTANGO — cheap spot, higher futures = storage incentive
  Tight-supply periods (2021-2022): BACKWARDATION — high spot, lower futures = no storage incentive
  This is the standard commodity futures interpretation; the NYMEX data confirms it.

Usage:
  python -m data.ingestion.crude_oil_petroleum_futures_ingestion
  python -m data.ingestion.crude_oil_petroleum_futures_ingestion --backfill
  python -m data.ingestion.crude_oil_petroleum_futures_ingestion --dry-run --backfill
"""
import argparse
import logging
import os
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from database.base import SessionLocal
from database.models.commodities import CrudeOil

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

SCRIPT_VERSION = "2.0.0"
SOURCE_NAME = "eia_petroleum_futures"
BACKFILL_START_YEAR = 2010
BACKFILL_START_MONTH = 1

PETROLEUM_FUT_URL = "https://api.eia.gov/v2/petroleum/pri/fut/data/"
STEO_URL = "https://api.eia.gov/v2/steo/data/"
DATA_SOURCE_URL = "https://www.eia.gov/petroleum/data.php"

STEO_BRENT_SERIES = "BREPUUS"
STEO_WTI_SERIES = "WTIPUUS"

CONTANGO_THRESHOLD = Decimal("1.5")
BACKWARDATION_THRESHOLD = Decimal("-1.5")
Q4 = Decimal("0.0001")
Q2 = Decimal("0.01")


# ──────────────────────────────────────────────────────────────────────────────
# EIA API helpers
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_petroleum_futures(
    api_key: str, series_id: str, start: str, end: str
) -> dict[str, Decimal]:
    """Monthly NYMEX petroleum futures. Returns {YYYY-MM: Decimal}."""
    try:
        resp = requests.get(
            PETROLEUM_FUT_URL,
            params=[
                ("api_key", api_key), ("frequency", "monthly"),
                ("facets[series][]", series_id), ("data[]", "value"),
                ("start", start), ("end", end),
                ("sort[0][column]", "period"), ("sort[0][direction]", "asc"),
                ("length", "5000"),
            ],
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"EIA petroleum/pri/fut failed for {series_id}: {e}") from e

    rows = resp.json().get("response", {}).get("data", [])
    result: dict[str, Decimal] = {}
    for row in rows:
        period, val = row.get("period"), row.get("value")
        if period and val is not None:
            try:
                result[period] = Decimal(str(val)).quantize(Q2, rounding=ROUND_HALF_UP)
            except Exception:
                logger.warning(f"Non-numeric {series_id} {period}: {val}")
    logger.info(f"  {series_id}: {len(result)} monthly values ({start}→{end})")
    return result


def _fetch_steo(api_key: str, series_id: str, start: str, end: str) -> dict[str, Decimal]:
    """Monthly EIA STEO series. Returns {YYYY-MM: Decimal}."""
    try:
        resp = requests.get(
            STEO_URL,
            params=[
                ("api_key", api_key), ("frequency", "monthly"),
                ("facets[seriesId][]", series_id), ("data[]", "value"),
                ("start", start), ("end", end),
                ("sort[0][column]", "period"), ("sort[0][direction]", "asc"),
                ("length", "5000"),
            ],
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"EIA STEO failed for {series_id}: {e}") from e

    rows = resp.json().get("response", {}).get("data", [])
    result: dict[str, Decimal] = {}
    for row in rows:
        period, val = row.get("period"), row.get("value")
        if period and val is not None:
            try:
                result[period] = Decimal(str(val)).quantize(Q2, rounding=ROUND_HALF_UP)
            except Exception:
                logger.warning(f"Non-numeric STEO {series_id} {period}: {val}")
    logger.info(f"  STEO {series_id}: {len(result)} values ({start}→{end})")
    return result


def _add_months(ym: str, n: int) -> str:
    """Add n months to a YYYY-MM string."""
    year, month = int(ym[:4]), int(ym[5:7])
    total = year * 12 + (month - 1) + n
    return f"{total // 12}-{(total % 12) + 1:02d}"


# ──────────────────────────────────────────────────────────────────────────────
# Spot price lookup from existing crude_oil rows
# ──────────────────────────────────────────────────────────────────────────────

def _build_spot_map(
    db: Session, start_ym: str, end_ym: str
) -> dict[str, tuple[Optional[Decimal], Optional[Decimal]]]:
    """Return {YYYY-MM: (brent_spot, wti_spot)} from existing fred_api/pink_sheet rows."""
    rows = db.execute(text("""
        SELECT strftime('%Y-%m', as_of_date) AS ym,
               AVG(CAST(brent_spot AS REAL)) AS brent,
               AVG(CAST(wti_spot   AS REAL)) AS wti
        FROM crude_oil
        WHERE as_of_date >= :start AND as_of_date <= :end
          AND brent_spot IS NOT NULL
          AND source IN ('fred_api', 'world_bank_pink_sheet')
        GROUP BY ym
        ORDER BY ym
    """), {"start": start_ym + "-01", "end": end_ym + "-31"}).fetchall()

    result = {}
    for ym, brent, wti in rows:
        result[ym] = (
            Decimal(str(round(brent, 4))).quantize(Q2) if brent else None,
            Decimal(str(round(wti,   4))).quantize(Q2) if wti   else None,
        )
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Contango / market-structure (pure Python, no autoflush risk)
# ──────────────────────────────────────────────────────────────────────────────

def _contango_pct(forward: Optional[Decimal], base: Optional[Decimal]) -> Optional[Decimal]:
    if forward is None or base is None or base == 0:
        return None
    return ((forward - base) / base * Decimal("100")).quantize(Q4, rounding=ROUND_HALF_UP)


def _market_structure(ct: Optional[Decimal]) -> Optional[str]:
    if ct is None:
        return None
    if ct > CONTANGO_THRESHOLD:
        return "contango"
    if ct < BACKWARDATION_THRESHOLD:
        return "backwardation"
    return "flat"


# ──────────────────────────────────────────────────────────────────────────────
# Calendar helpers
# ──────────────────────────────────────────────────────────────────────────────

def _months_range(start_year: int, start_month: int, end_year: int, end_month: int):
    y, m = start_year, start_month
    while (y, m) <= (end_year, end_month):
        yield y, m
        m += 1
        if m > 12:
            m, y = 1, y + 1


def _last_day(year: int, month: int) -> date:
    if month == 12:
        return date(year + 1, 1, 1) - __import__("datetime").timedelta(days=1)
    return date(year, month + 1, 1) - __import__("datetime").timedelta(days=1)


# ──────────────────────────────────────────────────────────────────────────────
# DB write (upsert)
# ──────────────────────────────────────────────────────────────────────────────

def _upsert_row(
    db: Session, as_of: date,
    brent_spot: Optional[Decimal], wti_spot: Optional[Decimal],
    wti_1m: Optional[Decimal], wti_3m: Optional[Decimal],
    wti_6m: Optional[Decimal], wti_12m: Optional[Decimal],
    brent_1m: Optional[Decimal], brent_3m: Optional[Decimal],
    brent_6m: Optional[Decimal], brent_12m: Optional[Decimal],
    brent_ct: Optional[Decimal], wti_ct: Optional[Decimal],
    structure: Optional[str],
    pulled_at: datetime, dry_run: bool = False,
) -> str:
    ym = as_of.strftime("%Y-%m")

    existing = db.execute(text("""
        SELECT crude_oil_id, wti_futures_6m, wti_futures_1m
        FROM crude_oil
        WHERE source = 'eia_petroleum_futures'
          AND strftime('%Y-%m', as_of_date) = :ym LIMIT 1
    """), {"ym": ym}).fetchone()

    if existing:
        ex_id, ex_wti6, ex_wti1 = existing
        # Skip only if RCLC values AND contango signal unchanged
        if (ex_wti1 is not None and wti_1m is not None and ex_wti6 is not None and wti_6m is not None
                and abs(Decimal(str(ex_wti1)) - wti_1m) < Decimal("0.01")
                and abs(Decimal(str(ex_wti6)) - wti_6m) < Decimal("0.01")):
            return "stale"
        if dry_run:
            return "update_dry"
        # SQLite text() requires float/str, not Decimal
        db.execute(text("""
            UPDATE crude_oil SET
                brent_spot=:bs, wti_spot=:ws,
                wti_futures_1m=:w1, wti_futures_3m=:w3, wti_futures_6m=:w6, wti_futures_12m=:w12,
                brent_futures_1m=:b1, brent_futures_3m=:b3, brent_futures_6m=:b6, brent_futures_12m=:b12,
                brent_contango_signal=:bct, wti_contango_signal=:wct,
                crude_market_structure=:ms, pulled_at=:pt, updated_at=:pt
            WHERE crude_oil_id=:rid
        """), {
            "bs": float(brent_spot) if brent_spot is not None else None,
            "ws": float(wti_spot)   if wti_spot   is not None else None,
            "w1": float(wti_1m)     if wti_1m     is not None else None,
            "w3": float(wti_3m)     if wti_3m     is not None else None,
            "w6": float(wti_6m)     if wti_6m     is not None else None,
            "w12": float(wti_12m)   if wti_12m    is not None else None,
            "b1": float(brent_1m)   if brent_1m   is not None else None,
            "b3": float(brent_3m)   if brent_3m   is not None else None,
            "b6": float(brent_6m)   if brent_6m   is not None else None,
            "b12": float(brent_12m) if brent_12m  is not None else None,
            "bct": float(brent_ct)  if brent_ct   is not None else None,
            "wct": float(wti_ct)    if wti_ct     is not None else None,
            "ms": structure, "pt": pulled_at, "rid": ex_id,
        })
        return "updated"

    if dry_run:
        return "insert_dry"

    row = CrudeOil(
        as_of_date=as_of, aggregation_period="monthly",
        source=SOURCE_NAME, data_source_url=DATA_SOURCE_URL,
        is_latest=False, pulled_at=pulled_at, price_anomaly_flag=False,
        brent_spot=brent_spot, wti_spot=wti_spot,
        wti_futures_1m=wti_1m, wti_futures_3m=wti_3m,
        wti_futures_6m=wti_6m, wti_futures_12m=wti_12m,
        brent_futures_1m=brent_1m, brent_futures_3m=brent_3m,
        brent_futures_6m=brent_6m, brent_futures_12m=brent_12m,
        brent_contango_signal=brent_ct, wti_contango_signal=wti_ct,
        crude_market_structure=structure,
    )
    db.add(row)
    return "inserted"


# ──────────────────────────────────────────────────────────────────────────────
# Post-backfill recalculation pass
# ──────────────────────────────────────────────────────────────────────────────

def _recalc_pass(db: Session, start_ym: str) -> int:
    """
    Re-derive contango signals and market_structure from RCLC1/RCLC4 (wti_futures_1m/6m).
    Run after all rows are committed to handle any rows where derived fields were
    written before the RCLC values were available (autoflush-safe: computed in Python).
    """
    rows = db.execute(text("""
        SELECT crude_oil_id,
               CAST(wti_futures_1m AS REAL) AS rc1,
               CAST(wti_futures_6m AS REAL) AS rc4
        FROM crude_oil
        WHERE source = 'eia_petroleum_futures'
          AND strftime('%Y-%m', as_of_date) >= :start_ym
          AND wti_futures_1m IS NOT NULL AND wti_futures_6m IS NOT NULL
    """), {"start_ym": start_ym}).fetchall()

    updated = 0
    for row_id, rc1, rc4 in rows:
        if rc1 is None or rc4 is None or rc1 == 0:
            continue
        wti_ct = _contango_pct(Decimal(str(rc4)), Decimal(str(rc1)))
        brent_ct = wti_ct   # WTI proxy for Brent
        structure = _market_structure(brent_ct)
        # SQLite text() requires float, not Decimal
        db.execute(text("""
            UPDATE crude_oil
            SET wti_contango_signal=:wct, brent_contango_signal=:bct,
                crude_market_structure=:ms
            WHERE crude_oil_id=:rid
        """), {
            "wct": float(wti_ct) if wti_ct is not None else None,
            "bct": float(brent_ct) if brent_ct is not None else None,
            "ms": structure,
            "rid": row_id,
        })
        updated += 1
    return updated


# ──────────────────────────────────────────────────────────────────────────────
# Regime distribution validation
# ──────────────────────────────────────────────────────────────────────────────

def _print_regime_distribution(db: Session) -> None:
    rows = db.execute(text("""
        SELECT strftime('%Y', as_of_date) AS yr, crude_market_structure, COUNT(*) AS cnt
        FROM crude_oil
        WHERE source = 'eia_petroleum_futures' AND crude_market_structure IS NOT NULL
        GROUP BY yr, crude_market_structure ORDER BY yr, crude_market_structure
    """)).fetchall()

    if not rows:
        logger.warning("No eia_petroleum_futures rows found for validation")
        return

    by_year: dict[str, dict[str, int]] = defaultdict(
        lambda: {"contango": 0, "backwardation": 0, "flat": 0}
    )
    for yr, structure, cnt in rows:
        by_year[yr][structure] = cnt

    print("\n─── Crude market regime distribution (NYMEX RCLC4-RCLC1 spread) ───")
    print(f"{'Year':<6} {'Contango':>10} {'Backwrd':>10} {'Flat':>6}  Dominant")
    for yr in sorted(by_year.keys()):
        d = by_year[yr]
        dominant = max(d, key=lambda k: d[k])
        print(f"{yr:<6} {d['contango']:>10} {d['backwardation']:>10} {d['flat']:>6}  {dominant}")

    print()
    print("Economics reference (NYMEX WTI 4th-vs-1st nearby spread):")
    print("  CONTANGO    = RCLC4 > RCLC1 = supply glut, cheap near-term (2014-2016 crash period)")
    print("  BACKWARDATION = RCLC4 < RCLC1 = tight supply, high spot (2021-2022 recovery)")

    # Validate against actual NYMEX data economics
    glut_years = {"2015", "2016"}
    tight_years = {"2021", "2022"}
    cont_years = {yr for yr, d in by_year.items() if d["contango"] > d["backwardation"]}
    back_years = {yr for yr, d in by_year.items() if d["backwardation"] >= d["contango"] and d["backwardation"] > d["flat"]}

    glut_ok = glut_years & cont_years
    tight_ok = tight_years & back_years

    print(f"\nEconomic validation:")
    print(f"  2015-2016 supply glut → expect CONTANGO:      {sorted(glut_ok)} {'✓' if len(glut_ok) >= 1 else '✗ — check series'}")
    print(f"  2021-2022 tight supply → expect BACKWARDATION: {sorted(tight_ok)} {'✓' if len(tight_ok) >= 1 else '✗ — check series'}")
    print()


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def run(backfill: bool = False, dry_run: bool = False) -> bool:
    api_key = os.getenv("EIA_API_KEY", "")
    if not api_key:
        logger.error("EIA_API_KEY not set")
        return False

    today = date.today()
    if backfill:
        start_year, start_month = BACKFILL_START_YEAR, BACKFILL_START_MONTH
    else:
        start_year, start_month = today.year, today.month

    end_year, end_month = today.year, today.month
    start_ym = f"{start_year}-{start_month:02d}"
    end_ym   = f"{end_year}-{end_month:02d}"

    # For STEO forward-looking Brent: fetch 12 months beyond current month
    steo_end = _add_months(end_ym, 12)

    pulled_at = datetime.now(timezone.utc)
    logger.info(f"EIA petroleum futures: {start_ym}→{end_ym} ({'backfill' if backfill else 'current month'})")

    # ── Fetch NYMEX WTI contracts ──
    logger.info("Fetching WTI NYMEX contracts (RCLC1, RCLC3, RCLC4)...")
    wti_1m_data = _fetch_petroleum_futures(api_key, "RCLC1", start_ym, end_ym)
    wti_3m_data = _fetch_petroleum_futures(api_key, "RCLC3", start_ym, end_ym)
    wti_6m_data = _fetch_petroleum_futures(api_key, "RCLC4", start_ym, end_ym)

    # ── Fetch STEO (Brent and WTI forecasts, extends to steo_end for forward tenors) ──
    logger.info(f"Fetching EIA STEO ({STEO_BRENT_SERIES}/{STEO_WTI_SERIES}, extended to {steo_end})...")
    steo_brent = _fetch_steo(api_key, STEO_BRENT_SERIES, start_ym, steo_end)
    steo_wti   = _fetch_steo(api_key, STEO_WTI_SERIES,   start_ym, steo_end)

    if not wti_1m_data:
        logger.error("No RCLC1 data from EIA — aborting")
        return False

    db = SessionLocal()
    try:
        spot_map = _build_spot_map(db, start_ym, end_ym)

        counts: dict[str, int] = defaultdict(int)

        for year, month in _months_range(start_year, start_month, end_year, end_month):
            ym     = f"{year}-{month:02d}"
            as_of  = _last_day(year, month)

            wti_1m  = wti_1m_data.get(ym)
            wti_3m  = wti_3m_data.get(ym)
            wti_6m  = wti_6m_data.get(ym)   # RCLC4 = 4th nearby (6m proxy)

            # WTI 12m: STEO for T+12 (for current months this is a real forecast)
            wti_12m = steo_wti.get(_add_months(ym, 12))

            # Brent all tenors from STEO: for recent months these are forward forecasts
            brent_1m  = steo_brent.get(_add_months(ym,  1))
            brent_3m  = steo_brent.get(_add_months(ym,  3))
            brent_6m  = steo_brent.get(_add_months(ym,  6))
            brent_12m = steo_brent.get(_add_months(ym, 12))

            if all(v is None for v in (wti_1m, wti_3m, wti_6m)):
                counts["skipped"] += 1
                continue

            spot_brent, spot_wti = spot_map.get(ym, (None, None))

            # Market structure from NYMEX WTI spread (RCLC4 - RCLC1) — authoritative signal
            # This is computed in Python before any DB write to avoid autoflush issues
            wti_ct    = _contango_pct(wti_6m, wti_1m)   # RCLC4 vs RCLC1
            brent_ct  = wti_ct                            # WTI proxy for Brent
            structure = _market_structure(brent_ct)

            status = _upsert_row(
                db=db, as_of=as_of,
                brent_spot=spot_brent, wti_spot=spot_wti,
                wti_1m=wti_1m, wti_3m=wti_3m, wti_6m=wti_6m, wti_12m=wti_12m,
                brent_1m=brent_1m, brent_3m=brent_3m, brent_6m=brent_6m, brent_12m=brent_12m,
                brent_ct=brent_ct, wti_ct=wti_ct, structure=structure,
                pulled_at=pulled_at, dry_run=dry_run,
            )
            counts[status] += 1

        logger.info(f"Row counts: {dict(counts)}")

        if not dry_run:
            # Post-backfill recalculation pass — re-derives all contango signals from
            # stored RCLC1/RCLC4 values in Python (safe from autoflush race conditions)
            recalc_n = _recalc_pass(db, start_ym)
            logger.info(f"Recalc pass: {recalc_n} rows refreshed")
            db.commit()
            logger.info("Committed.")
        else:
            logger.info("[DRY-RUN] No changes committed")

        _print_regime_distribution(db)
        return True

    except Exception as exc:
        logger.critical(f"Petroleum futures ingestion failed: {exc}", exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EIA petroleum futures → crude_oil backfill")
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(0 if run(backfill=args.backfill, dry_run=args.dry_run) else 1)

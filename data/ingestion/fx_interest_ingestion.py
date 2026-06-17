"""
Ingest central bank / benchmark interest rates from FRED + World Bank.

Sources and verified series:

  FRED (tested against live API):
    USD policy rate → FEDFUNDS  (monthly avg Fed Funds, or DFF daily averaged)
    USD 1yr bond    → DGS1      (1-yr Treasury, daily → monthly avg)
    EUR             → ECBMLFR   (ECB main refinancing rate, monthly, back to 1999)
    GBP             → IRSTCI01GBM156N (UK short-term interbank, monthly)
    MXN             → IRSTCI01MXM156N (Banxico policy-linked short rate, monthly)
    TRY             → IRSTCI01TRM156N (CBRT, monthly — currently 43.5%!)

  World Bank (FR.INR.DPST / FR.INR.LEND — annual, published with ~1yr lag):
    INR → FR.INR.LEND   (India lending rate — best available for INR)
    BDT → FR.INR.DPST   (Bangladesh deposit rate)
    CNY → FR.INR.DPST   (China deposit rate)
    IDR → FR.INR.DPST   (Indonesia deposit rate)
    PKR → FR.INR.LEND   (Pakistan lending rate)
    LKR → FR.INR.LEND   (Sri Lanka lending rate)
    THB → FR.INR.DPST   (Thailand deposit rate)
    VND → FR.INR.DPST   (Vietnam deposit rate)
    MAD → FR.INR.DPST   (Morocco deposit rate)

World Bank data is annual. The features pipeline forward-fills within-year using
the IRLookup bisect approach (last known value for all later dates).

These are LENDING/DEPOSIT rates, not exact policy rates, for EM countries — but the
rate differential is what matters for CIP, and the directional signal is accurate.
KHR (Cambodia) has no reliable free source; forward curve marked no_ir.

Run:
  python data/ingestion/fx_interest_ingestion.py
  python data/ingestion/fx_interest_ingestion.py --from 2004-01-01
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

import requests
from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from database.base import SessionLocal
from database.models.market_data import FxInterestRates

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

FRED_API_KEY = os.getenv("FRED_API_KEY", "")
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
WB_BASE = "https://api.worldbank.org/v2/country/{iso2}/indicator/{indicator}"
DEFAULT_START = date(2004, 1, 1)

# FRED series → (country_code, series_id, is_daily)
# is_daily=True: request monthly aggregation (avg). is_daily=False: already monthly.
FRED_POLICY: list[tuple[str, str, bool]] = [
    ("USD", "FEDFUNDS",          False),  # monthly avg Fed Funds
    ("EUR", "ECBMLFR",           False),  # ECB main refinancing rate (monthly)
    ("GBP", "IRSTCI01GBM156N",   False),  # UK short-term interbank (monthly)
    ("MXN", "IRSTCI01MXM156N",   False),  # Banxico (monthly)
    ("TRY", "IRSTCI01TRM156N",   False),  # CBRT (monthly)
]
FRED_BOND1YR: list[tuple[str, str]] = [
    ("USD", "DGS1"),   # 1-yr Treasury daily → monthly avg
]

# World Bank: (country_code, iso2, indicator)
# FR.INR.DPST = deposit rate; FR.INR.LEND = lending rate (annual)
WB_COUNTRIES: list[tuple[str, str, str]] = [
    ("INR", "IN", "FR.INR.LEND"),
    ("BDT", "BD", "FR.INR.DPST"),
    ("CNY", "CN", "FR.INR.DPST"),
    ("IDR", "ID", "FR.INR.DPST"),
    ("PKR", "PK", "FR.INR.LEND"),
    ("LKR", "LK", "FR.INR.LEND"),
    ("THB", "TH", "FR.INR.DPST"),
    ("VND", "VN", "FR.INR.DPST"),
    ("MAD", "MA", "FR.INR.DPST"),
]


def _fetch_fred(series_id: str, start: date, end: date, is_daily: bool) -> dict[date, Decimal]:
    params: dict = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "observation_start": start.isoformat(),
        "observation_end": end.isoformat(),
    }
    if is_daily:
        params["frequency"] = "m"
        params["aggregation_method"] = "avg"

    try:
        resp = requests.get(FRED_BASE, params=params, timeout=30)
        if resp.status_code == 400:
            err = resp.json().get("error_message", "unknown")
            logger.warning("FRED %s: 400 — %s", series_id, err[:80])
            return {}
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("FRED %s: request failed — %s", series_id, exc)
        return {}

    result: dict[date, Decimal] = {}
    for obs in resp.json().get("observations", []):
        val_str = obs.get("value", ".")
        if val_str == ".":
            continue
        try:
            result[date.fromisoformat(obs["date"])] = Decimal(val_str)
        except (ValueError, InvalidOperation, KeyError):
            continue

    logger.info("FRED %s: %d obs (%s → %s)",
                series_id, len(result),
                min(result) if result else "–",
                max(result) if result else "–")
    return result


def _fetch_worldbank(iso2: str, indicator: str, start_year: int, end_year: int) -> dict[date, Decimal]:
    """Annual central bank rates from World Bank. Returns {Jan-1-of-year: rate}."""
    url = WB_BASE.format(iso2=iso2, indicator=indicator)
    try:
        resp = requests.get(url, params={
            "format": "json",
            "date": f"{start_year}:{end_year}",
            "per_page": "100",
        }, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
            return {}
    except (requests.RequestException, (ValueError, IndexError)):
        logger.warning("World Bank %s %s: request failed", iso2, indicator)
        return {}

    result: dict[date, Decimal] = {}
    for obs in payload[1]:
        if obs.get("value") is None:
            continue
        try:
            yr = int(obs["date"])
            result[date(yr, 1, 1)] = Decimal(str(round(float(obs["value"]), 4)))
        except (ValueError, InvalidOperation, KeyError, TypeError):
            continue

    logger.info("WB %s %s: %d annual obs (%s → %s)",
                iso2, indicator, len(result),
                min(result).year if result else "–",
                max(result).year if result else "–")
    return result


def _upsert(
    db: Session,
    country_code: str,
    as_of_date: date,
    policy_rate: Decimal | None,
    bond_1yr: Decimal | None,
    source: str,
    fred_series: str,
) -> None:
    existing = (
        db.query(FxInterestRates)
        .filter(
            FxInterestRates.country_code == country_code,
            FxInterestRates.as_of_date == as_of_date,
        )
        .first()
    )
    if existing:
        if policy_rate is not None:
            existing.policy_rate_pct = policy_rate
        if bond_1yr is not None:
            existing.gov_bond_1yr_pct = bond_1yr
        existing.source = source
        existing.pulled_at = datetime.now(timezone.utc)
    else:
        db.add(FxInterestRates(
            country_code=country_code,
            as_of_date=as_of_date,
            policy_rate_pct=policy_rate,
            gov_bond_1yr_pct=bond_1yr,
            source=source,
            fred_series=fred_series,
            pulled_at=datetime.now(timezone.utc),
        ))


def ingest(start: date, end: date) -> None:
    db = SessionLocal()
    total = 0
    try:
        # ── FRED: USD, EUR, GBP, MXN, TRY ─────────────────────────────────
        if not FRED_API_KEY:
            logger.warning("FRED_API_KEY not set — skipping FRED series")
        else:
            for country_code, series_id, is_daily in FRED_POLICY:
                data = _fetch_fred(series_id, start, end, is_daily)
                for d, rate in sorted(data.items()):
                    _upsert(db, country_code, d, rate, None, "FRED", series_id)
                db.flush()
                total += len(data)
                logger.info("  %s: %d rows", country_code, len(data))

            # USD 1yr bond yield (stored in gov_bond_1yr_pct alongside FEDFUNDS)
            for country_code, series_id in FRED_BOND1YR:
                data = _fetch_fred(series_id, start, end, is_daily=True)
                for d, rate in sorted(data.items()):
                    existing = (
                        db.query(FxInterestRates)
                        .filter(
                            FxInterestRates.country_code == country_code,
                            FxInterestRates.as_of_date == d,
                        )
                        .first()
                    )
                    if existing:
                        existing.gov_bond_1yr_pct = rate
                    else:
                        db.add(FxInterestRates(
                            country_code=country_code,
                            as_of_date=d,
                            gov_bond_1yr_pct=rate,
                            source="FRED",
                            fred_series=series_id,
                            pulled_at=datetime.now(timezone.utc),
                        ))
                db.flush()
                logger.info("  %s 1yr bond: %d rows", country_code, len(data))

        # ── World Bank: EM central bank rates (annual) ──────────────────────
        start_yr, end_yr = start.year, end.year
        for country_code, iso2, indicator in WB_COUNTRIES:
            data = _fetch_worldbank(iso2, indicator, start_yr, end_yr)
            for d, rate in sorted(data.items()):
                _upsert(db, country_code, d, rate, None,
                        f"WorldBank:{indicator}", f"WB:{indicator}:{iso2}")
            db.flush()
            total += len(data)
            logger.info("  %s (WB %s %s): %d annual rows", country_code, iso2, indicator, len(data))

        db.commit()
        logger.info("Interest rate ingestion complete — %d total rows written", total)

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest central bank interest rates (FRED + World Bank)"
    )
    parser.add_argument(
        "--from", dest="start_date",
        default=DEFAULT_START.isoformat(),
        help="Start date YYYY-MM-DD (default: 2004-01-01)",
    )
    args = parser.parse_args()
    start = date.fromisoformat(args.start_date)
    ingest(start, date.today())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

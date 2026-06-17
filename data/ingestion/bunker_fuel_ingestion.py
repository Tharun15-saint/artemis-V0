"""
Bunker fuel price ingestion — the crude→freight transmission variable.

VLSFO (very-low-sulfur fuel oil) is what container vessels burn and is the
mechanical link between crude prices and freight bunker-adjustment surcharges.
Real VLSFO assessments (Platts/Argus/Ship&Bunker) are paid. Until that feed is
connected we ingest FREE EIA distillate spot prices as an HONEST proxy
(is_proxy=True): middle distillates track marine fuel closely and EIA gives
decades of clean weekly history — enough to calibrate the crude→fuel leg with
real statistical power rather than a guess.

Verified sources (EIA petroleum spot prices, weekly, free with EIA_API_KEY):
  - EER_EPD2DXL0_PF4_RGC_DPG  US Gulf Coast Ultra-Low Sulfur No.2 Diesel ($/gal)
  - EER_EPD2F_PF4_Y35NY_DPG   New York Harbor No.2 Heating Oil ($/gal)

Data discipline:
- Every row tagged is_proxy=True with proxy_basis explaining the substitution.
- Append-only; is_latest scoped per (port, grade); identical re-pulls skipped.
- Real VLSFO later: same table, is_proxy=False, grade='VLSFO', unit 'USD/tonne'.

Usage:
  python -m data.ingestion.bunker_fuel_ingestion --backfill          # full history
  python -m data.ingestion.bunker_fuel_ingestion --run-once          # recent weeks
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import requests

from data.ingestion._env import load_project_env
from database.base import SessionLocal, is_duplicate_row, mark_latest
from database.ingestion_context import IngestionContext
from database.models import BunkerFuelPrices

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

SOURCE_NAME = "bunker_fuel_eia"
SOURCE_SYSTEM = "eia_api"
SCRIPT_VERSION = "1.0.0"
EIA_SPOT_URL = "https://api.eia.gov/v2/petroleum/pri/spt/data/"
DATA_SOURCE_URL = "https://www.eia.gov/dnav/pet/pet_pri_spt_s1_w.htm"
REQUEST_TIMEOUT = 30
BACKFILL_START = "1990-01-01"

# Distillate spot series used as VLSFO transmission proxies.
PROXY_BASIS = (
    "EIA middle-distillate spot price used as a free proxy for VLSFO marine "
    "fuel. Distillates track bunker fuel closely post-IMO-2020. Replace with "
    "real VLSFO (is_proxy=False) when a Platts/Argus/Ship&Bunker feed is added."
)

SERIES = [
    {
        "series_id": "EER_EPD2DXL0_PF4_RGC_DPG",
        "port": "US Gulf Coast",
        "port_region": "US",
        "grade": "ULSD",
    },
    {
        "series_id": "EER_EPD2F_PF4_Y35NY_DPG",
        "port": "New York Harbor",
        "port_region": "US",
        "grade": "No2_heating_oil",
    },
]

# Sanity band for distillate spot, $/gallon. Outside → reject, never store.
PRICE_MIN = Decimal("0.30")
PRICE_MAX = Decimal("12.00")


def fetch_eia_series(series_id: str, start: str, end: str) -> list[tuple[date, Decimal]]:
    """Fetch a weekly EIA spot series in [start, end]. Returns (date, price)."""
    api_key = os.getenv("EIA_API_KEY", "")
    if not api_key:
        raise RuntimeError("EIA_API_KEY not set — cannot fetch bunker proxy series.")
    params = [
        ("api_key", api_key),
        ("frequency", "weekly"),
        ("data[0]", "value"),
        ("facets[series][]", series_id),
        ("start", start),
        ("end", end),
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "asc"),
        ("length", "5000"),
    ]
    try:
        resp = requests.get(EIA_SPOT_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        rows = resp.json().get("response", {}).get("data", [])
    except (requests.RequestException, ValueError) as exc:
        logger.error("EIA fetch failed for %s: %s", series_id, exc)
        return []

    out: list[tuple[date, Decimal]] = []
    for r in rows:
        period, val = r.get("period"), r.get("value")
        if val is None:
            continue
        try:
            d = datetime.strptime(period, "%Y-%m-%d").date()
            out.append((d, Decimal(str(val))))
        except (ValueError, TypeError):
            logger.warning("Skipping unparseable EIA row: %s = %s", period, val)
    return out


def _ingest_series(db, ctx: IngestionContext, spec: dict, start: str, end: str) -> dict:
    stats = {"inserted": 0, "rejected": 0}
    observations = fetch_eia_series(spec["series_id"], start, end)
    logger.info(
        "%s @ %s: %d observations %s→%s",
        spec["grade"], spec["port"], len(observations),
        observations[0][0] if observations else "-",
        observations[-1][0] if observations else "-",
    )
    pulled_at = datetime.now(timezone.utc)
    latest_date: Optional[date] = None

    for as_of, price in observations:
        if not (PRICE_MIN <= price <= PRICE_MAX):
            ctx.increment_rejected(
                f"{spec['grade']}@{spec['port']} {as_of}: ${price}/gal out of sanity band"
            )
            stats["rejected"] += 1
            continue
        entity = {"port": spec["port"], "grade": spec["grade"], "as_of_date": as_of}
        if is_duplicate_row(db, BunkerFuelPrices, entity, {"price_usd": price}):
            continue
        mark_latest(db, BunkerFuelPrices, {"port": spec["port"], "grade": spec["grade"]})
        db.add(BunkerFuelPrices(
            port=spec["port"],
            port_region=spec["port_region"],
            grade=spec["grade"],
            price_usd=price,
            price_unit="USD/gallon",
            is_proxy=True,
            proxy_basis=PROXY_BASIS,
            as_of_date=as_of,
            source=SOURCE_NAME,
            source_system=SOURCE_SYSTEM,
            data_source_url=DATA_SOURCE_URL,
            series_id=spec["series_id"],
            data_notes=None,
            pulled_at=pulled_at,
            is_latest=True,
        ))
        db.flush()
        stats["inserted"] += 1
        ctx.increment_inserted()
        latest_date = as_of if latest_date is None or as_of > latest_date else latest_date

    if latest_date:
        ctx.set_as_of_date(latest_date)
    return stats


def run(backfill: bool = False) -> bool:
    end = date.today().isoformat()
    start = BACKFILL_START if backfill else (date.today() - timedelta(days=120)).isoformat()
    db = SessionLocal()
    total = {"inserted": 0, "rejected": 0}
    try:
        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=DATA_SOURCE_URL,
            db=db,
        ) as ctx:
            for spec in SERIES:
                s = _ingest_series(db, ctx, spec, start, end)
                total["inserted"] += s["inserted"]
                total["rejected"] += s["rejected"]
            db.commit()
        logger.info(
            "Bunker fuel ingestion complete | inserted=%d rejected=%d (backfill=%s)",
            total["inserted"], total["rejected"], backfill,
        )
        return total["inserted"] > 0
    except Exception as exc:
        logger.critical("Bunker fuel ingestion failed: %s", exc, exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backfill", action="store_true", help="ingest full history from 1990")
    ap.add_argument("--run-once", action="store_true", help="ingest recent ~120 days")
    args = ap.parse_args()
    ok = run(backfill=args.backfill)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

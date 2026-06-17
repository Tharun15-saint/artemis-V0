"""
CFTC Commitments of Traders — ICE Cotton No. 2 futures.

Source: CFTC SOCRATA public API (no API key required).
  https://publicreporting.cftc.gov/resource/6dca-aqww.json
  Market: COTTON NO. 2 - ICE FUTURES U.S.  |  futonly_or_combined = FutOnly

Report schedule: published every Friday covering positions as of the prior Tuesday.
Backfill: data available from ~1986 to present.

Key signal stored: noncomm_net_pct_oi = (speculative long - short) / open_interest × 100
  > +20%: crowded long — reversal risk
  < -10%: crowded short — squeeze potential
  ~  0%: neutral

Run order:
  - Standalone: python -m data.ingestion.cftc_cot_ingestion
  - Backfill:   python -m data.ingestion.cftc_cot_ingestion --backfill
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import requests

from database.base import SessionLocal
from database.models.commodities import CftcCottonCot

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

CFTC_API_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
COTTON_MARKET = "COTTON NO. 2 - ICE FUTURES U.S."
CFTC_DATA_URL = "https://publicreporting.cftc.gov/d/6dca-aqww"

PAGE_SIZE = 1000
SLEEP_BETWEEN_PAGES = 1.0


def _safe_int(val) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _safe_decimal(val, places: int = 2) -> Optional[Decimal]:
    if val is None:
        return None
    try:
        return Decimal(str(val)).quantize(
            Decimal(10) ** -places, rounding=ROUND_HALF_UP
        )
    except Exception:
        return None


def _parse_report_date(raw: str) -> Optional[date]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except Exception:
        try:
            return date.fromisoformat(raw[:10])
        except Exception:
            return None


def _fetch_page(offset: int, limit: int = PAGE_SIZE) -> list[dict]:
    params = {
        "$where": f"market_and_exchange_names='{COTTON_MARKET}' AND futonly_or_combined='FutOnly'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": limit,
        "$offset": offset,
    }
    resp = requests.get(CFTC_API_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _row_to_model(rec: dict) -> Optional[CftcCottonCot]:
    report_date = _parse_report_date(rec.get("report_date_as_yyyy_mm_dd", ""))
    if not report_date:
        logger.warning("Skipping record with unparseable date: %s", rec.get("report_date_as_yyyy_mm_dd"))
        return None

    oi           = _safe_int(rec.get("open_interest_all"))
    nc_long      = _safe_int(rec.get("noncomm_positions_long_all"))
    nc_short     = _safe_int(rec.get("noncomm_positions_short_all"))
    nc_spread    = _safe_int(rec.get("noncomm_postions_spread_all"))  # CFTC typo: "postions"
    nc_net       = (nc_long - nc_short) if (nc_long is not None and nc_short is not None) else None
    nc_net_pct   = None
    if nc_net is not None and oi and oi > 0:
        nc_net_pct = _safe_decimal(round(nc_net / oi * 100, 2))

    comm_long    = _safe_int(rec.get("comm_positions_long_all"))
    comm_short   = _safe_int(rec.get("comm_positions_short_all"))
    comm_net     = (comm_long - comm_short) if (comm_long is not None and comm_short is not None) else None

    nr_long      = _safe_int(rec.get("nonrept_positions_long_all"))
    nr_short     = _safe_int(rec.get("nonrept_positions_short_all"))

    chg_nc_long  = _safe_int(rec.get("change_in_noncomm_long_all"))
    chg_nc_short = _safe_int(rec.get("change_in_noncomm_short_all"))
    chg_nc_net   = (chg_nc_long - chg_nc_short) if (chg_nc_long is not None and chg_nc_short is not None) else None

    return CftcCottonCot(
        report_date          = report_date,
        report_week          = rec.get("yyyy_report_week_ww"),
        open_interest        = oi,
        noncomm_long         = nc_long,
        noncomm_short        = nc_short,
        noncomm_spreading    = nc_spread,
        noncomm_net          = nc_net,
        noncomm_net_pct_oi   = nc_net_pct,
        comm_long            = comm_long,
        comm_short           = comm_short,
        comm_net             = comm_net,
        nonrept_long         = nr_long,
        nonrept_short        = nr_short,
        traders_noncomm_long = _safe_int(rec.get("traders_noncomm_long_all")),
        traders_noncomm_short= _safe_int(rec.get("traders_noncomm_short_all")),
        traders_comm_long    = _safe_int(rec.get("traders_comm_long_all")),
        traders_comm_short   = _safe_int(rec.get("traders_comm_short_all")),
        traders_total        = _safe_int(rec.get("traders_tot_all")),
        chg_open_interest    = _safe_int(rec.get("change_in_open_interest_all")),
        chg_noncomm_long     = chg_nc_long,
        chg_noncomm_short    = chg_nc_short,
        chg_noncomm_net      = chg_nc_net,
        chg_comm_long        = _safe_int(rec.get("change_in_comm_long_all")),
        chg_comm_short       = _safe_int(rec.get("change_in_comm_short_all")),
        pct_oi_noncomm_long  = _safe_decimal(rec.get("pct_of_oi_noncomm_long_all")),
        pct_oi_noncomm_short = _safe_decimal(rec.get("pct_of_oi_noncomm_short_all")),
        pct_oi_comm_long     = _safe_decimal(rec.get("pct_of_oi_comm_long_all")),
        pct_oi_comm_short    = _safe_decimal(rec.get("pct_of_oi_comm_short_all")),
        source               = "cftc_socrata",
        data_source_url      = CFTC_DATA_URL,
        pulled_at            = datetime.now(timezone.utc),
        is_latest            = True,
    )


def _existing_dates(db) -> set[date]:
    rows = db.execute(
        __import__("sqlalchemy").text("SELECT DISTINCT report_date FROM cftc_cotton_cot")
    ).fetchall()
    return {r[0] if isinstance(r[0], date) else date.fromisoformat(str(r[0])) for r in rows}


def run_ingestion(backfill: bool = False) -> dict:
    db = SessionLocal()
    stats = {"fetched": 0, "inserted": 0, "skipped": 0, "errors": 0}
    try:
        existing = _existing_dates(db)
        logger.info("Existing COT records: %d", len(existing))

        offset = 0
        while True:
            logger.info("Fetching CFTC COT page offset=%d ...", offset)
            try:
                records = _fetch_page(offset)
            except Exception as exc:
                logger.error("CFTC API fetch failed at offset=%d: %s", offset, exc)
                stats["errors"] += 1
                break

            if not records:
                logger.info("No more records — fetch complete.")
                break

            stats["fetched"] += len(records)
            inserted_this_page = 0

            for rec in records:
                report_date = _parse_report_date(rec.get("report_date_as_yyyy_mm_dd", ""))
                if report_date is None:
                    stats["errors"] += 1
                    continue

                if report_date in existing:
                    stats["skipped"] += 1
                    continue

                model = _row_to_model(rec)
                if model is None:
                    stats["errors"] += 1
                    continue

                db.add(model)
                existing.add(report_date)
                stats["inserted"] += 1
                inserted_this_page += 1

            db.commit()
            logger.info(
                "Page offset=%d | inserted=%d skipped=%d this page",
                offset, inserted_this_page, len(records) - inserted_this_page,
            )

            # If not backfilling and we got only skips on this page, we're caught up
            if not backfill and inserted_this_page == 0:
                logger.info("No new records — already up to date.")
                break

            if len(records) < PAGE_SIZE:
                break

            offset += PAGE_SIZE
            time.sleep(SLEEP_BETWEEN_PAGES)

        logger.info(
            "CFTC COT ingestion complete: fetched=%d inserted=%d skipped=%d errors=%d",
            stats["fetched"], stats["inserted"], stats["skipped"], stats["errors"],
        )
        return stats

    except Exception as exc:
        logger.critical("CFTC COT ingestion failed: %s", exc, exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest CFTC Commitments of Traders for ICE Cotton No. 2."
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Fetch full history (all pages back to 1986). Default: fetch until caught up.",
    )
    args = parser.parse_args()
    result = run_ingestion(backfill=args.backfill)
    raise SystemExit(0 if result["errors"] == 0 else 1)

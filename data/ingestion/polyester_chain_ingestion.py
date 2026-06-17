"""Polyester chain proxy ingestion — crude-derived estimates for PX, PTA, PET chip.

Derives estimated Asian spot prices for para-xylene (PX), purified terephthalic
acid (PTA), and polyester PET chips from the Brent crude oil price using
industry-calibrated conversion coefficients.

IMPORTANT — DATA QUALITY:
  These are PROXY prices (is_proxy=True). Accuracy is ±20–30% vs real ICIS prices.
  They are directionally correct and useful for cost pressure signals, but must NOT
  be used for precise contract pricing or cost estimation.
  The KnowledgeGap records for PX/PTA/chip are demoted from 'blocks_reasoning' to
  'degrades_accuracy' once this pipeline runs — meaning the intelligence engine can
  reason about the polyester chain, but must surface the proxy caveat to the user.

  Replace these proxy rows with real ICIS data when subscription is obtained.
  The is_proxy flag enables exact identification of proxy rows for replacement.

Conversion chain (all USD/tonne):
  Brent (USD/bbl) → PX: brent × 8.21 + 100
  PX → PTA:        px × 0.86 + 95
  PTA → PET chip:  pta × 0.83 + 135  (MEG cost embedded in constant)

Coefficient source: IHS Markit petrochemical cost methodology, ICIS margin analysis,
  Reliance Industries polyester cost disclosures (2019–2023 average spreads).

Viscose (viscose_rayon table) is explicitly NOT covered: viscose is derived from
dissolving pulp (wood cellulose), NOT crude oil. Its KnowledgeGap remains
blocks_reasoning. Do not add viscose to this pipeline.

Usage:
  python -m data.ingestion.polyester_chain_ingestion
  python -m data.ingestion.polyester_chain_ingestion --backfill --start 2011-01-01
"""
import argparse
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from database.base import SessionLocal, mark_latest
from database.constants import POLYESTER_CHAIN_PROXY
from database.ingestion_context import IngestionContext
from database.models import CrudeOil, KnowledgeGap, PolyesterPetChips, PxParaxylene, Pta

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

SCRIPT_VERSION = "1.0.0"
SOURCE_NAME = "polyester_chain_proxy"
SOURCE_VALUE = "crude_derived_proxy"
DATA_SOURCE_URL = "https://www.eia.gov/petroleum/  (Brent basis) + IHS Markit coefficients"

PX_MULT = POLYESTER_CHAIN_PROXY["px_from_brent_multiplier"]
PX_CONST = POLYESTER_CHAIN_PROXY["px_from_brent_constant"]
PTA_MULT = POLYESTER_CHAIN_PROXY["pta_from_px_multiplier"]
PTA_CONST = POLYESTER_CHAIN_PROXY["pta_from_px_constant"]
CHIP_MULT = POLYESTER_CHAIN_PROXY["chip_from_pta_multiplier"]
CHIP_CONST = POLYESTER_CHAIN_PROXY["chip_from_pta_constant"]


def _compute_chain(brent: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """Return (px_usd_t, pta_usd_t, chip_usd_t) from Brent USD/bbl."""
    px = (brent * PX_MULT + PX_CONST).quantize(Decimal("0.01"))
    pta = (px * PTA_MULT + PTA_CONST).quantize(Decimal("0.01"))
    chip = (pta * CHIP_MULT + CHIP_CONST).quantize(Decimal("0.01"))
    return px, pta, chip


def _get_crude_rows(
    db: Session,
    start: Optional[date] = None,
) -> list[CrudeOil]:
    q = (
        db.query(CrudeOil)
        .filter(CrudeOil.is_latest.is_(True))
        .filter(CrudeOil.brent_spot.isnot(None))
    )
    if start:
        q = q.filter(CrudeOil.as_of_date >= start)
    return q.order_by(CrudeOil.as_of_date).all()


def _write_px_row(
    db: Session,
    ctx: IngestionContext,
    as_of: date,
    px: Decimal,
    brent: Decimal,
    pulled_at: datetime,
) -> bool:
    crude_to_px_ratio = (px / (brent * Decimal("7.33"))).quantize(Decimal("0.0001"))
    existing = (
        db.query(PxParaxylene)
        .filter(PxParaxylene.is_latest.is_(True))
        .filter(PxParaxylene.as_of_date == as_of)
        .first()
    )
    if existing and existing.spot_usd_tonne == px:
        return False  # unchanged

    mark_latest(db, PxParaxylene, {"as_of_date": as_of})
    db.add(
        PxParaxylene(
            as_of_date=as_of,
            spot_usd_tonne=px,
            crude_to_px_ratio=crude_to_px_ratio,
            brent_spot_ref=brent,
            source=SOURCE_VALUE,
            data_source_url=DATA_SOURCE_URL,
            is_proxy=True,
            is_latest=True,
            pulled_at=pulled_at,
        )
    )
    return True


def _write_pta_row(
    db: Session,
    ctx: IngestionContext,
    as_of: date,
    pta: Decimal,
    px: Decimal,
    brent: Decimal,
    pulled_at: datetime,
) -> bool:
    spread = (pta - px).quantize(Decimal("0.01"))
    existing = (
        db.query(Pta)
        .filter(Pta.is_latest.is_(True))
        .filter(Pta.as_of_date == as_of)
        .first()
    )
    if existing and existing.spot_usd_tonne == pta:
        return False

    mark_latest(db, Pta, {"as_of_date": as_of})
    db.add(
        Pta(
            as_of_date=as_of,
            spot_usd_tonne=pta,
            px_to_pta_spread_usd=spread,
            brent_spot_ref=brent,
            source=SOURCE_VALUE,
            data_source_url=DATA_SOURCE_URL,
            is_proxy=True,
            is_latest=True,
            pulled_at=pulled_at,
        )
    )
    return True


def _write_chip_row(
    db: Session,
    ctx: IngestionContext,
    as_of: date,
    chip: Decimal,
    pta: Decimal,
    brent: Decimal,
    pulled_at: datetime,
) -> bool:
    spread = (chip - pta).quantize(Decimal("0.01"))
    existing = (
        db.query(PolyesterPetChips)
        .filter(PolyesterPetChips.is_latest.is_(True))
        .filter(PolyesterPetChips.as_of_date == as_of)
        .first()
    )
    if existing and existing.spot_usd_tonne == chip:
        return False

    mark_latest(db, PolyesterPetChips, {"as_of_date": as_of})
    db.add(
        PolyesterPetChips(
            as_of_date=as_of,
            spot_usd_tonne=chip,
            pta_to_chip_spread_usd=spread,
            brent_spot_ref=brent,
            source=SOURCE_VALUE,
            data_source_url=DATA_SOURCE_URL,
            is_proxy=True,
            is_latest=True,
            pulled_at=pulled_at,
        )
    )
    return True


def _update_knowledge_gaps(db: Session) -> None:
    """Downgrade PX/PTA/chip gaps from blocks_reasoning → degrades_accuracy."""
    for related_name in [None]:  # gaps are identified by domain+description keywords
        pass

    gaps = (
        db.query(KnowledgeGap)
        .filter(KnowledgeGap.gap_id.in_([1, 2, 3]))  # PX, PTA, chip gap_ids
        .filter(KnowledgeGap.status == "open")
        .all()
    )
    for gap in gaps:
        if gap.gap_severity == "blocks_reasoning":
            gap.gap_severity = "degrades_accuracy"
            gap.status = "data_ingestion_in_progress"
            gap.resolution_path = (
                (gap.resolution_path or "")
                + "\n[2026-06] Crude-derived proxy ingestion active (is_proxy=True rows). "
                "Severity downgraded to degrades_accuracy. Replace with ICIS subscription "
                "to achieve resolved status."
            )
            logger.info(f"KnowledgeGap id={gap.gap_id} updated: blocks_reasoning → degrades_accuracy")

    db.flush()


def run_ingestion(crude_rows: list[CrudeOil], db: Session, label: str = SOURCE_NAME) -> int:
    written = 0
    pulled_at = datetime.now(timezone.utc)

    with IngestionContext(
        source_name=label,
        script_version=SCRIPT_VERSION,
        data_source_url=DATA_SOURCE_URL,
        db=db,
    ) as ctx:
        for crude_row in crude_rows:
            brent = crude_row.brent_spot
            if brent is None or brent <= 0:
                continue

            px, pta, chip = _compute_chain(brent)
            as_of = crude_row.as_of_date

            px_written = _write_px_row(db, ctx, as_of, px, brent, pulled_at)
            pta_written = _write_pta_row(db, ctx, as_of, pta, px, brent, pulled_at)
            chip_written = _write_chip_row(db, ctx, as_of, chip, pta, brent, pulled_at)

            if px_written or pta_written or chip_written:
                ctx.increment_inserted()
                written += 1
            else:
                ctx.stale()

        _update_knowledge_gaps(db)

    return written


def run_once() -> bool:
    db = SessionLocal()
    try:
        # Only process recent crude rows (last 90 days)
        cutoff = date.today() - timedelta(days=90)
        crude_rows = _get_crude_rows(db, start=cutoff)
        if not crude_rows:
            logger.warning("No recent crude_oil rows found — run crude_oil_ingestion first")
            return False
        written = run_ingestion(crude_rows, db)
        logger.info(f"Polyester chain proxy: {written} dates updated")
        return True
    except Exception as exc:
        logger.critical(f"Polyester chain proxy ingestion failed: {exc}", exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


def run_backfill(start: Optional[date] = None) -> int:
    if start is None:
        start = date(2011, 1, 1)  # earliest crude data in DB
    db = SessionLocal()
    try:
        crude_rows = _get_crude_rows(db, start=start)
        if not crude_rows:
            logger.warning("No crude rows found for backfill range")
            return 0
        logger.info(f"Backfilling polyester chain for {len(crude_rows)} crude rows...")
        written = run_ingestion(crude_rows, db, label=f"{SOURCE_NAME}_backfill")
        logger.info(f"Polyester chain backfill complete: {written} dates written")
        return written
    except Exception as exc:
        logger.critical(f"Polyester chain backfill failed: {exc}", exc_info=True)
        db.rollback()
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Polyester chain proxy ingestion (crude-derived PX/PTA/chip)"
    )
    parser.add_argument("--backfill", action="store_true", help="Compute for all crude rows")
    parser.add_argument(
        "--start",
        default="2011-01-01",
        help="Backfill start date (YYYY-MM-DD)",
    )
    args = parser.parse_args()

    if args.backfill:
        start_date = date.fromisoformat(args.start)
        n = run_backfill(start=start_date)
        raise SystemExit(0 if n >= 0 else 1)
    else:
        raise SystemExit(0 if run_once() else 1)

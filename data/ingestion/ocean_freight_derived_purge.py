"""
One-time ocean freight integrity purge — remove fabricated / collinear rates.

What this script removes from ocean_freight_rates:
  1. rate_source_tier = 'drewry_wci_derived' rows. Every one of these is a
     Shanghai WCI rate multiplied by a STATIC 2023 differential constant
     (e.g. Chittagong→LAX = SHA-LAX × 0.95). They are perfectly collinear with
     the Shanghai base series and therefore carry zero independent information.
     Any crude→freight correlation fitted on them would be an artifact of the
     constant, not the market.
  2. source = 'manual_estimate' rows (the 6 seeded India-origin guesses).

What it KEEPS:
  - rate_source_tier = 'drewry_wci_direct' — genuine Drewry WCI corridor rates
    (Shanghai→LA, Shanghai→NY). These are real published assessments.

After deletion it re-asserts is_latest: exactly one is_latest=True row per
surviving (origin_port, destination_port), being the most recent as_of_date.

Why delete rather than NULL: these rows are not observations with a missing
field — the entire rate is a derived multiple. A derived multiple recorded as
if it were a market rate is the "wrong data" the platform must never train on.
Derived corridors can be re-introduced later ONLY as real per-lane data from a
paid FBX / Drewry feed.

Run once:  python -m data.ingestion.ocean_freight_derived_purge
Dry run:   python -m data.ingestion.ocean_freight_derived_purge --dry-run
"""

from __future__ import annotations

import argparse
import logging

from sqlalchemy import func

from database.base import SessionLocal
from database.models import OceanFreightRates

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def _summary(db) -> list[tuple]:
    return (
        db.query(
            OceanFreightRates.source,
            OceanFreightRates.rate_source_tier,
            func.count().label("cnt"),
        )
        .group_by(OceanFreightRates.source, OceanFreightRates.rate_source_tier)
        .all()
    )


def purge(dry_run: bool = False) -> int:
    db = SessionLocal()
    try:
        logger.info("BEFORE purge — rows by (source, tier):")
        for src, tier, cnt in _summary(db):
            logger.info("  %-22s %-22s %d", src, tier, cnt)

        doomed = db.query(OceanFreightRates).filter(
            (OceanFreightRates.rate_source_tier == "drewry_wci_derived")
            | (OceanFreightRates.source == "manual_estimate")
        )
        doomed_count = doomed.count()
        logger.info(
            "Targeting %d rows for deletion (derived + manual_estimate).", doomed_count
        )

        if dry_run:
            logger.info("DRY RUN — no rows deleted.")
            for row in doomed.limit(50).all():
                logger.info(
                    "  would delete: %s → %s | tier=%s source=%s as_of=%s",
                    row.origin_port, row.destination_port,
                    row.rate_source_tier, row.source, row.as_of_date,
                )
            db.rollback()
            return doomed_count

        doomed.delete(synchronize_session=False)
        db.flush()

        # Re-assert is_latest on survivors: newest as_of_date per corridor wins.
        survivors = db.query(OceanFreightRates).all()
        by_corridor: dict[tuple, list] = {}
        for row in survivors:
            by_corridor.setdefault((row.origin_port, row.destination_port), []).append(row)
        relatched = 0
        for rows in by_corridor.values():
            rows.sort(key=lambda r: (r.as_of_date, r.ocean_rate_id), reverse=True)
            for idx, row in enumerate(rows):
                want = 1 if idx == 0 else 0
                if int(bool(row.is_latest)) != want:
                    row.is_latest = bool(want)
                    relatched += 1

        db.commit()
        logger.info("Deleted %d rows. Re-latched is_latest on %d survivor rows.",
                    doomed_count, relatched)

        logger.info("AFTER purge — rows by (source, tier):")
        for src, tier, cnt in _summary(db):
            logger.info("  %-22s %-22s %d", src, tier, cnt)
        return doomed_count
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report only, delete nothing")
    args = ap.parse_args()
    purge(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

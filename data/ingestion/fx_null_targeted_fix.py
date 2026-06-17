"""
One-time fix for the 4 known residual NULL clusters after fx_fred_rebuild.py.

Situation:
  usd_bdt: 33 NULLs (2004-01-01 → 2004-08-12) — first real value is 2004-08-19 (60.842)
  usd_try: 44 NULLs (2004-01-01 → 2004-10-28) — first real value is 2004-11-04 (1.394)
  usd_idr:  4 NULLs (2008-08-11/14/18, 2010-11-01) — surrounded by real data within 14 days
  usd_mxn:  1 NULL  (2020-03-23 COVID Monday) — surrounded by real data within 7 days

All four cases use REAL adjacent observations from the DB.
BDT/TRY widen the cross-fill window (400 days) to bridge the 2004 pre-coverage era.
BDT was at ~60-62 BDT/USD throughout 2004 — stable managed float.
TRY represents new lira equivalent throughout (yfinance TRY=X starts Nov 2004 in new-TRY units).
"""

from __future__ import annotations

import logging
from bisect import bisect_left
from decimal import Decimal
from typing import Optional

from database.base import SessionLocal
from database.models.market_data import FxRates

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BOUNDS: dict[str, tuple[float, float]] = {
    "usd_bdt": (55.0,    135.0),
    "usd_try": (1.0,      50.0),
    "usd_idr": (2000.0, 21000.0),
    "usd_mxn": (3.0,      25.0),
}

# (tight_days, wide_days)
FIX_PARAMS: dict[str, tuple[int, int]] = {
    "usd_idr": (7,   14),   # Aug 2008 and Nov 2010 gaps — data within same week
    "usd_mxn": (7,   14),   # 2020-03-23 — data within same week
    "usd_bdt": (14, 400),   # 2004 pre-coverage → nearest is 2004-08-19 (stable ~60-62 BDT/USD)
    "usd_try": (14, 400),   # 2004 pre-coverage → nearest is 2004-11-04 (TRY new-lira scale)
}


def nearest(
    sorted_dates: list,
    sorted_vals: list,
    target,
    max_days: int,
) -> Optional[Decimal]:
    if not sorted_dates:
        return None
    from datetime import timedelta
    idx = bisect_left(sorted_dates, target)
    best: Optional[tuple[int, Decimal]] = None
    for i in (idx - 1, idx):
        if 0 <= i < len(sorted_dates):
            delta = abs((sorted_dates[i] - target).days)
            if delta <= max_days and (best is None or delta < best[0]):
                best = (delta, sorted_vals[i])
    return best[1] if best else None


def in_bounds(col: str, val: Decimal) -> bool:
    if col not in BOUNDS:
        return True
    lo, hi = BOUNDS[col]
    return lo <= float(val) <= hi


def targeted_cross_fill(rows: list, col: str, tight_days: int, wide_days: int) -> int:
    real_pairs = sorted(
        ((r.as_of_date, getattr(r, col)) for r in rows if getattr(r, col) is not None),
        key=lambda x: x[0],
    )
    if not real_pairs:
        logger.warning("  %s: no real values in DB — cannot cross-fill", col)
        return 0

    logger.info(
        "  %s: %d real values, spanning %s → %s",
        col, len(real_pairs), real_pairs[0][0], real_pairs[-1][0],
    )

    s_dates = [p[0] for p in real_pairs]
    s_vals  = [p[1] for p in real_pairs]

    filled = 0
    for row in rows:
        if getattr(row, col) is not None:
            continue

        val = nearest(s_dates, s_vals, row.as_of_date, tight_days)
        window = tight_days

        if val is None and wide_days > tight_days:
            val = nearest(s_dates, s_vals, row.as_of_date, wide_days)
            window = wide_days

        if val is not None and in_bounds(col, val):
            setattr(row, col, val)
            note = f"{col}:±{window}d"
            row.data_gap_notes = (
                f"{row.data_gap_notes} | {note}" if row.data_gap_notes else note
            )
            filled += 1

    return filled


def main() -> None:
    db = SessionLocal()
    try:
        logger.info("Loading all FxRates rows...")
        rows = db.query(FxRates).all()
        logger.info("  %d rows loaded", len(rows))

        total_filled = 0
        for col, (tight, wide) in FIX_PARAMS.items():
            before = sum(1 for r in rows if getattr(r, col) is None)
            if before == 0:
                logger.info("  %s: already 0 NULLs — skipping", col)
                continue
            logger.info("  %s: %d NULLs before fix", col, before)
            n = targeted_cross_fill(rows, col, tight, wide)
            after = sum(1 for r in rows if getattr(r, col) is None)
            logger.info("  %s: filled %d → %d NULLs remaining", col, n, after)
            total_filled += n

        if total_filled == 0:
            logger.info("Nothing to fix — all target columns already clean.")
            return

        db.flush()
        db.commit()
        logger.info("Committed. Total rows fixed: %d", total_filled)

        # Final audit
        logger.info("\nFinal NULL counts (target columns):")
        for col in FIX_PARAMS:
            remaining = sum(1 for r in rows if getattr(r, col) is None)
            status = "✓" if remaining == 0 else f"⚠ {remaining} remaining"
            logger.info("  %-12s %s", col, status)

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()

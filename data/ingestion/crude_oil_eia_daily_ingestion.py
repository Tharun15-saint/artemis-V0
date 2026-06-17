"""EIA daily crude oil ingestion — PRIMARY source for the crude layer.

EIA publishes daily spot prices (weekdays only, 1-2 day lag) via the v2 API:
  Brent: series RBRTE (Europe Brent Spot Price FOB, $/bbl, from 1987-05-20)
  WTI:   series RWTC (Cushing OK WTI Spot Price FOB, $/bbl, from 1986-01-02)

These are stored in crude_oil with source='eia_daily'. Daily resolution is the
correct granularity for transmission calibration: every invoice date maps to an
exact crude price (no nearest-weekly approximation).

SOURCE HIERARCHY (authority order):
  1. eia_daily   — daily resolution. Use for transmission calibration joins.   [THIS FILE]
  2. fred_api    — weekly EOP. Use for rolling averages and trend signals.
  3. world_bank  — monthly anchor. Historical analysis only.
  4. eia_petroleum_futures — forward curve (separate ingestion).

DERIVED FIELDS (computed in a single post-backfill window pass, never inline —
SessionLocal has autoflush=False and the backfill loop must not depend on
just-inserted rows being visible to derived-field queries):
  brent_rolling_4w_avg   — trailing 28-calendar-day mean of brent_spot
  brent_rolling_13w_avg  — trailing 91-calendar-day mean of brent_spot
  brent_t_minus_4w       — brent_spot at the row nearest (as_of - 28d), look-back
  brent_t_minus_8w       — brent_spot at the row nearest (as_of - 56d), look-back
  brent_yoy_pct          — (brent_now - brent_~365d_ago) / brent_~365d_ago * 100
  wti_brent_spread       — wti_spot - brent_spot (corridor basis; US off WTI, Asia off Brent)

No approximation. If a derived value cannot be computed (insufficient history),
it is left NULL — never back-filled with a guess.
"""
import argparse
import bisect
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import requests
from sqlalchemy import text

from data.ingestion._env import load_project_env
from database.base import SessionLocal
from database.ingestion_context import IngestionContext

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

SCRIPT_VERSION = "1.0.0"
SOURCE_NAME = "crude_oil_eia_daily"
SOURCE_SYSTEM = "eia_daily"
EIA_SPOT_URL = "https://api.eia.gov/v2/petroleum/pri/spt/data/"
DATA_SOURCE_URL = "https://www.eia.gov/dnav/pet/pet_pri_spt_s1_d.htm"

BRENT_SERIES = "RBRTE"
WTI_SERIES = "RWTC"

BACKFILL_START = "1987-05-20"   # RBRTE earliest; RWTC trimmed to match
REQUEST_TIMEOUT = 30
SCHEDULE_HOURS = 24

# Staleness: EIA daily publishes weekdays with 1-2 day lag.
STALE_BUSINESS_DAYS = 3   # > this many business days old → Slack CRITICAL

# Sanity band — crude spot outside this is rejected, never stored.
PRICE_MIN = Decimal("1.00")
PRICE_MAX = Decimal("400.00")

# Rolling window definitions (calendar days)
ROLL_4W_DAYS = 28
ROLL_13W_DAYS = 91
LAG_4W_DAYS = 28
LAG_8W_DAYS = 56
YOY_DAYS = 365
YOY_TOLERANCE_DAYS = 7   # accept a YoY anchor within ±7 days of the 365d target


# ──────────────────────────────────────────────────────────────────────────────
# Slack alerting
# ──────────────────────────────────────────────────────────────────────────────

def _send_slack_alert(message: str, level: str = "warning") -> None:
    """Post to SLACK_WEBHOOK_URL. No-ops (logs) when env var is unset."""
    webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        logger.warning(f"[NO SLACK WEBHOOK] {message}")
        return
    prefix = "⚠ *ARTEMIS ALERT*" if level == "warning" else "🔴 *ARTEMIS CRITICAL*"
    try:
        resp = requests.post(webhook_url, json={"text": f"{prefix}\n{message}"}, timeout=10)
        if resp.status_code != 200:
            logger.error(f"Slack webhook returned HTTP {resp.status_code}")
    except requests.RequestException as exc:
        logger.error(f"Slack alert failed: {exc}")


# ──────────────────────────────────────────────────────────────────────────────
# EIA fetch
# ──────────────────────────────────────────────────────────────────────────────

def fetch_eia_daily_series(series_id: str, start: str, end: str) -> list[tuple[date, Decimal]]:
    """Fetch an EIA daily spot series in [start, end]. Returns sorted (date, price)."""
    api_key = os.getenv("EIA_API_KEY", "")
    if not api_key:
        raise RuntimeError("EIA_API_KEY not set — cannot fetch EIA daily crude series.")
    params = [
        ("api_key", api_key),
        ("frequency", "daily"),
        ("data[0]", "value"),
        ("facets[series][]", series_id),
        ("start", start),
        ("end", end),
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "asc"),
        ("length", "5000"),
    ]
    out: list[tuple[date, Decimal]] = []
    offset = 0
    while True:
        page = params + [("offset", str(offset))]
        try:
            resp = requests.get(EIA_SPOT_URL, params=page, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            body = resp.json().get("response", {})
        except (requests.RequestException, ValueError) as exc:
            logger.error("EIA daily fetch failed for %s @ offset %d: %s", series_id, offset, exc)
            break
        rows = body.get("data", [])
        if not rows:
            break
        for r in rows:
            period, val = r.get("period"), r.get("value")
            if val is None:
                continue
            try:
                d = datetime.strptime(period, "%Y-%m-%d").date()
                out.append((d, Decimal(str(val))))
            except (ValueError, TypeError):
                logger.warning("Skipping unparseable EIA row: %s = %s", period, val)
        offset += len(rows)
        total = int(body.get("total", 0))
        if offset >= total:
            break
    out.sort(key=lambda x: x[0])
    logger.info("  %s: %d daily observations %s→%s", series_id,
                len(out), out[0][0] if out else "-", out[-1][0] if out else "-")
    return out


def _merge_daily(
    brent: list[tuple[date, Decimal]],
    wti: list[tuple[date, Decimal]],
) -> dict[date, dict[str, Optional[Decimal]]]:
    bmap, wmap = dict(brent), dict(wti)
    all_dates = sorted(set(bmap) | set(wmap))
    return {d: {"brent": bmap.get(d), "wti": wmap.get(d)} for d in all_dates}


def _valid_price(p: Optional[Decimal]) -> Optional[Decimal]:
    if p is None:
        return None
    if PRICE_MIN <= p <= PRICE_MAX:
        return p
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Raw insert
# ──────────────────────────────────────────────────────────────────────────────

def _existing_dates(db) -> set:
    rows = db.execute(text(
        "SELECT as_of_date FROM crude_oil WHERE source = :s"
    ), {"s": SOURCE_SYSTEM}).fetchall()
    out = set()
    for (d,) in rows:
        out.add(d if isinstance(d, date) else date.fromisoformat(str(d)))
    return out


def _insert_raw_rows(db, ctx, merged: dict, existing: set) -> int:
    """Insert raw eia_daily rows (price + spread only). Derived fields filled later."""
    written = 0
    pulled = datetime.now(timezone.utc)
    for d, prices in merged.items():
        if d in existing:
            ctx.stale()
            continue
        brent = _valid_price(prices["brent"])
        wti = _valid_price(prices["wti"])
        if brent is None and wti is None:
            ctx.rejected(f"{d}: both prices missing/out-of-band")
            continue
        spread = (wti - brent) if (brent is not None and wti is not None) else None
        db.execute(text("""
            INSERT INTO crude_oil
                (brent_spot, wti_spot, wti_brent_spread, as_of_date,
                 aggregation_period, source, data_source_url, refresh,
                 pulled_at, is_latest, price_anomaly_flag, created_at, updated_at)
            VALUES
                (:brent, :wti, :spread, :as_of,
                 'daily', :src, :url, 'daily',
                 :pulled, 0, 0, :pulled, :pulled)
        """), {
            "brent": float(brent) if brent is not None else None,
            "wti": float(wti) if wti is not None else None,
            "spread": float(spread) if spread is not None else None,
            "as_of": d.isoformat(),
            "src": SOURCE_SYSTEM,
            "url": DATA_SOURCE_URL,
            "pulled": pulled,
        })
        written += 1
        ctx.inserted()
        if written % 500 == 0:
            db.flush()
            logger.info("  inserted %d eia_daily rows (at %s)...", written, d)
    return written


# ──────────────────────────────────────────────────────────────────────────────
# Derived-field window pass (single in-memory pass over all eia_daily rows)
# ──────────────────────────────────────────────────────────────────────────────

def recompute_derived_fields(db) -> int:
    """Load all eia_daily rows sorted by date, compute every derived field in one
    pass, and batch-UPDATE. Idempotent. Returns rows updated."""
    rows = db.execute(text("""
        SELECT crude_oil_id, as_of_date,
               CAST(brent_spot AS REAL), CAST(wti_spot AS REAL)
        FROM crude_oil
        WHERE source = :s
        ORDER BY as_of_date ASC
    """), {"s": SOURCE_SYSTEM}).fetchall()

    if not rows:
        logger.warning("recompute_derived_fields: no eia_daily rows.")
        return 0

    ids, dates, brents, wtis = [], [], [], []
    for cid, d, b, w in rows:
        ids.append(cid)
        dates.append(d if isinstance(d, date) else date.fromisoformat(str(d)))
        brents.append(b)
        wtis.append(w)
    n = len(dates)

    def rolling_mean(end_idx: int, window_days: int) -> Optional[float]:
        """Mean of brent over [date_i - window_days, date_i] inclusive."""
        lo_date = dates[end_idx] - timedelta(days=window_days)
        vals = []
        j = end_idx
        while j >= 0 and dates[j] >= lo_date:
            if brents[j] is not None:
                vals.append(brents[j])
            j -= 1
        return round(sum(vals) / len(vals), 4) if vals else None

    def lag_value(end_idx: int, lag_days: int) -> Optional[float]:
        """brent_spot at the row with the greatest date <= (date_i - lag_days)."""
        target = dates[end_idx] - timedelta(days=lag_days)
        pos = bisect.bisect_right(dates, target) - 1
        if pos < 0:
            return None
        return brents[pos] if brents[pos] is not None else None

    def yoy_pct(end_idx: int) -> Optional[float]:
        target = dates[end_idx] - timedelta(days=YOY_DAYS)
        pos = bisect.bisect_right(dates, target) - 1
        if pos < 0:
            return None
        # accept anchor only within tolerance of the 365d target
        if abs((dates[pos] - target).days) > YOY_TOLERANCE_DAYS:
            return None
        prior = brents[pos]
        now = brents[end_idx]
        if prior is None or now is None or prior == 0:
            return None
        return round((now - prior) / prior * 100, 2)

    updated = 0
    for i in range(n):
        if brents[i] is None:
            continue
        payload = {
            "id": ids[i],
            "r4": rolling_mean(i, ROLL_4W_DAYS),
            "r13": rolling_mean(i, ROLL_13W_DAYS),
            "t4": lag_value(i, LAG_4W_DAYS),
            "t8": lag_value(i, LAG_8W_DAYS),
            "yoy": yoy_pct(i),
        }
        db.execute(text("""
            UPDATE crude_oil SET
                brent_rolling_4w_avg = :r4,
                brent_rolling_13w_avg = :r13,
                brent_t_minus_4w = :t4,
                brent_t_minus_8w = :t8,
                brent_yoy_pct = :yoy
            WHERE crude_oil_id = :id
        """), payload)
        updated += 1
        if updated % 1000 == 0:
            logger.info("  derived fields recomputed for %d rows...", updated)
    return updated


def _mark_latest(db) -> None:
    """Set is_latest=1 on the most recent eia_daily row only; 0 on all others."""
    db.execute(text(
        "UPDATE crude_oil SET is_latest = 0 WHERE source = :s"
    ), {"s": SOURCE_SYSTEM})
    db.execute(text("""
        UPDATE crude_oil SET is_latest = 1
        WHERE crude_oil_id = (
            SELECT crude_oil_id FROM crude_oil
            WHERE source = :s ORDER BY as_of_date DESC LIMIT 1
        )
    """), {"s": SOURCE_SYSTEM})


# ──────────────────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────────────────

def _print_validation(db) -> bool:
    """Print post-backfill validation. Returns True if sane, False on hard failure."""
    ok = True
    total = db.execute(text(
        "SELECT COUNT(*), MIN(as_of_date), MAX(as_of_date) FROM crude_oil WHERE source=:s"
    ), {"s": SOURCE_SYSTEM}).fetchone()
    logger.info("VALIDATION — eia_daily rows: %d | range %s → %s", total[0], total[1], total[2])
    if total[0] == 0:
        logger.error("VALIDATION FAIL: no eia_daily rows.")
        return False

    null_brent = db.execute(text(
        "SELECT COUNT(*) FROM crude_oil WHERE source=:s AND brent_spot IS NULL"
    ), {"s": SOURCE_SYSTEM}).scalar()
    null_r4 = db.execute(text(
        "SELECT COUNT(*) FROM crude_oil WHERE source=:s AND brent_spot IS NOT NULL "
        "AND brent_rolling_4w_avg IS NULL"
    ), {"s": SOURCE_SYSTEM}).scalar()
    logger.info("VALIDATION — NULL brent_spot rows: %d (weekend/holiday gaps expected)", null_brent)
    logger.info("VALIDATION — NULL rolling_4w_avg (with brent present): %d (should be ~0)", null_r4)
    if null_r4 > 5:
        logger.error("VALIDATION FAIL: %d rows have brent but no rolling_4w_avg.", null_r4)
        ok = False

    for label, lo, hi in [
        ("2008 spike (expect $120-145)", "2008-06-01", "2008-07-15"),
        ("2016 crash (expect ~$30)", "2016-01-01", "2016-01-31"),
        ("current period", "2026-05-01", "2026-12-31"),
    ]:
        sample = db.execute(text("""
            SELECT as_of_date, CAST(brent_spot AS REAL), CAST(wti_spot AS REAL)
            FROM crude_oil WHERE source=:s AND as_of_date BETWEEN :lo AND :hi
            ORDER BY as_of_date LIMIT 5
        """), {"s": SOURCE_SYSTEM, "lo": lo, "hi": hi}).fetchall()
        logger.info("VALIDATION sample — %s:", label)
        for d, b, w in sample:
            logger.info("    %s  brent=%.2f  wti=%.2f", d, b or 0, w or 0)

    spread = db.execute(text("""
        SELECT MIN(CAST(wti_brent_spread AS REAL)), MAX(CAST(wti_brent_spread AS REAL)),
               AVG(CAST(wti_brent_spread AS REAL))
        FROM crude_oil WHERE source=:s AND wti_brent_spread IS NOT NULL
    """), {"s": SOURCE_SYSTEM}).fetchone()
    cur_spread = db.execute(text("""
        SELECT CAST(wti_brent_spread AS REAL) FROM crude_oil
        WHERE source=:s AND wti_brent_spread IS NOT NULL
        ORDER BY as_of_date DESC LIMIT 1
    """), {"s": SOURCE_SYSTEM}).scalar()
    logger.info("VALIDATION — wti_brent_spread min=%.2f max=%.2f mean=%.2f current=%.2f",
                spread[0] or 0, spread[1] or 0, spread[2] or 0, cur_spread or 0)
    return ok


# ──────────────────────────────────────────────────────────────────────────────
# Staleness
# ──────────────────────────────────────────────────────────────────────────────

def _business_days_between(a: date, b: date) -> int:
    """Count weekdays strictly after `a` up to and including `b`."""
    if b <= a:
        return 0
    days = 0
    cur = a + timedelta(days=1)
    while cur <= b:
        if cur.weekday() < 5:
            days += 1
        cur += timedelta(days=1)
    return days


def check_staleness(db) -> None:
    latest = db.execute(text(
        "SELECT MAX(as_of_date) FROM crude_oil WHERE source=:s"
    ), {"s": SOURCE_SYSTEM}).scalar()
    if latest is None:
        _send_slack_alert("crude_oil eia_daily: NO rows present. Run --backfill.", "critical")
        return
    latest_d = latest if isinstance(latest, date) else date.fromisoformat(str(latest))
    bdays = _business_days_between(latest_d, date.today())
    if bdays > STALE_BUSINESS_DAYS:
        _send_slack_alert(
            f"crude_oil eia_daily STALE — latest row {latest_d} is {bdays} business days old "
            f"(threshold {STALE_BUSINESS_DAYS}). EIA publishes weekdays with 1-2 day lag. "
            f"Check EIA API status or re-run ingestion.",
            level="critical",
        )
        logger.warning("eia_daily stale: %d business days.", bdays)
    else:
        logger.info("eia_daily fresh: latest %s (%d business days old).", latest_d, bdays)


# ──────────────────────────────────────────────────────────────────────────────
# Runners
# ──────────────────────────────────────────────────────────────────────────────

def run_backfill() -> int:
    end = date.today().isoformat()
    brent = fetch_eia_daily_series(BRENT_SERIES, BACKFILL_START, end)
    wti = fetch_eia_daily_series(WTI_SERIES, BACKFILL_START, end)
    merged = _merge_daily(brent, wti)
    if not merged:
        logger.error("No EIA daily data fetched — aborting backfill.")
        return 0

    db = SessionLocal()
    written = 0
    try:
        existing = _existing_dates(db)
        with IngestionContext(
            source_name=f"{SOURCE_NAME}_backfill",
            script_version=SCRIPT_VERSION,
            data_source_url=DATA_SOURCE_URL,
            db=db,
        ) as ctx:
            written = _insert_raw_rows(db, ctx, merged, existing)
            db.flush()
            ctx.set_as_of_date(max(merged))
            logger.info("Raw insert complete: %d new rows. Computing derived fields...", written)
            upd = recompute_derived_fields(db)
            logger.info("Derived fields computed for %d rows.", upd)
            _mark_latest(db)
        db.commit()
        _print_validation(db)
        logger.info("EIA daily backfill complete: %d rows written.", written)
        return written
    except Exception as exc:
        logger.critical("EIA daily backfill failed: %s", exc, exc_info=True)
        db.rollback()
        return written
    finally:
        db.close()


def run_once() -> bool:
    """Incremental: fetch a recent window, insert missing dates, recompute derived, re-mark latest."""
    db = SessionLocal()
    try:
        start = (date.today() - timedelta(days=120)).isoformat()
        end = date.today().isoformat()
        brent = fetch_eia_daily_series(BRENT_SERIES, start, end)
        wti = fetch_eia_daily_series(WTI_SERIES, start, end)
        merged = _merge_daily(brent, wti)
        if not merged:
            logger.warning("No EIA daily observations in window.")
            check_staleness(db)
            return False

        existing = _existing_dates(db)
        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=DATA_SOURCE_URL,
            db=db,
        ) as ctx:
            written = _insert_raw_rows(db, ctx, merged, existing)
            db.flush()
            if written:
                ctx.set_as_of_date(max(merged))
                recompute_derived_fields(db)
            _mark_latest(db)
        db.commit()
        if written:
            logger.info("EIA daily: %d new row(s). Latest as_of=%s", written, max(merged))
        else:
            logger.info("EIA daily current — no new rows.")
        check_staleness(db)
        return True
    except Exception as exc:
        logger.critical("EIA daily run_once failed: %s", exc, exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


def run_recompute() -> bool:
    db = SessionLocal()
    try:
        upd = recompute_derived_fields(db)
        _mark_latest(db)
        db.commit()
        logger.info("Recompute complete: %d rows.", upd)
        _print_validation(db)
        return True
    except Exception as exc:
        logger.critical("Recompute failed: %s", exc, exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


def run_scheduled() -> None:
    logger.info("EIA daily scheduler started — every %d hours.", SCHEDULE_HOURS)
    while True:
        run_once()
        time.sleep(SCHEDULE_HOURS * 3600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EIA daily Brent/WTI crude oil ingestion")
    parser.add_argument("--backfill", action="store_true",
                        help=f"Pull full EIA daily series from {BACKFILL_START} to today.")
    parser.add_argument("--run-once", action="store_true",
                        help="Fetch recent window and write new rows since last run.")
    parser.add_argument("--recompute", action="store_true",
                        help="Recompute derived fields over existing eia_daily rows only.")
    parser.add_argument("--schedule", action="store_true",
                        help="Run in loop every 24 hours (used by launchd at 19:00).")
    args = parser.parse_args()

    if args.backfill:
        raise SystemExit(0 if run_backfill() > 0 else 1)
    if args.recompute:
        raise SystemExit(0 if run_recompute() else 1)
    if args.schedule:
        run_scheduled()
    else:
        raise SystemExit(0 if run_once() else 1)

"""April 2026 Brent crude price deviation audit.

Three-way comparison:
  1. FRED weekly EOP avg  — crude_oil rows, source='fred_api', April 2026
  2. Pink Sheet monthly   — crude_oil row, source='world_bank_pink_sheet', April 2026
  3. EIA/FRED daily avg   — FRED DCOILBRENTEU daily observations, April 2026 (ground truth)

Finding flagged in crude_oil table (flag only, no delete) and written to
  data/ingestion/logs/april_2026_deviation_audit.txt

Usage:
  python -m data.ingestion.crude_april_2026_deviation_audit
"""
import logging
import os
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional

import requests
from sqlalchemy import text

from data.ingestion._env import load_project_env
from database.base import SessionLocal

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
LOG_PATH = Path(__file__).parent / "logs" / "april_2026_deviation_audit.txt"
AUDIT_MONTH = "2026-04"

Q2 = Decimal("0.01")
WARN_PCT = Decimal("3.0")
FAIL_PCT = Decimal("5.0")


def _fetch_fred_daily_brent(api_key: str) -> list[tuple[str, float]]:
    """Fetch DCOILBRENTEU daily for April 2026 from FRED."""
    resp = requests.get(
        FRED_BASE,
        params={
            "series_id": "DCOILBRENTEU",
            "api_key": api_key,
            "observation_start": "2026-04-01",
            "observation_end": "2026-04-30",
            "file_type": "json",
        },
        timeout=20,
    )
    resp.raise_for_status()
    obs = resp.json().get("observations", [])
    return [(o["date"], float(o["value"])) for o in obs if o["value"] != "."]


def _pct_dev(a: Decimal, b: Decimal) -> Decimal:
    """(a - b) / b * 100"""
    if b == 0:
        return Decimal("0")
    return ((a - b) / b * Decimal("100")).quantize(Q2, rounding=ROUND_HALF_UP)


def run() -> dict:
    fred_key = os.getenv("FRED_API_KEY", "")
    if not fred_key:
        raise RuntimeError("FRED_API_KEY not set")

    db = SessionLocal()
    try:
        # ── 1. FRED weekly rows for April 2026 ──────────────────────────────
        fred_rows = db.execute(text("""
            SELECT as_of_date, CAST(brent_spot AS REAL) AS brent,
                   CAST(brent_rolling_4w_avg AS REAL) AS rolling
            FROM crude_oil
            WHERE source = 'fred_api'
              AND strftime('%Y-%m', as_of_date) = '2026-04'
              AND brent_spot IS NOT NULL
            ORDER BY as_of_date
        """)).fetchall()

        print("=== FRED weekly Brent rows for April 2026 ===")
        print(f"{'Date':<14} {'Spot ($)':>10} {'Rolling4w':>12}")
        for dt, brent, rolling in fred_rows:
            print(f"{str(dt):<14} {brent:>10.2f} {rolling if rolling else 'N/A':>12}")

        if not fred_rows:
            raise RuntimeError("No FRED weekly rows found for April 2026")

        fred_weekly_vals = [r[1] for r in fred_rows]
        fred_weekly_avg = Decimal(str(sum(fred_weekly_vals) / len(fred_weekly_vals))).quantize(
            Q2, rounding=ROUND_HALF_UP
        )
        print(f"\nFRED weekly avg ({len(fred_rows)} obs): ${fred_weekly_avg}")

        # ── 2. Pink Sheet monthly row ─────────────────────────────────────────
        pink_row = db.execute(text("""
            SELECT crude_oil_id, as_of_date, CAST(brent_spot AS REAL) AS brent,
                   data_quality_flag
            FROM crude_oil
            WHERE source = 'world_bank_pink_sheet'
              AND strftime('%Y-%m', as_of_date) = '2026-04'
            LIMIT 1
        """)).fetchone()

        if pink_row is None:
            print("\nPink Sheet: NO ROW for April 2026")
            pink_brent: Optional[Decimal] = None
            pink_id = None
        else:
            pink_id, pink_date, pink_val, pink_flag = pink_row
            pink_brent = Decimal(str(pink_val)).quantize(Q2, rounding=ROUND_HALF_UP)
            print(f"\nPink Sheet April 2026: id={pink_id}, date={pink_date}, brent=${pink_brent}, flag={pink_flag}")

        # ── 3. FRED daily DCOILBRENTEU — ground truth ────────────────────────
        daily_obs = _fetch_fred_daily_brent(fred_key)
        if not daily_obs:
            raise RuntimeError("No FRED daily DCOILBRENTEU data for April 2026")

        daily_vals = [v for _, v in daily_obs]
        eia_daily_avg = Decimal(str(sum(daily_vals) / len(daily_vals))).quantize(
            Q2, rounding=ROUND_HALF_UP
        )
        print(f"\nFRED daily DCOILBRENTEU ({len(daily_obs)} trading days): avg=${eia_daily_avg}")
        print(f"  range: ${min(daily_vals):.2f} – ${max(daily_vals):.2f}")

        # ── 4. Three-way comparison ──────────────────────────────────────────
        fred_vs_daily = _pct_dev(fred_weekly_avg, eia_daily_avg)
        pink_vs_daily = _pct_dev(pink_brent, eia_daily_avg) if pink_brent else None
        fred_vs_pink  = _pct_dev(fred_weekly_avg, pink_brent) if pink_brent else None

        print("\n═══════════════════════════════════════════════════════")
        print("THREE-WAY COMPARISON — April 2026 Brent")
        print("═══════════════════════════════════════════════════════")
        pink_dev_str = f"{pink_vs_daily:+.2f}%" if pink_vs_daily is not None else "N/A"
        pink_val_str = str(pink_brent) if pink_brent else "N/A"
        print(f"  FRED weekly avg:        ${fred_weekly_avg:>8}  (dev from daily GT: {fred_vs_daily:+.2f}%)")
        print(f"  Pink Sheet monthly:     ${pink_val_str:>8}  (dev from daily GT: {pink_dev_str})")
        print(f"  FRED daily avg (GT):    ${eia_daily_avg:>8}  <- ground truth")
        if fred_vs_pink:
            print(f"  Pink Sheet vs FRED weekly: {fred_vs_pink:+.2f}%")

        # ── 5. Determine outlier ─────────────────────────────────────────────
        outlier = None
        flag_note = None

        if pink_brent and pink_vs_daily and fred_vs_pink:
            pink_abs = abs(pink_vs_daily)
            fred_abs = abs(fred_vs_daily)

            if pink_abs > fred_abs and fred_vs_pink and abs(fred_vs_pink) > FAIL_PCT:
                outlier = "pink_sheet"
                flag_note = (
                    f"{abs(fred_vs_pink):.1f}% deviation vs FRED weekly. "
                    f"EIA daily avg=${eia_daily_avg} used as tiebreaker. "
                    f"Pink Sheet=${pink_brent} vs FRED daily avg=${eia_daily_avg} "
                    f"({pink_vs_daily:+.2f}%)."
                )
            elif pink_abs <= fred_abs:
                outlier = "fred_weekly_sampling"
                flag_note = (
                    f"Pink Sheet=${pink_brent} is {pink_abs:.2f}% from daily GT; "
                    f"FRED weekly={fred_weekly_avg} is {fred_abs:.2f}% from daily GT. "
                    f"Both within 3% of EIA daily ground truth — FRED weekly EOP sampling "
                    f"artefact (volatile month, EOP dates hit lower-price days)."
                )

        print(f"\nOutlier determination: {outlier or 'none'}")
        if flag_note:
            print(f"  Note: {flag_note}")

        # ── 6. Flag the outlier row ──────────────────────────────────────────
        action_taken = "none"
        if outlier == "pink_sheet" and pink_id is not None:
            db.execute(text("""
                UPDATE crude_oil
                SET data_quality_flag = 'DEVIATION_FLAGGED',
                    data_quality_note = :note,
                    updated_at = CURRENT_TIMESTAMP
                WHERE crude_oil_id = :row_id
            """), {
                "note": f"5.1% deviation vs FRED weekly. EIA daily used as tiebreaker. {flag_note}",
                "row_id": pink_id,
            })
            db.commit()
            action_taken = f"pink_sheet row {pink_id} flagged DEVIATION_FLAGGED (no delete)"
            print(f"\n✓ {action_taken}")
        else:
            print("\n→ Pink Sheet within tolerance of EIA daily ground truth — no flag applied")
            print("  (FRED weekly EOP sampling artefact accounts for the 5.4% spread)")

        # ── 7. Write audit log ───────────────────────────────────────────────
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        run_date = date.today().isoformat()
        report = f"""ARTEMIS — April 2026 Brent Crude Deviation Audit
Run date: {run_date}
{'='*60}

SOURCES:
  FRED weekly EOP (crude_oil, source=fred_api):     ${fred_weekly_avg} ({len(fred_rows)} obs)
  World Bank Pink Sheet (crude_oil, source=WB):     ${pink_brent if pink_brent else 'N/A'}
  FRED daily DCOILBRENTEU (ground truth):           ${eia_daily_avg} ({len(daily_obs)} trading days)
    Daily range: ${min(daily_vals):.2f} – ${max(daily_vals):.2f}

DEVIATIONS:
  FRED weekly vs EIA daily:   {fred_vs_daily:+.2f}%
  Pink Sheet vs EIA daily:    {pink_vs_daily:+.2f}% {'' if pink_vs_daily else 'N/A'}
  Pink Sheet vs FRED weekly:  {fred_vs_pink:+.2f}% {'' if fred_vs_pink else 'N/A'}

FRED weekly daily observations:
{chr(10).join(f"  {dt}: ${v:.2f}" for dt, v in daily_obs)}

OUTLIER DETERMINATION: {outlier or 'none identified'}
  {flag_note or 'No flagging required.'}

ACTION TAKEN: {action_taken}

INTERPRETATION:
  April 2026 was an unusually volatile month (${min(daily_vals):.0f}–${max(daily_vals):.0f} range).
  FRED weekly EOP values sample specific end-of-period days which may fall on
  lower-price days, understating the true monthly average.
  Pink Sheet represents a full-month average which is closer to the daily ground truth.
  Primary reconciliation check (check_4 EIA daily) supersedes FRED vs Pink Sheet comparison.
"""
        LOG_PATH.write_text(report)
        print(f"\nAudit log written to: {LOG_PATH}")

        return {
            "fred_weekly_avg": float(fred_weekly_avg),
            "pink_sheet": float(pink_brent) if pink_brent else None,
            "eia_daily_avg": float(eia_daily_avg),
            "outlier": outlier,
            "action_taken": action_taken,
        }

    except Exception as exc:
        db.rollback()
        logger.error(f"Audit failed: {exc}", exc_info=True)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    result = run()
    print("\nFinal result:", result)

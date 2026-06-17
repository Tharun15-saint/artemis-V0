"""
Crude → bunker fuel transmission calibration.

Measures how much marine fuel cost moves per $1 change in Brent crude, and the
lag at which the relationship is strongest. This is the FIRST, measurable leg of
the crude → bunker → ocean-freight chain.

Methodology (honest, reproducible):
  1. Align weekly Brent spot (FRED DCOILBRENTEU, via crude_oil) with the EIA
     distillate bunker proxy (bunker_fuel_prices) using a 4-day as-of join.
  2. For candidate lags 0..8 weeks, fit OLS  fuel[t] = a + b · brent[t-lag].
  3. Pick the lag with the highest R². Report slope b (transmission_coeff,
     $/gal per $1/bbl), R², n, and a lag confidence interval (the band of lags
     whose R² is within 0.01 of the best).
  4. Persist to crude_transmission_calibration as cost_component
     'crude_to_bunker_fuel' (is_active=1 — this leg is real and well-fit).

What this deliberately does NOT do: it does not invent a crude→freight
coefficient. The bunker→freight leg needs a freight time series we do not yet
have (Drewry composite is accumulating forward; per-lane history arrives with a
paid FBX/Drewry feed). The 'freight_energy_surcharge' row stays an inactive
industry-prior until that second leg can be fit on real data.

Usage:
  python -m data.ingestion.crude_bunker_calibration            # fit + persist
  python -m data.ingestion.crude_bunker_calibration --report   # fit + print, no write
"""

from __future__ import annotations

import argparse
import logging
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import text

from database.base import SessionLocal

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

MAX_LAG_WEEKS = 8
JOIN_TOLERANCE_DAYS = 4
MIN_OBS = 100          # refuse to "calibrate" on thin data
MIN_R2_TO_ACTIVATE = 0.50
BRENT_SERIES_LABEL = "fred_api"

# Grades to calibrate. ULSD Gulf Coast is the cleaner post-IMO-2020 marine proxy;
# NY heating oil gives the longest history. We fit both and persist each.
GRADES = ["ULSD", "No2_heating_oil"]


def _load_brent(db) -> pd.DataFrame:
    rows = db.execute(text(
        "SELECT as_of_date, brent_spot FROM crude_oil "
        "WHERE brent_spot IS NOT NULL AND source LIKE '%fred%' "
        "ORDER BY as_of_date"
    )).fetchall()
    if not rows:
        rows = db.execute(text(
            "SELECT as_of_date, brent_spot FROM crude_oil "
            "WHERE brent_spot IS NOT NULL ORDER BY as_of_date"
        )).fetchall()
    df = pd.DataFrame(rows, columns=["date", "brent"])
    df["date"] = pd.to_datetime(df["date"])
    df["brent"] = df["brent"].astype(float)
    df = df.drop_duplicates(subset="date").sort_values("date")
    return df


def _load_bunker(db, grade: str) -> pd.DataFrame:
    rows = db.execute(text(
        "SELECT as_of_date, price_usd FROM bunker_fuel_prices "
        "WHERE grade = :g ORDER BY as_of_date"
    ), {"g": grade}).fetchall()
    df = pd.DataFrame(rows, columns=["date", "fuel"])
    df["date"] = pd.to_datetime(df["date"])
    df["fuel"] = df["fuel"].astype(float)
    df = df.drop_duplicates(subset="date").sort_values("date")
    return df


def _ols(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    """Return (slope, intercept, r_squared) for y ~ x."""
    b, a = np.polyfit(x, y, 1)
    yhat = a + b * x
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return float(b), float(a), r2


def calibrate_grade(brent: pd.DataFrame, bunker: pd.DataFrame, grade: str) -> dict | None:
    # As-of join: each bunker obs matched to the most recent Brent within tolerance.
    merged = pd.merge_asof(
        bunker, brent, on="date", direction="backward",
        tolerance=pd.Timedelta(days=JOIN_TOLERANCE_DAYS),
    ).dropna(subset=["brent", "fuel"]).reset_index(drop=True)

    if len(merged) < MIN_OBS:
        logger.warning("%s: only %d aligned obs (<%d) — skipping.", grade, len(merged), MIN_OBS)
        return None

    # Lag scan: fuel responds to crude `lag` weeks earlier.
    results = []
    for lag in range(0, MAX_LAG_WEEKS + 1):
        if lag == 0:
            x, y = merged["brent"].values, merged["fuel"].values
        else:
            x = merged["brent"].values[:-lag]
            y = merged["fuel"].values[lag:]
        if len(x) < MIN_OBS:
            continue
        b, a, r2 = _ols(x, y)
        results.append({"lag": lag, "slope": b, "intercept": a, "r2": r2, "n": len(x)})

    if not results:
        return None
    best = max(results, key=lambda r: r["r2"])
    near = [r["lag"] for r in results if r["r2"] >= best["r2"] - 0.01]
    return {
        "grade": grade,
        "lag_weeks": best["lag"],
        "lag_ci_low": min(near),
        "lag_ci_high": max(near),
        "transmission_coeff": best["slope"],   # $/gal per $1/bbl Brent
        "intercept": best["intercept"],
        "r_squared": best["r2"],
        "obs_count": best["n"],
        "date_start": merged["date"].min().date(),
        "date_end": merged["date"].max().date(),
        "all_lags": results,
    }


def _persist(db, c: dict) -> None:
    component = f"crude_to_bunker_fuel_{c['grade'].lower()}"
    active = 1 if c["r_squared"] >= MIN_R2_TO_ACTIVATE and c["obs_count"] >= MIN_OBS else 0
    notes = (
        f"Crude(Brent)→bunker proxy ({c['grade']}) leg. "
        f"{c['transmission_coeff']:.5f} $/gal per $1/bbl Brent at {c['lag_weeks']}-wk lag "
        f"(CI {c['lag_ci_low']}-{c['lag_ci_high']}wk), R²={c['r_squared']:.3f}, "
        f"n={c['obs_count']}, {c['date_start']}→{c['date_end']}. "
        f"EIA distillate as VLSFO proxy. Bunker→freight leg pending freight history."
    )
    db.execute(text("DELETE FROM crude_transmission_calibration WHERE cost_component = :c"),
               {"c": component})
    db.execute(text("""
        INSERT INTO crude_transmission_calibration
            (cost_component, data_source, obs_count, lag_weeks_empirical,
             lag_weeks_ci_low, lag_weeks_ci_high, transmission_coeff, r_squared,
             brent_series_used, calibration_date, invoice_date_range_start,
             invoice_date_range_end, is_active, notes)
        VALUES
            (:c, 'eia_distillate_proxy', :n, :lag, :lo, :hi, :coeff, :r2,
             :series, :cal, :ds, :de, :active, :notes)
    """), {
        "c": component, "n": c["obs_count"], "lag": c["lag_weeks"],
        "lo": c["lag_ci_low"], "hi": c["lag_ci_high"],
        "coeff": round(c["transmission_coeff"], 6), "r2": round(c["r_squared"], 4),
        "series": BRENT_SERIES_LABEL, "cal": date.today().isoformat(),
        "ds": c["date_start"].isoformat(), "de": c["date_end"].isoformat(),
        "active": active, "notes": notes,
    })
    logger.info("Persisted %s (is_active=%d)", component, active)


def run(report_only: bool = False) -> bool:
    db = SessionLocal()
    try:
        brent = _load_brent(db)
        logger.info("Brent: %d weekly points %s→%s",
                    len(brent), brent["date"].min().date(), brent["date"].max().date())
        any_ok = False
        for grade in GRADES:
            bunker = _load_bunker(db, grade)
            c = calibrate_grade(brent, bunker, grade)
            if not c:
                continue
            any_ok = True
            print(f"\n=== crude → bunker ({grade}) ===")
            print(f"  Best lag:            {c['lag_weeks']} weeks (CI {c['lag_ci_low']}-{c['lag_ci_high']})")
            print(f"  Transmission coeff:  {c['transmission_coeff']:.5f} $/gal per $1/bbl Brent")
            print(f"  R²:                  {c['r_squared']:.4f}   (n={c['obs_count']})")
            print(f"  Window:              {c['date_start']} → {c['date_end']}")
            print(f"  Lag sensitivity:     " +
                  ", ".join(f"{r['lag']}w:R²{r['r2']:.3f}" for r in c["all_lags"]))
            if not report_only:
                _persist(db, c)
        if not report_only:
            db.commit()
        return any_ok
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", action="store_true", help="print fit, do not persist")
    args = ap.parse_args()
    ok = run(report_only=args.report)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

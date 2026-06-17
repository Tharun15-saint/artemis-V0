"""CrudeTransmissionCalibration — crude → dyeing chemical cost transmission engine.

Empirically derives the crude→dyeing chemical cost transmission coefficient from
RRK invoice data (fabric_dyeing). Designed to activate AUTOMATICALLY the moment the
statistical evidence is strong enough, and never before.

PRINCIPLE: no approximation. The $85/bbl threshold and any transmission coefficient
are industry priors until measured. This engine measures them. Until the activation
criteria are met, it activates nothing and reports exactly what is missing.

RUN: daily, as a background job. Idempotent. The cost engine reads the ACTIVE
coefficient from crude_transmission_calibration — it never calls this module.

JOIN SOURCE: crude_oil where source='eia_daily' (daily resolution) so every invoice
date maps to an EXACT crude price at the chosen lag — no nearest-weekly approximation.
This is why EIA daily ingestion (Task 2) is a prerequisite.

ACTIVATION CRITERIA (ALL must hold):
  n_clean >= 20
  R² >= 0.40
  p_value < 0.01            (strict — not 0.05)
  coefficient > 0           (cost rises with crude)
  95% CI for coefficient excludes zero

EIGHT STEPS:
  1 Data assembly (lag join at 4w and 8w)
  2 Minimum viability check (funnel)
  3 Outlier rejection (2.5σ on implied coefficient)
  4 Test both lag hypotheses (4w vs 8w OLS); pick higher R²
  5 Activation decision (strict)
  6 Empirical threshold detection (Chow test)
  7 Full report (txt + json)
  8 Update quality_check_log

Output: intelligence/calibration_reports/crude_transmission_{date}.{txt,json}
"""
import argparse
import json
import logging
import math
import os
from datetime import date
from pathlib import Path
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from database.base import SessionLocal

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

REPORTS_DIR = Path(__file__).parent / "calibration_reports"
MIN_N = 20
MIN_R2 = 0.40
MAX_PVALUE = 0.01
OUTLIER_SIGMA = 2.5
INDUSTRY_THRESHOLD = 85.0      # the prior being tested
THRESHOLD_ALERT_DELTA = 5.0    # alert if empirical threshold differs by more than this
COST_COMPONENT = "dyeing_chemical"
JOIN_SOURCE = "eia_daily"
QUALITY_GATE_SQL = (
    "fd.chemical_cost_per_kg_inr IS NOT NULL AND fd.production_date IS NOT NULL "
    "AND (fd.quality_result = 'pass' OR (fd.shade_pass_rate_pct IS NOT NULL "
    "AND fd.shade_pass_rate_pct >= 70))"
)


def _send_slack(message: str, level: str = "warning") -> None:
    webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        logger.warning(f"[NO SLACK WEBHOOK] {message}")
        return
    prefix = "⚠ *ARTEMIS ALERT*" if level == "warning" else "🔴 *ARTEMIS CRITICAL*"
    try:
        import requests
        requests.post(webhook_url, json={"text": f"{prefix}\n{message}"}, timeout=10)
    except Exception as e:
        logger.error(f"Slack alert failed: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Pure-Python statistics (no scipy). t and F p-values via regularized incomplete beta.
# ──────────────────────────────────────────────────────────────────────────────

def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta (Lentz's method)."""
    MAXIT, EPS, FPMIN = 200, 3.0e-12, 1.0e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def _t_two_tailed_p(t: float, df: int) -> float:
    """Two-tailed p-value for a t-statistic with df degrees of freedom."""
    if df <= 0:
        return 1.0
    x = df / (df + t * t)
    return max(0.0, min(1.0, _betainc(df / 2.0, 0.5, x)))


def _t_critical_95(df: int) -> float:
    """Approximate two-tailed 95% t critical value via bisection on the t CDF."""
    if df <= 0:
        return float("inf")
    lo, hi = 0.0, 100.0
    for _ in range(100):
        mid = (lo + hi) / 2
        # two-tailed p at mid
        p = _t_two_tailed_p(mid, df)
        if p > 0.05:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _f_upper_p(f: float, df1: int, df2: int) -> float:
    """Upper-tail p-value for an F-statistic."""
    if f <= 0 or df1 <= 0 or df2 <= 0:
        return 1.0
    x = df2 / (df2 + df1 * f)
    return max(0.0, min(1.0, _betainc(df2 / 2.0, df1 / 2.0, x)))


def _ols(x_vals: list[float], y_vals: list[float]) -> dict:
    """OLS y = alpha + beta*x with full inference (t-test on beta, 95% CI)."""
    n = len(x_vals)
    if n < 3:
        return {"n": n, "coefficient": None, "intercept": None, "r_squared": None,
                "p_value": None, "ci_low": None, "ci_high": None, "sse": None}
    mx = sum(x_vals) / n
    my = sum(y_vals) / n
    ss_xy = sum((x - mx) * (y - my) for x, y in zip(x_vals, y_vals))
    ss_xx = sum((x - mx) ** 2 for x in x_vals)
    ss_yy = sum((y - my) ** 2 for y in y_vals)
    if ss_xx == 0:
        return {"n": n, "coefficient": None, "intercept": None, "r_squared": None,
                "p_value": None, "ci_low": None, "ci_high": None, "sse": None}
    beta = ss_xy / ss_xx
    alpha = my - beta * mx
    r2 = (ss_xy ** 2) / (ss_xx * ss_yy) if ss_yy > 0 else 0.0
    resid = [y - (alpha + beta * x) for x, y in zip(x_vals, y_vals)]
    sse = sum(r * r for r in resid)
    df = n - 2
    se_beta = math.sqrt(sse / (df * ss_xx)) if df > 0 and ss_xx > 0 else None
    if se_beta and se_beta > 0:
        t_stat = beta / se_beta
        p_value = _t_two_tailed_p(t_stat, df)
        tcrit = _t_critical_95(df)
        ci_low = beta - tcrit * se_beta
        ci_high = beta + tcrit * se_beta
    elif df > 0 and sse == 0 and ss_xx > 0:
        # Perfect (or numerically perfect) fit: zero residual variance.
        t_stat = float("inf")
        p_value = 0.0
        ci_low = ci_high = beta
    else:
        t_stat = p_value = ci_low = ci_high = None
    return {
        "n": n, "coefficient": beta, "intercept": alpha, "r_squared": r2,
        "p_value": p_value, "t_stat": t_stat, "se_beta": se_beta,
        "ci_low": ci_low, "ci_high": ci_high, "sse": sse,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Step 1 — data assembly (lag join against eia_daily)
# ──────────────────────────────────────────────────────────────────────────────

def _assemble(db: Session) -> list[dict]:
    """Return rows: {date, cost, brent_4w, brent_8w, corridor}. Exact lag join on eia_daily."""
    rows = db.execute(text(f"""
        SELECT fd.production_date,
               CAST(fd.chemical_cost_per_kg_inr AS REAL) AS cost,
               (SELECT CAST(c.brent_spot AS REAL) FROM crude_oil c
                  WHERE c.source = :src AND c.brent_spot IS NOT NULL
                    AND c.as_of_date <= date(fd.production_date, '-28 days')
                  ORDER BY c.as_of_date DESC LIMIT 1) AS brent_4w,
               (SELECT CAST(c.brent_spot AS REAL) FROM crude_oil c
                  WHERE c.source = :src AND c.brent_spot IS NOT NULL
                    AND c.as_of_date <= date(fd.production_date, '-56 days')
                  ORDER BY c.as_of_date DESC LIMIT 1) AS brent_8w,
               fd.colour_category
        FROM fabric_dyeing fd
        WHERE {QUALITY_GATE_SQL}
        ORDER BY fd.production_date
    """), {"src": JOIN_SOURCE}).fetchall()
    out = []
    for prod_date, cost, b4, b8, corridor in rows:
        if cost is None or b4 is None:
            continue
        out.append({"date": str(prod_date), "cost": float(cost),
                    "brent_4w": float(b4), "brent_8w": float(b8) if b8 is not None else None,
                    "corridor": corridor})
    return out


def _funnel(db: Session) -> dict:
    total = db.execute(text("SELECT COUNT(*) FROM fabric_dyeing")).scalar() or 0
    with_date = db.execute(text(
        "SELECT COUNT(*) FROM fabric_dyeing WHERE production_date IS NOT NULL")).scalar() or 0
    with_cost = db.execute(text(
        "SELECT COUNT(*) FROM fabric_dyeing WHERE chemical_cost_per_kg_inr IS NOT NULL")).scalar() or 0
    high_conf = db.execute(text(
        f"SELECT COUNT(*) FROM fabric_dyeing fd WHERE {QUALITY_GATE_SQL}")).scalar() or 0
    return {"total": total, "with_date": with_date, "with_cost": with_cost, "high_conf": high_conf}


# ──────────────────────────────────────────────────────────────────────────────
# Step 3 — outlier rejection
# ──────────────────────────────────────────────────────────────────────────────

def _reject_outliers(pairs: list[dict]) -> tuple[list[dict], list[dict]]:
    implied = [(p["cost"] / p["brent_4w"]) for p in pairs if p["brent_4w"]]
    if len(implied) < 3:
        return pairs, []
    mean = sum(implied) / len(implied)
    std = math.sqrt(sum((v - mean) ** 2 for v in implied) / len(implied))
    if std == 0:
        return pairs, []
    clean, outliers = [], []
    for p in pairs:
        if not p["brent_4w"]:
            outliers.append(p)
            continue
        z = abs((p["cost"] / p["brent_4w"]) - mean) / std
        (outliers if z > OUTLIER_SIGMA else clean).append(p)
    return clean, outliers


# ──────────────────────────────────────────────────────────────────────────────
# Step 6 — Chow test for empirical threshold
# ──────────────────────────────────────────────────────────────────────────────

def _chow_threshold(x: list[float], y: list[float]) -> dict:
    """Find the structural-break price with the highest Chow F-statistic."""
    n = len(x)
    if n < 12:
        return {"threshold": None, "f_statistic": None, "p_value": None,
                "note": f"n={n} < 12 — Chow test not run."}
    pooled = _ols(x, y)
    sse_pooled = pooled.get("sse")
    if sse_pooled is None:
        return {"threshold": None, "f_statistic": None, "p_value": None,
                "note": "Pooled regression degenerate."}
    xs = sorted(x)
    p10, p90 = xs[int(0.1 * n)], xs[int(0.9 * n)]
    candidates = sorted({v for v in x if p10 <= v <= p90})
    best = {"threshold": None, "f_statistic": -1.0, "p_value": None}
    k = 2  # params per segment (slope + intercept)
    for t in candidates:
        lo = [(xi, yi) for xi, yi in zip(x, y) if xi < t]
        hi = [(xi, yi) for xi, yi in zip(x, y) if xi >= t]
        if len(lo) < 3 or len(hi) < 3:
            continue
        sse_lo = _ols([a for a, _ in lo], [b for _, b in lo]).get("sse")
        sse_hi = _ols([a for a, _ in hi], [b for _, b in hi]).get("sse")
        if sse_lo is None or sse_hi is None:
            continue
        sse_split = sse_lo + sse_hi
        if sse_split <= 0:
            continue
        df2 = n - 2 * k
        if df2 <= 0:
            continue
        f = ((sse_pooled - sse_split) / k) / (sse_split / df2)
        if f > best["f_statistic"]:
            best = {"threshold": t, "f_statistic": f,
                    "p_value": _f_upper_p(f, k, df2)}
    if best["threshold"] is None:
        return {"threshold": None, "f_statistic": None, "p_value": None,
                "note": "No valid breakpoint found."}
    best["note"] = (f"Empirical threshold ${best['threshold']:.1f}/bbl "
                    f"(F={best['f_statistic']:.2f}, p={best['p_value']:.4f}) "
                    f"vs industry prior ${INDUSTRY_THRESHOLD}/bbl.")
    return best


# ──────────────────────────────────────────────────────────────────────────────
# Step 5 — activation
# ──────────────────────────────────────────────────────────────────────────────

def _activate(db: Session, fit: dict, lag_weeks: int, n: int, dates: list[str],
              chow: dict, dry_run: bool) -> str:
    coeff = fit["coefficient"]
    if dry_run:
        return f"[DRY-RUN] would activate: coeff={coeff:.6f} R²={fit['r_squared']:.3f} n={n} lag={lag_weeks}w"
    db.execute(text("""
        UPDATE crude_transmission_calibration
        SET data_source='rrk_invoice_data', calibrated_from='rrk_invoice_empirical',
            obs_count=:n, lag_weeks_empirical=:lag, transmission_coeff=:coeff,
            r_squared=:r2, p_value=:p, coeff_ci_low=:cilo, coeff_ci_high=:cihi,
            brent_series_used=:src, calibration_date=:today,
            invoice_date_range_start=:dmin, invoice_date_range_end=:dmax,
            empirical_threshold=:thr, threshold_f_statistic=:fstat, threshold_p_value=:tp,
            is_active=1,
            notes=:notes
        WHERE cost_component=:cc
    """), {
        "n": n, "lag": lag_weeks, "coeff": coeff, "r2": fit["r_squared"],
        "p": fit["p_value"], "cilo": fit["ci_low"], "cihi": fit["ci_high"],
        "src": JOIN_SOURCE, "today": date.today().isoformat(),
        "dmin": min(dates), "dmax": max(dates),
        "thr": chow.get("threshold"), "fstat": chow.get("f_statistic"),
        "tp": chow.get("p_value"), "cc": COST_COMPONENT,
        "notes": (f"Auto-activated by calibration engine. Supersedes industry prior. "
                  f"OLS chemical_cost ~ {coeff:.4f}×brent_lag_{lag_weeks}w + const. "
                  f"R²={fit['r_squared']:.3f}, p={fit['p_value']:.4f}, n={n}."),
    })
    db.commit()
    _send_slack(
        f"CALIBRATION ACTIVATED: empirical crude→dyeing coefficient = {coeff:.4f} "
        f"(R²={fit['r_squared']:.3f}, n={n}, p={fit['p_value']:.4f}, lag={lag_weeks}w). "
        f"Cost engine will use this coefficient from {date.today()}.",
        level="critical")
    return f"ACTIVATED coeff={coeff:.6f} R²={fit['r_squared']:.4f} p={fit['p_value']:.4f} n={n} lag={lag_weeks}w"


# ──────────────────────────────────────────────────────────────────────────────
# Step 8 — quality_check_log
# ──────────────────────────────────────────────────────────────────────────────

def _log_quality(db: Session, result: str, details: str) -> None:
    try:
        db.execute(text("""
            INSERT INTO quality_check_log (check_name, check_date, result, details, resolved)
            VALUES ('calibration_readiness_check', :dt, :r, :d, 0)
        """), {"dt": date.today().isoformat(), "r": result, "d": details})
        db.commit()
    except Exception as e:
        logger.debug(f"_log_quality failed: {e}")
        db.rollback()


# ──────────────────────────────────────────────────────────────────────────────
# Orchestration
# ──────────────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> dict:
    today = date.today()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    txt_path = REPORTS_DIR / f"crude_transmission_{today.isoformat()}.txt"
    json_path = REPORTS_DIR / f"crude_transmission_{today.isoformat()}.json"

    db = SessionLocal()
    try:
        funnel = _funnel(db)
        pairs = _assemble(db)
        n_raw = len(pairs)

        report: dict = {"run_date": today.isoformat(), "funnel": funnel, "n_raw_pairs": n_raw}

        # Step 2 — minimum viability
        if n_raw < MIN_N:
            gap = MIN_N - n_raw
            extraction_yield = round(n_raw / funnel["total"] * 100, 1) if funnel["total"] else 0.0
            report.update({
                "status": "PENDING", "activated": False,
                "extraction_yield_pct": extraction_yield, "pairs_needed": gap,
                "reason": f"{n_raw}/{MIN_N} validated pairs. {gap} more required.",
            })
            details = (f"{n_raw} validated invoice pairs (min {MIN_N}). PENDING. "
                       f"{gap} more pairs needed.")
            _log_quality(db, "warn", details)
            _write_reports(txt_path, json_path, report)
            logger.info("CALIBRATION PENDING: %s", report["reason"])
            return report

        # Step 3 — outlier rejection
        clean, outliers = _reject_outliers(pairs)
        n_clean = len(clean)
        report["n_outliers"] = len(outliers)
        report["n_clean"] = n_clean

        # Step 4 — test both lag hypotheses
        x4 = [p["brent_4w"] for p in clean]
        y = [p["cost"] for p in clean]
        fit4 = _ols(x4, y)
        clean8 = [p for p in clean if p["brent_8w"] is not None]
        fit8 = None
        if len(clean8) >= 3:
            fit8 = _ols([p["brent_8w"] for p in clean8], [p["cost"] for p in clean8])

        r2_4 = fit4.get("r_squared") or 0.0
        r2_8 = (fit8.get("r_squared") or 0.0) if fit8 else -1.0
        if r2_8 > r2_4:
            winner, fit, lag_weeks, xv = "8w", fit8, 8, [p["brent_8w"] for p in clean8]
            yv = [p["cost"] for p in clean8]
        else:
            winner, fit, lag_weeks, xv, yv = "4w", fit4, 4, x4, y

        report["lag_comparison"] = {"r2_4w": round(r2_4, 4), "r2_8w": round(r2_8, 4) if fit8 else None,
                                    "winner": winner}
        report["regression"] = {k: (round(v, 6) if isinstance(v, float) else v)
                                for k, v in fit.items() if k != "sse"}

        # Step 6 — empirical threshold (Chow)
        chow = _chow_threshold(xv, yv)
        report["chow_threshold"] = {k: (round(v, 4) if isinstance(v, float) else v)
                                    for k, v in chow.items()}

        # Step 5 — strict activation
        r2 = fit.get("r_squared") or 0.0
        p = fit.get("p_value")
        coeff = fit.get("coefficient")
        ci_low, ci_high = fit.get("ci_low"), fit.get("ci_high")
        ci_excludes_zero = (ci_low is not None and ci_high is not None
                            and (ci_low > 0 or ci_high < 0))
        criteria = {
            "n>=20": n_clean >= MIN_N,
            "R2>=0.40": r2 >= MIN_R2,
            "p<0.01": p is not None and p < MAX_PVALUE,
            "coeff>0": coeff is not None and coeff > 0,
            "CI_excludes_0": ci_excludes_zero,
        }
        report["activation_criteria"] = criteria

        if all(criteria.values()):
            outcome = _activate(db, fit, lag_weeks, n_clean,
                                [p["date"] for p in clean], chow, dry_run)
            report["status"] = "ACTIVE"
            report["activated"] = not dry_run
            _log_quality(db, "pass" if not dry_run else "warn",
                         f"Calibration {'activated' if not dry_run else 'dry-run'}: {outcome}")
        else:
            failed = [k for k, v in criteria.items() if not v]
            outcome = f"NOT ACTIVATED — failed: {failed}"
            report["status"] = "NOT_ACTIVATED"
            report["activated"] = False
            _log_quality(db, "warn",
                         f"Calibration not activated (n={n_clean}, R²={r2:.3f}, p={p}). Failed: {failed}")
        report["outcome"] = outcome

        # Step 6 — threshold alert
        if chow.get("threshold") is not None and abs(chow["threshold"] - INDUSTRY_THRESHOLD) > THRESHOLD_ALERT_DELTA:
            _send_slack(
                f"THRESHOLD FINDING: empirical crude→dyeing threshold "
                f"${chow['threshold']:.0f}/bbl (vs industry prior $85/bbl). "
                f"Consider updating CRUDE_OIL_DYEING_PRESSURE_THRESHOLD.", level="warning")

        _write_reports(txt_path, json_path, report)
        logger.info("Calibration outcome: %s", outcome)
        return report
    except Exception as exc:
        db.rollback()
        logger.error("Calibration failed: %s", exc, exc_info=True)
        raise
    finally:
        db.close()


def _write_reports(txt_path: Path, json_path: Path, report: dict) -> None:
    json_path.write_text(json.dumps(report, indent=2, default=str))
    f = report["funnel"]
    lines = [
        "ARTEMIS — Crude → Dyeing Chemical Transmission Calibration",
        f"Run date: {report['run_date']}",
        "=" * 66,
        "",
        "STEP 1-2 — DATA ASSEMBLY FUNNEL:",
        f"  Total fabric_dyeing rows:           {f['total']}",
        f"  With production_date:               {f['with_date']}",
        f"  With chemical_cost:                 {f['with_cost']}",
        f"  Quality-gated (pass / shade≥70):    {f['high_conf']}",
        f"  Crude join succeeds (raw pairs):    {report['n_raw_pairs']}",
        f"  Extraction yield:                   "
        f"{report.get('extraction_yield_pct', round(report['n_raw_pairs']/f['total']*100,1) if f['total'] else 0)}%",
        "",
    ]
    if report.get("status") == "PENDING":
        lines += [
            f"STATUS: PENDING — {report['reason']}",
            f"  Minimum required: n>={MIN_N}.  Need {report['pairs_needed']} more validated pair(s).",
            "",
            "Activation requires: n>=20, R²>=0.40, p<0.01, positive coefficient, 95% CI excludes 0.",
            "Next scheduled run: tomorrow.",
        ]
    else:
        reg = report["regression"]
        lc = report["lag_comparison"]
        chow = report["chow_threshold"]
        crit = report["activation_criteria"]
        lines += [
            f"STEP 3 — OUTLIER REJECTION: {report['n_outliers']} excluded, {report['n_clean']} clean.",
            "",
            f"STEP 4 — LAG COMPARISON: 4w R²={lc['r2_4w']} | 8w R²={lc['r2_8w']} | winner={lc['winner']}",
            "",
            "STEP 4 — REGRESSION (chemical_cost ~ brent_lag + const):",
            f"  n={reg.get('n')}  coefficient={reg.get('coefficient')}  intercept={reg.get('intercept')}",
            f"  R²={reg.get('r_squared')}  p_value={reg.get('p_value')}  "
            f"95%CI=[{reg.get('ci_low')}, {reg.get('ci_high')}]",
            "",
            f"STEP 6 — EMPIRICAL THRESHOLD (Chow test): {chow.get('note', 'n/a')}",
            "",
            "STEP 5 — ACTIVATION CRITERIA:",
        ] + [f"  {'✓' if v else '✗'} {k}" for k, v in crit.items()] + [
            "",
            f"OUTCOME: {report['outcome']}",
            f"STATUS: {report['status']}",
            "Next scheduled run: tomorrow.",
        ]
    txt_path.write_text("\n".join(lines) + "\n")
    logger.info("Reports written: %s | %s", txt_path.name, json_path.name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crude→dyeing transmission calibration engine")
    parser.add_argument("--run", action="store_true", help="Run calibration.")
    parser.add_argument("--dry-run", action="store_true", help="Compute but do not activate.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    result = run(dry_run=args.dry_run)
    print(f"\nCalibration done: status={result.get('status')}, "
          f"n_raw={result.get('n_raw_pairs')}, activated={result.get('activated')}")

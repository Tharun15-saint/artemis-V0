"""Quality checks for the crude oil data layer.

Every check writes a row to quality_check_log and returns a QualityCheckResult.
get_blocking_failures() returns the names of unresolved 'fail' results for the
BLOCKING checks only. No cost computation may proceed while blocking failures exist.

SEVEN CHECKS
  1. daily_increment_check          [BLOCKING]      EIA daily has a row each business day.
  2. sigma_anomaly_check            [non-blocking]  Flag >3σ prices; set price_anomaly_flag.
  3. source_reconciliation_check    [BLOCKING]      EIA daily vs FRED weekly vs Pink Sheet.
  4. futures_curve_integrity_check  [BLOCKING]      Brent forward curve internally consistent.
  5. eia_daily_coverage_check       [BLOCKING]      Backfill complete 1987→present.
  6. wti_brent_spread_sanity_check  [non-blocking]  Basis spread within historical norms.
  7. calibration_readiness_check    [non-blocking]  Reports transmission-calibration status.

PRINCIPLE: a check's blocking-ness is a property of the CHECK, not the result string.
get_blocking_failures() filters to BLOCKING_CHECKS so a non-blocking check can never
halt the cost engine even if it records a 'fail'.

US federal holidays are excluded from business-day expectations via the `holidays` lib.
SLACK: set SLACK_WEBHOOK_URL to receive alerts; otherwise alerts log to stderr.
"""
import argparse
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

try:
    import holidays as _holidays_lib
except ImportError:  # pragma: no cover
    _holidays_lib = None

logger = logging.getLogger(__name__)

# ── Thresholds ──────────────────────────────────────────────────────────────
INCREMENT_FAIL_BUSINESS_DAYS = 4      # latest eia_daily row older than this → fail
ANOMALY_SIGMA_FAIL = 3.0
ANOMALY_SIGMA_WARN = 2.5
RECONCILE_WARN_PCT = 2.0
RECONCILE_FAIL_PCT = 4.0
CURVE_SPOT_DEV_FAIL_PCT = 40.0        # 12m vs spot deviation ceiling
COVERAGE_WARN_PCT = 90.0
COVERAGE_FAIL_PCT = 80.0
COVERAGE_MODERN_FAIL_PCT = 95.0       # stricter floor for 2010+
COVERAGE_MODERN_YEAR = 2010
EARLY_YEAR_EXEMPT = 1990              # 1987-1990 flagged but never auto-fail
SPREAD_PASS = (-8.0, 4.0)
SPREAD_WARN = (-10.0, 6.0)
CALIBRATION_MIN_PAIRS = 20
CALIBRATION_MIN_R2 = 0.40
EIA_DAILY_BACKFILL_START_YEAR = 1987

BRENT_FUTURES_TENORS = ["brent_futures_1m", "brent_futures_3m",
                        "brent_futures_6m", "brent_futures_12m"]

# Which checks gate the cost engine. The rest are advisory.
BLOCKING_CHECKS = {
    "daily_increment_check",
    "source_reconciliation_check",
    "futures_curve_integrity_check",
    "eia_daily_coverage_check",
}


@dataclass
class QualityCheckResult:
    name: str
    result: str           # 'pass' / 'warn' / 'fail' / 'skip'
    details: str
    resolved: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.name in BLOCKING_CHECKS and self.result == "fail"


# ──────────────────────────────────────────────────────────────────────────────
# Slack + logging
# ──────────────────────────────────────────────────────────────────────────────

def _send_slack(message: str, level: str = "warning") -> None:
    webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        logger.warning(f"[NO SLACK WEBHOOK] {message}")
        return
    prefix = "⚠ *ARTEMIS ALERT*" if level == "warning" else "🔴 *ARTEMIS CRITICAL*"
    try:
        resp = requests.post(webhook_url, json={"text": f"{prefix}\n{message}"}, timeout=10)
        if resp.status_code != 200:
            logger.error(f"Slack alert failed (HTTP {resp.status_code})")
    except requests.RequestException as e:
        logger.error(f"Slack alert request error: {e}")


def _alert_for(res: QualityCheckResult) -> None:
    """Fire Slack per result: fail → CRITICAL; persistent warn (>48h) → WARNING."""
    guidance = res.extra.get("action", "Review crude data layer.")
    if res.result == "fail":
        _send_slack(
            f"[CRITICAL] Artemis Crude Layer — {res.name}\nResult: fail\n"
            f"Details: {res.details}\nDate: {date.today()}\nAction required: {guidance}",
            level="critical",
        )
    elif res.result == "warn" and res.extra.get("persistent_warn"):
        _send_slack(
            f"[WARNING] Artemis Crude Layer — {res.name}\nResult: warn (persisting >48h)\n"
            f"Details: {res.details}\nDate: {date.today()}\nAction required: {guidance}",
            level="warning",
        )


def _warn_persisting(db: Session, check_name: str, hours: int = 48) -> bool:
    """True if this check has an unresolved 'warn' first seen more than `hours` ago.
    Checked BEFORE inserting the current result so 'first seen' reflects prior runs."""
    try:
        row = db.execute(text("""
            SELECT MIN(check_date) FROM quality_check_log
            WHERE check_name = :n AND result = 'warn' AND (resolved IS NULL OR resolved = 0)
        """), {"n": check_name}).scalar()
        if row is None:
            return False
        first = row if isinstance(row, date) else date.fromisoformat(str(row))
        return (date.today() - first).days * 24 >= hours
    except Exception:
        return False


def _log_check(db: Session, res: QualityCheckResult) -> None:
    """Write a result to quality_check_log, then fire alerts as warranted."""
    if res.result == "warn" and _warn_persisting(db, res.name):
        res.extra["persistent_warn"] = True
    try:
        db.execute(text("""
            INSERT INTO quality_check_log (check_name, check_date, result, details, resolved)
            VALUES (:name, :dt, :result, :details, :resolved)
        """), {
            "name": res.name, "dt": date.today().isoformat(),
            "result": res.result, "details": res.details,
            "resolved": 1 if res.resolved else 0,
        })
        db.commit()
    except Exception as e:
        logger.debug(f"_log_check write failed: {e}")
        try:
            db.rollback()
        except Exception:
            pass
    _alert_for(res)


# ──────────────────────────────────────────────────────────────────────────────
# Business-day helpers
# ──────────────────────────────────────────────────────────────────────────────

def _us_holidays(years):
    if _holidays_lib is None:
        logger.warning("`holidays` library not installed — federal holidays not excluded.")
        return set()
    return _holidays_lib.US(years=list(years))


def _business_days(start: date, end: date, hol=None) -> list[date]:
    """Weekdays in [start, end] excluding US federal holidays."""
    if hol is None:
        hol = _us_holidays(range(start.year, end.year + 1))
    out, cur = [], start
    while cur <= end:
        if cur.weekday() < 5 and cur not in hol:
            out.append(cur)
        cur += timedelta(days=1)
    return out


def _eia_dates(db, start: date, end: date) -> set:
    rows = db.execute(text("""
        SELECT as_of_date FROM crude_oil
        WHERE source = 'eia_daily' AND brent_spot IS NOT NULL
          AND as_of_date BETWEEN :a AND :b
    """), {"a": start.isoformat(), "b": end.isoformat()}).fetchall()
    out = set()
    for (d,) in rows:
        out.add(d if isinstance(d, date) else date.fromisoformat(str(d)))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# CHECK 1 — daily increment (BLOCKING)
# ──────────────────────────────────────────────────────────────────────────────

def daily_increment_check(db: Session) -> QualityCheckResult:
    today = date.today()
    window_start = today - timedelta(days=10)
    hol = _us_holidays(range(window_start.year, today.year + 1))
    expected = [d for d in _business_days(window_start, today, hol)][-7:]
    present = _eia_dates(db, window_start, today)
    missing = [d for d in expected if d not in present]

    latest = db.execute(text(
        "SELECT MAX(as_of_date) FROM crude_oil WHERE source='eia_daily' AND brent_spot IS NOT NULL"
    )).scalar()
    latest_d = (latest if isinstance(latest, date)
                else date.fromisoformat(str(latest))) if latest else None

    # consecutive-missing run length (most recent expected days)
    consec = 0
    for d in reversed(expected):
        if d in missing:
            consec += 1
        else:
            break

    latest_age_bdays = len(_business_days(latest_d, today, hol)) if latest_d else 999

    if latest_d is None or consec >= 3 or latest_age_bdays > INCREMENT_FAIL_BUSINESS_DAYS:
        res = QualityCheckResult(
            "daily_increment_check", "fail",
            f"EIA daily gap. Missing: {[d.isoformat() for d in missing]}. "
            f"Latest row: {latest_d}. Expected latest: {expected[-1] if expected else 'n/a'}. "
            f"Latest is {latest_age_bdays} business days old; consec-missing={consec}.",
            extra={"action": "Check EIA API status; run crude_oil_eia_daily_ingestion --run-once."},
        )
    elif consec == 2:
        res = QualityCheckResult(
            "daily_increment_check", "warn",
            f"2 consecutive EIA business days missing: {[d.isoformat() for d in missing]}.",
            extra={"action": "Monitor; EIA may be lagging."},
        )
    elif len(missing) <= 1:
        res = QualityCheckResult(
            "daily_increment_check", "pass",
            f"EIA daily current. Latest {latest_d} ({latest_age_bdays} bdays old). "
            f"Missing in last 7 bdays: {len(missing)} (≤1 allowed).",
        )
    else:
        res = QualityCheckResult(
            "daily_increment_check", "warn",
            f"{len(missing)} non-consecutive business days missing in last 7: "
            f"{[d.isoformat() for d in missing]}.",
            extra={"action": "Re-run ingestion to fill gaps."},
        )
    _log_check(db, res)
    return res


# ──────────────────────────────────────────────────────────────────────────────
# CHECK 2 — sigma anomaly (non-blocking; sets price_anomaly_flag)
# ──────────────────────────────────────────────────────────────────────────────

def sigma_anomaly_check(db: Session) -> QualityCheckResult:
    today = date.today()
    window_start = today - timedelta(days=30)
    year_start = today - timedelta(days=365)

    base = db.execute(text("""
        SELECT CAST(brent_spot AS REAL) FROM crude_oil
        WHERE source='eia_daily' AND brent_spot IS NOT NULL AND as_of_date >= :ys
    """), {"ys": year_start.isoformat()}).fetchall()
    prices = [r[0] for r in base if r[0] is not None]
    if len(prices) < 30:
        res = QualityCheckResult("sigma_anomaly_check", "skip",
                                 f"Only {len(prices)} obs in trailing 365d — insufficient.")
        _log_check(db, res)
        return res

    mean = sum(prices) / len(prices)
    std = math.sqrt(sum((p - mean) ** 2 for p in prices) / len(prices))
    if std < 0.01:
        res = QualityCheckResult("sigma_anomaly_check", "pass", "Zero variance — no anomaly.")
        _log_check(db, res)
        return res

    recent = db.execute(text("""
        SELECT crude_oil_id, as_of_date, CAST(brent_spot AS REAL), price_anomaly_flag
        FROM crude_oil
        WHERE source='eia_daily' AND brent_spot IS NOT NULL AND as_of_date >= :ws
        ORDER BY as_of_date
    """), {"ws": window_start.isoformat()}).fetchall()

    over3, warn25, flagged = [], 0, 0
    for cid, d, price, flag in recent:
        z = (price - mean) / std
        if abs(z) > ANOMALY_SIGMA_FAIL:
            over3.append((d, price, round(z, 2)))
            if not flag:
                db.execute(text("UPDATE crude_oil SET price_anomaly_flag=1, price_anomaly_sigma=:z "
                                "WHERE crude_oil_id=:id"), {"z": round(z, 3), "id": cid})
                flagged += 1
        elif ANOMALY_SIGMA_WARN < abs(z) <= ANOMALY_SIGMA_FAIL:
            warn25 += 1
    db.commit()

    if over3:
        res = QualityCheckResult(
            "sigma_anomaly_check", "fail",
            f"{len(over3)} price(s) >3σ from 365d mean (${mean:.2f}±{std:.2f}): "
            f"{[(d.isoformat(), p, z) for d, p, z in over3]}. {flagged} newly flagged. "
            f"Human review required — confirm real event or correct data error.",
            extra={"action": "Review flagged rows; confirm (real shock) or correct (data error)."},
        )
    elif warn25:
        res = QualityCheckResult("sigma_anomaly_check", "warn",
                                 f"{warn25} price(s) in 2.5-3.0σ band. No action yet.")
    else:
        res = QualityCheckResult("sigma_anomaly_check", "pass",
                                 f"No anomalies in last 30d (365d mean ${mean:.2f}±{std:.2f}).")
    _log_check(db, res)
    return res


# ──────────────────────────────────────────────────────────────────────────────
# CHECK 3 — source reconciliation (BLOCKING)
# ──────────────────────────────────────────────────────────────────────────────

def source_reconciliation_check(db: Session) -> QualityCheckResult:
    # most recent complete month = previous calendar month
    today = date.today()
    first_this = today.replace(day=1)
    last_prev = first_this - timedelta(days=1)
    month = last_prev.strftime("%Y-%m")

    def month_avg(source):
        return db.execute(text("""
            SELECT AVG(CAST(brent_spot AS REAL)), COUNT(*) FROM crude_oil
            WHERE source=:s AND strftime('%Y-%m', as_of_date)=:m AND brent_spot IS NOT NULL
        """), {"s": source, "m": month}).fetchone()

    eia = month_avg("eia_daily")
    fred = month_avg("fred_api")
    pink = month_avg("world_bank_pink_sheet")

    if not eia[0] or not fred[0]:
        res = QualityCheckResult("source_reconciliation_check", "skip",
                                 f"Insufficient data for {month}: eia={eia[1]} fred={fred[1]}.")
        _log_check(db, res)
        return res

    dev = abs(eia[0] - fred[0]) / fred[0] * 100
    pink_str = f"{pink[0]:.2f}" if pink[0] else "n/a"
    details = (f"{month}: eia_daily_avg={eia[0]:.2f} ({eia[1]}d) | "
               f"fred_weekly_avg={fred[0]:.2f} ({fred[1]}w) | pink_sheet={pink_str} | "
               f"eia-vs-fred dev={dev:.2f}%")

    if dev > RECONCILE_FAIL_PCT:
        # flag the outlier source's rows for that month
        outlier = "eia_daily" if (pink[0] and abs(eia[0] - pink[0]) > abs(fred[0] - pink[0])) else "fred_api"
        db.execute(text("""
            UPDATE crude_oil SET data_quality_flag='RECONCILE_FAILED'
            WHERE source=:s AND strftime('%Y-%m', as_of_date)=:m
        """), {"s": outlier, "m": month})
        db.commit()
        res = QualityCheckResult("source_reconciliation_check", "fail",
                                 details + f" | outlier={outlier} flagged RECONCILE_FAILED",
                                 extra={"action": f"Human review of {outlier} for {month}."})
    elif dev > RECONCILE_WARN_PCT:
        res = QualityCheckResult("source_reconciliation_check", "warn", details,
                                 extra={"action": "Monitor source drift."})
    else:
        res = QualityCheckResult("source_reconciliation_check", "pass", details)
    _log_check(db, res)
    return res


# ──────────────────────────────────────────────────────────────────────────────
# CHECK 4 — futures curve integrity (BLOCKING)
# ──────────────────────────────────────────────────────────────────────────────

def futures_curve_integrity_check(db: Session) -> QualityCheckResult:
    sel = ", ".join(f"CAST({t} AS REAL)" for t in BRENT_FUTURES_TENORS)
    row = db.execute(text(f"""
        SELECT as_of_date, {sel}, brent_contango_signal, crude_market_structure,
               brent_futures_is_market_price, brent_futures_source
        FROM crude_oil WHERE source='eia_petroleum_futures' AND brent_futures_1m IS NOT NULL
        ORDER BY as_of_date DESC LIMIT 1
    """)).fetchone()
    if row is None:
        res = QualityCheckResult("futures_curve_integrity_check", "fail",
                                 "No eia_petroleum_futures rows found.",
                                 extra={"action": "Run crude_oil_petroleum_futures_ingestion."})
        _log_check(db, res)
        return res

    as_of, b1, b3, b6, b12, signal, structure, is_market, src = row
    spot = db.execute(text(
        "SELECT CAST(brent_spot AS REAL) FROM crude_oil WHERE source='eia_daily' "
        "AND brent_spot IS NOT NULL ORDER BY as_of_date DESC LIMIT 1"
    )).scalar()

    null_tenors = [t for t, v in zip(BRENT_FUTURES_TENORS, [b1, b3, b6, b12]) if v is None]
    dev_pct = (abs(b12 - spot) / spot * 100) if (b12 and spot) else None
    front_month_real = bool(is_market) and src == "ice_yfinance"
    full_real = bool(is_market) and src == "cme_delayed"

    base = (f"as_of={as_of} curve=[{b1},{b3},{b6},{b12}] spot={spot} "
            f"12m/spot_dev={dev_pct:.1f}% src={src} is_market={bool(is_market)} structure={structure}"
            if dev_pct is not None else
            f"as_of={as_of} curve=[{b1},{b3},{b6},{b12}] spot={spot} src={src} structure={structure}")

    if null_tenors:
        res = QualityCheckResult("futures_curve_integrity_check", "fail",
                                 base + f" | NULL tenors: {null_tenors}",
                                 extra={"action": "Re-run futures ingestion; missing tenors."})
    elif dev_pct is not None and dev_pct > CURVE_SPOT_DEV_FAIL_PCT:
        res = QualityCheckResult("futures_curve_integrity_check", "fail",
                                 base + f" | 12m deviates from spot >{CURVE_SPOT_DEV_FAIL_PCT}% — series mapping likely wrong",
                                 extra={"action": "Verify futures series→tenor mapping."})
    elif front_month_real:
        # Front-month real, 3m/6m/12m STEO → structure intentionally NULL. Acceptable WARN.
        res = QualityCheckResult("futures_curve_integrity_check", "warn",
                                 base + " | front-month real ICE; 3m/6m/12m STEO; structure pending full curve. "
                                        "Forward confidence: 0.85 (1m), 0.55 (3m+).",
                                 extra={"action": "Source a real ICE term structure (CME/Platts) to enable structure."})
    elif not is_market:
        # Full STEO curve. structure must be present.
        if structure is None or structure not in ("contango", "backwardation", "flat"):
            res = QualityCheckResult("futures_curve_integrity_check", "fail",
                                     base + " | STEO curve but market_structure NULL/invalid",
                                     extra={"action": "Recompute structure in futures ingestion."})
        else:
            res = QualityCheckResult("futures_curve_integrity_check", "warn",
                                     base + " | STEO forecast curve (is_market_price=False). Forward confidence 0.55.",
                                     extra={"action": "Source real ICE Brent futures to lift confidence to 0.85."})
    else:  # full real curve
        if structure is None or structure not in ("contango", "backwardation", "flat"):
            res = QualityCheckResult("futures_curve_integrity_check", "fail",
                                     base + " | real curve but market_structure NULL/invalid",
                                     extra={"action": "Recompute structure from real curve."})
        else:
            res = QualityCheckResult("futures_curve_integrity_check", "pass",
                                     base + " | full real ICE curve, structure valid.")
    _log_check(db, res)
    return res


# ──────────────────────────────────────────────────────────────────────────────
# CHECK 5 — EIA daily coverage (BLOCKING)
# ──────────────────────────────────────────────────────────────────────────────

def eia_daily_coverage_check(db: Session) -> QualityCheckResult:
    today = date.today()
    # Coverage measures BACKFILL completeness (historical holes), not current staleness
    # (which is daily_increment_check's job). Cap the window at the latest available EIA
    # date so the trailing stale days are not double-counted as a coverage gap.
    latest = db.execute(text(
        "SELECT MAX(as_of_date) FROM crude_oil WHERE source='eia_daily' AND brent_spot IS NOT NULL"
    )).scalar()
    coverage_end = today
    if latest is not None:
        latest_d = latest if isinstance(latest, date) else date.fromisoformat(str(latest))
        coverage_end = min(today, latest_d)
    failing, warning, early_low = [], [], []
    for yr in range(EIA_DAILY_BACKFILL_START_YEAR, coverage_end.year + 1):
        y_start = date(yr, 1, 1) if yr > EIA_DAILY_BACKFILL_START_YEAR else date(1987, 5, 20)
        y_end = min(date(yr, 12, 31), coverage_end)
        hol = _us_holidays([yr])
        expected = len(_business_days(y_start, y_end, hol))
        if expected == 0:
            continue
        present = len(_eia_dates(db, y_start, y_end))
        cov = present / expected * 100
        modern = yr >= COVERAGE_MODERN_YEAR
        if yr <= EARLY_YEAR_EXEMPT:
            if cov < COVERAGE_WARN_PCT:
                early_low.append((yr, round(cov, 1)))
            continue
        if cov < COVERAGE_FAIL_PCT or (modern and cov < COVERAGE_MODERN_FAIL_PCT):
            failing.append((yr, round(cov, 1)))
        elif cov < COVERAGE_WARN_PCT:
            warning.append((yr, round(cov, 1)))

    summary = (f"Coverage 1987-{coverage_end.year} (through {coverage_end}): "
               f"{len(failing)} failing, {len(warning)} warn, "
               f"{len(early_low)} early-year(<90%, exempt).")
    if failing:
        res = QualityCheckResult("eia_daily_coverage_check", "fail",
                                 summary + f" Failing years: {failing}",
                                 extra={"action": "Re-run --backfill; investigate EIA gaps for these years."})
    elif warning:
        res = QualityCheckResult("eia_daily_coverage_check", "warn",
                                 summary + f" Warn years: {warning} | early-low: {early_low}")
    else:
        res = QualityCheckResult("eia_daily_coverage_check", "pass",
                                 summary + f" early-low(exempt): {early_low}")
    _log_check(db, res)
    return res


# ──────────────────────────────────────────────────────────────────────────────
# CHECK 6 — WTI/Brent spread sanity (non-blocking)
# ──────────────────────────────────────────────────────────────────────────────

def wti_brent_spread_sanity_check(db: Session) -> QualityCheckResult:
    row = db.execute(text("""
        SELECT as_of_date, CAST(wti_brent_spread AS REAL)
        FROM crude_oil WHERE source='eia_daily' AND wti_brent_spread IS NOT NULL
        ORDER BY as_of_date DESC LIMIT 1
    """)).fetchone()
    if row is None:
        res = QualityCheckResult("wti_brent_spread_sanity_check", "skip",
                                 "No eia_daily row with wti_brent_spread.")
        _log_check(db, res)
        return res
    as_of, spread = row
    base = (f"wti_brent_spread={spread:.2f} on {as_of} (norm -$6..+$2). ")
    if SPREAD_PASS[0] <= spread <= SPREAD_PASS[1]:
        res = QualityCheckResult("wti_brent_spread_sanity_check", "pass", base + "Within normal band.")
    elif SPREAD_WARN[0] <= spread <= SPREAD_WARN[1]:
        res = QualityCheckResult("wti_brent_spread_sanity_check", "warn",
                                 base + "Outside normal but plausible — verify both series same date.",
                                 extra={"action": "Confirm WTI/Brent from same trading date."})
    else:
        res = QualityCheckResult("wti_brent_spread_sanity_check", "fail",
                                 base + "Outside -$10..+$6 — data error or major dislocation.",
                                 extra={"action": "Verify both series; check for data error."})
    _log_check(db, res)
    return res


# ──────────────────────────────────────────────────────────────────────────────
# CHECK 7 — calibration readiness (non-blocking, never fails)
# ──────────────────────────────────────────────────────────────────────────────

def calibration_readiness_check(db: Session) -> QualityCheckResult:
    active = db.execute(text("""
        SELECT cost_component, CAST(r_squared AS REAL), obs_count
        FROM crude_transmission_calibration
        WHERE is_active = 1 AND cost_component LIKE '%dyeing%'
        ORDER BY calibration_date DESC LIMIT 1
    """)).fetchone()

    # high-quality dyeing invoices: chemical cost present, dated, quality passed
    n_quality = db.execute(text("""
        SELECT COUNT(*) FROM fabric_dyeing
        WHERE chemical_cost_per_kg_inr IS NOT NULL AND production_date IS NOT NULL
          AND (quality_result = 'pass' OR (shade_pass_rate_pct IS NOT NULL AND shade_pass_rate_pct >= 70))
    """)).scalar() or 0

    # of those, how many map to an eia_daily crude price at the 4w lag
    n_pairs = db.execute(text("""
        SELECT COUNT(*) FROM fabric_dyeing fd
        WHERE fd.chemical_cost_per_kg_inr IS NOT NULL AND fd.production_date IS NOT NULL
          AND (fd.quality_result = 'pass' OR (fd.shade_pass_rate_pct IS NOT NULL AND fd.shade_pass_rate_pct >= 70))
          AND EXISTS (
            SELECT 1 FROM crude_oil c
            WHERE c.source='eia_daily' AND c.brent_spot IS NOT NULL
              AND c.as_of_date <= date(fd.production_date, '-28 days')
          )
    """)).scalar() or 0

    if active and active[1] is not None and active[1] >= CALIBRATION_MIN_R2:
        res = QualityCheckResult(
            "calibration_readiness_check", "pass",
            f"ACTIVE empirical calibration: {active[0]} R²={active[1]:.3f} n={active[2]}. "
            f"Validated pairs now: {n_pairs}.")
    else:
        gap = max(0, CALIBRATION_MIN_PAIRS - n_pairs)
        res = QualityCheckResult(
            "calibration_readiness_check", "warn",
            f"CALIBRATION STATUS: {n_pairs} validated invoice pairs available "
            f"(of {n_quality} quality invoices). Minimum required: {CALIBRATION_MIN_PAIRS}. "
            f"Status: PENDING. {gap} more pair(s) needed to activate empirical calibration.",
            extra={"action": "Ingest more RRK dyeing invoices with chemical cost + production date."})
    _log_check(db, res)
    return res


# ──────────────────────────────────────────────────────────────────────────────
# Blocking gate
# ──────────────────────────────────────────────────────────────────────────────

def get_blocking_failures(db: Session) -> list[str]:
    """Names of BLOCKING checks whose MOST RECENT result is an unresolved 'fail'.

    Uses the latest row per check (by check_id) so a check that has since recovered
    to pass/warn no longer blocks. Non-blocking checks are excluded structurally even
    if their latest result is a fail."""
    try:
        rows = db.execute(text("""
            SELECT q.check_name, q.result, q.resolved
            FROM quality_check_log q
            JOIN (
                SELECT check_name, MAX(check_id) AS max_id
                FROM quality_check_log GROUP BY check_name
            ) latest ON q.check_id = latest.max_id
        """)).fetchall()
        return sorted(
            name for name, result, resolved in rows
            if name in BLOCKING_CHECKS and result == "fail" and not resolved
        )
    except Exception as e:
        logger.debug(f"get_blocking_failures: {e}")
        return []


# ──────────────────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────────────────

ALL_CHECKS = [
    daily_increment_check,
    sigma_anomaly_check,
    source_reconciliation_check,
    futures_curve_integrity_check,
    eia_daily_coverage_check,
    wti_brent_spread_sanity_check,
    calibration_readiness_check,
]


def run_all_checks(db: Session, verbose: bool = False) -> dict:
    results = []
    for fn in ALL_CHECKS:
        try:
            res = fn(db)
        except Exception as e:
            logger.error("Check %s errored: %s", fn.__name__, e, exc_info=True)
            res = QualityCheckResult(fn.__name__, "fail", f"check raised: {e}")
        results.append(res)
        if verbose:
            print(f"  [{res.result.upper():4}] {res.name}: {res.details}")
    blocking = get_blocking_failures(db)
    return {
        "results": {r.name: {"result": r.result, "details": r.details} for r in results},
        "blocking_failures": blocking,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Crude oil data quality checks")
    parser.add_argument("--run-all", action="store_true", help="Run all 7 checks.")
    parser.add_argument("--verbose", action="store_true", help="Print each result.")
    args = parser.parse_args()

    from data.ingestion._env import load_project_env
    load_project_env()
    from database.base import SessionLocal
    db = SessionLocal()
    try:
        summary = run_all_checks(db, verbose=args.verbose or args.run_all)
        print("\nBLOCKING FAILURES:", summary["blocking_failures"] or "none")
    finally:
        db.close()

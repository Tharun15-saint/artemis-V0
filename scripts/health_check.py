#!/usr/bin/env python3
"""
Artemis daily health check — verifies data freshness, ingestion integrity,
cross-table consistency, and retailer signal coverage. Reports only; does not fix.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from database.base import SessionLocal
from database.constants import STALENESS
from database.models import (
    Cotton,
    CrudeOil,
    FxRates,
    IngestionLog,
    MajorRetailers,
    OceanFreightRates,
    RetailerFinancials,
    RetailerIntelligenceExtract,
    Yarn,
)

APPEND_ONLY_ENTITY_KEYS: dict[str, list[str]] = {
    "cotton": ["origin_country", "as_of_date"],
    "crude_oil": ["as_of_date"],
    "fx_rates": [],
    "commodity_futures": ["as_of_date"],
    "ocean_freight_rates": [
        "origin_port",
        "destination_port",
        "origin_country",
        "destination_country",
        "as_of_date",
    ],
    "retailer_financials": ["retailer_id", "fiscal_year", "fiscal_quarter"],
    "retailer_intelligence_extract": [
        "retailer_id",
        "fiscal_year",
        "fiscal_quarter",
        "extract_id",
    ],
    "retailer_signal_evidence": ["evidence_id"],
    "labour_cost_by_country": ["effective_date"],
    "energy_cost": ["effective_date"],
    "factory_financing_cost": [],
    "yarn": ["yarn_id"],
}

FRESHNESS_CHECKS = [
    ("cotton", Cotton, "as_of_date", STALENESS["cotton"], "warn"),
    ("fx_rates", FxRates, "pulled_at", STALENESS["fx_rates"], "warn"),
    ("crude_oil", CrudeOil, "as_of_date", STALENESS["crude_oil_warn"], "warn"),
]


def _days_old(value: Optional[date | datetime]) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.date()
    return (date.today() - value).days


def _status_symbol(ok: bool, critical: bool = False) -> str:
    if ok:
        return "✓"
    return "✗" if critical else "✗"


class HealthReport:
    def __init__(self) -> None:
        self.warnings = 0
        self.critical = 0
        self.lines: list[str] = []

    def ok(self, line: str) -> None:
        self.lines.append(f"    ✓ {line}")

    def warn(self, line: str) -> None:
        self.warnings += 1
        self.lines.append(f"    ✗ {line}")

    def critical_msg(self, line: str) -> None:
        self.critical += 1
        self.lines.append(f"    ✗ {line}")


def _check_ocean_freight_freshness(db: Session, report: HealthReport) -> None:
    threshold = STALENESS["ocean_freight"]
    latest_date = (
        db.query(func.max(OceanFreightRates.as_of_date))
        .filter(OceanFreightRates.is_latest.is_(True))
        .scalar()
    )
    if latest_date is None:
        report.critical_msg("ocean_freight: NO DATA — never ingested")
        return

    route_count = (
        db.query(func.count(OceanFreightRates.ocean_rate_id))
        .filter(
            OceanFreightRates.is_latest.is_(True),
            OceanFreightRates.as_of_date == latest_date,
        )
        .scalar()
    )
    age = _days_old(latest_date)
    if age is None:
        report.warn("ocean_freight: is_latest rows have NULL as_of_date")
        return
    if age > threshold:
        report.warn(
            f"ocean_freight: {age} days old ({latest_date}) — "
            f"{route_count or 0} route(s), threshold {threshold}d"
        )
    else:
        report.ok(
            f"ocean_freight: {age} days old ({latest_date}) — "
            f"{route_count or 0} route(s) with fresh data"
        )


def check_freshness(db: Session, report: HealthReport) -> None:
    report.lines.append("  FRESHNESS")

    for label, model, date_field, threshold, _ in FRESHNESS_CHECKS:
        row = (
            db.query(model)
            .filter(model.is_latest.is_(True))
            .order_by(desc(getattr(model, date_field)))
            .first()
        )
        if row is None:
            report.critical_msg(f"{label}: NO DATA — never ingested")
            continue

        ref_value = getattr(row, date_field)
        age = _days_old(ref_value)
        if age is None:
            report.warn(f"{label}: is_latest row has NULL {date_field}")
            continue
        if age > threshold:
            report.warn(f"{label}: {age} days old ({ref_value}) — threshold {threshold}d")
        else:
            report.ok(f"{label}: {age} days old ({ref_value})")

    _check_ocean_freight_freshness(db, report)

    fin_row = (
        db.query(RetailerFinancials)
        .filter(RetailerFinancials.is_latest.is_(True))
        .order_by(
            desc(RetailerFinancials.period_end_date),
            desc(RetailerFinancials.fiscal_year),
            desc(RetailerFinancials.fiscal_quarter),
        )
        .first()
    )
    if fin_row is None or fin_row.period_end_date is None:
        report.critical_msg("retailer_financials: NO DATA — never ingested")
    else:
        age = _days_old(fin_row.period_end_date)
        if age is not None and age > 100:
            report.warn(
                f"retailer_financials: most recent quarter {age} days old "
                f"(FY{fin_row.fiscal_year} Q{fin_row.fiscal_quarter})"
            )
        else:
            report.ok(
                f"retailer_financials: FY{fin_row.fiscal_year} Q{fin_row.fiscal_quarter} "
                f"({fin_row.period_end_date})"
            )


def check_ingestion_log(db: Session, report: HealthReport) -> None:
    report.lines.append("")
    report.lines.append("  INGESTION LOG (last 24h)")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    recent = (
        db.query(IngestionLog)
        .filter(IngestionLog.pull_started_at >= cutoff)
        .order_by(desc(IngestionLog.pull_started_at))
        .all()
    )
    if not recent:
        report.warn("No ingestion runs logged in the last 24 hours")
        return

    success = [r for r in recent if r.status == "success"]
    failed = [r for r in recent if r.status == "failed"]
    partial = [r for r in recent if r.status == "partial"]

    report.ok(f"{len(success)} successful run(s)")
    if partial:
        report.warn(f"{len(partial)} partial run(s)")
    for row in failed:
        started = row.pull_started_at.strftime("%H:%M") if row.pull_started_at else "?"
        err = (row.error_message or "unknown error").splitlines()[0][:120]
        report.warn(f"{row.source_name} — failed at {started} — {err}")
    for row in partial:
        started = row.pull_started_at.strftime("%H:%M") if row.pull_started_at else "?"
        report.warn(f"{row.source_name} — partial at {started}")


def check_cross_table_consistency(db: Session, report: HealthReport) -> None:
    report.lines.append("")
    report.lines.append("  CROSS-TABLE CONSISTENCY")

    fx = (
        db.query(FxRates)
        .filter(FxRates.is_latest.is_(True))
        .order_by(desc(FxRates.pulled_at))
        .first()
    )
    yarn = (
        db.query(Yarn)
        .filter(Yarn.is_latest.is_(True), Yarn.price_per_kg.isnot(None))
        .order_by(desc(Yarn.pulled_at))
        .first()
    )

    if fx is None or fx.usd_inr is None:
        report.warn("Cannot check yarn USD implied — no USD/INR FX rate")
    elif yarn is None or yarn.price_per_kg is None:
        report.warn("Cannot check yarn USD implied — no yarn price data")
    else:
        usd_inr = Decimal(str(fx.usd_inr))
        price_inr = Decimal(str(yarn.price_per_kg))
        if usd_inr <= 0:
            report.warn("USD/INR rate is zero or negative")
        else:
            implied_usd = price_inr / usd_inr
            lo, hi = Decimal("1.50"), Decimal("4.50")
            if lo <= implied_usd <= hi:
                report.ok(
                    f"Yarn price USD implied: ${implied_usd:.2f}/kg "
                    f"(within ${lo}-${hi} range)"
                )
            else:
                report.warn(
                    f"Yarn price USD implied: ${implied_usd:.2f}/kg "
                    f"outside ${lo}-${hi} range — possible data quality issue"
                )

    # Crude oil derived fields: post-2004 rows must have INR materialized
    # (FX history starts 2004-01-01; pre-2004 crude rows correctly have NULL INR)
    from datetime import date as _date
    cutoff = _date(2004, 1, 1)
    crude_total_post2004 = (
        db.query(func.count(CrudeOil.crude_oil_id))
        .filter(CrudeOil.is_latest.is_(True), CrudeOil.as_of_date >= cutoff)
        .scalar()
        or 0
    )
    crude_null_inr = (
        db.query(func.count(CrudeOil.crude_oil_id))
        .filter(
            CrudeOil.is_latest.is_(True),
            CrudeOil.as_of_date >= cutoff,
            CrudeOil.brent_inr_per_barrel.is_(None),
        )
        .scalar()
        or 0
    )
    if crude_null_inr == 0:
        report.ok(
            f"crude_oil INR materialized: all {crude_total_post2004} "
            f"post-2004 rows have brent_inr_per_barrel"
        )
    else:
        report.warn(
            f"crude_oil: {crude_null_inr}/{crude_total_post2004} post-2004 rows "
            f"missing brent_inr_per_barrel — run crude_oil_cleanup.py"
        )

    # trend_30d_pct: recent rows (last 60 days) must be populated
    sixty_days_ago = _date.today() - timedelta(days=60)
    crude_recent_null_trend = (
        db.query(func.count(CrudeOil.crude_oil_id))
        .filter(
            CrudeOil.is_latest.is_(True),
            CrudeOil.as_of_date >= sixty_days_ago,
            CrudeOil.trend_30d_pct.is_(None),
        )
        .scalar()
        or 0
    )
    if crude_recent_null_trend == 0:
        report.ok("crude_oil trend_30d_pct: populated on all recent rows")
    else:
        report.warn(
            f"crude_oil: {crude_recent_null_trend} recent row(s) with NULL trend_30d_pct"
        )


def _duplicate_violations(db: Session, table_name: str, entity_keys: list[str]) -> list[str]:
    from database.base import Base

    table = Base.metadata.tables.get(table_name)
    if table is None:
        return []

    model = None
    for mapper in Base.registry.mappers:
        if mapper.local_table.name == table_name:
            model = mapper.class_
            break
    if model is None:
        return []

    violations: list[str] = []
    if not entity_keys:
        count = (
            db.query(func.count())
            .select_from(model)
            .filter(model.is_latest.is_(True))
            .scalar()
        )
        if count and count > 1:
            violations.append(f"{table_name}: {count} rows with is_latest=True (expected ≤1)")
        return violations

    group_cols = [getattr(model, key) for key in entity_keys]
    rows = (
        db.query(*group_cols, func.count().label("cnt"))
        .filter(model.is_latest.is_(True))
        .group_by(*group_cols)
        .having(func.count() > 1)
        .all()
    )
    for row in rows:
        key_vals = ", ".join(
            f"{entity_keys[i]}={row[i]}" for i in range(len(entity_keys))
        )
        violations.append(f"{table_name}: {row[-1]} is_latest rows for {key_vals}")
    return violations


def check_duplicate_detection(db: Session, report: HealthReport) -> None:
    report.lines.append("")
    report.lines.append("  DUPLICATE DETECTION")

    all_violations: list[str] = []
    for table_name, entity_keys in APPEND_ONLY_ENTITY_KEYS.items():
        all_violations.extend(_duplicate_violations(db, table_name, entity_keys))

    if all_violations:
        for v in all_violations:
            report.critical_msg(v)
    else:
        report.ok("No duplicate is_latest rows detected")


def check_retailer_signal_coverage(db: Session, report: HealthReport) -> None:
    report.lines.append("")
    report.lines.append("  RETAILER SIGNAL COVERAGE")

    retailers = db.query(MajorRetailers).order_by(MajorRetailers.name).all()
    if not retailers:
        report.warn("No retailers in major_retailers")
        return

    for retailer in retailers:
        latest_fin = (
            db.query(RetailerFinancials)
            .filter(
                RetailerFinancials.retailer_id == retailer.retailer_id,
                RetailerFinancials.is_latest.is_(True),
            )
            .order_by(
                desc(RetailerFinancials.fiscal_year),
                desc(RetailerFinancials.fiscal_quarter),
            )
            .first()
        )
        if latest_fin is None:
            report.warn(f"{retailer.name}: no retailer_financials rows")
            continue

        signal_count = (
            db.query(func.count(RetailerIntelligenceExtract.extract_id))
            .filter(
                RetailerIntelligenceExtract.retailer_id == retailer.retailer_id,
                RetailerIntelligenceExtract.fiscal_year == latest_fin.fiscal_year,
                RetailerIntelligenceExtract.fiscal_quarter == latest_fin.fiscal_quarter,
                RetailerIntelligenceExtract.is_latest.is_(True),
            )
            .scalar()
        )
        label = (
            f"{retailer.name}: FY{latest_fin.fiscal_year} Q{latest_fin.fiscal_quarter}"
        )
        if signal_count and signal_count > 0:
            report.ok(f"{label} — {signal_count} signal(s)")
        else:
            report.warn(f"{label} — no signals for current quarter")


def run_health_check(db: Session) -> HealthReport:
    report = HealthReport()
    check_freshness(db, report)
    check_ingestion_log(db, report)
    check_cross_table_consistency(db, report)
    check_duplicate_detection(db, report)
    check_retailer_signal_coverage(db, report)
    return report


def main() -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"=== ARTEMIS HEALTH CHECK — {now} ===")
    print()

    db = SessionLocal()
    try:
        report = run_health_check(db)
        for line in report.lines:
            print(line)
        print()
        print(
            f"  === OVERALL: {report.warnings} warning(s), "
            f"{report.critical} critical ==="
        )
        return 1 if report.critical > 0 else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

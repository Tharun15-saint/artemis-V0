"""
Populate retailer_metric with DIRECT SEC-XBRL facts from the catalog (Pass A).

Loads only metrics that come straight from SEC companyfacts — one value, one source
concept, one filing URL each — so every row is reconcilable 1:1 against SEC. Derived
ratios are a separate pass.

Three period shapes are handled so the FULL fiscal year — including Q4, the holiday
quarter — is captured:
  * Income/flow metrics: discrete Q1-Q3 from 10-Q 3-month facts; Q4 = annual (10-K)
    − (Q1+Q2+Q3). Annual facts are keyed by END DATE, never the `fy` tag (which is the
    filing cycle and carries prior-year comparatives under the same number).
  * Cash-flow metrics: reported cumulative (YTD) → differenced to discrete quarters
    (Q4 = FY − 9mo) by grouping on the fiscal-year start date.
  * Balance-sheet instants: matched at the period-end date (the Jan-31 fiscal-year-end
    balance is the Q4 instant).

Discipline: per-retailer concept resolution; exact period-end matching; writes a value
ONLY when SEC has one (never imputes/copies); full provenance + source_concept; certified
stays False; idempotent upsert of the is_latest row per (retailer, metric_key, period).
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from data.verification.retail_financials_reconcile import (
    COMPANYFACTS_URL, _match, _parse_date, _sec_get_json,
    duration_facts_by_end, instant_facts_by_end,
)
from database.base import SessionLocal
from database.models.retail import MajorRetailers, RetailerFinancials
from database.models.retail_metrics import MetricDefinition, RetailerMetric

load_project_env()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

SCRIPT_VERSION = "retailer-metric-populate-A-v1.2"

INSTANT_METRICS = {"inventory_usd", "accounts_payable_usd", "cash_and_equivalents_usd", "total_debt_usd",
                   "current_assets_usd", "current_liabilities_usd", "accounts_receivable_usd"}
CUMULATIVE_METRICS = {"operating_cash_flow_usd", "capex_usd", "depreciation_amortization_usd"}
# Income-statement flows whose Q4 = annual − (Q1+Q2+Q3 discrete).
INCOME_FLOW_METRICS = {"merchandise_sales_usd", "total_revenue_usd", "cogs_usd", "sga_usd",
                       "operating_income_usd", "net_income_usd", "gross_profit_usd"}
_LTD_CURRENT_CONCEPTS = ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"]

_SLACK = 8


def _concepts_for(defn: MetricDefinition, ticker: str) -> list[str]:
    if not defn.xbrl_concepts_json:
        return []
    cfg = json.loads(defn.xbrl_concepts_json)
    return cfg.get(ticker.upper()) or cfg.get("default") or []


def _maps(ug: dict, concepts: list[str], instant: bool) -> list[tuple[str, dict]]:
    fn = instant_facts_by_end if instant else duration_facts_by_end
    return [(c, fn(ug, [c])) for c in concepts]


def _resolve(maps: list[tuple[str, dict]], end) -> tuple[Optional[Decimal], Optional[str]]:
    for concept, m in maps:
        v = _match(end, m)
        if v is not None:
            return v, concept
    return None, None


def annual_flows(ug: dict, concepts: list[str]) -> dict:
    """Annual (~365-day) flow facts keyed by END DATE → (value, concept). Concept priority
    then latest-filed. Keying by end date avoids the filing-cycle `fy` ambiguity."""
    best: dict = {}
    for rank, concept in enumerate(concepts):
        node = ug.get(concept)
        if not node:
            continue
        for unit_vals in node.get("units", {}).values():
            for f in unit_vals:
                s, e, v = _parse_date(f.get("start")), _parse_date(f.get("end")), f.get("val")
                if s is None or e is None or v is None:
                    continue
                if not (350 <= (e - s).days <= 380):
                    continue
                filed = str(f.get("filed") or "")
                cur = best.get(e)
                if cur is None or rank < cur[0] or (rank == cur[0] and filed > cur[1]):
                    best[e] = (rank, filed, Decimal(str(v)), concept)
    return {e: (b[2], b[3]) for e, b in best.items()}


def annual_flows_candidates(ug: dict, concepts: list[str]) -> dict:
    """ALL distinct annual (~365-day) flow values per END DATE (across every concept and
    filing) — e.g. a restated annual AND the original co-exist. Used to repair a Q4 = annual −
    sum(Q1..Q3) derivation when the default (latest-filed) annual is on a different basis than
    the original quarterly parts (e.g. Target's FY2013 credit-card divestiture restated the
    annual Revenues but not the quarterly), which would otherwise produce an inconsistent Q4."""
    out: dict = defaultdict(set)
    for concept in concepts:
        node = ug.get(concept)
        if not node:
            continue
        for unit_vals in node.get("units", {}).values():
            for f in unit_vals:
                s, e, v = _parse_date(f.get("start")), _parse_date(f.get("end")), f.get("val")
                if s is None or e is None or v is None:
                    continue
                if 350 <= (e - s).days <= 380:
                    out[e].add(Decimal(str(v)))
    return {e: sorted(vals) for e, vals in out.items()}


def cumulative_to_discrete(ug: dict, concepts: list[str]) -> dict:
    """Discrete-quarter values from cumulative (YTD) cash-flow facts: group by fiscal-year
    START and difference consecutive cumulatives (Q4 = FY − 9mo). Returns {end: (val, concept)}."""
    facts: dict = {}
    for rank, concept in enumerate(concepts):
        node = ug.get(concept)
        if not node:
            continue
        for unit_vals in node.get("units", {}).values():
            for f in unit_vals:
                s, e, v = _parse_date(f.get("start")), _parse_date(f.get("end")), f.get("val")
                if s is None or e is None or v is None:
                    continue
                filed = str(f.get("filed") or "")
                key = (s, e)
                cur = facts.get(key)
                if cur is None or rank < cur[1] or (rank == cur[1] and filed > cur[3]):
                    facts[key] = (Decimal(str(v)), rank, concept, filed)
    by_start: dict = defaultdict(list)
    for (s, e), (v, _rank, concept, _filed) in facts.items():
        by_start[s].append((e, v, concept))
    discrete: dict = {}
    for s, lst in by_start.items():
        lst.sort()
        prev_end, prev_val = None, Decimal("0")
        for (e, v, concept) in lst:
            if prev_end is None:
                if 80 <= (e - s).days <= 100:
                    discrete[e] = (v, concept)
            elif 80 <= (e - prev_end).days <= 100:
                discrete[e] = (v - prev_val, concept)
            prev_end, prev_val = e, v
    return discrete


def _match_discrete(end, dd: dict) -> tuple[Optional[Decimal], Optional[str]]:
    if end in dd:
        return dd[end]
    best, gap = (None, None), _SLACK + 1
    for e, t in dd.items():
        g = abs((e - end).days)
        if g < gap:
            best, gap = t, g
    return best if gap <= _SLACK else (None, None)


def _q(v: Decimal) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.0001"))


def _q4_income_flow(mk, fy, period_end, point_maps, annual_maps, grid_end):
    """Default Q4 = annual − sum(Q1..Q3) for an income-flow metric; None if unavailable.
    Used to compute the merchandise-sales floor (gross_profit + cogs) when repairing a
    restatement-inconsistent Q4 total_revenue."""
    am = annual_maps.get(mk) or {}
    ann = am.get(period_end)
    if ann is None:
        m = _match_discrete(period_end, am)
        ann = m if m[0] is not None else None
    maps = point_maps.get(mk)
    if ann is None or not maps:
        return None
    total = Decimal("0")
    for q in (1, 2, 3):
        e = grid_end.get((fy, q))
        if e is None:
            return None
        v, _c = _resolve(maps, e)
        if v is None:
            return None
        total += v
    return ann[0] - total


def _upsert(db, retailer_id, metric_key, unit, period, value, concept, url, dry_run) -> bool:
    fy, fq, ped, filed = period
    ledger = json.dumps({"source_type": "xbrl_companyfacts", "concept": concept,
                         "source_url": url, "confidence": "high", "script": SCRIPT_VERSION})
    val = _q(value)
    row = (db.query(RetailerMetric)
           .filter_by(retailer_id=retailer_id, metric_key=metric_key,
                      fiscal_year=fy, fiscal_quarter=fq, is_latest=True).first())
    if row:
        if row.value_numeric is not None and Decimal(str(row.value_numeric)) == val and row.source_concept == concept:
            return False
        if not dry_run:
            row.value_numeric, row.unit = val, unit
            row.source, row.source_concept, row.source_url = "sec_companyfacts", concept, url
            row.confidence, row.data_quality = Decimal("0.95"), ledger
        return True
    if not dry_run:
        db.add(RetailerMetric(
            retailer_id=retailer_id, metric_key=metric_key, fiscal_year=fy, fiscal_quarter=fq,
            period_end_date=ped, filing_date=filed,
            calendar_year=ped.year, calendar_quarter=(ped.month - 1) // 3 + 1,
            value_numeric=val, unit=unit, source="sec_companyfacts", source_concept=concept,
            source_url=url, confidence=Decimal("0.95"), data_quality=ledger,
            certified=False, is_latest=True))
    return True


def populate_retailer(db: Session, retailer: MajorRetailers, dry_run: bool) -> dict:
    ticker = (retailer.ticker or "").upper()
    cik = (retailer.cik or "").zfill(10)
    fj = _sec_get_json(COMPANYFACTS_URL.format(cik=cik))
    if not fj or not fj.get("facts"):
        logger.error("No companyfacts for %s", ticker)
        return {}
    ug = fj["facts"].get("us-gaap", {})
    url = COMPANYFACTS_URL.format(cik=cik)

    direct = (db.query(MetricDefinition)
              .filter(MetricDefinition.xbrl_concepts_json.isnot(None),
                      MetricDefinition.source_priority.in_(("xbrl", "xbrl_or_reported")))
              .all())

    point_maps, cum_maps, annual_maps, annual_cands, units = {}, {}, {}, {}, {}
    for defn in direct:
        concepts = _concepts_for(defn, ticker)
        if not concepts:
            continue
        units[defn.metric_key] = defn.unit
        if defn.metric_key in CUMULATIVE_METRICS:
            cum_maps[defn.metric_key] = cumulative_to_discrete(ug, concepts)
        else:
            point_maps[defn.metric_key] = _maps(ug, concepts, defn.metric_key in INSTANT_METRICS)
        if defn.metric_key in INCOME_FLOW_METRICS:
            annual_maps[defn.metric_key] = annual_flows(ug, concepts)
            annual_cands[defn.metric_key] = annual_flows_candidates(ug, concepts)
    ltd_current_maps = _maps(ug, _LTD_CURRENT_CONCEPTS, instant=True)

    # Grid from retailer_financials (Q1-Q3 + any existing Q4) ...
    grid_map = {(r.fiscal_year, r.fiscal_quarter): (r.period_end_date, r.filing_date)
                for r in db.query(RetailerFinancials)
                .filter(RetailerFinancials.retailer_id == retailer.retailer_id,
                        RetailerFinancials.is_latest.is_(True),
                        RetailerFinancials.period_end_date.isnot(None)).all()}
    # ... then add the missing Q4: its end = the annual fact ending ~one quarter after Q3.
    rev_annual_ends = sorted((annual_maps.get("total_revenue_usd") or {}).keys())
    for (fy, fq), (end, _filed) in list(grid_map.items()):
        if fq == 3 and (fy, 4) not in grid_map and end is not None:
            fye = next((e for e in rev_annual_ends if 80 <= (e - end).days <= 100), None)
            if fye:
                grid_map[(fy, 4)] = (fye, None)
    grid_end = {k: v[0] for k, v in grid_map.items()}

    written = unchanged = 0

    def emit(mk, fy, fq, period, value, concept):
        nonlocal written, unchanged
        if _upsert(db, retailer.retailer_id, mk, units.get(mk, "usd"), (fy, fq) + period, value, concept, url, dry_run):
            written += 1
        else:
            unchanged += 1

    for (fy, fq), (period_end, filing_date) in sorted(grid_map.items()):
        period = (period_end, filing_date)
        for mk, dd in cum_maps.items():                       # cash flow (incl. Q4 = FY−9mo)
            value, concept = _match_discrete(period_end, dd)
            if value is not None:
                emit(mk, fy, fq, period, value, concept)
        for mk, maps in point_maps.items():
            if fq == 4 and mk in INCOME_FLOW_METRICS:         # Q4 income = annual − (Q1+Q2+Q3)
                am = annual_maps.get(mk) or {}
                ann = am.get(period_end)
                if ann is None:
                    m = _match_discrete(period_end, am)   # nearest annual end within slack
                    ann = m if m[0] is not None else None
                if ann is None:
                    continue
                ann_val, ann_concept = ann
                parts, ok = [], True
                for q in (1, 2, 3):
                    e = grid_end.get((fy, q))
                    if e is None:
                        ok = False
                        break
                    v, _c = _resolve(maps, e)
                    if v is None:
                        ok = False
                        break
                    parts.append(v)
                if not ok:
                    continue
                q4val = ann_val - sum(parts)
                concept = f"{ann_concept or 'annual'}(FY)-sum(Q1..Q3)"
                if mk == "total_revenue_usd":
                    # Hard invariant: total revenue ≥ merchandise sales (gross_profit + cogs).
                    # A violation means the default annual is on a different basis than the
                    # original quarterly parts (e.g. a restated annual vs original quarters).
                    # Pick the annual candidate that restores consistency.
                    floor = _q4_income_flow("gross_profit_usd", fy, period_end, point_maps,
                                            annual_maps, grid_end)
                    cg = _q4_income_flow("cogs_usd", fy, period_end, point_maps,
                                         annual_maps, grid_end)
                    if floor is not None and cg is not None:
                        merch = floor + cg
                        if q4val < merch:
                            base = sum(parts)
                            fixed = sorted(c - base for c in annual_cands.get(mk, {}).get(period_end, [])
                                           if c - base >= merch)
                            if fixed:
                                q4val = fixed[0]
                                concept += "+restate-consistent"
                emit(mk, fy, fq, period, q4val, concept)
            elif mk == "total_debt_usd":
                noncurrent, concept = _resolve(maps, period_end)
                if noncurrent is None:
                    continue
                current = _match(period_end, ltd_current_maps[0][1]) or _match(period_end, ltd_current_maps[1][1]) or Decimal("0")
                emit(mk, fy, fq, period, noncurrent + current, f"{concept}+current")
            else:
                value, concept = _resolve(maps, period_end)
                if value is not None:
                    emit(mk, fy, fq, period, value, concept)

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return {"periods": len(grid_map), "written": written, "unchanged": unchanged}


def main() -> int:
    parser = argparse.ArgumentParser(description="Populate retailer_metric with direct SEC-XBRL facts")
    parser.add_argument("--retailer", metavar="TICKER")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        q = db.query(MajorRetailers).filter(MajorRetailers.cik.isnot(None))
        if args.retailer:
            q = q.filter(MajorRetailers.ticker == args.retailer.upper())
        else:
            have = {rid for (rid,) in db.query(RetailerFinancials.retailer_id)
                    .filter(RetailerFinancials.is_latest.is_(True)).distinct()}
            q = q.filter(MajorRetailers.retailer_id.in_(have))
        for retailer in q.all():
            s = populate_retailer(db, retailer, args.dry_run)
            mode = "DRY-RUN" if args.dry_run else "WROTE"
            print(f"{mode} {retailer.name} [{retailer.ticker}]: {s.get('written', 0)} rows "
                  f"written/changed, {s.get('unchanged', 0)} unchanged, over {s.get('periods', 0)} periods")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

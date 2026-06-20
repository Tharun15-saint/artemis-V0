"""
Derived-metrics pass (Pass B): compute ratios STRICTLY on top of the direct SEC
facts already in retailer_metric. Never re-reads SEC; never invents inputs.

Each derived row records its formula and the exact input values in the provenance
ledger, so any number can be re-checked by hand. A metric is written only when ALL
its inputs exist for that period — otherwise it's an honest gap.

Computed here:
  gross_profit_usd (where GrossProfit absent)   = merchandise_sales - cogs
  gross_margin_pct (XBRL-derivable retailers)   = gross_profit / merchandise_sales x100
  operating_margin_pct                          = operating_income / total_revenue x100
  net_margin_pct                                = net_income / total_revenue x100
  sga_rate_pct                                  = sga / total_revenue x100
  free_cash_flow_usd                            = operating_cash_flow - capex
  inventory_turnover                            = ttm_cogs / inventory
  inventory_days (DIO)                          = inventory / (ttm_cogs / 365)
  inventory_to_sales_ratio                      = inventory / merchandise_sales
  days_payable_outstanding                      = accounts_payable / (ttm_cogs / 365)
  inventory_vs_sales_growth_gap_pct             = inventory_yoy% - sales_yoy%
  gross_margin_change_bps                       = (gm - gm_prior_year) x100

Deferred (need inputs not yet captured — flagged, NOT faked):
  ebitda_usd / debt_to_ebitda  → need quarterly D&A
  current_ratio                → need current assets / current liabilities
  cash_conversion_cycle_days   → need DSO (receivables)
"""

from __future__ import annotations

import argparse
import json
import logging
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from data.verification.retail_financials_reconcile import RETAILER_PROFILES, DEFAULT_PROFILE
from database.base import SessionLocal
from database.models.retail import MajorRetailers, RetailerFinancials
from database.models.retail_metrics import MetricDefinition, RetailerMetric

load_project_env()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

SCRIPT_VERSION = "retailer-metric-derive-B-v1.0"
_TTM_WINDOW_DAYS = 371


def _q(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.0001"))


class Facts:
    """In-memory view of a retailer's direct metric facts, keyed for derivation."""
    def __init__(self, db: Session, retailer_id: int):
        self.val: dict = {}            # (metric_key, fy, fq) -> Decimal
        self.period: dict = {}         # (fy, fq) -> (period_end, filing_date)
        rows = (db.query(RetailerMetric)
                .filter(RetailerMetric.retailer_id == retailer_id, RetailerMetric.is_latest.is_(True))
                .all())
        for r in rows:
            self.val[(r.metric_key, r.fiscal_year, r.fiscal_quarter)] = Decimal(str(r.value_numeric))
            self.period[(r.fiscal_year, r.fiscal_quarter)] = (r.period_end_date, r.filing_date)
    def get(self, mk, fy, fq) -> Optional[Decimal]:
        return self.val.get((mk, fy, fq))

    def ttm(self, metric_key, period_end) -> Optional[Decimal]:
        """Trailing-12-month sum: the 4 quarters ending at period_end. Returns None unless
        they are 4 CONSECUTIVE quarters (each ~one quarter apart) — a missing quarter (e.g.
        an absent Q4) would otherwise silently sum the wrong four and corrupt the ratio."""
        ends = [(self.period[(fy, fq)][0], v) for (mk, fy, fq), v in self.val.items()
                if mk == metric_key and (fy, fq) in self.period
                and self.period[(fy, fq)][0] is not None
                and 0 <= (period_end - self.period[(fy, fq)][0]).days < _TTM_WINDOW_DAYS]
        ends.sort(reverse=True)
        ends = ends[:4]
        if len(ends) < 4:
            return None
        for i in range(3):                       # every consecutive gap must be ~one quarter
            if not (80 <= (ends[i][0] - ends[i + 1][0]).days <= 100):
                return None
        return sum(v for _e, v in ends)


def _upsert(db: Session, retailer_id, metric_key, unit, fy, fq, period, value, formula, inputs, dry_run) -> bool:
    period_end, filing_date = period
    ledger = json.dumps({"source_type": "derived", "formula": formula,
                         "inputs": {k: str(v) for k, v in inputs.items()},
                         "script": SCRIPT_VERSION})
    val = _q(value)
    row = (db.query(RetailerMetric)
           .filter_by(retailer_id=retailer_id, metric_key=metric_key,
                      fiscal_year=fy, fiscal_quarter=fq, is_latest=True).first())
    if row:
        if row.value_numeric is not None and Decimal(str(row.value_numeric)) == val and row.source == "derived":
            return False
        if not dry_run:
            row.value_numeric, row.unit, row.source = val, unit, "derived"
            row.source_concept, row.source_url = formula, "internal:derived"
            row.confidence, row.data_quality = Decimal("0.90"), ledger
        return True
    if not dry_run:
        db.add(RetailerMetric(
            retailer_id=retailer_id, metric_key=metric_key, fiscal_year=fy, fiscal_quarter=fq,
            period_end_date=period_end, filing_date=filing_date,
            calendar_year=period_end.year, calendar_quarter=(period_end.month - 1) // 3 + 1,
            value_numeric=val, unit=unit, source="derived", source_concept=formula,
            source_url="internal:derived", confidence=Decimal("0.90"), data_quality=ledger,
            certified=False, is_latest=True))
    return True


def derive_retailer(db: Session, retailer: MajorRetailers, dry_run: bool) -> dict:
    ticker = (retailer.ticker or "").upper()
    # Clean slate: derived metrics are pure functions of the certified direct facts, so
    # wipe and recompute. This guarantees no stale/invalid derived value can survive a
    # formula or TTM-validity fix (a None result writes nothing, so in-place upsert alone
    # would leave the old wrong row behind).
    if not dry_run:
        db.query(RetailerMetric).filter_by(retailer_id=retailer.retailer_id, source="derived").delete()
        db.flush()
    f = Facts(db, retailer.retailer_id)
    units = {d.metric_key: d.unit for d in db.query(MetricDefinition).all()}
    profile = RETAILER_PROFILES.get(ticker, DEFAULT_PROFILE)
    derive_gm = profile.get("verify_gross_margin", True)  # only where gm is XBRL-derivable

    written = unchanged = 0

    def put(mk, fy, fq, period, value, formula, inputs):
        nonlocal written, unchanged
        if value is None:
            return
        if _upsert(db, retailer.retailer_id, mk, units.get(mk, "ratio"), fy, fq, period, value, formula, inputs, dry_run):
            written += 1
        else:
            unchanged += 1

    for (fy, fq), (period_end, filing_date) in f.period.items():
        period = (period_end, filing_date)
        merch = f.get("merchandise_sales_usd", fy, fq)
        total = f.get("total_revenue_usd", fy, fq)
        cogs = f.get("cogs_usd", fy, fq)
        gp = f.get("gross_profit_usd", fy, fq)
        oi = f.get("operating_income_usd", fy, fq)
        ni = f.get("net_income_usd", fy, fq)
        sga = f.get("sga_usd", fy, fq)
        ocf = f.get("operating_cash_flow_usd", fy, fq)
        capex = f.get("capex_usd", fy, fq)
        inv = f.get("inventory_usd", fy, fq)
        ap = f.get("accounts_payable_usd", fy, fq)
        da = f.get("depreciation_amortization_usd", fy, fq)
        ca = f.get("current_assets_usd", fy, fq)
        cl = f.get("current_liabilities_usd", fy, fq)
        ar = f.get("accounts_receivable_usd", fy, fq)

        # gross profit (only when not directly reported)
        if gp is None and merch is not None and cogs is not None:
            gp = merch - cogs
            put("gross_profit_usd", fy, fq, period, gp, "merchandise_sales_usd - cogs_usd",
                {"merchandise_sales_usd": merch, "cogs_usd": cogs})

        if derive_gm and gp is not None and merch:
            put("gross_margin_pct", fy, fq, period, gp / merch * 100, "gross_profit_usd / merchandise_sales_usd x100",
                {"gross_profit_usd": gp, "merchandise_sales_usd": merch})
        if oi is not None and total:
            put("operating_margin_pct", fy, fq, period, oi / total * 100, "operating_income_usd / total_revenue_usd x100",
                {"operating_income_usd": oi, "total_revenue_usd": total})
        if ni is not None and total:
            put("net_margin_pct", fy, fq, period, ni / total * 100, "net_income_usd / total_revenue_usd x100",
                {"net_income_usd": ni, "total_revenue_usd": total})
        if sga is not None and total:
            put("sga_rate_pct", fy, fq, period, sga / total * 100, "sga_usd / total_revenue_usd x100",
                {"sga_usd": sga, "total_revenue_usd": total})
        if ocf is not None and capex is not None:
            put("free_cash_flow_usd", fy, fq, period, ocf - capex, "operating_cash_flow_usd - capex_usd",
                {"operating_cash_flow_usd": ocf, "capex_usd": capex})

        ttm_cogs = f.ttm("cogs_usd", period_end)
        ttm_rev = f.ttm("total_revenue_usd", period_end)
        if ttm_cogs and inv:
            put("inventory_turnover", fy, fq, period, ttm_cogs / inv, "ttm_cogs / inventory_usd",
                {"ttm_cogs": ttm_cogs, "inventory_usd": inv})
            put("inventory_days", fy, fq, period, inv / (ttm_cogs / 365), "inventory_usd / (ttm_cogs/365)",
                {"inventory_usd": inv, "ttm_cogs": ttm_cogs})
        if ttm_cogs and ap is not None:
            put("days_payable_outstanding", fy, fq, period, ap / (ttm_cogs / 365), "accounts_payable_usd / (ttm_cogs/365)",
                {"accounts_payable_usd": ap, "ttm_cogs": ttm_cogs})
        if oi is not None and da is not None:
            put("ebitda_usd", fy, fq, period, oi + da, "operating_income_usd + depreciation_amortization_usd",
                {"operating_income_usd": oi, "depreciation_amortization_usd": da})
        if ca is not None and cl:
            put("current_ratio", fy, fq, period, ca / cl, "current_assets_usd / current_liabilities_usd",
                {"current_assets_usd": ca, "current_liabilities_usd": cl})
        if inv and ap is not None and ar is not None and ttm_cogs and ttm_rev:
            dio = inv / (ttm_cogs / 365)
            dpo = ap / (ttm_cogs / 365)
            dso = ar / (ttm_rev / 365)
            put("cash_conversion_cycle_days", fy, fq, period, dio + dso - dpo, "DIO + DSO - DPO",
                {"dio": _q(dio), "dso": _q(dso), "dpo": _q(dpo)})
        if inv and merch:
            put("inventory_to_sales_ratio", fy, fq, period, inv / merch, "inventory_usd / merchandise_sales_usd",
                {"inventory_usd": inv, "merchandise_sales_usd": merch})

        # YoY-based: same fiscal quarter, prior year
        inv_py = f.get("inventory_usd", fy - 1, fq)
        sales_py = f.get("merchandise_sales_usd", fy - 1, fq)
        if inv is not None and inv_py:
            inv_yoy = (inv - inv_py) / inv_py * 100
            if merch is not None and sales_py:
                sales_yoy = (merch - sales_py) / sales_py * 100
                put("inventory_vs_sales_growth_gap_pct", fy, fq, period, inv_yoy - sales_yoy,
                    "inventory_yoy% - merchandise_sales_yoy%",
                    {"inventory_yoy_pct": _q(inv_yoy), "sales_yoy_pct": _q(sales_yoy)})

    # Second pass: metrics that need prior-year, or a ttm of a derived value (ebitda).
    # The session is autoflush=False, so explicitly flush pass-1 derived rows first —
    # otherwise the reload below can't see them and these metrics silently come up empty.
    db.flush()
    f2 = Facts(db, retailer.retailer_id)
    for (fy, fq), (period_end, filing_date) in f2.period.items():
        period2 = (period_end, filing_date)
        # gm change applies wherever gross_margin_pct exists — derived (Walmart) OR reported
        # (Target). Requires this pass run AFTER the reported migration (pipeline order:
        # populate -> migrate -> derive -> certify -> coverage).
        gm = f2.get("gross_margin_pct", fy, fq)
        gm_py = f2.get("gross_margin_pct", fy - 1, fq)
        if gm is not None and gm_py is not None:
            put("gross_margin_change_bps", fy, fq, period2, (gm - gm_py) * 100,
                "(gross_margin_pct - prior_year) x100", {"gm": gm, "gm_prior_year": gm_py})
        td = f2.get("total_debt_usd", fy, fq)
        ttm_eb = f2.ttm("ebitda_usd", period_end)
        if td is not None and ttm_eb and ttm_eb > 0:
            put("debt_to_ebitda", fy, fq, period2, td / ttm_eb, "total_debt_usd / ttm_ebitda",
                {"total_debt_usd": td, "ttm_ebitda": ttm_eb})

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return {"written": written, "unchanged": unchanged}


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute derived retailer metrics on certified facts")
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
            s = derive_retailer(db, retailer, args.dry_run)
            mode = "DRY-RUN" if args.dry_run else "WROTE"
            print(f"{mode} {retailer.name} [{retailer.ticker}]: {s['written']} derived rows written/changed, {s['unchanged']} unchanged")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

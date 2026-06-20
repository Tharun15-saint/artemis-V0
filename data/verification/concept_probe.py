"""
Concept probe — calibrate which SEC XBRL concept a retailer uses for each metric
BEFORE trusting (or recomputing) its rows. Different retailers tag the same
economic figure differently (e.g. Walmart net sales = RevenueFromContract...;
Target net sales = merchandise SalesRevenueNet, while its RevenueFromContract
includes credit/other revenue). Run this when onboarding any new retailer.

Usage: python -m data.verification.concept_probe TGT 2017-04-29 2022-07-30
"""

from __future__ import annotations

import sys
from datetime import date

from data.verification.retail_financials_reconcile import (
    COMPANYFACTS_URL, _match, _sec_get_json, duration_facts_by_end, instant_facts_by_end,
)
from database.base import SessionLocal
from database.models.retail import MajorRetailers, RetailerFinancials

REV = ["RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
       "SalesRevenueGoodsNet", "Revenues", "RevenueFromContractWithCustomerIncludingAssessedTax"]
COGS = ["CostOfGoodsSold", "CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSoldAndServicesSold"]
GP = ["GrossProfit"]
INV = ["InventoryNet"]


def main() -> int:
    ticker = sys.argv[1].upper()
    ends = [date.fromisoformat(s) for s in sys.argv[2:]]
    db = SessionLocal()
    r = db.query(MajorRetailers).filter_by(ticker=ticker).first()
    fj = _sec_get_json(COMPANYFACTS_URL.format(cik=r.cik.zfill(10)))
    ug = fj["facts"]["us-gaap"]
    rev = {c: duration_facts_by_end(ug, [c]) for c in REV}
    cogs = {c: duration_facts_by_end(ug, [c]) for c in COGS}
    gp = duration_facts_by_end(ug, GP)
    inv = instant_facts_by_end(ug, INV)

    for e in ends:
        print(f"\n===== {ticker} END {e} =====")
        for c in REV:
            v = _match(e, rev[c])
            if v is not None:
                print(f"  REV  {c:58s} {v}")
        for c in COGS:
            v = _match(e, cogs[c])
            if v is not None:
                print(f"  COGS {c:58s} {v}")
        g = _match(e, gp)
        if g is not None:
            print(f"  GP   GrossProfit{'':47s} {g}")
        iv = _match(e, inv)
        if iv is not None:
            print(f"  INV  InventoryNet{'':46s} {iv}")
        row = (db.query(RetailerFinancials)
               .filter_by(retailer_id=r.retailer_id, is_latest=True, period_end_date=e).first())
        if row:
            print(f"  STORED sales={row.total_net_sales_usd} gm={row.gross_margin_pct} inv={row.inventory_usd}")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

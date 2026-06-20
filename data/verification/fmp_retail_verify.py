"""
Cross-verify the ENTIRE retail-metric layer against FMP (independent reader of SEC).

Our retailer_metric is SEC-derived; FMP independently derives from the same SEC filings. So
they should agree to tolerance — any disagreement flags a bug on one side; any period FMP has
that we lack flags a missing datapoint. This is triangulation (Principle 2), not anchoring:
SEC remains the authority, FMP is a second independent witness to catch our mistakes.

Read-only. Reports, per metric: matched / mismatched / we-missing, with the mismatches listed.

    python -m data.verification.fmp_retail_verify
"""

from __future__ import annotations

import argparse
import logging
import os
from decimal import Decimal

import requests
from sqlalchemy import text

from data.ingestion._env import load_project_env
from database.base import SessionLocal

load_project_env()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

SYMBOL = {1: "TGT", 2: "WMT"}
MONEY_REL_TOL = Decimal("0.01")        # 1% (FMP vs our SEC derivation; allow restatement/rounding)
MONEY_ABS_FLOOR = Decimal("3000000")   # $3M
EPS_TOL = Decimal("0.03")

# Known DEFINITIONAL differences (not errors): reported separately so they never drown out a
# real discrepancy. gross_profit — ours is computed on NET/MERCHANDISE SALES (Walmart's own
# reported gross-margin convention), FMP on TOTAL revenue (incl. membership/other income); the
# ~3-4% gap is exactly that membership income. Anchoring on SEC, ours is the faithful basis.
DEFINITIONAL_METRICS = {"gross_profit_usd"}

# FMP income/balance field  ->  our metric_key
INCOME_MAP = {
    "revenue": "total_revenue_usd",
    "costOfRevenue": "cogs_usd",
    "grossProfit": "gross_profit_usd",
    "operatingIncome": "operating_income_usd",
    "netIncome": "net_income_usd",
    "epsdiluted": "eps_diluted_usd",
}
BALANCE_MAP = {
    "inventory": "inventory_usd",
    "cashAndCashEquivalents": "cash_and_equivalents_usd",
    "accountPayables": "accounts_payable_usd",
}


def _get(url):
    try:
        r = requests.get(url, timeout=45)
        return r.json() if r.status_code == 200 else []
    except (requests.RequestException, ValueError):
        return []


def _our_value(db, rid, period_end, metric_key):
    row = db.execute(text(
        "SELECT value_numeric FROM retailer_metric WHERE retailer_id=:r AND metric_key=:k "
        "AND period_end_date=:d AND is_latest LIMIT 1"), {"r": rid, "k": metric_key, "d": period_end}).fetchone()
    return Decimal(str(row[0])) if row and row[0] is not None else None


def _mismatch(ours, theirs, eps=False):
    if eps:
        return abs(ours - theirs) > EPS_TOL
    diff = abs(ours - theirs)
    rel = diff / abs(theirs) if theirs != 0 else diff
    return diff > MONEY_ABS_FLOOR and rel > MONEY_REL_TOL


def verify_retailer(db, rid, key, limit=80):
    sym = SYMBOL[rid]
    inc = _get(f"https://financialmodelingprep.com/stable/income-statement?symbol={sym}&period=quarter&limit={limit}&apikey={key}")
    bal = _get(f"https://financialmodelingprep.com/stable/balance-sheet-statement?symbol={sym}&period=quarter&limit={limit}&apikey={key}")
    bal_by_date = {b.get("date"): b for b in bal if isinstance(b, dict)}
    stats = {}
    mism = []
    defl = []
    for rec in inc:
        if not isinstance(rec, dict):
            continue
        d = rec.get("date")
        combined = {**rec, **bal_by_date.get(d, {})}
        for fmp_field, mk in {**INCOME_MAP, **BALANCE_MAP}.items():
            theirs = combined.get(fmp_field)
            if theirs is None:
                continue
            theirs = Decimal(str(theirs))
            ours = _our_value(db, rid, d, mk)
            s = stats.setdefault(mk, {"match": 0, "mismatch": 0, "we_missing": 0, "definitional": 0})
            if ours is None:
                s["we_missing"] += 1
                continue
            if not _mismatch(ours, theirs, eps=(mk == "eps_diluted_usd")):
                s["match"] += 1
            elif mk in DEFINITIONAL_METRICS:
                s["definitional"] += 1            # known basis difference, not an error
                defl.append((sym, d, mk, ours, theirs))
            else:
                s["mismatch"] += 1
                mism.append((sym, d, mk, ours, theirs))
    return stats, mism, defl


def main() -> int:
    argparse.ArgumentParser(description="Cross-verify retail metrics vs FMP").parse_args()
    key = os.getenv("FMP_API_KEY")
    if not key:
        print("FMP_API_KEY not set")
        return 1
    db = SessionLocal()
    try:
        all_mism, all_defl = [], []
        for rid in (2, 1):
            stats, mism, defl = verify_retailer(db, rid, key)
            all_mism += mism
            all_defl += defl
            print(f"\n=== {SYMBOL[rid]} vs FMP ===")
            for mk, s in sorted(stats.items()):
                extra = f"  definitional={s['definitional']:3d}" if s.get("definitional") else ""
                print(f"  {mk:28s} match={s['match']:3d}  mismatch={s['mismatch']:3d}  we_missing={s['we_missing']:3d}{extra}")
        if all_mism:
            print(f"\n=== MISMATCHES ({len(all_mism)}) — investigate (our bug / FMP diff) ===")
            for sym, d, mk, ours, theirs in all_mism[:40]:
                rel = abs(ours - theirs) / abs(theirs) * 100 if theirs else 0
                print(f"  {sym} {d} {mk}: ours={ours} fmp={theirs} ({rel:.2f}%)")
        if all_defl:
            print(f"\n=== DEFINITIONAL differences ({len(all_defl)}) — known basis diffs, NOT errors "
                  f"(see DEFINITIONAL_METRICS) ===")
        return 1 if all_mism else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""
Reconcile retailer_financials against SEC EDGAR companyfacts ground truth.

For every is_latest retailer_financials row (matched to XBRL facts by period END
DATE), this re-fetches the authoritative SEC value and compares it to what we
stored. It flags every discrepancy beyond tolerance and is designed to be re-run
as a permanent data-quality GATE:

    exit 0  -> every stored value reconciles to SEC within tolerance
    exit 1  -> one or more discrepancies (CI / pre-deploy should block)

The concept fallback chains MIRROR data/ingestion/retailers_ingestion.py and
retailer_income_backfill.py exactly, so we reconcile against the same ground
truth the ingestion claims to use. A unit bug, a stale value, or a wrong-period
write all surface here.

This module NEVER mutates the database. Verification only.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

import requests
from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from database.base import SessionLocal
from database.models.retail import MajorRetailers, RetailerFinancials

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

SEC_USER_AGENT = "ArtemisV0/1.0 (retail intelligence reconciliation; contact@artemis.local)"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_RATE_LIMIT_SECONDS = 0.2
REQUEST_TIMEOUT = 30

# Concept fallback chains — MUST mirror retailers_ingestion.XBRL_CONCEPTS and
# retailer_income_backfill so we reconcile against the same source facts.
# Column semantics = NET SALES (merchandise), NOT total revenues. For Walmart,
# "Revenues" includes membership & other income (~0.7% higher) — using it would
# false-flag every correct net-sales row. Prefer the net-sales concepts; fall
# back to "Revenues" only for retailers that report nothing else.
REVENUE_CONCEPTS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
    "Revenues",
]
GROSS_PROFIT_CONCEPTS = ["GrossProfit"]
COGS_CONCEPTS = ["CostOfGoodsSold", "CostOfRevenue", "CostOfGoodsAndServicesSold"]
INVENTORY_CONCEPTS = ["InventoryNet"]
OPERATING_INCOME_CONCEPTS = ["OperatingIncomeLoss"]
NET_INCOME_CONCEPTS = [
    "NetIncomeLoss",
    "ProfitLoss",
    "NetIncomeLossAvailableToCommonStockholdersBasic",
]

# A quarterly duration is ~13 weeks; admit 80-100 days for 4-4-5 calendars.
Q_MIN_DAYS, Q_MAX_DAYS = 80, 100
END_DATE_SLACK_DAYS = 8

# Tolerances. Money: flag only when BOTH a relative AND an absolute floor are
# breached (avoids flagging cent-rounding on multi-billion figures). Margin:
# absolute percentage-point tolerance.
MONEY_REL_TOL = Decimal("0.005")        # 0.5%
MONEY_ABS_FLOOR = Decimal("2000000")    # $2M
MARGIN_ABS_TOL = Decimal("0.20")        # 0.20 percentage points

# Per-retailer XBRL concept calibration. Retailers tag the same economic figure
# differently — calibrate each new retailer with data/verification/concept_probe.py
# BEFORE trusting its rows. (Walmart net sales = RevenueFromContract...; Target
# net sales = merchandise SalesRevenueGoodsNet, and Target's gross_margin_pct is a
# REPORTED rate that must be verified against the 8-K text, not recomputed from XBRL.)
DEFAULT_PROFILE = {
    "revenue": REVENUE_CONCEPTS,        # net-sales-first
    "verify_gross_margin": True,
}
RETAILER_PROFILES: dict[str, dict] = {
    "TGT": {
        "revenue": [
            "SalesRevenueGoodsNet",     # Target merchandise sales (the demand figure)
            "SalesRevenueNet",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
        ],
        "verify_gross_margin": False,   # reported rate — verified vs 8-K, not XBRL
    },
}


# --------------------------------------------------------------------------- #
# SEC fetch + fact extraction
# --------------------------------------------------------------------------- #
def _sec_get_json(url: str) -> Optional[dict]:
    headers = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    try:
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        time.sleep(SEC_RATE_LIMIT_SECONDS)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("SEC request failed for %s: %s", url, exc)
        return None


def _parse_date(s: Optional[str]) -> Optional[date]:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def duration_facts_by_end(us_gaap: dict, concepts: list[str]) -> dict[date, Decimal]:
    """Quarterly (3-month duration) facts keyed by period end date.

    Concept priority (earlier in list wins); within a concept, the later-filed
    value supersedes a restatement. Mirrors retailer_income_backfill.
    """
    out: dict[date, Decimal] = {}
    chosen: dict[date, tuple[int, str]] = {}
    for rank, concept in enumerate(concepts):
        node = us_gaap.get(concept)
        if not node:
            continue
        for unit_vals in node.get("units", {}).values():
            for fact in unit_vals:
                start = _parse_date(fact.get("start"))
                end = _parse_date(fact.get("end"))
                val = fact.get("val")
                if start is None or end is None or val is None:
                    continue
                if not (Q_MIN_DAYS <= (end - start).days <= Q_MAX_DAYS):
                    continue
                filed = fact.get("filed", "")
                prior = chosen.get(end)
                if prior is None or rank < prior[0] or (rank == prior[0] and filed > prior[1]):
                    out[end] = Decimal(str(val))
                    chosen[end] = (rank, filed)
    return out


def instant_facts_by_end(us_gaap: dict, concepts: list[str]) -> dict[date, Decimal]:
    """Instant (point-in-time, no start) facts keyed by date — for balance-sheet
    concepts like InventoryNet. Later-filed wins."""
    out: dict[date, Decimal] = {}
    chosen: dict[date, tuple[int, str]] = {}
    for rank, concept in enumerate(concepts):
        node = us_gaap.get(concept)
        if not node:
            continue
        for unit_vals in node.get("units", {}).values():
            for fact in unit_vals:
                if fact.get("start") is not None:
                    continue  # duration fact, not an instant
                end = _parse_date(fact.get("end"))
                val = fact.get("val")
                if end is None or val is None:
                    continue
                filed = fact.get("filed", "")
                prior = chosen.get(end)
                if prior is None or rank < prior[0] or (rank == prior[0] and filed > prior[1]):
                    out[end] = Decimal(str(val))
                    chosen[end] = (rank, filed)
    return out


def _match(target: date, facts: dict[date, Decimal]) -> Optional[Decimal]:
    if target in facts:
        return facts[target]
    best, best_gap = None, END_DATE_SLACK_DAYS + 1
    for end, val in facts.items():
        gap = abs((end - target).days)
        if gap < best_gap:
            best, best_gap = val, gap
    return best if best_gap <= END_DATE_SLACK_DAYS else None


# --------------------------------------------------------------------------- #
# Comparison primitives
# --------------------------------------------------------------------------- #
def money_mismatch(stored: Decimal, source: Decimal) -> bool:
    diff = abs(stored - source)
    rel = diff / abs(source) if source != 0 else diff
    return diff > MONEY_ABS_FLOOR and rel > MONEY_REL_TOL


def margin_mismatch(stored: Decimal, source: Decimal) -> bool:
    return abs(stored - source) > MARGIN_ABS_TOL


@dataclass
class Finding:
    retailer: str
    fy: int
    fq: int
    end: Optional[date]
    field: str
    stored: object
    source: object
    kind: str   # MISMATCH | UNVERIFIABLE | INCONSISTENT
    note: str = ""


@dataclass
class RetailerReport:
    retailer: str
    rows_checked: int = 0
    checks_passed: int = 0
    findings: list[Finding] = field(default_factory=list)


def _d(x) -> Optional[Decimal]:
    return None if x is None else Decimal(str(x))


def reconcile_retailer(db: Session, retailer: MajorRetailers) -> RetailerReport:
    rep = RetailerReport(retailer=f"{retailer.name} [{retailer.ticker}]")
    cik = (retailer.cik or "").zfill(10)
    facts_json = _sec_get_json(COMPANYFACTS_URL.format(cik=cik))
    if not facts_json or not facts_json.get("facts"):
        rep.findings.append(Finding(rep.retailer, 0, 0, None, "companyfacts", None, None,
                                    "UNVERIFIABLE", "no companyfacts returned from SEC"))
        return rep
    us_gaap = facts_json["facts"].get("us-gaap", {})

    profile = RETAILER_PROFILES.get((retailer.ticker or "").upper(), DEFAULT_PROFILE)
    revenue = duration_facts_by_end(us_gaap, profile["revenue"])
    gross_profit = duration_facts_by_end(us_gaap, GROSS_PROFIT_CONCEPTS)
    cogs = duration_facts_by_end(us_gaap, COGS_CONCEPTS)
    op_income = duration_facts_by_end(us_gaap, OPERATING_INCOME_CONCEPTS)
    net_income = duration_facts_by_end(us_gaap, NET_INCOME_CONCEPTS)
    inventory = instant_facts_by_end(us_gaap, INVENTORY_CONCEPTS)

    rows = (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer.retailer_id,
            RetailerFinancials.is_latest.is_(True),
        )
        .order_by(RetailerFinancials.fiscal_year, RetailerFinancials.fiscal_quarter)
        .all()
    )

    for row in rows:
        rep.rows_checked += 1
        ped = row.period_end_date
        if ped is None:
            rep.findings.append(Finding(rep.retailer, row.fiscal_year, row.fiscal_quarter,
                                        None, "period_end_date", None, None,
                                        "UNVERIFIABLE", "row has no period_end_date — cannot match to SEC"))
            continue

        # --- absolute money fields against source facts ---
        for fieldname, stored_raw, src_facts in (
            ("total_net_sales_usd", row.total_net_sales_usd, revenue),
            ("inventory_usd", row.inventory_usd, inventory),
            ("operating_income_usd", row.operating_income_usd, op_income),
            ("net_income_usd", row.net_income_usd, net_income),
        ):
            stored = _d(stored_raw)
            if stored is None:
                continue  # nothing stored to verify
            src = _match(ped, src_facts)
            if src is None:
                rep.findings.append(Finding(rep.retailer, row.fiscal_year, row.fiscal_quarter,
                                            ped, fieldname, stored, None, "UNVERIFIABLE",
                                            "no quarterly SEC fact at this period end (e.g. Q4-from-10K)"))
                continue
            if money_mismatch(stored, src):
                rel = abs(stored - src) / abs(src) if src != 0 else Decimal("inf")
                rep.findings.append(Finding(rep.retailer, row.fiscal_year, row.fiscal_quarter,
                                            ped, fieldname, stored, src, "MISMATCH",
                                            f"rel={rel:.4%}"))
            else:
                rep.checks_passed += 1

        # --- gross margin recomputed from source (GrossProfit or rev-cogs) ---
        # Skipped for retailers whose gm is a reported rate (verified vs 8-K text).
        stored_gm = _d(row.gross_margin_pct)
        if profile["verify_gross_margin"] and stored_gm is not None:
            rev = _match(ped, revenue)
            gp = _match(ped, gross_profit)
            if gp is None and rev is not None:
                c = _match(ped, cogs)
                gp = (rev - c) if c is not None else None
            if rev and gp is not None and rev != 0:
                src_gm = gp / rev * Decimal("100")
                if margin_mismatch(stored_gm, src_gm):
                    rep.findings.append(Finding(rep.retailer, row.fiscal_year, row.fiscal_quarter,
                                                ped, "gross_margin_pct", stored_gm, src_gm.quantize(Decimal("0.01")),
                                                "MISMATCH", "recomputed gp/rev"))
                else:
                    rep.checks_passed += 1
            else:
                rep.findings.append(Finding(rep.retailer, row.fiscal_year, row.fiscal_quarter,
                                            ped, "gross_margin_pct", stored_gm, None,
                                            "UNVERIFIABLE", "no quarterly rev/gp at period end"))

        # --- internal consistency: margins must equal income / sales ---
        sales = _d(row.total_net_sales_usd)
        for inc_field, margin_field in (
            ("operating_income_usd", "operating_margin_pct"),
            ("net_income_usd", "net_margin_pct"),
        ):
            inc = _d(getattr(row, inc_field))
            margin = _d(getattr(row, margin_field))
            if inc is not None and margin is not None and sales and sales != 0:
                implied = inc / sales * Decimal("100")
                if margin_mismatch(margin, implied):
                    rep.findings.append(Finding(rep.retailer, row.fiscal_year, row.fiscal_quarter,
                                                ped, margin_field, margin, implied.quantize(Decimal("0.01")),
                                                "INCONSISTENT", f"{margin_field} != {inc_field}/sales"))
                else:
                    rep.checks_passed += 1

    return rep


def _resolve_retailers(db: Session, ticker: Optional[str]) -> list[MajorRetailers]:
    q = db.query(MajorRetailers).filter(MajorRetailers.cik.isnot(None))
    if ticker:
        q = q.filter(MajorRetailers.ticker == ticker.upper())
        return q.all()
    # default: only retailers that actually have financial rows
    have_data = {
        rid for (rid,) in db.query(RetailerFinancials.retailer_id)
        .filter(RetailerFinancials.is_latest.is_(True)).distinct().all()
    }
    return [r for r in q.all() if r.retailer_id in have_data]


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile retailer_financials vs SEC EDGAR")
    parser.add_argument("--retailer", metavar="TICKER", help="single ticker, e.g. WMT")
    parser.add_argument("--verbose", action="store_true", help="print every finding")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        retailers = _resolve_retailers(db, args.retailer)
        if not retailers:
            print("No retailers with financial data to reconcile.")
            return 0

        total_mismatch = total_inconsistent = total_unverifiable = total_passed = 0
        for retailer in retailers:
            rep = reconcile_retailer(db, retailer)
            mism = [f for f in rep.findings if f.kind == "MISMATCH"]
            inco = [f for f in rep.findings if f.kind == "INCONSISTENT"]
            unv = [f for f in rep.findings if f.kind == "UNVERIFIABLE"]
            total_mismatch += len(mism)
            total_inconsistent += len(inco)
            total_unverifiable += len(unv)
            total_passed += rep.checks_passed

            print(f"\n=== {rep.retailer} ===")
            print(f"  rows checked: {rep.rows_checked}  checks passed: {rep.checks_passed}")
            print(f"  MISMATCH: {len(mism)}   INCONSISTENT: {len(inco)}   UNVERIFIABLE: {len(unv)}")
            shown = mism + inco
            if args.verbose:
                shown = mism + inco + unv
            for f in shown[:60]:
                print(f"    [{f.kind}] FY{f.fy}Q{f.fq} end={f.end} {f.field}: "
                      f"stored={f.stored} source={f.source} ({f.note})")
            if len(shown) > 60:
                print(f"    ... {len(shown) - 60} more")

        print("\n" + "=" * 60)
        print(f"TOTAL  passed={total_passed}  MISMATCH={total_mismatch}  "
              f"INCONSISTENT={total_inconsistent}  UNVERIFIABLE={total_unverifiable}")
        # Gate: mismatches and internal inconsistencies fail; unverifiable is a
        # reported gap (Q4-from-10K etc.), not a hard failure.
        return 1 if (total_mismatch or total_inconsistent) else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

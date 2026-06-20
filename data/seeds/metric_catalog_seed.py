"""
The retail metric catalog — the single source of truth for WHAT we capture and what
each number means. This is the "don't miss anything important" checklist: the coverage
gate compares, per retailer, the metrics expected for its archetype against what's
actually been captured, so a missing metric is surfaced, never silently dropped.

Discipline:
  - One row per metric_key, one precise definition, one unit.
  - xbrl_concepts carries a per-retailer concept map ({"default": [...], "TGT": [...]}).
    Concepts are VERIFIED per retailer with data/verification/concept_probe.py before
    population trusts them. Where a metric is reported (8-K MD&A) or computed, that is
    declared in source_priority/derivation rather than faked as XBRL.
  - applies_to_archetypes records which retailer types a metric is even expected for
    (type-specific metrics like membership/packaway are first-class, not afterthoughts).
  - investor_grade / vision_critical tag why each metric earns its place.

Idempotent: re-running upserts by metric_key / (archetype, metric_key).
"""

from __future__ import annotations

import json
import logging

from data.ingestion._env import load_project_env
from database.base import SessionLocal
from database.models.retail_metrics import MetricDefinition, MetricInterpretation

load_project_env()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

ALL = "all"


def M(metric_key, label, definition, unit, category, *, investor=True, vision=False,
      direction=None, archetypes=ALL, xbrl=None, derivation=None, source="xbrl", notes=None):
    return {
        "metric_key": metric_key, "label": label, "definition": definition, "unit": unit,
        "category": category, "investor_grade": investor, "vision_critical": vision,
        "direction": direction,
        "applies_to_archetypes": ALL if archetypes == ALL else json.dumps(archetypes),
        "xbrl_concepts_json": json.dumps(xbrl) if xbrl else None,
        "derivation": derivation, "source_priority": source, "notes": notes,
    }


# Per-retailer concept maps reused below (verified via concept_probe.py).
_MERCH = {"default": ["RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
          "TGT": ["SalesRevenueGoodsNet", "SalesRevenueNet"]}
_TOTAL = {"default": ["Revenues"],
          "TGT": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"]}
_COGS = {"default": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"]}

CATALOG = [
    # ── Demand: what consumers actually bought ──────────────────────────────
    M("merchandise_sales_usd", "Merchandise (net) sales", "Revenue from selling products to customers — the pure consumer-demand figure, excluding non-merchandise (membership/credit/ads) revenue.", "usd", "demand", vision=True, direction="higher_better", xbrl=_MERCH, notes="Walmart calls this 'net sales'; Target calls it 'Sales'. Same concept, different XBRL tag — hence the per-retailer map."),
    M("total_revenue_usd", "Total revenue", "Merchandise sales + other revenue (membership, credit, advertising). The full top line, available every period.", "usd", "demand", direction="higher_better", xbrl=_TOTAL),
    M("comparable_sales_growth_pct", "Comparable (same-store) sales growth", "YoY growth of sales at stores open >1yr (+ digital). The #1 retail demand KPI; isolates true demand from new-store noise.", "pct", "demand", vision=True, direction="higher_better", source="8k_release", notes="Reported in 8-K/MD&A, not XBRL."),
    M("transaction_count_growth_pct", "Traffic (transaction count) growth", "YoY change in number of transactions/trips — the volume half of demand.", "pct", "demand", vision=True, direction="higher_better", source="8k_release"),
    M("average_ticket_change_pct", "Average ticket change", "YoY change in average spend per transaction — the price/mix half of demand.", "pct", "demand", direction="context", source="8k_release"),
    M("ecommerce_penetration_pct", "E-commerce penetration", "Digital sales as % of total — channel-shift signal.", "pct", "demand", direction="context", source="8k_release"),
    M("apparel_revenue_usd", "Apparel segment revenue", "Revenue attributable to apparel/general-merchandise softlines — the directly vision-relevant demand slice.", "usd", "demand", vision=True, direction="higher_better", source="10q_segment", notes="Parsed from segment disclosures (HTML), not headline XBRL."),
    M("apparel_revenue_pct_total", "Apparel % of total", "Apparel revenue as % of total — apparel mix.", "pct", "demand", vision=True, direction="context", derivation="apparel_revenue_usd / total_revenue_usd", source="derived"),
    M("apparel_yoy_growth_pct", "Apparel revenue YoY growth", "YoY growth of apparel revenue.", "pct", "demand", vision=True, direction="higher_better", derivation="apparel_revenue_usd YoY", source="derived"),
    M("units_sold", "Units sold", "Unit volume where disclosed — separates volume from price.", "count", "demand", direction="higher_better", source="8k_release"),

    # ── Margin & profitability: pricing power / markdown pressure ────────────
    M("cogs_usd", "Cost of goods sold", "Merchandise cost of sales.", "usd", "margin", direction="lower_better", xbrl=_COGS),
    M("gross_profit_usd", "Gross profit", "Merchandise sales minus cost of goods sold.", "usd", "margin", direction="higher_better", xbrl={"default": ["GrossProfit"]}, derivation="merchandise_sales_usd - cogs_usd (if GrossProfit absent)"),
    M("gross_margin_pct", "Gross margin rate", "Gross profit as % of sales — pricing power and markdown pressure.", "pct", "margin", vision=True, direction="context", notes="Walmart: recomputed gp/net-sales. Target: REPORTED rate (8-K), verified vs filing text, not recomputed.", source="xbrl_or_reported"),
    M("gross_margin_change_bps", "Gross margin change (bps)", "YoY change in gross margin, in basis points — the markdown-pressure signal.", "bps", "margin", vision=True, direction="context", derivation="gross_margin_pct YoY delta x100", source="derived"),
    M("sga_usd", "SG&A expense", "Selling, general & administrative expense.", "usd", "margin", direction="lower_better", xbrl={"default": ["SellingGeneralAndAdministrativeExpense"]}),
    M("sga_rate_pct", "SG&A rate", "SG&A as % of sales — cost discipline.", "pct", "margin", direction="lower_better", derivation="sga_usd / total_revenue_usd", source="derived"),
    M("operating_income_usd", "Operating income", "Income from operations.", "usd", "margin", direction="higher_better", xbrl={"default": ["OperatingIncomeLoss"]}),
    M("operating_margin_pct", "Operating margin", "Operating income as % of sales.", "pct", "margin", direction="higher_better", derivation="operating_income_usd / total_revenue_usd", source="derived"),
    M("net_income_usd", "Net income", "Bottom-line profit attributable to the company.", "usd", "margin", direction="higher_better", xbrl={"default": ["NetIncomeLoss", "ProfitLoss"]}),
    M("net_margin_pct", "Net margin", "Net income as % of sales.", "pct", "margin", direction="higher_better", derivation="net_income_usd / total_revenue_usd", source="derived"),
    M("eps_diluted_usd", "Diluted EPS", "Diluted earnings per share — investor-core profitability.", "usd_per_share", "margin", direction="higher_better", xbrl={"default": ["EarningsPerShareDiluted"]}),
    M("eps_basic_usd", "Basic EPS", "Basic earnings per share.", "usd_per_share", "margin", direction="higher_better", xbrl={"default": ["EarningsPerShareBasic"]}),
    M("ebitda_usd", "EBITDA", "Operating income + depreciation & amortization — cash earnings proxy.", "usd", "margin", direction="higher_better", derivation="operating_income_usd + DepreciationDepletionAndAmortization", source="derived"),

    # ── Inventory & working capital: THE order-pressure transmitter ──────────
    M("inventory_usd", "Inventory", "Merchandise inventory at period end (balance-sheet instant).", "usd", "inventory_working_capital", vision=True, direction="context", xbrl={"default": ["InventoryNet"]}),
    M("inventory_days", "Days inventory outstanding (DIO)", "Inventory / (trailing-4Q COGS / 365). How many days of cost sit in stock.", "days", "inventory_working_capital", vision=True, direction="context", derivation="inventory_usd / (ttm cogs / 365)", source="derived"),
    M("inventory_turnover", "Inventory turnover", "Trailing-4Q COGS / inventory — how fast stock sells.", "ratio", "inventory_working_capital", direction="higher_better", derivation="ttm cogs / inventory_usd", source="derived"),
    M("inventory_to_sales_ratio", "Inventory-to-sales ratio", "Inventory / quarterly sales — stock relative to demand.", "ratio", "inventory_working_capital", vision=True, direction="context", derivation="inventory_usd / merchandise_sales_usd", source="derived"),
    M("inventory_vs_sales_growth_gap_pct", "Inventory-vs-sales growth gap", "Inventory YoY growth minus sales YoY growth. Positive = inventory outrunning demand = the leading indicator of markdowns and apparel order cuts (the 2022 glut in one number).", "pct", "inventory_working_capital", vision=True, direction="context", derivation="inventory_yoy_pct - sales_yoy_pct", source="derived", notes="Interpretation flips by archetype — see metric_interpretation (full-price bearish vs off-price bullish)."),
    M("accounts_payable_usd", "Accounts payable", "Trade payables at period end — what the retailer owes suppliers.", "usd", "inventory_working_capital", xbrl={"default": ["AccountsPayableCurrent", "AccountsPayableTradeCurrent"]}),
    M("days_payable_outstanding", "Days payable outstanding (DPO)", "AP / (trailing-4Q COGS / 365). How long the retailer takes to pay suppliers — direct read on importer cash-flow exposure.", "days", "inventory_working_capital", vision=True, direction="context", derivation="accounts_payable_usd / (ttm cogs / 365)", source="derived"),
    M("cash_conversion_cycle_days", "Cash conversion cycle", "DIO + DSO - DPO. Working-capital efficiency.", "days", "inventory_working_capital", direction="lower_better", derivation="inventory_days + DSO - days_payable_outstanding", source="derived"),

    # ── Cash flow & balance-sheet strength: ability to pay / distress ────────
    M("operating_cash_flow_usd", "Operating cash flow", "Cash generated by operations.", "usd", "cashflow", direction="higher_better", xbrl={"default": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]}),
    M("capex_usd", "Capital expenditure", "Cash spent on property/plant/equipment — store & DC investment (expansion signal).", "usd", "cashflow", direction="context", xbrl={"default": ["PaymentsToAcquirePropertyPlantAndEquipment"]}),
    M("depreciation_amortization_usd", "Depreciation & amortization", "D&A add-back from the cash-flow statement — the bridge from operating income to EBITDA.", "usd", "cashflow", direction="context", xbrl={"default": ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet", "DepreciationAndAmortization"]}, notes="Reported cumulative (YTD); differenced to discrete quarters like cash flow."),
    M("free_cash_flow_usd", "Free cash flow", "Operating cash flow minus capex.", "usd", "cashflow", direction="higher_better", derivation="operating_cash_flow_usd - capex_usd", source="derived"),
    M("cash_and_equivalents_usd", "Cash & equivalents", "Cash and short-term equivalents at period end.", "usd", "balance_sheet", direction="higher_better", xbrl={"default": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "CashAndCashEquivalentsAtCarryingValueIncludingDiscontinuedOperations"]}, notes="Concept evolved post-ASU-2016-18 (restricted cash merged); source_concept records which was used per period."),
    M("total_debt_usd", "Total debt", "Long-term debt (incl. finance leases where that's how the retailer reports) + current portion.", "usd", "balance_sheet", direction="lower_better", xbrl={"default": ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermDebt"]}, notes="Walmart reports LongTermDebtNoncurrent (debt only); Target reports LongTermDebtAndCapitalLeaseObligations (incl. leases). Current portion added at population. source_concept records the basis."),
    M("current_assets_usd", "Current assets", "Total current assets — short-term resources.", "usd", "balance_sheet", direction="higher_better", xbrl={"default": ["AssetsCurrent"]}),
    M("current_liabilities_usd", "Current liabilities", "Total current liabilities — short-term obligations.", "usd", "balance_sheet", direction="lower_better", xbrl={"default": ["LiabilitiesCurrent"]}),
    M("accounts_receivable_usd", "Accounts receivable", "Net current receivables — input to DSO / cash-conversion cycle.", "usd", "inventory_working_capital", direction="context", xbrl={"default": ["ReceivablesNetCurrent", "AccountsReceivableNet", "AccountsAndOtherReceivablesNetCurrent", "NontradeReceivablesCurrent"]}, notes="Walmart=ReceivablesNetCurrent; Target=AccountsAndOtherReceivablesNetCurrent (sparse, post-credit-card-sale). source_concept records which."),
    M("current_ratio", "Current ratio", "Current assets / current liabilities — short-term liquidity.", "ratio", "balance_sheet", direction="higher_better", derivation="current_assets_usd / current_liabilities_usd", source="derived"),
    M("debt_to_ebitda", "Debt / EBITDA", "Total debt / trailing-4Q EBITDA — leverage & distress signal (a stressed buyer cuts orders and stretches terms).", "ratio", "balance_sheet", vision=True, direction="lower_better", derivation="total_debt_usd / ttm ebitda_usd", source="derived"),

    # ── Forward-looking: forward demand ─────────────────────────────────────
    M("guidance_comp_sales_pct", "Guided comparable sales", "Management's forward comparable-sales guidance.", "pct", "guidance", vision=True, direction="higher_better", source="8k_release"),
    M("guidance_eps_low_usd", "EPS guidance (low)", "Low end of EPS guidance range.", "usd_per_share", "guidance", direction="higher_better", source="8k_release"),
    M("guidance_eps_high_usd", "EPS guidance (high)", "High end of EPS guidance range.", "usd_per_share", "guidance", direction="higher_better", source="8k_release"),
    M("planned_store_openings", "Planned store openings", "Announced net new stores — future order capacity.", "count", "guidance", vision=True, direction="higher_better", source="8k_release"),
    M("capex_guidance_usd", "Capex guidance", "Guided capital expenditure — forward expansion intent.", "usd", "guidance", direction="context", source="8k_release"),

    # ── Apparel / supply-chain specific (vision differentiators) ─────────────
    M("private_brand_penetration_pct", "Private/owned-brand penetration", "Owned-brand sales as % of total — owned brands compete directly with branded imports.", "pct", "apparel_supply_chain", vision=True, direction="context", source="8k_release"),

    # ── Type-specific metrics (first-class; applies_to_archetypes scopes them) ─
    M("membership_fee_revenue_usd", "Membership fee revenue", "Recurring membership fee income — for clubs this is most of operating profit.", "usd", "demand", vision=True, direction="higher_better", archetypes=["warehouse_club"], source="xbrl_probe_pending", notes="Verify exact XBRL concept per club via concept_probe before population."),
    M("membership_renewal_rate_pct", "Membership renewal rate", "% of members who renew — leading indicator of club traffic/loyalty.", "pct", "demand", vision=True, direction="higher_better", archetypes=["warehouse_club"], source="8k_release"),
    M("packaway_inventory_pct", "Packaway inventory %", "Off-price inventory bought opportunistically and held for a later season — proxy for buying capacity and confidence.", "pct", "inventory_working_capital", vision=True, direction="context", archetypes=["off_price"], source="10k"),
    M("full_price_sellthrough_pct", "Full-price sell-through", "% of units sold at full price — brand health for specialty/branded retail.", "pct", "demand", vision=True, direction="higher_better", archetypes=["specialty"], source="8k_release"),
    M("credit_card_revenue_usd", "Credit/financial revenue", "Revenue from the retailer's credit/financial operations (legacy for some department stores).", "usd", "demand", direction="context", archetypes=["department"], source="xbrl_probe_pending"),
]


# Archetype-aware interpretation: same metric, different demand meaning by type.
INTERP = [
    ("mass_market", "inventory_vs_sales_growth_gap_pct", "context",
     "Positive gap = inventory outrunning demand → markdowns then CUT reorders to apparel suppliers. Bearish for importers selling to this retailer."),
    ("specialty", "inventory_vs_sales_growth_gap_pct", "context",
     "Positive gap = overbought → promotions + order cuts. Bearish for their apparel suppliers."),
    ("department", "inventory_vs_sales_growth_gap_pct", "context",
     "Positive gap = glut → aggressive markdowns + order cancellations. Bearish for suppliers."),
    ("off_price", "inventory_vs_sales_growth_gap_pct", "context",
     "A SECTOR-WIDE positive gap is BULLISH for off-price: more closeout supply to buy cheaply → off-price ramps buying. Demand relocates here rather than disappearing."),
    ("warehouse_club", "gross_margin_pct", "context",
     "Structurally low (~11-13%) BY DESIGN; judge trend/stability, not level. Low margin is the model, not weakness."),
    ("off_price", "gross_margin_pct", "context",
     "Resilient mid-20s% because they buy cheap; margin holds even in gluts."),
    ("mass_market", "gross_margin_pct", "context",
     "Low-20s to high-20s; under EDLP, margin STABILITY is the signal — a sharp drop = markdown distress."),
    ("specialty", "gross_margin_pct", "context",
     "High-20s to 40s; level and markdown-driven swings both matter — sensitive to fashion misses."),
    ("off_price", "inventory_days", "context",
     "Elevated DIO can be opportunistic packaway, not distress — read alongside buying commentary, not as a glut."),
    ("fast_fashion", "inventory_days", "lower_better",
     "Velocity is the model; rising DIO is an early warning of stale assortment."),
]


def upsert(db, dry_run=False):
    defs = ints = 0
    for d in CATALOG:
        existing = db.get(MetricDefinition, d["metric_key"])
        if existing:
            for k, v in d.items():
                setattr(existing, k, v)
        else:
            db.add(MetricDefinition(**d))
        defs += 1
    for archetype, key, direction, implication in INTERP:
        row = (db.query(MetricInterpretation)
               .filter_by(archetype=archetype, metric_key=key).first())
        if row:
            row.direction = direction
            row.demand_implication = implication
        else:
            db.add(MetricInterpretation(archetype=archetype, metric_key=key,
                                        direction=direction, demand_implication=implication))
        ints += 1
    if dry_run:
        db.rollback()
    else:
        db.commit()
    return defs, ints


def main() -> int:
    db = SessionLocal()
    try:
        defs, ints = upsert(db)
        vision = sum(1 for d in CATALOG if d["vision_critical"])
        print(f"Seeded {defs} metric definitions ({vision} vision-critical) + {ints} archetype interpretations.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

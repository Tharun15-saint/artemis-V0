# ARTEMIS — RETAIL INTELLIGENCE BUILD PROMPT
# Paste this into Cursor with docs/MASTER_SCHEMA.md added as context

---

Build the complete retail intelligence layer for Artemis. This is not a simple
data ingestion script. It is the system that tells an apparel importer exactly
what their retail customers are doing, what it means for their open programs,
and what they should do right now. Every piece of data collected must be
interpreted, not just stored.

---

## WHAT TO BUILD — THREE FILES

### FILE 1: data/ingestion/retailers_ingestion.py
### FILE 2: intelligence/retail_engine.py
### FILE 3: tests/test_retail_intelligence.py

---

## FILE 1 — data/ingestion/retailers_ingestion.py

Fetch public financial data from SEC EDGAR for the 10 major US apparel retailers
and write to major_retailers and demand_signals tables.

### Retailers to cover with their SEC CIK numbers:

```python
RETAILERS = [
    {"name": "Target Corporation",       "cik": "0000027419", "ticker": "TGT"},
    {"name": "Walmart Inc",              "cik": "0000104169", "ticker": "WMT"},
    {"name": "TJX Companies",            "cik": "0000109198", "ticker": "TJX"},
    {"name": "Burlington Coat Factory",  "cik": "0001579298", "ticker": "BURL"},
    {"name": "Ross Stores",              "cik": "0000745732", "ticker": "ROST"},
    {"name": "Kohls Corporation",        "cik": "0000885639", "ticker": "KSS"},
    {"name": "Macys Inc",                "cik": "0000794367", "ticker": "M"},
    {"name": "Gap Inc",                  "cik": "0000039911", "ticker": "GPS"},
    {"name": "PVH Corp",                 "cik": "0000078239", "ticker": "PVH"},
    {"name": "Amazon",                   "cik": "0001018724", "ticker": "AMZN"},
]
```

### Data to fetch from SEC EDGAR XBRL API:

Base URL: https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json

Extract these XBRL concepts for each retailer — use the most recent 4 quarters:

```python
XBRL_CONCEPTS = {
    "revenues":          ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                          "SalesRevenueNet", "SalesRevenueGoodsNet"],
    "gross_profit":      ["GrossProfit"],
    "cogs":              ["CostOfGoodsSold", "CostOfRevenue",
                          "CostOfGoodsAndServicesSold"],
    "inventory":         ["InventoryNet"],
    "store_count":       ["NumberOfStores", "NumberOfOperatedStores"],
    "net_income":        ["NetIncomeLoss"],
    "operating_income":  ["OperatingIncomeLoss"],
}
```

### Calculations to derive:

```python
# Gross margin percentage
gross_margin_pct = (gross_profit / revenues) * 100

# Inventory turnover ratio
inventory_turnover = cogs_annual / inventory_current

# YoY revenue growth
revenue_growth_pct = (revenue_current_quarter / revenue_same_quarter_prior_year - 1) * 100

# QoQ inventory turnover change
turnover_change_pct = (turnover_current / turnover_prior_quarter - 1) * 100

# Gross margin change vs prior year same quarter
margin_change_pct = gross_margin_current - gross_margin_prior_year_same_quarter
```

### Write to major_retailers table:

One row per retailer per quarter. Use upsert logic — if a row for this
retailer and this filing period already exists, update it. Never duplicate.

```python
{
    "name":               retailer_name,
    "store_count":        latest_store_count,
    "total_sales":        latest_quarterly_revenue,
    "apparel_revenue":    apparel_revenue_if_disclosed,  # None if not broken out
    "gross_margin":       gross_margin_pct,
    "inventory_turnover": inventory_turnover_ratio,
    "forward_guidance":   extract_guidance_from_filing(),  # see below
    "source":             "SEC EDGAR XBRL API",
    "status":             "LIVE",
}
```

### Forward guidance extraction:

Fetch the most recent 10-Q or 10-K filing text from:
https://data.sec.gov/submissions/CIK{cik}.json

Find the filing, then fetch the document. Search for these keyword patterns
and extract the surrounding sentence (max 200 characters):

```python
GUIDANCE_PATTERNS = [
    "we expect", "we anticipate", "outlook", "guidance",
    "next quarter", "full year", "fiscal year",
    "inventory levels", "gross margin", "comparable store",
    "we are pleased", "headwinds", "tailwinds",
]
```

Store the most relevant extracted sentence in forward_guidance field.
If nothing found, store "No forward guidance extracted from latest filing."

### Write to demand_signals table:

One row per retailer derived from the financial data.
This is where raw numbers become intelligence:

```python
{
    "retailer_id":            retailer.retailer_id,

    # Is the retailer expanding its physical footprint?
    "store_expansion":        "expanding" if store_count_yoy_change > 0
                              else "contracting" if store_count_yoy_change < 0
                              else "stable",

    # Is inventory health improving or deteriorating?
    # Higher turnover = selling faster = healthier = likely to maintain volumes
    "inventory_improving":    "improving" if turnover_change_pct > 2.0
                              else "deteriorating" if turnover_change_pct < -2.0
                              else "stable",

    # Is gross margin under pressure?
    # Margin compression = retailer will squeeze supplier FOB prices
    "margin_compression":     "compressing" if margin_change_pct < -1.0
                              else "expanding" if margin_change_pct > 1.0
                              else "stable",

    # Combined buying volume signal
    # This is the single most important output — what will they buy next season?
    "buying_volume_signal":   derive_buying_signal(
                                  store_expansion, inventory_improving,
                                  margin_compression, revenue_growth_pct
                              ),

    "status": "LIVE",
}
```

### Buying signal derivation logic — this is the intelligence core:

```python
def derive_buying_signal(store_expansion, inventory_improving,
                          margin_compression, revenue_growth_pct):
    """
    Combine signals into a single buying volume prediction.
    An importer needs to know: will this retailer buy more, same, or less next season?
    """
    score = 0

    # Store expansion is the strongest signal — more stores = more inventory needed
    if store_expansion == "expanding":   score += 2
    if store_expansion == "contracting": score -= 2

    # Healthy inventory turnover means they are selling well and will reorder
    if inventory_improving == "improving":     score += 1
    if inventory_improving == "deteriorating": score -= 2

    # Margin compression means they will resist price increases and may cut volume
    if margin_compression == "compressing": score -= 1
    if margin_compression == "expanding":   score += 1

    # Revenue growth adds confidence
    if revenue_growth_pct > 5:  score += 1
    if revenue_growth_pct < -5: score -= 1

    if score >= 3:   return "strongly_increasing"
    if score >= 1:   return "increasing"
    if score == 0:   return "stable"
    if score >= -2:  return "declining"
    return "strongly_declining"
```

### Run schedule and entry point:

```python
if __name__ == "__main__":
    # Fetch data, write to DB, print summary
    # Add --retailer TARGET flag for single-retailer refresh
    # Add --all flag to refresh all 10 retailers
    # Rate limit: 100ms sleep between SEC EDGAR requests (their limit)
```

---

## FILE 2 — intelligence/retail_engine.py

This is where stored retailer data becomes operator-specific intelligence.
It reads from major_retailers and demand_signals and connects them to the
operator's open programs.

### Function 1 — generate_retailer_intelligence(importer_id, db)

```python
def generate_retailer_intelligence(importer_id: int, db: Session) -> dict:
    """
    For a specific importer, combine:
    1. What each retailer's financial health signals
    2. What the operator's historical relationship with each retailer is
    3. What the seasonal commit windows say about timing
    4. What the operator's current open programs look like
    5. What action they should take RIGHT NOW

    Returns a dict with retailer-by-retailer intelligence and
    a prioritised action list.
    """
```

Logic to implement:

```python
# Step 1: Load all retailers with their latest demand_signals
retailers_with_signals = db.query(MajorRetailers, DemandSignals)...

# Step 2: Load operator's open programs — which retailers are they serving?
# An importer's programs link to factories, not retailers directly.
# But seasonal patterns tell us which retailers buy in which windows.
# For now, use seasonal_patterns to map commit windows to retailer signals.
open_programs = db.query(Program).filter(
    Program.importer_id == importer_id,
    Program.status.in_(["PLANNING", "COMMITTED", "IN_PRODUCTION"])
).all()

# Step 3: Load current seasonal patterns
seasonal = db.query(SeasonalPatterns).first()

# Step 4: For each retailer, generate specific intelligence:
retailer_intelligence = []
for retailer, signals in retailers_with_signals:
    intel = {
        "retailer_name":        retailer.name,
        "buying_volume_signal": signals.buying_volume_signal,
        "inventory_status":     signals.inventory_improving,
        "margin_pressure":      signals.margin_compression,
        "store_trajectory":     signals.store_expansion,
        "gross_margin_pct":     retailer.gross_margin,
        "inventory_turnover":   retailer.inventory_turnover,

        # What this means for the operator
        "implication":          generate_implication(retailer, signals, seasonal),

        # What the operator should do right now
        "recommended_action":   generate_action(retailer, signals, seasonal, open_programs),

        # How urgent is this action?
        "urgency":              calculate_urgency(signals, seasonal),
    }
    retailer_intelligence.append(intel)

# Step 5: Write to retailer_demand_forecast output table
# One row per retailer per run
for intel in retailer_intelligence:
    forecast = RetailerDemandForecast(
        retailer_id         = retailer.retailer_id,
        buying_volume_signal= intel["buying_volume_signal"],
        store_count_trend   = intel["store_trajectory"],
        unit_growth_pct     = calculate_unit_growth(retailer),
        category_focus      = derive_category_focus(retailer),
        confidence_score    = calculate_confidence(retailer),
        as_of_date          = date.today(),
        model_version       = INTELLIGENCE_MODEL_VERSION,
    )
    db.add(forecast)

# Step 6: Write prediction_log records for accuracy tracking
# Step 7: Return full intelligence dict
```

### generate_implication() — the narrative intelligence layer:

```python
def generate_implication(retailer, signals, seasonal) -> str:
    """
    Translate financial signals into plain English that an operator understands.
    Not 'inventory turnover improved 8%' — that is data.
    'Target is selling through faster than last year — they will need to
    replenish SS27 inventory and the commit window opens this month' — that is intelligence.
    """
    implications = []

    if signals.inventory_improving == "improving":
        implications.append(
            f"{retailer.name} inventory turnover is improving — "
            f"healthy sell-through signals they will maintain or increase buying volumes."
        )
    elif signals.inventory_improving == "deteriorating":
        implications.append(
            f"{retailer.name} inventory is building — "
            f"risk of order cancellations or volume reductions on open programs."
        )

    if signals.margin_compression == "compressing":
        implications.append(
            f"Gross margin under pressure at {retailer.gross_margin:.1f}% — "
            f"expect FOB price negotiations to be harder this season."
        )

    if signals.store_expansion == "expanding":
        implications.append(
            f"Store count growing — "
            f"incremental volume demand across all categories."
        )

    if signals.buying_volume_signal in ("strongly_increasing", "increasing"):
        implications.append(
            f"Combined signals point to volume growth — "
            f"early factory commitment recommended to secure capacity."
        )

    return " ".join(implications) if implications else "Signals are neutral — monitor next quarter."
```

### generate_action() — the operator instruction:

```python
def generate_action(retailer, signals, seasonal, open_programs) -> str:
    """
    Turn intelligence into a specific, timed action.
    Every output must answer: what should the operator do THIS WEEK?
    """
    from datetime import date, timedelta
    today = date.today()

    # Check if SS commit window is open
    # SS factory commits: Sep-Nov for Mar-May delivery
    # FW factory commits: Mar-May for Sep-Nov delivery
    ss_window_open  = today.month in [9, 10, 11]
    fw_window_open  = today.month in [3, 4, 5]
    # June-August: SS27 window opening
    ss27_early      = today.month in [6, 7, 8]

    if signals.buying_volume_signal in ("strongly_increasing", "increasing"):
        if ss27_early:
            return (
                f"COMMIT EARLY: {retailer.name} signals are strong and SS27 factory "
                f"commit window is opening now (June-August). Secure factory capacity "
                f"before FW27 commitments compete for the same slots in March 2027."
            )
        if fw_window_open:
            return (
                f"COMMIT NOW: {retailer.name} buying signals are positive and FW factory "
                f"commit window is open. Lock in capacity for Sep-Nov delivery."
            )

    if signals.inventory_improving == "deteriorating":
        # Check if operator has open programs for this retailer's season
        at_risk_programs = [p for p in open_programs
                           if p.status == "IN_PRODUCTION"]
        if at_risk_programs:
            return (
                f"MONITOR CLOSELY: {retailer.name} inventory is building. "
                f"Risk of order reduction on {len(at_risk_programs)} open program(s). "
                f"Confirm delivery schedule with buyer before committing more production."
            )

    if signals.margin_compression == "compressing":
        return (
            f"PREPARE FOR PRICE PRESSURE: {retailer.name} gross margin is compressing. "
            f"Expect tighter FOB negotiation. Use Artemis corridor comparison to "
            f"identify lowest-cost sourcing option before buyer meeting."
        )

    return f"No immediate action required for {retailer.name}. Review again next quarter."
```

### Function 2 — detect_order_cancellation_risk(importer_id, db)

```python
def detect_order_cancellation_risk(importer_id: int, db: Session) -> list[dict]:
    """
    Cross-reference retailer inventory signals with open programs.
    If a retailer shows inventory build risk AND the operator has
    programs IN_PRODUCTION for that season, flag the risk explicitly.

    This is one of the highest-value signals Artemis can surface —
    knowing a cancellation is coming before the buyer calls.
    """
    risks = []

    # Load all retailers with deteriorating inventory signals
    at_risk_retailers = db.query(MajorRetailers).join(DemandSignals).filter(
        DemandSignals.inventory_improving == "deteriorating"
    ).all()

    # Load operator's in-production programs
    active_programs = db.query(Program).filter(
        Program.importer_id == importer_id,
        Program.status.in_(["COMMITTED", "IN_PRODUCTION"]),
    ).all()

    for program in active_programs:
        for retailer in at_risk_retailers:
            # Flag the risk
            risks.append({
                "program_id":        program.program_id,
                "season":            program.season,
                "retailer_name":     retailer.name,
                "risk_type":         "order_cancellation",
                "signal":            "inventory_deteriorating",
                "inventory_turnover": retailer.inventory_turnover,
                "gross_margin":      retailer.gross_margin,
                "recommended_action": (
                    f"Contact {retailer.name} buyer to confirm {program.season} "
                    f"program volumes before CMT start date. "
                    f"Their inventory turnover signals potential volume reduction."
                ),
                "urgency": "HIGH" if program.status == "IN_PRODUCTION" else "MEDIUM",
            })

    return risks
```

### Function 3 — generate_commit_timing_intelligence(importer_id, db)

```python
def generate_commit_timing_intelligence(importer_id: int, db: Session) -> dict:
    """
    Map retailer health signals to seasonal commit windows.
    Tell the operator exactly which retailers to commit with first,
    in what order, and why — based on their financial health and
    the current position in the seasonal calendar.
    """
    from datetime import date
    today = date.today()
    month = today.month

    seasonal = db.query(SeasonalPatterns).first()
    retailers = db.query(MajorRetailers).join(DemandSignals).all()

    # Sort by buying signal strength and urgency
    commit_priority = []
    for retailer, signals in retailers:
        priority_score = 0
        if signals.buying_volume_signal == "strongly_increasing": priority_score = 5
        elif signals.buying_volume_signal == "increasing":        priority_score = 4
        elif signals.buying_volume_signal == "stable":            priority_score = 3
        elif signals.buying_volume_signal == "declining":         priority_score = 2
        else:                                                      priority_score = 1

        # Boost priority if margin is NOT under pressure
        # (easier negotiations = commit sooner)
        if signals.margin_compression != "compressing":
            priority_score += 1

        commit_priority.append({
            "retailer_name":    retailer.name,
            "priority_score":   priority_score,
            "signal":           signals.buying_volume_signal,
            "margin_pressure":  signals.margin_compression,
            "commit_rationale": generate_commit_rationale(retailer, signals, month),
        })

    # Sort highest priority first
    commit_priority.sort(key=lambda x: x["priority_score"], reverse=True)

    return {
        "as_of_date":        str(today),
        "season_context":    detect_current_season_context(month, seasonal),
        "commit_priority":   commit_priority,
        "top_action":        commit_priority[0]["commit_rationale"] if commit_priority else None,
    }
```

---

## FILE 3 — tests/test_retail_intelligence.py

```python
"""
Tests for retail intelligence layer.
Run after ingestion: pytest tests/test_retail_intelligence.py -v
"""

import pytest
from database.base import SessionLocal
from database.models.retail import MajorRetailers, DemandSignals
from database.models.outputs import RetailerDemandForecast


class TestRetailIntelligence:

    def setup_method(self):
        self.db = SessionLocal()

    def teardown_method(self):
        self.db.close()

    def test_retailers_are_seeded(self):
        """At least 5 major retailers must be in the database."""
        count = self.db.query(MajorRetailers).count()
        assert count >= 5, f"Only {count} retailers found — run retailers_ingestion.py"

    def test_all_retailers_have_demand_signals(self):
        """Every retailer must have a corresponding demand_signals row."""
        retailers = self.db.query(MajorRetailers).all()
        for retailer in retailers:
            signal = self.db.query(DemandSignals).filter(
                DemandSignals.retailer_id == retailer.retailer_id
            ).first()
            assert signal is not None, f"{retailer.name} has no demand_signals row"

    def test_buying_signals_are_valid_values(self):
        """buying_volume_signal must be one of the five valid values."""
        valid = {"strongly_increasing", "increasing", "stable", "declining", "strongly_declining"}
        signals = self.db.query(DemandSignals).all()
        for s in signals:
            assert s.buying_volume_signal in valid, \
                f"Invalid signal value: {s.buying_volume_signal}"

    def test_gross_margin_is_realistic(self):
        """Gross margin must be between 0 and 100 percent."""
        retailers = self.db.query(MajorRetailers).filter(
            MajorRetailers.gross_margin.isnot(None)
        ).all()
        for r in retailers:
            assert 0 < float(r.gross_margin) < 100, \
                f"{r.name} gross margin {r.gross_margin} is not realistic"

    def test_inventory_turnover_is_realistic(self):
        """Inventory turnover must be between 1 and 20 for apparel retail."""
        retailers = self.db.query(MajorRetailers).filter(
            MajorRetailers.inventory_turnover.isnot(None)
        ).all()
        for r in retailers:
            assert 1 <= float(r.inventory_turnover) <= 20, \
                f"{r.name} turnover {r.inventory_turnover} is not realistic"

    def test_retailer_demand_forecast_has_required_fields(self):
        """All retailer_demand_forecast rows must have as_of_date and model_version."""
        forecasts = self.db.query(RetailerDemandForecast).all()
        for f in forecasts:
            assert f.as_of_date is not None, "retailer_demand_forecast missing as_of_date"
            assert f.model_version is not None, "retailer_demand_forecast missing model_version"

    def test_retail_engine_generates_intelligence(self):
        """retail_engine must return intelligence without errors for importer_id=1."""
        from intelligence.retail_engine import generate_retailer_intelligence
        result = generate_retailer_intelligence(importer_id=1, db=self.db)
        assert "retailer_intelligence" in result
        assert len(result["retailer_intelligence"]) > 0
        # Every retailer must have an implication and recommended_action
        for intel in result["retailer_intelligence"]:
            assert intel.get("implication"), "Missing implication"
            assert intel.get("recommended_action"), "Missing recommended_action"
```

---

## CONSTRAINTS FOR ALL THREE FILES

- Use requests library for HTTP. No external data libraries.
- Rate limit SEC EDGAR calls: time.sleep(0.1) between requests.
- SQLAlchemy ORM only. No pandas. No raw SQL.
- All amounts as Decimal. No float for financial figures.
- Use INTELLIGENCE_MODEL_VERSION from database/constants.py.
- Every retail_demand_forecast row needs as_of_date and model_version.
- Every forecast write must also write a prediction_log record.
- Use upsert pattern — never create duplicate rows for same retailer.
- If SEC EDGAR returns no data for a concept, log a warning and continue.
  Never crash on missing data. Partial data is better than no data.
- Run from project root: python data/ingestion/retailers_ingestion.py --all

## THE SOPHISTICATION GOAL

When this is complete, an operator opens Artemis and sees:

  "Target inventory turnover improved 12% last quarter. Their SS27
   commit window opens this month. Burlington is stable. Neither
   retailer is showing margin compression — negotiations will be
   straightforward. Commit Target SS27 first, Burlington second.
   Secure Tex-Knit Gazipur capacity now before FW27 program commits
   compete for the same factory time in March 2027."

That is the difference between a data platform and an intelligence platform.
The data is public. The interpretation — timed, operator-specific, actionable —
is the product.

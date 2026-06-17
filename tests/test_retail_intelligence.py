"""
Tests for retail intelligence layer.
Run after ingestion: pytest tests/test_retail_intelligence.py -v
"""

import pytest
from database.base import SessionLocal
from database.models.retail import DemandSignals, MajorRetailers, RetailerFinancials
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

    def test_all_retailers_with_financials_have_demand_signals(self):
        """Every retailer that has RetailerFinancials data must also have an
        is_latest=True demand_signals row. Retailers with no financials yet
        (not ingested) are skipped — run retailers_ingestion.py --all to fill gaps.
        """
        retailers_with_data = (
            self.db.query(MajorRetailers)
            .join(RetailerFinancials, RetailerFinancials.retailer_id == MajorRetailers.retailer_id)
            .filter(RetailerFinancials.is_latest.is_(True))
            .distinct()
            .all()
        )
        for retailer in retailers_with_data:
            signal = (
                self.db.query(DemandSignals)
                .filter(
                    DemandSignals.retailer_id == retailer.retailer_id,
                    DemandSignals.is_latest.is_(True),
                )
                .first()
            )
            assert signal is not None, (
                f"{retailer.name} has RetailerFinancials data but no is_latest demand_signals row — "
                f"run retailers_ingestion.py to compute signals"
            )

    def test_demand_signals_have_temporal_keys(self):
        """All is_latest demand_signals rows must carry fiscal_year and fiscal_quarter."""
        signals = (
            self.db.query(DemandSignals).filter(DemandSignals.is_latest.is_(True)).all()
        )
        for s in signals:
            assert s.fiscal_year is not None, (
                f"DemandSignals id={s.demand_signal_id} (retailer {s.retailer_id}) "
                f"missing fiscal_year"
            )
            assert s.fiscal_quarter in (1, 2, 3, 4), (
                f"DemandSignals id={s.demand_signal_id} has invalid fiscal_quarter "
                f"{s.fiscal_quarter}"
            )

    def test_buying_signals_are_valid_values(self):
        """buying_volume_signal must be one of the six valid values.
        'unknown' is the demand_forecast_synthesis fallback when signals are insufficient.
        """
        valid = {
            "strongly_increasing", "increasing", "stable",
            "declining", "strongly_declining", "unknown",
        }
        signals = self.db.query(DemandSignals).all()
        for s in signals:
            assert s.buying_volume_signal in valid, \
                f"Invalid signal value: {s.buying_volume_signal}"

    def test_gross_margin_is_realistic(self):
        """Gross margin in retailer_financials must be between 0 and 100 percent."""
        rows = (
            self.db.query(RetailerFinancials)
            .filter(
                RetailerFinancials.is_latest.is_(True),
                RetailerFinancials.gross_margin_pct.isnot(None),
            )
            .all()
        )
        for r in rows:
            assert 0 < float(r.gross_margin_pct) < 100, (
                f"retailer_id={r.retailer_id} FY{r.fiscal_year}Q{r.fiscal_quarter} "
                f"gross_margin_pct={r.gross_margin_pct} is not realistic"
            )

    def test_inventory_days_is_realistic(self):
        """Inventory days must be between 10 and 200 for apparel retail."""
        rows = (
            self.db.query(RetailerFinancials)
            .filter(
                RetailerFinancials.is_latest.is_(True),
                RetailerFinancials.inventory_days.isnot(None),
            )
            .all()
        )
        for r in rows:
            assert 10 <= float(r.inventory_days) <= 200, (
                f"retailer_id={r.retailer_id} FY{r.fiscal_year}Q{r.fiscal_quarter} "
                f"inventory_days={r.inventory_days} is not realistic"
            )

    def test_apparel_revenue_is_not_equal_to_total_sales(self):
        """Apparel revenue must not equal total net sales — that indicates the XBRL
        total-revenue concept bug (RevenueFromContractWithCustomerExcludingAssessedTax).
        """
        rows = (
            self.db.query(RetailerFinancials)
            .filter(
                RetailerFinancials.is_latest.is_(True),
                RetailerFinancials.apparel_revenue_usd.isnot(None),
                RetailerFinancials.total_net_sales_usd.isnot(None),
            )
            .all()
        )
        for r in rows:
            assert r.apparel_revenue_usd != r.total_net_sales_usd, (
                f"retailer_id={r.retailer_id} FY{r.fiscal_year}Q{r.fiscal_quarter}: "
                f"apparel_revenue_usd == total_net_sales_usd ({r.total_net_sales_usd}) — "
                f"XBRL total-revenue concept bug; must not equal total sales"
            )

    def test_seasonal_patterns_are_seeded(self):
        """seasonal_patterns must have at least one row — engine falls back to hardcoded
        defaults if empty, producing incorrect commit window advice.
        """
        from database.models.retail import SeasonalPatterns
        count = self.db.query(SeasonalPatterns).count()
        assert count >= 1, (
            "seasonal_patterns table is empty — run migration i1j2k3l4m5n6 to seed"
        )

    def test_seasonal_patterns_have_valid_windows(self):
        """Commit window strings must be non-empty and freight lead must be positive."""
        from database.models.retail import SeasonalPatterns
        patterns = self.db.query(SeasonalPatterns).all()
        for p in patterns:
            assert p.ss_factory_commit_window, "ss_factory_commit_window is empty"
            assert p.fw_factory_commit_window, "fw_factory_commit_window is empty"
            assert p.freight_book_lead_days and p.freight_book_lead_days > 0, (
                f"freight_book_lead_days={p.freight_book_lead_days} is not positive"
            )

    def test_no_duplicate_financials_for_same_quarter(self):
        """No two is_latest=True rows should share the same (retailer_id, fiscal_year, fiscal_quarter).
        Duplicates indicate a broken upsert that didn't demote prior rows.
        """
        from sqlalchemy import func
        dupes = (
            self.db.query(
                RetailerFinancials.retailer_id,
                RetailerFinancials.fiscal_year,
                RetailerFinancials.fiscal_quarter,
                func.count().label("cnt"),
            )
            .filter(RetailerFinancials.is_latest.is_(True))
            .group_by(
                RetailerFinancials.retailer_id,
                RetailerFinancials.fiscal_year,
                RetailerFinancials.fiscal_quarter,
            )
            .having(func.count() > 1)
            .all()
        )
        assert not dupes, (
            f"Duplicate is_latest=True rows in retailer_financials: "
            + ", ".join(
                f"retailer={d.retailer_id} FY{d.fiscal_year}Q{d.fiscal_quarter} ({d.cnt} rows)"
                for d in dupes
            )
        )

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
        for intel in result["retailer_intelligence"]:
            assert intel.get("implication"), "Missing implication"
            assert intel.get("recommended_action"), "Missing recommended_action"
            assert intel.get("fiscal_year") is not None, (
                f"{intel.get('retailer_name')} intelligence missing fiscal_year — "
                f"demand_signals temporal keys not populated"
            )

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

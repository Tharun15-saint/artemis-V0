"""
Hard tests for the retailer stock-price dimension and market-signal intelligence.

These tests treat the stock data as a first-class intelligence source: they
enforce OHLC integrity, append-only discipline, cross-table linkage to the same
retailer's fundamentals, and the correctness/sanity of every derived market metric.

Run: pytest tests/test_retailer_stock_data.py -v
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from database.base import SessionLocal
from database.models.retail import (
    MajorRetailers,
    RetailerFinancials,
    RetailerStockPrices,
)
from intelligence.retail_market_signals import (
    generate_market_signal,
    load_price_series,
)

WALMART_ID = 2
WALMART_TICKER = "WMT"
_OHLC_TOL = Decimal("0.01")


class TestStockDataIntegrity:

    def setup_method(self):
        self.db = SessionLocal()

    def teardown_method(self):
        self.db.close()

    def _latest(self):
        return (
            self.db.query(RetailerStockPrices)
            .filter(
                RetailerStockPrices.retailer_id == WALMART_ID,
                RetailerStockPrices.is_latest.is_(True),
            )
            .all()
        )

    def test_walmart_stock_data_exists(self):
        rows = self._latest()
        assert len(rows) > 2000, (
            f"Expected 2000+ Walmart daily bars, got {len(rows)} — run "
            f"retailer_stock_ingestion.py --ticker WMT"
        )

    def test_every_bar_links_to_a_real_retailer(self):
        """Every stock row must point at a retailer that exists in major_retailers."""
        valid_ids = {r.retailer_id for r in self.db.query(MajorRetailers).all()}
        orphans = (
            self.db.query(RetailerStockPrices.retailer_id)
            .filter(RetailerStockPrices.retailer_id.notin_(valid_ids))
            .distinct()
            .all()
        )
        assert not orphans, f"Stock rows reference non-existent retailer_ids: {orphans}"

    def test_ticker_matches_retailer_identity(self):
        """Stock ticker must match the retailer's registered ticker in major_retailers."""
        walmart = self.db.query(MajorRetailers).filter_by(retailer_id=WALMART_ID).first()
        assert walmart is not None
        assert walmart.ticker == WALMART_TICKER
        tickers = {
            r.ticker
            for r in self.db.query(RetailerStockPrices.ticker)
            .filter(RetailerStockPrices.retailer_id == WALMART_ID)
            .distinct()
            .all()
        }
        assert tickers == {WALMART_TICKER}, f"Mixed/incorrect tickers on Walmart bars: {tickers}"

    def test_no_duplicate_latest_dates(self):
        """Append-only discipline: at most one is_latest=True bar per (retailer, date)."""
        from sqlalchemy import func
        dupes = (
            self.db.query(
                RetailerStockPrices.retailer_id,
                RetailerStockPrices.price_date,
                func.count().label("cnt"),
            )
            .filter(RetailerStockPrices.is_latest.is_(True))
            .group_by(RetailerStockPrices.retailer_id, RetailerStockPrices.price_date)
            .having(func.count() > 1)
            .all()
        )
        assert not dupes, f"Duplicate is_latest bars: {dupes}"

    def test_ohlc_integrity_or_flagged(self):
        """For every bar: low<=high and open/close/vwap within [low,high]±tol, OR the
        anomaly is explicitly recorded in data_quality. Never silently wrong.
        """
        failures = []
        for r in self._latest():
            if r.high_price is None or r.low_price is None:
                # partial bar must be flagged
                if not r.data_quality:
                    failures.append(f"{r.price_date}: partial OHLC not flagged")
                continue
            if r.low_price > r.high_price and not r.data_quality:
                failures.append(f"{r.price_date}: low>high not flagged")
            for name, val in (("open", r.open_price), ("close", r.close_price), ("vwap", r.vwap)):
                if val is None:
                    continue
                in_range = (r.low_price - _OHLC_TOL) <= val <= (r.high_price + _OHLC_TOL)
                if not in_range and not r.data_quality:
                    failures.append(f"{r.price_date}: {name}={val} outside range, not flagged")
        assert not failures, "OHLC integrity failures:\n" + "\n".join(failures[:20])

    def test_close_price_always_present(self):
        """close_price is the one non-null field — the spine of the series."""
        missing = [r.price_date for r in self._latest() if r.close_price is None]
        assert not missing, f"Bars missing close_price: {missing[:10]}"

    def test_prices_are_split_adjusted_and_positive(self):
        for r in self._latest():
            assert r.is_split_adjusted is True, f"{r.price_date}: not marked split-adjusted"
            assert r.close_price > 0, f"{r.price_date}: non-positive close {r.close_price}"

    def test_no_absurd_daily_moves(self):
        """A split-adjusted series should have no >35% single-day move; such a move
        would indicate an unadjusted split or a parsing error, not real trading.
        """
        offenders = [
            (r.price_date, float(r.pct_change))
            for r in self._latest()
            if r.pct_change is not None and abs(float(r.pct_change)) > 35
        ]
        assert not offenders, f"Implausible daily moves (possible split artifact): {offenders[:10]}"

    def test_history_depth_spans_multiple_years(self):
        rows = self._latest()
        dates = [r.price_date for r in rows]
        span_years = (max(dates) - min(dates)).days / 365.25
        assert span_years >= 5, f"Only {span_years:.1f}y of history — expected multi-year depth"


class TestStockToFundamentalsLinkage:
    """The whole point: stock data must relate to the SAME retailer's other data."""

    def setup_method(self):
        self.db = SessionLocal()

    def teardown_method(self):
        self.db.close()

    def test_stock_history_overlaps_fiscal_periods(self):
        """Walmart's price history must overlap its fundamentals' reporting periods,
        otherwise the two datasets cannot be cross-referenced.
        """
        series = load_price_series(self.db, WALMART_ID)
        assert len(series) > 0
        price_lo, price_hi = series.dates[0], series.dates[-1]

        filings = (
            self.db.query(RetailerFinancials.filing_date)
            .filter(
                RetailerFinancials.retailer_id == WALMART_ID,
                RetailerFinancials.is_latest.is_(True),
                RetailerFinancials.filing_date.isnot(None),
            )
            .all()
        )
        overlapping = [f.filing_date for f in filings if price_lo <= f.filing_date <= price_hi]
        assert len(overlapping) >= 8, (
            f"Only {len(overlapping)} fiscal filings overlap the price window "
            f"[{price_lo}, {price_hi}] — insufficient cross-reference coverage"
        )

    def test_earnings_reactions_are_computed(self):
        signal = generate_market_signal(self.db, WALMART_ID)
        assert signal is not None
        reactions = signal["earnings_reactions"]
        assert len(reactions) >= 6, f"Expected >=6 earnings reactions, got {len(reactions)}"
        for r in reactions:
            # Each reaction must carry both the market move AND the fundamental it reacts to
            assert r["filing_date"] is not None
            assert r["total_net_sales_usd"] is not None, (
                f"FY{r['fiscal_year']}Q{r['fiscal_quarter']} reaction missing fundamental linkage"
            )
            if r["filing_window_return_pct"] is not None:
                assert abs(r["filing_window_return_pct"]) < 50, "Implausible filing-window return"


class TestMarketSignalCorrectness:

    def setup_method(self):
        self.db = SessionLocal()

    def teardown_method(self):
        self.db.close()

    def test_signal_structure_complete(self):
        s = generate_market_signal(self.db, WALMART_ID)
        assert s is not None
        for key in (
            "as_of_date", "latest_close", "bars", "return_3m_pct", "return_12m_pct",
            "high_52w", "low_52w", "drawdown_from_52w_high_pct",
            "annualized_volatility_pct", "market_demand_signal", "market_implication",
        ):
            assert key in s, f"market signal missing key: {key}"

    def test_52w_high_low_bracket_latest_close(self):
        s = generate_market_signal(self.db, WALMART_ID)
        assert s["low_52w"] <= s["latest_close"] <= s["high_52w"], (
            f"latest close {s['latest_close']} not within 52w range "
            f"[{s['low_52w']}, {s['high_52w']}]"
        )

    def test_drawdown_is_non_positive(self):
        """Drawdown from the 52-week high can only be <= 0."""
        s = generate_market_signal(self.db, WALMART_ID)
        assert s["drawdown_from_52w_high_pct"] <= 0.01

    def test_volatility_in_plausible_range(self):
        """Annualized equity vol for a mega-cap retailer should be ~10-60%."""
        s = generate_market_signal(self.db, WALMART_ID)
        assert 5 <= s["annualized_volatility_pct"] <= 80, (
            f"Implausible annualized vol: {s['annualized_volatility_pct']}%"
        )

    def test_market_signal_is_valid_category(self):
        s = generate_market_signal(self.db, WALMART_ID)
        assert s["market_demand_signal"] in {"bullish", "bearish", "neutral", "unknown"}

    def test_no_stock_data_returns_none(self):
        """A retailer with no price data must return None, not a fabricated signal."""
        # Target (id=1) has no stock data ingested
        s = generate_market_signal(self.db, 1)
        assert s is None, "Expected None for retailer with no stock data"

    def test_returns_are_internally_consistent(self):
        """Recompute 12M return from raw closes and match the engine's value."""
        series = load_price_series(self.db, WALMART_ID)
        latest = series.latest_close
        target = series.latest_date - timedelta(days=365)
        start = series.close_asof(target)
        expected = float(((latest / start) - 1) * 100)
        s = generate_market_signal(self.db, WALMART_ID)
        assert abs(s["return_12m_pct"] - expected) < 0.05, (
            f"12M return mismatch: engine={s['return_12m_pct']} recomputed={expected:.2f}"
        )


class TestEngineMarketIntegration:

    def setup_method(self):
        self.db = SessionLocal()

    def teardown_method(self):
        self.db.close()

    def test_engine_attaches_market_block_for_walmart(self):
        from intelligence.retail_engine import generate_retailer_intelligence
        result = generate_retailer_intelligence(importer_id=1, db=self.db)
        walmart = next(
            (i for i in result["retailer_intelligence"] if i["retailer_id"] == WALMART_ID),
            None,
        )
        assert walmart is not None, "Walmart missing from engine output"
        assert walmart["market"] is not None, "Walmart intelligence has no market block"
        assert walmart["market_demand_signal"] in {"bullish", "bearish", "neutral"}

    def test_divergence_or_confirmation_surfaced_in_implication(self):
        """When market and fundamentals are both directional, the implication must
        explicitly relate them (confirmation or divergence) — not ignore the market.
        """
        from intelligence.retail_engine import generate_retailer_intelligence
        result = generate_retailer_intelligence(importer_id=1, db=self.db)
        walmart = next(
            i for i in result["retailer_intelligence"] if i["retailer_id"] == WALMART_ID
        )
        impl = walmart["implication"].lower()
        market_sig = walmart["market_demand_signal"]
        if market_sig in ("bullish", "bearish"):
            assert any(
                kw in impl for kw in ("market", "divergence", "street", "equity")
            ), "Implication does not relate market view to fundamentals"

    def test_retailer_without_stock_data_still_works(self):
        """Target has no stock data — engine must still produce intelligence, with a
        null market block, never crashing on the missing dimension.
        """
        from intelligence.retail_engine import generate_retailer_intelligence
        result = generate_retailer_intelligence(importer_id=1, db=self.db)
        target = next(
            (i for i in result["retailer_intelligence"] if i["retailer_id"] == 1), None
        )
        assert target is not None
        assert target["market"] is None
        assert target["implication"], "Target lost its fundamental implication"

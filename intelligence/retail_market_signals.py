"""
Market-signal intelligence for the retail layer.

Turns a retailer's daily equity OHLCV series (retailer_stock_prices) into
forward-looking demand intelligence and — critically — cross-references it
against the same retailer's fundamentals (retailer_financials) so the market's
view sits alongside the reported numbers and the earnings-call narrative.

The stock price is the market's *aggregate forward expectation* of the
retailer's demand and margin trajectory. It is a complement to fundamentals,
never a replacement: where the two disagree, that disagreement is itself signal.

All metrics are computed from real observed closes. Nothing is imputed; gaps
(weekends, holidays, missing bars) are handled by "as-of" lookups that take the
most recent close at or before a target date.
"""

from __future__ import annotations

import bisect
import statistics
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from database.models.retail import RetailerFinancials, RetailerStockPrices

# Trading days in a year — for annualizing daily-return volatility.
_TRADING_DAYS_YEAR = 252

# Momentum thresholds for the categorical market_demand_signal.
_BULLISH_3M = Decimal("8.0")     # +8% over 3M with non-negative 12M → bullish
_BEARISH_3M = Decimal("-8.0")
_BEARISH_12M = Decimal("-15.0")


class PriceSeries:
    """Ascending-by-date view of a retailer's is_latest closes, with as-of lookup."""

    def __init__(self, rows: list[RetailerStockPrices]):
        ordered = sorted(rows, key=lambda r: r.price_date)
        self.dates: list[date] = [r.price_date for r in ordered]
        self.closes: list[Decimal] = [r.close_price for r in ordered]
        self.pct: list[Optional[Decimal]] = [r.pct_change for r in ordered]
        self.volumes: list[Optional[int]] = [r.volume for r in ordered]

    def __len__(self) -> int:
        return len(self.dates)

    @property
    def latest_date(self) -> Optional[date]:
        return self.dates[-1] if self.dates else None

    @property
    def latest_close(self) -> Optional[Decimal]:
        return self.closes[-1] if self.closes else None

    def close_asof(self, target: date) -> Optional[Decimal]:
        """Most recent close at or before `target`; None if target precedes all data."""
        idx = bisect.bisect_right(self.dates, target) - 1
        if idx < 0:
            return None
        return self.closes[idx]

    def window(self, start: date, end: date) -> list[Decimal]:
        lo = bisect.bisect_left(self.dates, start)
        hi = bisect.bisect_right(self.dates, end)
        return self.closes[lo:hi]


def load_price_series(db: Session, retailer_id: int) -> PriceSeries:
    rows = (
        db.query(RetailerStockPrices)
        .filter(
            RetailerStockPrices.retailer_id == retailer_id,
            RetailerStockPrices.is_latest.is_(True),
        )
        .all()
    )
    return PriceSeries(rows)


def _pct_return(start: Optional[Decimal], end: Optional[Decimal]) -> Optional[Decimal]:
    if start is None or end is None or start == 0:
        return None
    return ((end / start) - Decimal("1")) * Decimal("100")


def _trailing_return(series: PriceSeries, days: int) -> Optional[Decimal]:
    if series.latest_date is None:
        return None
    target = series.latest_date - timedelta(days=days)
    start = series.close_asof(target)
    # Guard: if the lookback predates all data, there's no honest return to report.
    if start is None or target < series.dates[0]:
        return None
    return _pct_return(start, series.latest_close)


def _ytd_return(series: PriceSeries) -> Optional[Decimal]:
    if series.latest_date is None:
        return None
    jan1 = date(series.latest_date.year, 1, 1)
    start = series.close_asof(jan1 - timedelta(days=1)) or series.close_asof(
        series.dates[bisect.bisect_left(series.dates, jan1)]
        if bisect.bisect_left(series.dates, jan1) < len(series.dates)
        else series.latest_date
    )
    return _pct_return(start, series.latest_close)


def _annualized_volatility(series: PriceSeries, lookback: int = _TRADING_DAYS_YEAR) -> Optional[Decimal]:
    """Std of trailing daily % returns, annualized. Uses stored pct_change when present."""
    pcts = [float(p) for p in series.pct[-lookback:] if p is not None]
    if len(pcts) < 20:
        return None
    daily_std = statistics.pstdev(pcts)
    annualized = daily_std * (_TRADING_DAYS_YEAR ** 0.5)
    return Decimal(str(annualized)).quantize(Decimal("0.01"))


def _drawdown_from_high(series: PriceSeries, lookback_days: int = 365) -> tuple[Optional[Decimal], Optional[Decimal], Optional[Decimal]]:
    """Return (52w_high, 52w_low, drawdown_pct_from_high)."""
    if series.latest_date is None:
        return None, None, None
    start = series.latest_date - timedelta(days=lookback_days)
    window = series.window(start, series.latest_date)
    if not window:
        return None, None, None
    high = max(window)
    low = min(window)
    drawdown = _pct_return(high, series.latest_close)  # negative when below high
    return high, low, drawdown


def _q(value: Optional[Decimal]) -> Optional[Decimal]:
    return value.quantize(Decimal("0.01")) if value is not None else None


def _classify_market_signal(
    r_3m: Optional[Decimal],
    r_12m: Optional[Decimal],
    drawdown: Optional[Decimal],
) -> str:
    """Categorical read of where the market is positioning the retailer."""
    if r_3m is None and r_12m is None:
        return "unknown"
    three = r_3m if r_3m is not None else Decimal("0")
    twelve = r_12m if r_12m is not None else Decimal("0")

    if three >= _BULLISH_3M and twelve >= 0:
        return "bullish"
    if three <= _BEARISH_3M or twelve <= _BEARISH_12M:
        return "bearish"
    if drawdown is not None and drawdown <= Decimal("-20"):
        return "bearish"
    return "neutral"


def compute_earnings_reactions(
    db: Session,
    retailer_id: int,
    series: PriceSeries,
    last_n: int = 8,
    forward_days: int = 5,
) -> list[dict]:
    """Cross-reference each recent fiscal quarter to the stock's reaction.

    For each is_latest RetailerFinancials quarter, measures the stock return over
    the window anchored on the 10-Q/10-K filing_date (the structured catalyst we
    hold): from the last close on/before filing_date to ~forward_days later.

    Limitation, stated honestly: the earnings *press release* typically precedes
    the 10-Q filing by days-to-weeks, so this window captures post-filing
    digestion/drift rather than the announcement pop. It is a directional
    market-reception proxy, not an event-study abnormal return.
    """
    if len(series) == 0:
        return []

    quarters = (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.is_latest.is_(True),
            RetailerFinancials.filing_date.isnot(None),
        )
        .order_by(
            RetailerFinancials.fiscal_year.desc(),
            RetailerFinancials.fiscal_quarter.desc(),
        )
        .limit(last_n)
        .all()
    )

    reactions: list[dict] = []
    for q in quarters:
        anchor = q.filing_date
        if anchor < series.dates[0] or anchor > series.latest_date:
            continue
        close_at_filing = series.close_asof(anchor)
        close_forward = series.close_asof(anchor + timedelta(days=forward_days + 4))
        reaction = _pct_return(close_at_filing, close_forward)
        reactions.append(
            {
                "fiscal_year": q.fiscal_year,
                "fiscal_quarter": q.fiscal_quarter,
                "period_end_date": str(q.period_end_date) if q.period_end_date else None,
                "filing_date": str(anchor),
                "close_at_filing": float(close_at_filing) if close_at_filing is not None else None,
                "filing_window_return_pct": float(_q(reaction)) if reaction is not None else None,
                "total_net_sales_usd": (
                    float(q.total_net_sales_usd) if q.total_net_sales_usd is not None else None
                ),
                "gross_margin_pct": (
                    float(q.gross_margin_pct) if q.gross_margin_pct is not None else None
                ),
                "comparable_sales_growth_pct": (
                    float(q.comparable_sales_growth_pct)
                    if q.comparable_sales_growth_pct is not None
                    else None
                ),
            }
        )
    return reactions


def _build_market_implication(
    signal: str,
    r_3m: Optional[Decimal],
    r_12m: Optional[Decimal],
    drawdown: Optional[Decimal],
    vol: Optional[Decimal],
) -> str:
    r3 = f"{float(r_3m):+.1f}%" if r_3m is not None else "n/a"
    r12 = f"{float(r_12m):+.1f}%" if r_12m is not None else "n/a"
    dd = f"{float(drawdown):.1f}%" if drawdown is not None else "n/a"

    if signal == "bullish":
        return (
            f"Market is pricing in demand strength — shares up {r3} over 3M ({r12} over 12M). "
            f"An operator should read this as the Street expecting the retailer to sustain or "
            f"grow buying; commit capacity ahead of competitors who wait for the next print."
        )
    if signal == "bearish":
        return (
            f"Market is pricing in demand softness — shares {r3} over 3M, {r12} over 12M, "
            f"now {dd} off the 52-week high. Treat open programs with this retailer as at "
            f"elevated cancellation/markdown risk and confirm volumes before adding production."
        )
    if signal == "unknown":
        return "Insufficient price history to read a market demand signal for this retailer."
    return (
        f"Market view is balanced — shares {r3} over 3M, {r12} over 12M. No strong directional "
        f"read; let fundamentals and order signals lead, using price moves only as confirmation."
    )


def generate_market_signal(db: Session, retailer_id: int) -> Optional[dict]:
    """Full market-signal block for one retailer, or None if no price data exists."""
    series = load_price_series(db, retailer_id)
    if len(series) == 0:
        return None

    r_1m = _trailing_return(series, 30)
    r_3m = _trailing_return(series, 91)
    r_6m = _trailing_return(series, 182)
    r_12m = _trailing_return(series, 365)
    r_ytd = _ytd_return(series)
    high_52w, low_52w, drawdown = _drawdown_from_high(series)
    vol = _annualized_volatility(series)

    recent_vols = [v for v in series.volumes[-63:] if v is not None]
    avg_daily_volume_3m = int(sum(recent_vols) / len(recent_vols)) if recent_vols else None

    signal = _classify_market_signal(r_3m, r_12m, drawdown)

    return {
        "as_of_date": str(series.latest_date),
        "latest_close": float(series.latest_close) if series.latest_close is not None else None,
        "bars": len(series),
        "history_start": str(series.dates[0]),
        "return_1m_pct": float(_q(r_1m)) if r_1m is not None else None,
        "return_3m_pct": float(_q(r_3m)) if r_3m is not None else None,
        "return_6m_pct": float(_q(r_6m)) if r_6m is not None else None,
        "return_12m_pct": float(_q(r_12m)) if r_12m is not None else None,
        "return_ytd_pct": float(_q(r_ytd)) if r_ytd is not None else None,
        "high_52w": float(high_52w) if high_52w is not None else None,
        "low_52w": float(low_52w) if low_52w is not None else None,
        "drawdown_from_52w_high_pct": float(_q(drawdown)) if drawdown is not None else None,
        "annualized_volatility_pct": float(vol) if vol is not None else None,
        "avg_daily_volume_3m": avg_daily_volume_3m,
        "market_demand_signal": signal,
        "market_implication": _build_market_implication(signal, r_3m, r_12m, drawdown, vol),
        "earnings_reactions": compute_earnings_reactions(db, retailer_id, series),
    }

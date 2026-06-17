"""
FX features pipeline — computes realized volatility and CIP forward curve from existing data.

Outputs:
  fx_volatility   — one row per (as_of_date, currency_pair); 14 pairs × 3,081 dates = ~43k rows
  fx_forward_curve — one row per (as_of_date, currency_pair, tenor_days); 14 × 5 tenors = ~215k rows

Volatility methodology:
  Weekly log returns from fx_rates time series → annualized by √52.
  Windows: 4w (≈30d), 13w (≈90d), 26w (≈180d), 52w (≈365d).

Forward curve methodology (Covered Interest Parity):
  For USD_XXX pairs (foreign per USD):
    F = S × (1 + r_foreign × T/360) / (1 + r_USD × T/360)
  For EUR_USD, GBP_USD (USD per foreign unit, inverted convention):
    F = S × (1 + r_USD × T/360) / (1 + r_foreign × T/360)

  Interest rates sourced from fx_interest_rates (FRED monthly, forward-filled).
  cip_quality = 'proxy' when using policy rate as tenor proxy (most EM cases).

Hedge signal logic:
  vol_regime derived from vol_90d vs its own trailing 3-year distribution.
  hedge_urgency maps regime → action level.
  suggested_hedge_ratio_pct scales with vol_regime (25/50/75/100%).

Run:
  python data/ingestion/fx_features_pipeline.py
  python data/ingestion/fx_features_pipeline.py --from 2010-01-01
"""

from __future__ import annotations

import argparse
import logging
import math
from bisect import bisect_right
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from database.base import SessionLocal
from database.models.market_data import FxForwardCurve, FxInterestRates, FxRates, FxVolatility

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# (fx_rates_column, currency_pair_label, foreign_country_code, inverted_convention)
# inverted=True for EUR_USD/GBP_USD — those are "USD per foreign unit"
CURRENCY_PAIRS: list[tuple[str, str, str, bool]] = [
    ("usd_inr", "USD_INR", "INR", False),
    ("usd_bdt", "USD_BDT", "BDT", False),
    ("usd_vnd", "USD_VND", "VND", False),
    ("usd_cny", "USD_CNY", "CNY", False),
    ("usd_try", "USD_TRY", "TRY", False),
    ("usd_mad", "USD_MAD", "MAD", False),
    ("usd_pkr", "USD_PKR", "PKR", False),
    ("usd_idr", "USD_IDR", "IDR", False),
    ("usd_lkr", "USD_LKR", "LKR", False),
    ("usd_mxn", "USD_MXN", "MXN", False),
    ("usd_thb", "USD_THB", "THB", False),
    ("usd_khr", "USD_KHR", "KHR", False),
    ("eur_usd", "EUR_USD", "EUR", True),
    ("gbp_usd", "GBP_USD", "GBP", True),
]

TENORS = [30, 60, 90, 180, 365]

# Rolling windows in weeks
VOL_WINDOWS  = {"vol_30d_ann": 4,  "vol_90d_ann": 13, "vol_180d_ann": 26, "vol_365d_ann": 52}
MA_WINDOWS   = {"ma_50d": 7,       "ma_200d": 29}
RET_WINDOWS  = {"ret_30d": 4,      "ret_90d": 13,     "ret_180d": 26,     "ret_365d": 52}
RANK_WINDOWS = {"pct_rank_1yr": 52, "pct_rank_3yr": 156, "pct_rank_5yr": 260}

# Regime classification thresholds (percentile of vol_90d vs trailing 3yr vol distribution)
REGIME_THRESHOLDS = {
    "calm":     0.25,
    "normal":   0.75,
    "elevated": 0.95,
    # > 0.95 → "stressed"
}
REGIME_URGENCY  = {"calm": "monitor", "normal": "watch", "elevated": "hedge", "stressed": "urgent"}
REGIME_RATIO    = {"calm": 25.0,      "normal": 50.0,    "elevated": 75.0,    "stressed": 100.0}

# Percentile bands per regime (for self-describing fx_volatility rows). Mirrors
# REGIME_THRESHOLDS above; expressed as 0–100 to match the stored columns.
REGIME_BANDS = {
    "calm":     (0.0, 25.0),
    "normal":   (25.0, 75.0),
    "elevated": (75.0, 95.0),
    "stressed": (95.0, 100.0),
}
REGIME_METHODOLOGY = (
    "Realised-volatility regime from vol_90d_ann vs its own trailing 3-year "
    "(156-week) distribution. calm: <25th percentile. normal: 25th-75th. "
    "elevated: 75th-95th. stressed: >95th. Recomputed weekly from fx_rates "
    "weekly-close log returns (annualised x sqrt(52))."
)
VOL_REGIME_WINDOW_DAYS = 90

# Executability note keyed by forward_market_liquidity (from fx_currency_config).
EXEC_NOTE = {
    "liquid": "Forward/NDF market liquid. Executable at standard tenors.",
    "semi_liquid": "Forward/NDF market exists but bid/ask wide. Executable with a "
                   "broker relationship.",
    "cip_implied_only": "NO liquid forward market. Rate is CIP-implied from the "
                        "interest-rate differential only and is NOT executable. "
                        "Use for cost-analysis reference, never for hedging decisions.",
}


def _load_liquidity_map(db) -> dict[str, str]:
    """currency_pair -> forward_market_liquidity from fx_currency_config.

    Single source of truth for executability. Empty dict if the table is
    unseeded (forward rows then keep NULL observability — honest, not wrong)."""
    from database.models.market_data import FxCurrencyConfig
    return {
        r.currency_pair: r.forward_market_liquidity
        for r in db.query(FxCurrencyConfig).all()
        if r.forward_market_liquidity
    }

BATCH_SIZE = 500


# ── Pure math helpers ──────────────────────────────────────────────────────────

def _log_ret(a: float, b: float) -> float:
    """log(b/a) — positive when b > a (rate increased)."""
    return math.log(b / a)


def _rolling_vol_ann(values: list[float], window: int) -> list[Optional[float]]:
    """Annualized realized vol from a list of spot rates using weekly log returns × √52.

    Returns a list aligned to `values` — first `window` entries are None.
    """
    result: list[Optional[float]] = [None] * len(values)
    for i in range(window, len(values)):
        window_vals = values[i - window: i + 1]   # window+1 prices → window returns
        rets = [_log_ret(window_vals[j], window_vals[j + 1]) for j in range(window)]
        if not rets:
            continue
        mean = sum(rets) / len(rets)
        variance = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
        result[i] = math.sqrt(variance) * math.sqrt(52)
    return result


def _rolling_mean(values: list[float], window: int) -> list[Optional[float]]:
    result: list[Optional[float]] = [None] * len(values)
    for i in range(window - 1, len(values)):
        result[i] = sum(values[i - window + 1: i + 1]) / window
    return result


def _cumulative_log_ret(values: list[float], window: int) -> list[Optional[float]]:
    """log(values[i] / values[i-window]) — cumulative return over exactly `window` steps."""
    result: list[Optional[float]] = [None] * len(values)
    for i in range(window, len(values)):
        result[i] = _log_ret(values[i - window], values[i])
    return result


def _pct_rank(values: list[float], window: int) -> list[Optional[float]]:
    """0–100 percentile rank of values[i] within values[i-window+1..i]."""
    result: list[Optional[float]] = [None] * len(values)
    for i in range(window - 1, len(values)):
        window_vals = values[i - window + 1: i + 1]
        current = values[i]
        rank = sum(1.0 for v in window_vals if v <= current) / window * 100.0
        result[i] = rank
    return result


def _classify_vol_regime(
    vol_90d_series: list[Optional[float]],
    idx: int,
    history_window: int = 156,   # 3-year trailing vol distribution
) -> Optional[str]:
    current = vol_90d_series[idx]
    if current is None:
        return None
    start = max(0, idx - history_window + 1)
    past = [v for v in vol_90d_series[start: idx + 1] if v is not None]
    if len(past) < 13:
        return "normal"
    pct = sum(1 for v in past if v <= current) / len(past)
    if pct < REGIME_THRESHOLDS["calm"]:
        return "calm"
    if pct < REGIME_THRESHOLDS["normal"]:
        return "normal"
    if pct < REGIME_THRESHOLDS["elevated"]:
        return "elevated"
    return "stressed"


# ── Interest rate lookup ───────────────────────────────────────────────────────

class IRLookup:
    """Forward-fills monthly FRED rates to any arbitrary date."""

    def __init__(self, db: Session) -> None:
        rows = (
            db.query(FxInterestRates)
            .order_by(FxInterestRates.country_code, FxInterestRates.as_of_date)
            .all()
        )
        self._policy: dict[str, tuple[list[date], list[float]]] = {}
        self._bond1yr: dict[str, tuple[list[date], list[float]]] = {}
        for row in rows:
            code = row.country_code
            if row.policy_rate_pct is not None:
                d_list, v_list = self._policy.setdefault(code, ([], []))
                d_list.append(row.as_of_date)
                v_list.append(float(row.policy_rate_pct))
            if row.gov_bond_1yr_pct is not None:
                d_list, v_list = self._bond1yr.setdefault(code, ([], []))
                d_list.append(row.as_of_date)
                v_list.append(float(row.gov_bond_1yr_pct))

        covered = len(self._policy)
        logger.info("IRLookup: loaded %d country series", covered)

    def policy_rate(self, country_code: str, as_of: date) -> Optional[float]:
        return self._lookup(self._policy, country_code, as_of)

    def bond_1yr(self, country_code: str, as_of: date) -> Optional[float]:
        return self._lookup(self._bond1yr, country_code, as_of)

    @staticmethod
    def _lookup(
        store: dict[str, tuple[list[date], list[float]]],
        code: str,
        target: date,
    ) -> Optional[float]:
        if code not in store:
            return None
        dates, vals = store[code]
        idx = bisect_right(dates, target) - 1   # largest date ≤ target
        if idx < 0:
            return None
        return vals[idx]


# ── CIP forward rate computation ───────────────────────────────────────────────

def _cip_forward(
    spot: float,
    r_usd: float,
    r_foreign: float,
    tenor_days: int,
    inverted: bool,
) -> tuple[float, float]:
    """Returns (implied_forward, annualized_premium_pct).

    inverted=True for EUR_USD/GBP_USD (USD per foreign unit).
    forward_premium_pct_ann > 0 → foreign currency weakens vs USD in forward market.
    """
    T = tenor_days / 360.0
    r_d = r_usd / 100.0
    r_f = r_foreign / 100.0

    if inverted:
        # F = S × (1 + r_USD × T) / (1 + r_foreign × T)
        fwd = spot * (1 + r_d * T) / (1 + r_f * T)
    else:
        # F = S × (1 + r_foreign × T) / (1 + r_USD × T)
        fwd = spot * (1 + r_f * T) / (1 + r_d * T)

    premium_ann = ((fwd - spot) / spot) * (360 / tenor_days) * 100.0
    return fwd, premium_ann


# ── FX rates deduplication ────────────────────────────────────────────────────

def _load_deduplicated_rates(db: Session, start: date) -> list[FxRates]:
    """One row per as_of_date, keeping the highest fx_rate_id (most recent write)."""
    subq = (
        db.query(func.max(FxRates.fx_rate_id).label("max_id"))
        .filter(FxRates.as_of_date >= start)
        .group_by(FxRates.as_of_date)
        .subquery()
    )
    return (
        db.query(FxRates)
        .join(subq, FxRates.fx_rate_id == subq.c.max_id)
        .order_by(FxRates.as_of_date)
        .all()
    )


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_pipeline(start: date) -> None:
    db = SessionLocal()
    now = datetime.now(timezone.utc)

    try:
        logger.info("Loading interest rates...")
        ir = IRLookup(db)

        logger.info("Loading fx_rates (deduplicated, from %s)...", start)
        fx_rows = _load_deduplicated_rates(db, start)
        logger.info("  %d rows loaded", len(fx_rows))

        # Pre-load existing keys to avoid O(N) SELECT queries in the inner loop.
        # On a fresh run both sets are empty → all rows go to bulk insert (fast).
        # On re-runs only the new dates need inserts; existing rows are updated.
        logger.info("Pre-loading existing volatility + forward curve keys...")
        existing_vol: set[tuple[date, str]] = {
            (r.as_of_date, r.currency_pair)
            for r in db.query(FxVolatility.as_of_date, FxVolatility.currency_pair).all()
        }
        existing_fwd: set[tuple[date, str, int]] = {
            (r.as_of_date, r.currency_pair, r.tenor_days)
            for r in db.query(
                FxForwardCurve.as_of_date,
                FxForwardCurve.currency_pair,
                FxForwardCurve.tenor_days,
            ).all()
        }
        logger.info("  existing_vol=%d, existing_fwd=%d", len(existing_vol), len(existing_fwd))

        liquidity_map = _load_liquidity_map(db)
        logger.info("  liquidity_map: %d pairs from fx_currency_config", len(liquidity_map))

        vol_written = 0
        fwd_written = 0

        for col, pair, foreign_cc, inverted in CURRENCY_PAIRS:
            logger.info("Processing %s...", pair)

            # Extract (date, rate) series for this pair
            series: list[tuple[date, float]] = []
            for row in fx_rows:
                val = getattr(row, col)
                if val is not None:
                    series.append((row.as_of_date, float(val)))

            if len(series) < 5:
                logger.warning("  %s: too few data points (%d) — skipping", pair, len(series))
                continue

            dates = [s[0] for s in series]
            rates = [s[1] for s in series]
            n = len(rates)

            # ── Compute all rolling metrics ────────────────────────────────
            vol_series   = {k: _rolling_vol_ann(rates, w) for k, w in VOL_WINDOWS.items()}
            ma_series    = {k: _rolling_mean(rates, w) for k, w in MA_WINDOWS.items()}
            ret_series   = {k: _cumulative_log_ret(rates, w) for k, w in RET_WINDOWS.items()}
            rank_series  = {k: _pct_rank(rates, w) for k, w in RANK_WINDOWS.items()}

            vol_90d_list = vol_series["vol_90d_ann"]

            # ── Build fx_volatility rows (set-lookup instead of per-row SELECT) ──
            vol_new: list[FxVolatility] = []
            for i in range(n):
                d = dates[i]
                spot = rates[i]
                vol_90d = vol_90d_list[i]
                regime = _classify_vol_regime(vol_90d_list, i)
                urgency     = REGIME_URGENCY.get(regime) if regime else None
                hedge_ratio = REGIME_RATIO.get(regime) if regime else None
                band        = REGIME_BANDS.get(regime) if regime else None
                ma_200 = ma_series["ma_200d"][i]
                above_200 = (spot > ma_200) if ma_200 is not None else None

                kwargs = dict(
                    spot_rate                 = Decimal(str(round(spot, 6))),
                    vol_30d_ann               = _dec4(vol_series["vol_30d_ann"][i]),
                    vol_90d_ann               = _dec4(vol_90d),
                    vol_180d_ann              = _dec4(vol_series["vol_180d_ann"][i]),
                    vol_365d_ann              = _dec4(vol_series["vol_365d_ann"][i]),
                    ma_50d                    = _dec6(ma_series["ma_50d"][i]),
                    ma_200d                   = _dec6(ma_200),
                    above_ma_200d             = above_200,
                    ret_30d                   = _dec4(ret_series["ret_30d"][i]),
                    ret_90d                   = _dec4(ret_series["ret_90d"][i]),
                    ret_180d                  = _dec4(ret_series["ret_180d"][i]),
                    ret_365d                  = _dec4(ret_series["ret_365d"][i]),
                    pct_rank_1yr              = _dec2(rank_series["pct_rank_1yr"][i]),
                    pct_rank_3yr              = _dec2(rank_series["pct_rank_3yr"][i]),
                    pct_rank_5yr              = _dec2(rank_series["pct_rank_5yr"][i]),
                    vol_regime                = regime,
                    vol_window_days           = VOL_REGIME_WINDOW_DAYS if regime else None,
                    regime_methodology        = REGIME_METHODOLOGY if regime else None,
                    regime_percentile_low     = Decimal(str(band[0])) if band else None,
                    regime_percentile_high    = Decimal(str(band[1])) if band else None,
                    hedge_urgency             = urgency,
                    suggested_hedge_ratio_pct = Decimal(str(hedge_ratio)) if hedge_ratio else None,
                    computed_at               = now,
                )
                key = (d, pair)
                if key in existing_vol:
                    # Update in-place (only for re-runs; rare)
                    db.query(FxVolatility).filter(
                        FxVolatility.as_of_date == d,
                        FxVolatility.currency_pair == pair,
                    ).update(kwargs)
                else:
                    vol_new.append(FxVolatility(as_of_date=d, currency_pair=pair, **kwargs))

            if vol_new:
                db.bulk_save_objects(vol_new)
                vol_written += len(vol_new)

            # ── Build fx_forward_curve rows (same set-lookup pattern) ─────────
            fwd_new: list[FxForwardCurve] = []
            for i in range(n):
                d = dates[i]
                spot = rates[i]
                r_usd = ir.policy_rate("USD", d)
                r_for = ir.policy_rate(foreign_cc, d)

                for tenor in TENORS:
                    if r_usd is None or r_for is None:
                        quality  = "no_ir"
                        fwd      = None
                        premium  = None
                        r_used_d = None
                        r_used_f = None
                    else:
                        r_usd_t = ir.bond_1yr("USD", d) or r_usd if tenor == 365 else r_usd
                        fwd_val, premium_val = _cip_forward(spot, r_usd_t, r_for, tenor, inverted)
                        fwd      = Decimal(str(round(fwd_val, 6)))
                        premium  = Decimal(str(round(premium_val, 4)))
                        r_used_d = Decimal(str(round(r_usd_t, 4)))
                        r_used_f = Decimal(str(round(r_for, 4)))
                        quality  = "proxy"

                    liq = liquidity_map.get(pair)
                    kwargs_f = dict(
                        spot_rate               = Decimal(str(round(spot, 6))),
                        implied_forward_rate    = fwd,
                        forward_premium_pct_ann = premium,
                        domestic_rate_pct       = r_used_d,
                        foreign_rate_pct        = r_used_f,
                        cip_quality             = quality,
                        market_liquidity        = liq,
                        is_market_observable    = (liq != "cip_implied_only") if liq else None,
                        execution_note          = EXEC_NOTE.get(liq) if liq else None,
                        computed_at             = now,
                    )
                    key_f = (d, pair, tenor)
                    if key_f in existing_fwd:
                        db.query(FxForwardCurve).filter(
                            FxForwardCurve.as_of_date    == d,
                            FxForwardCurve.currency_pair == pair,
                            FxForwardCurve.tenor_days    == tenor,
                        ).update(kwargs_f)
                    else:
                        fwd_new.append(FxForwardCurve(
                            as_of_date=d, currency_pair=pair, tenor_days=tenor,
                            **kwargs_f,
                        ))

            if fwd_new:
                db.bulk_save_objects(fwd_new)
                fwd_written += len(fwd_new)

            db.flush()
            logger.info("  %s — vol: %d new, fwd: %d new", pair, len(vol_new), len(fwd_new))

        db.commit()
        logger.info(
            "Pipeline complete — fx_volatility rows: %d new | fx_forward_curve rows: %d new",
            vol_written, fwd_written,
        )

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _dec4(v: Optional[float]) -> Optional[Decimal]:
    return Decimal(str(round(v, 4))) if v is not None else None

def _dec6(v: Optional[float]) -> Optional[Decimal]:
    return Decimal(str(round(v, 6))) if v is not None else None

def _dec2(v: Optional[float]) -> Optional[Decimal]:
    return Decimal(str(round(v, 2))) if v is not None else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute FX volatility features and CIP forward curve"
    )
    parser.add_argument(
        "--from", dest="start_date",
        default="2004-01-01",
        help="Start date YYYY-MM-DD (default: 2004-01-01)",
    )
    args = parser.parse_args()
    start = date.fromisoformat(args.start_date)
    logger.info("FX features pipeline: %s → today", start)
    run_pipeline(start)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

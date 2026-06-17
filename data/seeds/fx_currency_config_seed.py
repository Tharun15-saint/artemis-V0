"""Seed fx_currency_config and propagate executability/methodology to the FX layer.

Idempotent — safe to re-run. Three steps:

  1. Upsert the 14-pair governance table (underscore PK).
  2. Populate fx_forward_curve.is_market_observable / market_liquidity / execution_note
     by joining on currency_pair — config is the single source of truth.
  3. Backfill fx_volatility regime methodology columns from the AS-BUILT thresholds
     in fx_features_pipeline.py (calm <25th, normal 25–75th, elevated 75–95th,
     stressed >95th of trailing 3yr vol_90d_ann).

Run:
  .venv/bin/python -m data.seeds.fx_currency_config_seed
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from database.base import SessionLocal
from database.models.market_data import FxCurrencyConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# currency_pair, local_ccy, name, country, relevance, tier, fx_field, yf_ticker,
# fred_series, fwd_liquidity, classic_fashion_relevant, notes
CONFIG: list[tuple] = [
    ("USD_INR", "INR", "Indian Rupee", "India", "PRIMARY", 1,
     "usd_inr", "INR=X", "DEXINUS", "semi_liquid", True,
     "Primary procurement currency. RRK yarn suppliers and Tirupur/Coimbatore "
     "spinning mills invoice in INR. ~7% avg annual depreciation. Forward market "
     "exists but bid/ask wide for small operators."),
    ("USD_BDT", "BDT", "Bangladeshi Taka", "Bangladesh", "PRIMARY", 1,
     "usd_bdt", "BDT=X", None, "cip_implied_only", True,
     "Largest garment exporter globally; Classic Fashion's primary competing "
     "origin. Managed float — Bangladesh Bank intervenes. NO liquid forward "
     "market; CIP-implied only. BDT stress is critical for competitor cost watch."),
    ("USD_CNY", "CNY", "Chinese Yuan", "China", "PRIMARY", 1,
     "usd_cny", "CNY=X", "DEXCHUS", "semi_liquid", True,
     "Competing apparel origin and fabric/trim supplier. PBOC-managed. Renminbi "
     "moves directly affect Classic Fashion competitiveness. Most liquid EM "
     "forward market of the set."),
    ("USD_VND", "VND", "Vietnamese Dong", "Vietnam", "SECONDARY", 2,
     "usd_vnd", "VND=X", None, "semi_liquid", True,
     "Major competing apparel origin (Nike, Gap, H&M benchmark). SBV-managed. "
     "Forward market semi-liquid. Key signal for US-importer alternatives to Jordan."),
    ("USD_PKR", "PKR", "Pakistani Rupee", "Pakistan", "SECONDARY", 2,
     "usd_pkr", "PKR=X", None, "cip_implied_only", True,
     "Cotton and yarn origin. Multiple devaluation events (2018, 2023). Highly "
     "stressed. No liquid forward market. Monitor for input cost volatility."),
    ("USD_LKR", "LKR", "Sri Lankan Rupee", "Sri Lanka", "SECONDARY", 2,
     "usd_lkr", None, "DEXSLUS", "cip_implied_only", True,
     "MAS Holdings, Brandix operate here. 2022 crisis = ~80% devaluation, the "
     "decade's sharpest manufacturing cost shock. Recovery ongoing. Monitor for "
     "competitor cost shifts."),
    ("USD_IDR", "IDR", "Indonesian Rupiah", "Indonesia", "SECONDARY", 2,
     "usd_idr", "IDR=X", None, "semi_liquid", False,
     "Growing apparel origin (Nike footwear). Apparel still developing. IDR "
     "volatile. Monitor for future sourcing relevance."),
    ("USD_TRY", "TRY", "Turkish Lira", "Turkey", "MONITOR", 3,
     "usd_try", "TRY=X", "DEXTRUS", "liquid", False,
     "Not relevant to Classic Fashion Jordan operations. CBRT 35.5% policy rate; "
     "90d forward premium 31%+ annualised makes hedging prohibitive. High inflation "
     "distorts cost comparisons. EXCLUDE from sourcing cost models."),
    ("USD_MAD", "MAD", "Moroccan Dirham", "Morocco", "MONITOR", 3,
     "usd_mad", "MAD=X", None, "cip_implied_only", False,
     "Nearshore-Europe apparel; not Classic Fashion relevant. Basket-managed, "
     "relatively stable. Included for completeness."),
    ("USD_KHR", "KHR", "Cambodian Riel", "Cambodia", "MONITOR", 3,
     None, "KHR=X", None, "cip_implied_only", False,
     "Cambodian garment trade is largely USD-invoiced in practice; KHR rarely used. "
     "NBC peg ~4000/USD. No internationally published rate, so no CIP forward. "
     "Monitor only."),
    ("USD_MXN", "MXN", "Mexican Peso", "Mexico", "MONITOR", 3,
     "usd_mxn", "MXN=X", "DEXMXUS", "liquid", False,
     "Nearshore-US apparel; USMCA-relevant for US buyers. Not Classic Fashion "
     "Jordan-focused. Liquid forward market."),
    ("USD_THB", "THB", "Thai Baht", "Thailand", "MONITOR", 3,
     "usd_thb", "THB=X", "DEXTHUS", "semi_liquid", False,
     "Upstream textiles / synthetic fabric, not a primary garment origin. Monitor "
     "for input material costs."),
    ("EUR_USD", "EUR", "Euro", "Eurozone", "MONITOR", 3,
     "eur_usd", "EURUSD=X", "DEXUSEU", "liquid", False,
     "Relevant for EU retail programs and Classic Fashion EU-buyer invoicing. Most "
     "liquid FX pair globally. Not a manufacturing currency."),
    ("GBP_USD", "GBP", "British Pound", "United Kingdom", "MONITOR", 3,
     "gbp_usd", "GBPUSD=X", "DEXUSUK", "liquid", False,
     "Relevant for UK retail programs. Not a manufacturing currency."),
]

# AS-BUILT regime bands (fx_features_pipeline.py REGIME_THRESHOLDS).
# vs trailing 3yr (156-week) distribution of vol_90d_ann.
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

# Executability derived from forward_market_liquidity.
EXEC_NOTE = {
    "liquid": "Forward/NDF market liquid. Executable at standard tenors.",
    "semi_liquid": "Forward/NDF market exists but bid/ask wide. Executable with a "
                   "broker relationship.",
    "cip_implied_only": "NO liquid forward market. Rate is CIP-implied from the "
                        "interest-rate differential only and is NOT executable. "
                        "Use for cost-analysis reference, never for hedging decisions.",
}


def seed() -> None:
    db = SessionLocal()
    try:
        # 1. Upsert config rows
        for row in CONFIG:
            (pair, lc, name, country, rel, tier, field, yf, fred,
             liq, cf, notes) = row
            existing = db.get(FxCurrencyConfig, pair)
            if existing:
                existing.local_currency = lc
                existing.local_currency_name = name
                existing.country = country
                existing.manufacturing_relevance = rel
                existing.sourcing_tier = tier
                existing.fx_table_field = field
                existing.yfinance_ticker = yf
                existing.fred_series = fred
                existing.forward_market_liquidity = liq
                existing.classic_fashion_relevant = cf
                existing.notes = notes
                existing.is_active = True
            else:
                db.add(FxCurrencyConfig(
                    currency_pair=pair, local_currency=lc, local_currency_name=name,
                    country=country, manufacturing_relevance=rel, sourcing_tier=tier,
                    fx_table_field=field, yfinance_ticker=yf, fred_series=fred,
                    forward_market_liquidity=liq, classic_fashion_relevant=cf,
                    notes=notes, is_active=True,
                ))
        db.flush()
        logger.info("fx_currency_config: %d pairs upserted", len(CONFIG))

        # 2. Propagate executability to fx_forward_curve from config (join on pair)
        for row in CONFIG:
            pair = row[0]
            liq = row[9]
            observable = 0 if liq == "cip_implied_only" else 1
            note = EXEC_NOTE[liq]
            res = db.execute(text(
                "UPDATE fx_forward_curve SET is_market_observable=:obs, "
                "market_liquidity=:liq, execution_note=:note "
                "WHERE currency_pair=:pair"
            ), {"obs": observable, "liq": liq, "note": note, "pair": pair})
            logger.info("  fwd_curve %s -> %s (%d rows)", pair, liq, res.rowcount)

        # 3. Backfill fx_volatility regime methodology from AS-BUILT bands
        for regime, (low, high) in REGIME_BANDS.items():
            res = db.execute(text(
                "UPDATE fx_volatility SET vol_window_days=90, "
                "regime_methodology=:m, regime_percentile_low=:lo, "
                "regime_percentile_high=:hi WHERE vol_regime=:r"
            ), {"m": REGIME_METHODOLOGY, "lo": low, "hi": high, "r": regime})
            logger.info("  fx_volatility %s -> [%g, %g] (%d rows)",
                        regime, low, high, res.rowcount)

        db.commit()
        logger.info("FX governance seed complete.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()

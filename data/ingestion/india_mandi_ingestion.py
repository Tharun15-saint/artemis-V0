"""
India cotton mandi (farm-gate cash) prices — PLACEHOLDER. NOT YET BUILT.

Source: Agmarknet (agmarknet.gov.in) — Government of India Directorate of Marketing
        and Inspection. Collects daily arrival and price data from APMC-regulated markets.

Why deferred:
  1. API reliability: Agmarknet SOAP/form API has known instability; production-grade
     ingestion requires heavy error handling that still cannot guarantee completeness.
  2. Coverage is uneven: Gujarat mandis (Rajkot, Gondal, Surendranagar) report daily;
     most other states are sporadic. Gappy data creates harder-to-detect noise than NULL.
  3. Variety inconsistency: different mandis report different cotton varieties
     (Wagad, Shankar-6, DCH-32, etc.). Cross-market price comparison requires
     variety mapping that is not reliable across the API.
  4. Real-data-only policy: patchy real data with inconsistent variety labels is worse
     for model training than honest NULLs on origin-specific cotton rows.

When to revisit:
  - If Agmarknet publishes a stable REST API with consistent variety codes.
  - Or if a reliable third-party aggregator (e.g., Knoema, Bloomberg Terminal feed)
    provides cleaned, variety-normalised mandi prices.
  - Or if RRK/Classic Fashion can provide their own purchase price records directly
    (actual transactions beat any survey-based mandi data).

What this script would track when built:
  Table: india_mandi_cotton_price
  - market_name (e.g., "Rajkot", "Gondal", "Surendranagar")
  - state (e.g., "Gujarat", "Maharashtra")
  - variety (e.g., "Shankar-6", "Wagad", "DCH-32")
  - price_inr_per_quintal (100 kg)
  - price_inr_per_kg (derived: price / 100)
  - arrivals_bales (volume at that market on that day)
  - as_of_date
  - vs_ice_near_cents_lb (cross-ref to Cotton table, same week)
  - implied_basis_inr_kg (mandi price - ICE equivalent in INR/kg)

Key model signal: implied_basis_inr_kg — the premium/discount that Gujarat spinners
pay vs the ICE world price. Narrows when global prices rise (arbitrage); widens when
India has a bumper crop. This is the most direct input cost signal for Tirupur spinners.
"""

raise NotImplementedError(
    "india_mandi_ingestion.py is a placeholder — not yet implemented.\n"
    "See module docstring for why this is deferred and what would be needed to build it."
)

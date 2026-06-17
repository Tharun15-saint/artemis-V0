import sqlalchemy as sa
from sqlalchemy import Boolean, Column, Integer, String, Numeric, Date, DateTime
from sqlalchemy.sql import func
from database.base import Base


class Cotton(Base):
    """
    ICE No.2 cotton world benchmark price (Cotlook A / FRED PCOTTINDUSDM).

    Real data only policy:
      - spot_price is always real (FRED PCOTTINDUSDM monthly average)
      - ice_futures_* are real ICE contracts via yfinance or NULL — never synthetic
      - is_real_futures_data = False means no ICE contract data was available;
        futures fields will be NULL for that row
      - data_quality_tier: 'full' (spot + all futures), 'spot_only', 'unavailable'

    The INR fields are materialized at ingestion time using the latest FxRates.usd_inr.
    They are the entry point for the cost chain: cotton_price → yarn_price → fabric_cost.
    """
    __tablename__ = "cotton"
    cotton_id            = Column(Integer, primary_key=True)
    origin_country       = Column(String(100), nullable=False)
    grade                = Column(String(50))
    staple_length        = Column(String(30))

    # --- ICE / world price (USD cents per lb) ---
    spot_price           = Column(Numeric(10, 4))    # FRED PCOTTINDUSDM — always real
    ice_futures_near     = Column(Numeric(10, 4))    # nearest ICE contract — NULL if unavailable
    ice_futures_3m       = Column(Numeric(10, 4))    # NULL if unavailable (never synthetic)
    ice_futures_6m       = Column(Numeric(10, 4))
    ice_futures_9m       = Column(Numeric(10, 4))
    ice_futures_12m      = Column(Numeric(10, 4))
    contango_signal      = Column(Numeric(10, 4))    # (12m - spot) / spot × 100

    # --- INR/kg materialized at ingestion (the cost-chain entry point) ---
    # Formula: (spot_price / 100) / 0.453592 × usd_inr
    spot_price_inr_per_kg     = Column(Numeric(10, 4))
    # The usd_inr rate used at materialization time (for audit/reconciliation)
    fx_usd_inr_at_ingestion   = Column(Numeric(10, 4))

    # --- Data quality ---
    is_real_futures_data      = Column(Boolean, nullable=False, server_default="0")
    futures_contracts_available = Column(Integer)   # count of real ICE contracts (0–5)
    data_quality_tier         = Column(String(20))  # 'full' | 'spot_only' | 'unavailable'

    # --- WASDE enrichment (stamped by wasde_ingestion.py) ---
    wasde_forecast       = Column(Numeric(10, 4))
    wasde_ending_stocks  = Column(Numeric(14, 2))
    wasde_su_ratio_pct   = Column(Numeric(6, 2))

    crop_year            = Column(Integer)
    as_of_date           = Column(Date, primary_key=True)
    source               = Column(String(100), nullable=False, server_default="unknown")
    data_source_url      = Column(String(500), nullable=False, server_default="unknown")
    refresh              = Column(String(50))
    pulled_at            = Column(DateTime, nullable=False, server_default=func.now())
    is_latest            = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at           = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at           = Column(DateTime, server_default=func.now(),
                                  onupdate=func.now(), nullable=False)


class CrudeOil(Base):
    """
    Brent and WTI spot prices. Two sources, two roles:

    SOURCE HIERARCHY:
      PRIMARY (operational): fred_api — FRED DCOILBRENTEU/DCOILWTICO, weekly EOP, 1987–present.
        aggregation_period='weekly'. The live signal for cost-engine triggers:
        dyeing chemical premium flag, polyester yarn cost pressure, freight energy surcharge.
        Updated daily by launchd com.artemis.crude_oil at 18:30.
      ANCHOR (historical): world_bank_pink_sheet — World Bank Pink Sheet monthly averages, 1960–present.
        aggregation_period='monthly'. Used for long-run model calibration and historical analysis.
        Has 797 rows back to 1960. Do not use for operational cost-engine triggers.

    DERIVED COST-ENGINE FIELDS (computed at ingestion, fred_api rows only):
      brent_rolling_4w_avg        — 4-week rolling average of Brent EOP prices. Smoother trigger
                                    signal than raw EOP; reduces false alerts from intraweek spikes.
      brent_dyeing_premium_active — True when rolling_4w_avg > $85/bbl (CRUDE_OIL_DYEING_PRESSURE_THRESHOLD).
                                    The primary boolean gate for the dyeing cost premium in synthesis.py.
      brent_t_minus_4w            — Brent spot 4 weeks prior (28d ±7d). The 'crude input price' for
                                    programs manufactured TODAY — accounts for the ~4w crude→dye chemical
                                    transmission lag (to be calibrated from RRK invoices; see
                                    crude_transmission_calibration table).

    ANOMALY DETECTION:
      price_anomaly_flag  — True when the new price is >3σ from the 30-day mean. Human review gate.
      price_anomaly_sigma — Z-score of price vs 30d mean. NULL when not anomalous.

    INR fields are materialized at ingestion from the latest FxRates.usd_inr.
    trend_30d_pct uses Brent only (global benchmark; WTI has US-supply noise).
    Positive trend_30d_pct = cost pressure building on polyester yarn in ~4–6 weeks.
    """
    __tablename__ = "crude_oil"
    crude_oil_id               = Column(Integer, primary_key=True)
    brent_spot                 = Column(Numeric(10, 4))   # USD/barrel
    wti_spot                   = Column(Numeric(10, 4))   # USD/barrel
    brent_wti_spread_usd       = Column(Numeric(8, 4))    # brent − wti
    trend_30d_pct              = Column(Numeric(6, 2))    # (brent_now − brent_30d_ago) / brent_30d_ago × 100
    brent_inr_per_barrel       = Column(Numeric(12, 4))
    wti_inr_per_barrel         = Column(Numeric(12, 4))
    fx_usd_inr_at_ingestion    = Column(Numeric(10, 4))
    days_since_refresh         = Column(Integer)
    aggregation_period         = Column(String(10), nullable=True)   # 'weekly' | 'monthly'
    # Derived cost-engine fields (added in migration u3v4w5x6y7z8; fred_api rows only)
    brent_rolling_4w_avg       = Column(Numeric(10, 4), nullable=True)
    brent_dyeing_premium_active = Column(Boolean, nullable=True)
    brent_t_minus_4w           = Column(Numeric(10, 4), nullable=True)
    price_anomaly_flag         = Column(Boolean, nullable=False, default=False, server_default="0")
    price_anomaly_sigma        = Column(Numeric(6, 3), nullable=True)
    # EIA daily-resolution derived fields (added migration s3t4u5v6w7x8; eia_daily rows)
    brent_rolling_13w_avg      = Column(Numeric(10, 4), nullable=True)   # 91-day rolling avg
    brent_t_minus_8w           = Column(Numeric(10, 4), nullable=True)   # Brent 56 days prior
    brent_yoy_pct              = Column(Numeric(6, 2), nullable=True)    # year-over-year % change
    wti_brent_spread           = Column(Numeric(8, 4), nullable=True)    # wti − brent (corridor basis)
    # EIA futures curve (added migration q1r2s3t4u5v6)
    # WTI: RCLC1/3 = NYMEX settlement; RCLC4 = 6m proxy (4th nearby); STEO WTIPUUS = 12m forecast
    # Brent: all from EIA STEO BREPUUS (no ICE Brent in EIA petroleum/pri/fut)
    wti_futures_1m             = Column(Numeric(10, 4), nullable=True)
    wti_futures_3m             = Column(Numeric(10, 4), nullable=True)
    wti_futures_6m             = Column(Numeric(10, 4), nullable=True)
    wti_futures_12m            = Column(Numeric(10, 4), nullable=True)
    brent_futures_1m           = Column(Numeric(10, 4), nullable=True)
    brent_futures_3m           = Column(Numeric(10, 4), nullable=True)
    brent_futures_6m           = Column(Numeric(10, 4), nullable=True)
    brent_futures_12m          = Column(Numeric(10, 4), nullable=True)
    brent_contango_signal      = Column(Numeric(6, 4), nullable=True)   # (12m - spot) / spot * 100
    wti_contango_signal        = Column(Numeric(6, 4), nullable=True)
    crude_market_structure     = Column(String(20), nullable=True)       # contango / backwardation / flat
    # Brent futures market provenance (added migration t4u5v6w7x8y9)
    brent_futures_source          = Column(String(50), nullable=True)    # ice_yfinance / cme_delayed / steo_forecast
    brent_futures_is_market_price = Column(Boolean, nullable=True)       # True=real settlement, False=forecast
    brent_futures_delay_minutes   = Column(Integer, nullable=True)       # 0=settlement, 15=delayed, NULL=STEO
    # Data quality audit flags
    data_quality_flag          = Column(String(50), nullable=True)
    data_quality_note          = Column(sa.Text, nullable=True)
    as_of_date                 = Column(Date, primary_key=True)
    source                     = Column(String(100), nullable=False, server_default="unknown")
    data_source_url            = Column(String(500), nullable=False, server_default="unknown")
    refresh                    = Column(String(50))
    pulled_at                  = Column(DateTime, nullable=False, server_default=func.now())
    is_latest                  = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at                 = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                 = Column(DateTime, server_default=func.now(),
                                        onupdate=func.now(), nullable=False)


class PxParaxylene(Base):
    """
    Para-xylene spot price — first petrochemical derivative on the crude → polyester path.
    crude → naphtha → PX → PTA → PET chip → polyester yarn.

    Two ingestion modes:
      source='crude_derived_proxy' (is_proxy=True): computed from Brent spot using
        industry-calibrated coefficients. Directionally useful, ±20% accuracy.
        crude_to_px_ratio tracks whether the processing spread is normal or compressed.
      source='icis_weekly' (is_proxy=False): real Asian spot price from ICIS.
        Use this for precise cost estimation.

    The processing spread (crude_to_px_ratio) has diagnostic value independent of
    whether we have real prices: a compression signals refinery margin stress.
    """
    __tablename__ = "px_paraxylene"
    px_id                = Column(Integer, primary_key=True)
    # Legacy stub columns — retained for backward compatibility; prefer spot_usd_tonne
    asian_spot_price     = Column(Numeric(10, 4))
    pta_price_lag_1_2w   = Column(Numeric(10, 4))
    # Temporal columns added in migration p8q9r0s1t2u3
    as_of_date           = Column(Date, nullable=False, primary_key=True)
    spot_usd_tonne       = Column(Numeric(10, 2), nullable=True)
    crude_to_px_ratio    = Column(Numeric(6, 4), nullable=True)   # px / (brent * 7.33)
    brent_spot_ref       = Column(Numeric(10, 4), nullable=True)  # Brent used for proxy calc
    source               = Column(String(100), nullable=False, server_default="unknown")
    data_source_url      = Column(String(500), nullable=False, server_default="unknown")
    is_proxy             = Column(Boolean, nullable=False, server_default="1")
    is_latest            = Column(Boolean, nullable=False, default=True, server_default="1")
    pulled_at            = Column(DateTime, nullable=False, server_default=func.now())
    created_at           = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at           = Column(DateTime, server_default=func.now(),
                                  onupdate=func.now(), nullable=False)


class Pta(Base):
    """
    Purified Terephthalic Acid — the intermediary between PX and PET chip.
    PX → PTA conversion adds ~$80–120/tonne in normal markets.

    Chinese spot is most relevant: >70% of Asian PTA capacity is in China (Hengli,
    Yisheng, Xinfengming). Chinese export price governs Tirupur polyester yarn cost.
    """
    __tablename__ = "pta"
    pta_id               = Column(Integer, primary_key=True)
    # Legacy stub columns
    chinese_spot         = Column(Numeric(10, 4))
    asian_export         = Column(Numeric(10, 4))
    polyester_chip_price = Column(Numeric(10, 4))
    # Temporal columns added in migration p8q9r0s1t2u3
    as_of_date           = Column(Date, nullable=False, primary_key=True)
    spot_usd_tonne       = Column(Numeric(10, 2), nullable=True)  # Chinese spot (USD/tonne)
    px_to_pta_spread_usd = Column(Numeric(8, 2), nullable=True)   # pta - px (conversion margin)
    brent_spot_ref       = Column(Numeric(10, 4), nullable=True)
    source               = Column(String(100), nullable=False, server_default="unknown")
    data_source_url      = Column(String(500), nullable=False, server_default="unknown")
    is_proxy             = Column(Boolean, nullable=False, server_default="1")
    is_latest            = Column(Boolean, nullable=False, default=True, server_default="1")
    pulled_at            = Column(DateTime, nullable=False, server_default=func.now())
    created_at           = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at           = Column(DateTime, server_default=func.now(),
                                  onupdate=func.now(), nullable=False)


class PolyesterPetChips(Base):
    """
    Polyester PET chip — the direct feedstock for polyester yarn spinning.
    This is the most operationally relevant price for Tirupur cost estimation.

    In CVC (chief-value-cotton) programs (60/40 C/P), PET chip cost governs the
    polyester yarn component. For polyester fleece, it is the primary cost driver.

    PTA → chip conversion adds ~$80–150/tonne (includes MEG, polymerisation, and margin).
    MEG (mono-ethylene glycol, ~33% by weight, also crude-derived) is embedded in the
    proxy formula rather than tracked separately.
    """
    __tablename__ = "polyester_pet_chips"
    chip_id                    = Column(Integer, primary_key=True)
    # Legacy stub columns
    chinese_spot               = Column(Numeric(10, 4))
    asian_spot                 = Column(Numeric(10, 4))
    polyester_yarn_price_lag   = Column(Numeric(10, 4))
    # Temporal columns added in migration p8q9r0s1t2u3
    as_of_date                 = Column(Date, nullable=False, primary_key=True)
    spot_usd_tonne             = Column(Numeric(10, 2), nullable=True)  # Chinese/Asian spot
    pta_to_chip_spread_usd     = Column(Numeric(8, 2), nullable=True)   # chip - pta (polymerisation margin)
    brent_spot_ref             = Column(Numeric(10, 4), nullable=True)
    source                     = Column(String(100), nullable=False, server_default="unknown")
    data_source_url            = Column(String(500), nullable=False, server_default="unknown")
    is_proxy                   = Column(Boolean, nullable=False, server_default="1")
    is_latest                  = Column(Boolean, nullable=False, default=True, server_default="1")
    pulled_at                  = Column(DateTime, nullable=False, server_default=func.now())
    created_at                 = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                 = Column(DateTime, server_default=func.now(),
                                        onupdate=func.now(), nullable=False)


class ViscoseRayon(Base):
    __tablename__ = "viscose_rayon"
    viscose_id           = Column(Integer, primary_key=True)
    asian_spot_price     = Column(Numeric(10, 4))
    blended_yarn_price   = Column(Numeric(10, 4))
    created_at           = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at           = Column(DateTime, server_default=func.now(),
                                  onupdate=func.now(), nullable=False)


class CftcCottonCot(Base):
    """
    CFTC Commitments of Traders — ICE Cotton No. 2 futures (legacy format, futures-only).

    Published every Friday by the CFTC, covering positions as of the prior Tuesday.
    All large traders above CFTC reporting thresholds are legally required to file.
    This is regulatory enforcement data — not self-reported or voluntary.

    Key model signal: noncomm_net_pct_oi (speculative net long as % of open interest).
      > +20%: crowded long — elevated reversal risk
      < -10%: crowded short — squeeze/bounce potential
      near 0%: neutral positioning, price driven by fundamentals

    Source: CFTC SOCRATA API (publicreporting.cftc.gov), no API key required.
    Market: COTTON NO. 2 - ICE FUTURES U.S. | commodity_code=033
    """
    __tablename__ = "cftc_cotton_cot"

    cot_id               = Column(Integer, primary_key=True)
    report_date          = Column(Date, nullable=False, primary_key=True)   # Tuesday "as of" date
    report_week          = Column(String(20))             # e.g. "2026 Report Week 23"

    # Open interest
    open_interest        = Column(Integer)

    # Non-commercial (speculators: hedge funds, CTAs, managed money)
    noncomm_long         = Column(Integer)
    noncomm_short        = Column(Integer)
    noncomm_spreading    = Column(Integer)   # spread positions (long+short offset)
    noncomm_net          = Column(Integer)   # computed at write: long - short
    noncomm_net_pct_oi   = Column(Numeric(6, 2))  # (long-short)/oi × 100 — key signal

    # Commercial (hedgers: merchants, mills, producers)
    comm_long            = Column(Integer)
    comm_short           = Column(Integer)
    comm_net             = Column(Integer)   # computed at write: long - short

    # Non-reportable (small traders below reporting threshold)
    nonrept_long         = Column(Integer)
    nonrept_short        = Column(Integer)

    # Trader counts (how many entities hold each category)
    traders_noncomm_long = Column(Integer)
    traders_noncomm_short= Column(Integer)
    traders_comm_long    = Column(Integer)
    traders_comm_short   = Column(Integer)
    traders_total        = Column(Integer)

    # Week-over-week changes
    chg_open_interest    = Column(Integer)
    chg_noncomm_long     = Column(Integer)
    chg_noncomm_short    = Column(Integer)
    chg_noncomm_net      = Column(Integer)   # computed at write: chg_long - chg_short
    chg_comm_long        = Column(Integer)
    chg_comm_short       = Column(Integer)

    # Pct of open interest
    pct_oi_noncomm_long  = Column(Numeric(6, 2))
    pct_oi_noncomm_short = Column(Numeric(6, 2))
    pct_oi_comm_long     = Column(Numeric(6, 2))
    pct_oi_comm_short    = Column(Numeric(6, 2))

    source               = Column(String(100), nullable=False, server_default="cftc_socrata")
    data_source_url      = Column(String(500))
    pulled_at            = Column(DateTime, nullable=False, server_default=func.now())
    is_latest            = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at           = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at           = Column(DateTime, server_default=func.now(),
                                  onupdate=func.now(), nullable=False)

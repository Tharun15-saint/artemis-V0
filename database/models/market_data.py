from sqlalchemy import Boolean, Column, Integer, String, Numeric, Date, DateTime, Text, UniqueConstraint
from sqlalchemy.sql import func
from database.base import Base


class FxRates(Base):
    __tablename__ = "fx_rates"
    fx_rate_id       = Column(Integer, primary_key=True)
    # Pairs quoted as "currency units per 1 USD" (e.g. usd_inr=84.5 → 84.5 INR per dollar)
    usd_inr          = Column(Numeric(10, 4))
    usd_bdt          = Column(Numeric(10, 4))
    usd_vnd          = Column(Numeric(10, 4))
    usd_cny          = Column(Numeric(10, 4))
    usd_try          = Column(Numeric(10, 4))
    usd_mad          = Column(Numeric(10, 4))
    usd_pkr          = Column(Numeric(10, 4))
    # SE/South Asian competitors — paired with India for cost-competitiveness signals
    usd_idr          = Column(Numeric(12, 2))  # Indonesian Rupiah (~16,000/USD; yfinance IDR=X)
    usd_lkr          = Column(Numeric(10, 4))  # Sri Lanka Rupee (~325/USD; FRED DEXSLUS)
    usd_mxn          = Column(Numeric(10, 4))  # Mexican Peso (~17/USD; FRED DEXMXUS)
    usd_thb          = Column(Numeric(10, 4))  # Thai Baht (~33/USD; FRED DEXTHUS)
    usd_khr          = Column(Numeric(10, 2))  # Cambodian Riel (~4,000/USD; NBC peg; yfinance KHR=X)
    # Pairs quoted as "USD per 1 foreign unit" — needed for UK/EU buyer value flows
    eur_usd          = Column(Numeric(10, 6))  # USD per 1 EUR (FRED DEXUSEU)
    gbp_usd          = Column(Numeric(10, 6))  # USD per 1 GBP (FRED DEXUSUK)
    source           = Column(String(100), nullable=False, server_default="unknown")
    data_source_url  = Column(String(500), nullable=False, server_default="unknown")
    refresh          = Column(String(50))
    status           = Column(String(50))
    data_gap_notes   = Column(Text)
    as_of_date       = Column(Date, primary_key=True)
    pulled_at        = Column(DateTime, nullable=False, server_default=func.now())
    is_latest        = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at       = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at       = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class CommodityFutures(Base):
    """
    Forward curve snapshots for all Artemis commodities, one row per observation date.

    Taxonomy 8.1 requires futures at 3m/6m/9m/12m tenor for each commodity.
    Columns are sparse: a cotton-futures-only row will have NULLs in the crude columns
    and vice versa. The synthesis engine must handle sparse rows gracefully.

    Crude source: EIA Short-Term Energy Outlook (STEO), /v2/steo/data/
      Brent: BREPUUS — monthly forecast, updated monthly by EIA
      WTI:   WTIPUUS — monthly forecast, updated monthly by EIA
      Covers 18 months forward. 3m/6m/9m/12m tenors computed by selecting the
      STEO forecast for the month that is N months from ingestion date.
      NOTE: these are EIA model projections (authoritative government forecasts),
      not exchange-traded settlement prices. They represent EIA's best estimate of
      average monthly prices, suitable for cost-pressure trend analysis.
    Cotton source: yfinance ICE No. 2 contracts (existing pipeline).

    contango_pct interpretation:
      positive = EIA projects prices to be higher in N months (cost pressure persists)
      negative = EIA projects prices to be lower in N months (cost pressure transient)
    """
    __tablename__ = "commodity_futures"
    commodity_futures_id = Column(Integer, primary_key=True)
    # ICE Cotton No. 2 futures (existing; populated by cotton_futures ingestion)
    ice_cotton_2_spot    = Column(Numeric(10, 4))
    ice_cotton_2_3m      = Column(Numeric(10, 4))
    ice_cotton_2_6m      = Column(Numeric(10, 4))
    ice_cotton_2_9m      = Column(Numeric(10, 4))
    ice_cotton_2_12m     = Column(Numeric(10, 4))
    ocean_freight_ffa    = Column(Numeric(10, 4))
    # Crude oil forward curve — EIA STEO (BREPUUS/WTIPUUS), migrations o7p8q9r0s1t2 + t2u3v4w5x6y7
    # Brent forwards: EIA STEO BREPUUS forecast (USD/barrel)
    brent_3m_fwd           = Column(Numeric(10, 4))
    brent_6m_fwd           = Column(Numeric(10, 4))
    brent_9m_fwd           = Column(Numeric(10, 4))
    brent_12m_fwd          = Column(Numeric(10, 4))
    # WTI forwards: EIA STEO WTIPUUS forecast (USD/barrel)
    wti_3m_fwd             = Column(Numeric(10, 4))
    wti_6m_fwd             = Column(Numeric(10, 4))
    wti_9m_fwd             = Column(Numeric(10, 4))
    wti_12m_fwd            = Column(Numeric(10, 4))
    # Derived: (forward - spot) / spot * 100, spot = latest crude_oil row
    brent_3m_contango_pct  = Column(Numeric(6, 2))
    brent_9m_contango_pct  = Column(Numeric(6, 2))
    brent_12m_contango_pct = Column(Numeric(6, 2))
    wti_3m_contango_pct    = Column(Numeric(6, 2))
    wti_12m_contango_pct   = Column(Numeric(6, 2))
    # Signal: 'contango' | 'flat' | 'backwardation' (driven by brent_12m_contango_pct)
    crude_curve_signal     = Column(String(20))
    # Source tag: 'eia_steo' for crude rows
    crude_source           = Column(String(50))
    as_of_date           = Column(Date)
    source               = Column(String(100), nullable=False, server_default="unknown")
    data_source_url      = Column(String(500), nullable=False, server_default="unknown")
    status               = Column(String(50))
    pulled_at            = Column(DateTime, nullable=False, server_default=func.now())
    is_latest            = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at           = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at           = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class Shipper(Base):
    __tablename__ = "shipper"
    shipper_id       = Column(Integer, primary_key=True)
    factory_name     = Column(String(255))
    country          = Column(String(100))
    hs_codes_shipped = Column(String(255))
    volume_by_month  = Column(String(255))
    active_buyers    = Column(String(255))
    yoy_trend        = Column(Numeric(8, 2))
    as_of_date       = Column(Date)
    source           = Column(String(255))
    created_at       = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at       = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class Consignee(Base):
    __tablename__ = "consignee"
    consignee_id              = Column(Integer, primary_key=True)
    company_name              = Column(String(255))
    sourcing_country_mix      = Column(String(255))
    monthly_volume            = Column(String(255))
    yoy_origin_shift          = Column(String(255))
    new_factory_relationships = Column(Integer)
    as_of_date                = Column(Date)
    created_at                = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class TradeFlowSignals(Base):
    __tablename__ = "trade_flow_signals"
    trade_signal_id        = Column(Integer, primary_key=True)
    market_share_by_origin = Column(String(255))
    competitor_shifts      = Column(String(255))
    new_entrants           = Column(String(255))
    seasonal_patterns      = Column(String(255))
    created_at             = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at             = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class FxInterestRates(Base):
    """Central bank policy rates per country, sourced from FRED IMF-IFS series.

    Stored at source frequency (monthly). The features pipeline forward-fills
    to weekly when computing forward curves — do NOT treat a monthly row as
    'no data existed on other days', just 'last known rate'.
    """
    __tablename__ = "fx_interest_rates"
    ir_id            = Column(Integer, primary_key=True)
    country_code     = Column(String(3), nullable=False)   # "INR", "USD", "CNY" etc.
    as_of_date       = Column(Date, nullable=False, primary_key=True)
    # Central bank discount / repo rate (annualized %)
    policy_rate_pct  = Column(Numeric(7, 4))
    # 1-yr government bond yield — available for USD (FRED DGS1); NULL for most EM
    gov_bond_1yr_pct = Column(Numeric(7, 4))
    source           = Column(String(50))
    fred_series      = Column(String(30))
    pulled_at        = Column(DateTime)
    created_at       = Column(DateTime, server_default=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint("country_code", "as_of_date", name="uq_ir_country_date"),
    )


class FxVolatility(Base):
    """Realized volatility features per currency pair per week.

    Computed by fx_features_pipeline.py from the fx_rates time series.
    Weekly log returns annualized by sqrt(52). Windows expressed in weeks:
      4w ≈ 30d,  13w ≈ 90d,  26w ≈ 180d,  52w ≈ 365d
      7w ≈ 50d MA,  29w ≈ 200d MA

    hedge_urgency and suggested_hedge_ratio_pct are the intelligence outputs —
    the rest of the table is the factual evidence those signals rest on.
    """
    __tablename__ = "fx_volatility"
    vol_id         = Column(Integer, primary_key=True)
    as_of_date     = Column(Date, nullable=False, primary_key=True)
    currency_pair  = Column(String(10), nullable=False)   # "USD_INR", "EUR_USD", etc.
    spot_rate      = Column(Numeric(14, 6), nullable=False)
    # Annualized realized volatility (log returns × √52)
    vol_30d_ann    = Column(Numeric(7, 4))   # 4-week window
    vol_90d_ann    = Column(Numeric(7, 4))   # 13-week window
    vol_180d_ann   = Column(Numeric(7, 4))   # 26-week window
    vol_365d_ann   = Column(Numeric(7, 4))   # 52-week window
    # Moving averages of spot rate
    ma_50d         = Column(Numeric(14, 6))  # 7-week simple MA
    ma_200d        = Column(Numeric(14, 6))  # 29-week simple MA
    above_ma_200d  = Column(Boolean)         # spot > ma_200d
    # Cumulative log-return momentum (not annualized — raw move magnitude)
    ret_30d        = Column(Numeric(8, 4))   # 4-week cumulative
    ret_90d        = Column(Numeric(8, 4))   # 13-week
    ret_180d       = Column(Numeric(8, 4))   # 26-week
    ret_365d       = Column(Numeric(8, 4))   # 52-week
    # Percentile rank of spot vs trailing window (0=at-or-below historic low; 100=at high)
    pct_rank_1yr   = Column(Numeric(5, 2))
    pct_rank_3yr   = Column(Numeric(5, 2))
    pct_rank_5yr   = Column(Numeric(5, 2))
    # Volatility regime (vol_90d_ann vs its own trailing distribution)
    vol_regime     = Column(String(10))      # 'calm'/'normal'/'elevated'/'stressed'
    # Regime methodology (so models training on the label know how it was derived)
    vol_window_days        = Column(Integer)   # which vol_*d_ann drives the regime (90)
    regime_methodology     = Column(Text)      # human-readable definition of the bands
    regime_percentile_low  = Column(Numeric(5, 2))  # lower percentile bound of this regime
    regime_percentile_high = Column(Numeric(5, 2))  # upper percentile bound of this regime
    # Intelligence outputs
    hedge_urgency          = Column(String(10))   # 'monitor'/'watch'/'hedge'/'urgent'
    suggested_hedge_ratio_pct = Column(Numeric(5, 1))  # 0–100
    computed_at    = Column(DateTime)
    created_at     = Column(DateTime, server_default=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint("as_of_date", "currency_pair", name="uq_vol_date_pair"),
    )


class FxForwardCurve(Base):
    """CIP-implied forward rates per currency pair per tenor.

    Formula (for USD_INR-style pairs — foreign units per USD):
      F = S × (1 + r_foreign × T/360) / (1 + r_USD × T/360)

    For EUR_USD / GBP_USD (USD per foreign unit, inverted convention):
      F = S × (1 + r_USD × T/360) / (1 + r_foreign × T/360)

    forward_premium_pct_ann > 0 means the foreign currency weakens vs USD in
    the forward market (buyer pays a premium to lock in the rate).

    cip_quality:
      'exact'   — both rates from FRED for the relevant tenor
      'proxy'   — used policy rate as tenor proxy (most EM cases)
      'no_ir'   — no interest rate data for this country; forward omitted
    """
    __tablename__ = "fx_forward_curve"
    fwd_id               = Column(Integer, primary_key=True)
    as_of_date           = Column(Date, nullable=False, primary_key=True)
    currency_pair        = Column(String(10), nullable=False)
    tenor_days           = Column(Integer, nullable=False)    # 30, 60, 90, 180, 365
    spot_rate            = Column(Numeric(14, 6), nullable=False)
    implied_forward_rate = Column(Numeric(14, 6))   # CIP-derived forward
    forward_premium_pct_ann = Column(Numeric(7, 4)) # annualized premium (+ = local currency weakens)
    domestic_rate_pct    = Column(Numeric(7, 4))    # USD rate used (DFF or DGS1)
    foreign_rate_pct     = Column(Numeric(7, 4))    # foreign policy rate used
    cip_quality          = Column(String(10))        # 'exact'/'proxy'/'no_ir'
    # Executability: is this an observable/tradeable forward, or theory-only?
    # A model must never recommend hedging at a CIP-implied rate that has no market.
    is_market_observable = Column(Boolean)           # True only where a real fwd/NDF market exists
    market_liquidity     = Column(String(20))        # 'liquid'/'semi_liquid'/'cip_implied_only'
    execution_note       = Column(Text)
    computed_at          = Column(DateTime)
    created_at           = Column(DateTime, server_default=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "as_of_date", "currency_pair", "tenor_days",
            name="uq_fwd_date_pair_tenor",
        ),
    )


class FxCurrencyConfig(Base):
    """Governance table: which currencies matter for apparel sourcing and how
    downstream models should weight / interpret them.

    This is reference metadata, not time-series data — one row per pair. It tells
    the intelligence layer e.g. "USD_BDT is PRIMARY but has no liquid forward
    market, so monitor it for competitor cost tracking but never propose hedging
    it at the CIP-implied rate."

    currency_pair uses the underscore convention (USD_INR, EUR_USD) to join
    against fx_volatility / fx_forward_curve / fx_rates column naming.
    """
    __tablename__ = "fx_currency_config"
    currency_pair            = Column(String(10), primary_key=True)
    local_currency           = Column(String(5), nullable=False)
    local_currency_name      = Column(String(50), nullable=False)
    country                  = Column(String(50), nullable=False)
    manufacturing_relevance  = Column(String(20), nullable=False)  # PRIMARY/SECONDARY/MONITOR
    sourcing_tier            = Column(Integer, nullable=False)      # 1/2/3
    fx_table_field           = Column(String(20))   # column name in fx_rates (e.g. 'usd_inr')
    yfinance_ticker          = Column(String(20))
    fred_series              = Column(String(30))
    forward_market_liquidity = Column(String(20))   # liquid/semi_liquid/cip_implied_only
    classic_fashion_relevant = Column(Boolean, server_default="0")
    notes                    = Column(Text)
    is_active                = Column(Boolean, server_default="1")
    created_at               = Column(DateTime, server_default=func.now(), nullable=False)

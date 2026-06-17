from sqlalchemy import BigInteger, Boolean, Column, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.sql import func
from database.base import Base


class MajorRetailers(Base):
    """Identity/master table — one row per retailer, never carries financial data.
    Financial data lives in RetailerFinancials (temporal, append-only).
    """
    __tablename__ = "major_retailers"
    retailer_id       = Column(Integer, primary_key=True)
    name              = Column(String(255))
    cik               = Column(String(15))
    ticker            = Column(String(10))
    source            = Column(String(255))
    status            = Column(String(50))
    retailer_type     = Column(String(50))
    retailer_sub_type = Column(String(100))
    created_at        = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at        = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class DemandSignals(Base):
    """Derived categorical signals per retailer per reporting period.
    Append-only: new row per (retailer_id, fiscal_year, fiscal_quarter); old rows demoted via is_latest=False.
    """
    __tablename__ = "demand_signals"
    demand_signal_id    = Column(Integer, primary_key=True)
    retailer_id         = Column(Integer)
    fiscal_year         = Column(Integer)
    fiscal_quarter      = Column(Integer)
    period_end_date     = Column(Date)
    store_expansion     = Column(String(20))
    inventory_improving = Column(String(20))
    margin_compression  = Column(String(20))
    buying_volume_signal = Column(String(30))
    revenue_growth_pct  = Column(Numeric(8, 4))
    turnover_change_pct = Column(Numeric(8, 4))
    margin_change_pct   = Column(Numeric(8, 4))
    status              = Column(String(50))
    source              = Column(String(100), nullable=False, server_default="unknown")
    data_source_url     = Column(String(500), nullable=False, server_default="unknown")
    is_latest           = Column(Boolean, nullable=False, default=True, server_default="1")
    pulled_at           = Column(DateTime, nullable=False, server_default=func.now())
    created_at          = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at          = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class SeasonalPatterns(Base):
    __tablename__ = "seasonal_patterns"
    seasonal_pattern_id      = Column(Integer, primary_key=True)
    ss_factory_commit_window = Column(String(50))
    ss_delivery_window       = Column(String(50))
    fw_factory_commit_window = Column(String(50))
    fw_delivery_window       = Column(String(50))
    freight_book_lead_days   = Column(Integer)
    hedge_window_days        = Column(Integer)
    created_at               = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at               = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class RetailerFinancials(Base):
    __tablename__ = "retailer_financials"
    retailer_financials_id      = Column(Integer, primary_key=True, autoincrement=True)
    retailer_id                 = Column(Integer)   # FK → major_retailers
    fiscal_year                 = Column(Integer)
    fiscal_quarter              = Column(Integer)
    period_end_date             = Column(Date)
    filing_date                 = Column(Date)
    apparel_revenue_usd         = Column(Numeric(14, 2))
    apparel_revenue_pct_total   = Column(Numeric(6, 4))
    apparel_yoy_growth_pct      = Column(Numeric(8, 4))
    total_net_sales_usd         = Column(Numeric(14, 2))
    comparable_sales_growth_pct = Column(Numeric(8, 4))
    digital_comp_sales_pct      = Column(Numeric(8, 4))
    gross_margin_pct            = Column(Numeric(6, 4))
    gross_margin_change_bps     = Column(Numeric(8, 2))
    sga_rate_pct                = Column(Numeric(6, 4))
    operating_income_usd        = Column(Numeric(18, 2))
    operating_margin_pct        = Column(Numeric(6, 4))
    net_income_usd              = Column(Numeric(18, 2))
    net_margin_pct              = Column(Numeric(6, 4))
    inventory_usd               = Column(Numeric(14, 2))
    inventory_days              = Column(Numeric(8, 2))
    store_count_total           = Column(Integer)
    store_count_net_change      = Column(Integer)
    ecommerce_penetration_pct   = Column(Numeric(6, 4))
    guidance_sales_direction    = Column(String(50))
    guidance_sales_range_low    = Column(Numeric(12, 4))
    guidance_sales_range_high   = Column(Numeric(12, 4))
    guidance_eps_low            = Column(Numeric(8, 2))
    guidance_eps_high           = Column(Numeric(8, 2))
    source_10q_url              = Column(String(500))
    source_8k_url               = Column(String(500))
    source_8k_presentation_url  = Column(String(500))
    walmart_us_general_merch_usd        = Column(Numeric(14, 2))
    walmart_us_general_merch_pct        = Column(Numeric(6, 4))
    walmart_us_general_merch_yoy_pct    = Column(Numeric(8, 4))
    walmart_us_ecommerce_usd            = Column(Numeric(14, 2))
    walmart_us_ecommerce_pct_of_total   = Column(Numeric(6, 4))
    walmart_us_ecommerce_yoy_growth_pct = Column(Numeric(8, 4))
    sams_club_home_apparel_usd          = Column(Numeric(14, 2))
    sams_club_home_apparel_pct          = Column(Numeric(6, 4))
    sams_club_home_apparel_yoy_pct      = Column(Numeric(8, 4))
    sams_club_total_usd                 = Column(Numeric(14, 2))
    sams_club_ecommerce_usd             = Column(Numeric(14, 2))
    sams_club_comp_sales_ex_fuel_pct    = Column(Numeric(8, 4))
    walmart_us_store_count              = Column(Integer)
    sams_club_count                     = Column(Integer)
    walmart_us_model_note               = Column(String(200))
    sams_club_model_note                = Column(String(500))
    walmart_us_inventory_usd            = Column(Numeric(14, 2))
    sams_club_inventory_usd             = Column(Numeric(14, 2))
    walmart_international_inventory_usd = Column(Numeric(14, 2))
    walmart_us_inventory_yoy_change_pct = Column(Numeric(8, 4))
    sams_club_inventory_yoy_change_pct  = Column(Numeric(8, 4))
    walmart_us_inventory_days           = Column(Numeric(8, 2))
    sams_club_inventory_days            = Column(Numeric(8, 2))
    walmart_us_inventory_to_sales_ratio = Column(Numeric(8, 4))
    general_merch_inventory_proxy_signal = Column(String(50))
    inventory_positioning_language      = Column(String(500))
    inventory_change_narrative          = Column(String(200))
    transaction_count_growth_pct        = Column(Numeric(8, 4))
    average_transaction_value_change_pct = Column(Numeric(8, 4))
    ticket_vs_traffic_split             = Column(String(50))
    walmart_plus_member_count           = Column(Numeric(10, 0))
    walmart_plus_membership_growth_pct  = Column(Numeric(8, 4))
    sams_club_member_count              = Column(Numeric(10, 0))
    sams_club_membership_fee_revenue_usd = Column(Numeric(14, 2))
    sams_club_member_count_yoy_pct      = Column(Numeric(8, 4))
    private_brand_mix_change_bps        = Column(Numeric(8, 2))
    xbrl_extracted              = Column(Boolean)
    manually_verified           = Column(Boolean)
    source                      = Column(String(100), nullable=False, server_default="unknown")
    data_source_url             = Column(String(500), nullable=False, server_default="unknown")
    pulled_at                   = Column(DateTime, nullable=False, server_default=func.now())
    is_latest                   = Column(Boolean, nullable=False, default=True, server_default="1")
    calendar_year               = Column(Integer)
    calendar_quarter            = Column(Integer)
    data_quality                = Column(Text)
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class RetailerIntelligenceExtract(Base):
    __tablename__ = "retailer_intelligence_extract"
    extract_id              = Column(Integer, primary_key=True, autoincrement=True)
    retailer_id             = Column(Integer)   # FK → major_retailers
    fiscal_year             = Column(Integer)
    fiscal_quarter          = Column(Integer)
    period_end_date         = Column(Date)
    filing_date             = Column(Date)
    document_type           = Column(String(50))
    document_section        = Column(String(100))
    source_url              = Column(String(500))
    signal_category         = Column(String(100))
    canonical_category      = Column(String(100))  # FK → signal_category_taxonomy.canonical_category
    business_segment        = Column(String(40))  # enterprise | walmart_us | sams_club | target_us
    raw_text_passage        = Column(Text)
    extracted_signal        = Column(String(500))
    signal_sentiment        = Column(String(20))
    signal_strength         = Column(String(20))
    artemis_implication     = Column(String(500))
    artemis_implication_full = Column(Text)
    calendar_year           = Column(Integer)
    calendar_quarter        = Column(Integer)
    confidence_score        = Column(Numeric(4, 2))
    speaker                 = Column(String(20))
    is_forward_looking      = Column(Boolean)
    contains_number         = Column(Boolean)
    number_mentioned        = Column(String(255))
    time_period_referenced  = Column(String(30))
    affected_decision       = Column(String(50))
    time_horizon            = Column(String(30))
    historical_pattern_found = Column(Boolean)
    similar_prior_quarter   = Column(String(20))
    similar_prior_language  = Column(Text)
    observed_outcome        = Column(Text)
    pattern_confidence      = Column(Numeric(4, 2))
    extraction_model        = Column(String(100))
    extraction_prompt_ver   = Column(String(20))
    human_verified          = Column(Boolean)
    evidence_count          = Column(Integer)
    corroboration_score     = Column(Numeric(4, 2))
    has_contradiction       = Column(Boolean)
    primary_document_type   = Column(String(50))
    primary_speaker         = Column(String(20))
    source                  = Column(String(100), nullable=False, server_default="unknown")
    data_source_url         = Column(String(500), nullable=False, server_default="unknown")
    pulled_at               = Column(DateTime, nullable=False, server_default=func.now())
    is_latest               = Column(Boolean, nullable=False, default=True, server_default="1")
    excluded_reason         = Column(String(500))
    created_at              = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at              = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class RetailerSignalEvidence(Base):
    __tablename__ = "retailer_signal_evidence"
    evidence_id             = Column(Integer, primary_key=True, autoincrement=True)
    extract_id              = Column(Integer)   # FK → retailer_intelligence_extract
    retailer_id             = Column(Integer)
    fiscal_year             = Column(Integer)
    fiscal_quarter          = Column(Integer)
    period_end_date         = Column(Date)
    calendar_year           = Column(Integer)
    calendar_quarter        = Column(Integer)
    document_type           = Column(String(50))
    document_section        = Column(String(100))
    source_url              = Column(String(500))
    speaker                 = Column(String(20))
    raw_text_passage        = Column(Text)
    is_forward_looking      = Column(Boolean)
    contains_number         = Column(Boolean)
    number_mentioned        = Column(String(255))
    time_period_referenced  = Column(String(30))
    extraction_confidence   = Column(Numeric(4, 2))
    document_priority       = Column(Integer)
    corroborates_master     = Column(Boolean)
    contradicts_master      = Column(Boolean)
    is_analyst_pressure     = Column(Boolean)
    source_is_sec_filing    = Column(Boolean)
    source                  = Column(String(100), nullable=False, server_default="unknown")
    data_source_url         = Column(String(500), nullable=False, server_default="unknown")
    pulled_at               = Column(DateTime, nullable=False, server_default=func.now())
    is_latest               = Column(Boolean, nullable=False, default=True, server_default="1")
    excluded_reason         = Column(String(500))
    created_at              = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at              = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class RetailerStockPrices(Base):
    """Daily equity OHLCV time series per retailer — the market's forward view.

    Append-only: one row per (retailer_id, price_date); on re-ingest the prior
    is_latest=True row for that day is demoted. Only cleanly-extractable fields
    are stored (OHLC, VWAP, volume, pct_change); derivative tail fields from the
    source ($Chg / trade value / trade count) are intentionally NOT stored rather
    than fabricated. Prices are split-adjusted by the source vendor.
    """
    __tablename__ = "retailer_stock_prices"
    stock_price_id   = Column(Integer, primary_key=True, autoincrement=True)
    retailer_id      = Column(Integer)          # FK → major_retailers
    ticker           = Column(String(10))
    price_date       = Column(Date, nullable=False, primary_key=True)
    open_price       = Column(Numeric(12, 4))
    high_price       = Column(Numeric(12, 4))
    low_price        = Column(Numeric(12, 4))
    close_price      = Column(Numeric(12, 4), nullable=False)
    vwap             = Column(Numeric(12, 4))
    volume           = Column(BigInteger)
    pct_change       = Column(Numeric(8, 4))
    is_split_adjusted = Column(Boolean, nullable=False, server_default="1")
    data_quality     = Column(String(200))      # flags vendor anomalies; never imputed
    source           = Column(String(100), nullable=False, server_default="unknown")
    data_source_url  = Column(String(500), nullable=False, server_default="unknown")
    pulled_at        = Column(DateTime, nullable=False, server_default=func.now())
    is_latest        = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at       = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at       = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

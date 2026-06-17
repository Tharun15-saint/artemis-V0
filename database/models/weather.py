"""
Weather and harvest models for the commodity intelligence chain.

CottonRegionWeather — weekly weather observations for 7 key cotton-growing
  regions (4 in India, 3 in the US).  Source: NASA POWER Agroclimatology API.
  Powers early-warning signals on yield stress and crop quality risk.

IndiaHarvestSignal — monthly India-specific cotton production estimates from
  USDA FAS PSD (authoritative) and Cotton Association of India (CAI, early signal).
  India produces ~25% of world cotton output; Gujarat and Vidarbha conditions
  flow directly into Tirupur yarn supply and price within 3-6 months.

CottonSupplyDemand — full world cotton balance sheet from USDA FAS WASDE
  (replaces the broken legacy table that lived in database/models.py).
"""

from sqlalchemy import Boolean, Column, Integer, String, Numeric, Date, DateTime, Text
from sqlalchemy.sql import func

from database.base import Base


class CottonRegionWeather(Base):
    """
    Weekly weather for cotton-growing regions.

    Growing Degree Days base: 15.5°C (60°F) — standard cotton heat accumulation.
    season_assessment compresses the week into a single signal the model can act on:
      'favorable' | 'heat_stress' | 'drought_stress' | 'excess_moisture' | 'normal'

    Regions tracked:
      India  — gujarat_india, vidarbha_maharashtra_india, telangana_india, andhra_pradesh_india
      US     — west_texas_us, mississippi_delta_us, southeast_georgia_us
    """
    __tablename__ = "cotton_region_weather"

    weather_id               = Column(Integer, primary_key=True, autoincrement=True)
    region_name              = Column(String(100), nullable=False)
    country                  = Column(String(2), nullable=False)        # 'IN' or 'US'
    latitude                 = Column(Numeric(7, 4), nullable=False)
    longitude                = Column(Numeric(8, 4), nullable=False)
    week_ending              = Column(Date, nullable=False, primary_key=True)             # Saturday of the week
    avg_temp_celsius         = Column(Numeric(5, 2))
    max_temp_celsius         = Column(Numeric(5, 2))
    min_temp_celsius         = Column(Numeric(5, 2))
    total_rainfall_mm        = Column(Numeric(8, 2))
    # Deviation from the 1991-2020 climatological normal for this week/region
    rainfall_vs_normal_pct   = Column(Numeric(8, 2))
    # MJ/m²/day — important for boll opening and fibre quality
    solar_radiation_mj_m2    = Column(Numeric(8, 2))
    relative_humidity_pct    = Column(Numeric(5, 2))
    # Accumulated heat units: max(0, ((Tmax + Tmin) / 2) - 15.5) per day, summed over week
    growing_degree_days      = Column(Numeric(6, 2))
    # Compound signal for the intelligence layer
    season_assessment        = Column(String(50))
    # True when this week falls within the region's cotton growing season
    is_cotton_season         = Column(Boolean)
    as_of_date               = Column(Date, nullable=False)
    source                   = Column(String(100), nullable=False)
    data_source_url          = Column(String(500))
    pulled_at                = Column(DateTime, nullable=False, server_default=func.now())
    is_latest                = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at               = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at               = Column(DateTime, server_default=func.now(),
                                      onupdate=func.now(), nullable=False)


class IndiaHarvestSignal(Base):
    """
    India cotton crop production estimates by marketing year (Oct–Sep).

    Primary source: USDA FAS PSD API (same infrastructure as WASDE).
    Secondary source: CAI (Cotton Association of India) — leads USDA by 1-2 months
    during the season with market arrival data.

    Units: all production/stock quantities in lakh bales (1 lakh bale = 100,000 × 170 kg).
    India officially reports in bales of 170 kg (different from the 480-lb US bale).

    The vs_previous_estimate_lakh_bales field is the most actionable signal:
    a downward revision of 5+ lakh bales in October/November flows into higher
    Indian raw cotton prices within 4-8 weeks, then into Tirupur yarn prices 2-4
    weeks after that.
    """
    __tablename__ = "india_harvest_signal"

    harvest_id                        = Column(Integer, primary_key=True, autoincrement=True)
    marketing_year                    = Column(Integer, nullable=False)  # e.g. 2024 for Oct2024–Sep2025
    report_month                      = Column(Date, nullable=False)     # first day of the report month
    # Production estimate (lakh bales of 170 kg)
    estimated_production_lakh_bales   = Column(Numeric(8, 2))
    # Area sown in lakh hectares
    acreage_lakh_hectares             = Column(Numeric(8, 2))
    # Cumulative arrivals to market in lakh bales (cotton coming out of farms)
    arrivals_lakh_bales               = Column(Numeric(8, 2))
    # Carry-out / closing stock at season end
    closing_stock_lakh_bales          = Column(Numeric(8, 2))
    # Change from the prior month's estimate (negative = crop downgrade)
    vs_previous_estimate_lakh_bales   = Column(Numeric(8, 2))
    # YoY production change %
    vs_last_year_production_pct       = Column(Numeric(6, 2))
    # Qualitative crop grade: 'bumper' | 'normal' | 'below_average' | 'crop_failure'
    season_assessment                 = Column(String(50))
    # 'CAI' | 'USDA_FAS' | 'CCI' | 'Ministry_Textiles'
    source_agency                     = Column(String(100))
    report_url                        = Column(String(500))
    as_of_date                        = Column(Date, nullable=False)
    source                            = Column(String(100), nullable=False)
    pulled_at                         = Column(DateTime, nullable=False, server_default=func.now())
    is_latest                         = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at                        = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                        = Column(DateTime, server_default=func.now(),
                                               onupdate=func.now(), nullable=False)


class CottonSupplyDemand(Base):
    """
    Full world cotton balance sheet from USDA FAS WASDE.

    One row per (marketing_year, report_month, source_agency) combination.
    Updated monthly when USDA publishes WASDE.

    The world_stocks_to_use_ratio_pct is the primary driver of the ICE cotton
    futures curve shape (see LearnedCoefficient: su_ratio_bearish_threshold_pct etc.)

    India-specific crop progress fields (us_pct_planted, us_crop_condition_good_excellent_pct)
    from USDA NASS are also stored here when in-season.
    """
    __tablename__ = "cotton_supply_demand"

    supply_demand_id                      = Column(Integer, primary_key=True, autoincrement=True)
    marketing_year                        = Column(Integer, nullable=False)
    report_month                          = Column(Date, nullable=False, index=True)
    forecast_provider                     = Column(String(50), nullable=False)  # 'USDA_WASDE'

    # World balance (million 480-lb bales)
    world_production_million_bales        = Column(Numeric(10, 4))
    world_mill_use_million_bales          = Column(Numeric(10, 4))
    world_exports_million_bales           = Column(Numeric(10, 4))
    world_ending_stocks_million_bales     = Column(Numeric(10, 4))
    world_stocks_to_use_ratio_pct         = Column(Numeric(8, 4))

    # Country-level production (million 480-lb bales)
    us_production_million_bales           = Column(Numeric(10, 4))
    us_harvested_area_thousand_acres      = Column(Numeric(10, 4))
    india_production_million_bales        = Column(Numeric(10, 4))
    china_production_million_bales        = Column(Numeric(10, 4))
    pakistan_production_million_bales     = Column(Numeric(10, 4))
    australia_production_million_bales    = Column(Numeric(10, 4))
    brazil_production_million_bales       = Column(Numeric(10, 4))
    west_africa_production_million_bales  = Column(Numeric(10, 4))

    # US crop progress from NASS (in-season only, Apr–Nov)
    us_pct_planted                        = Column(Numeric(6, 4))
    us_crop_condition_good_excellent_pct  = Column(Numeric(6, 4))

    # Price benchmarks
    usda_season_avg_price_cents_per_lb    = Column(Numeric(10, 4))
    cotlook_a_index_cents_per_lb          = Column(Numeric(10, 4))

    source                                = Column(String(100), nullable=False)
    data_source_url                       = Column(String(500))
    notes                                 = Column(String(500))
    pulled_at                             = Column(DateTime, nullable=False, server_default=func.now())
    is_latest                             = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at                            = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                            = Column(DateTime, server_default=func.now(),
                                                   onupdate=func.now(), nullable=False)

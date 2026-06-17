"""
Tirupur local yarn market rate model.

This is the critical bridge between ICE cotton macro signal and RRK's actual
yarn procurement cost.  The temporal gap between an ICE cotton price move and
the resulting Tirupur yarn price move (seeded at 6 weeks in LearnedCoefficient
as cotton_to_yarn_price_transmission_lag_weeks_tirupur) is what this table
exists to measure and calibrate.

Sources (in order of preference):
  1. TEXPROCIL weekly price bulletin  (texprocil.org, published Fridays)
  2. Manual entry from Tirupur market surveys or trader contacts
  3. CNAYarn.com market reports (structured when parseable)

Every row stores the prevailing ICE cotton price at the observation week
and at the 6-week-prior week, so the lag correlation can be computed
directly without joining multiple tables.
"""

from sqlalchemy import Boolean, Column, Integer, String, Numeric, Date, DateTime, Text
from sqlalchemy.sql import func

from database.base import Base


class TirupurYarnMarketRate(Base):
    """
    Weekly spot price for cotton yarn in the Tirupur local market.

    yarn_count_ne: English metric count (Ne).  Most common in Tirupur: 20, 24, 30, 34, 40, 60.
    spinning_method: combed | semi_combed | open_end | compact | vortex
    fibre_type: cotton | cotton_poly_blend | cotton_viscose_blend | modal
    cotton_pct: actual cotton content (e.g. 100 for pure cotton, 65 for CVC)

    The correlation fields (ice_cotton_*) are populated at write time by looking
    up the Cotton table — they should never be NULL for rows with real ICE data.
    """
    __tablename__ = "tirupur_yarn_market_rate"

    rate_id                          = Column(Integer, primary_key=True, autoincrement=True)
    week_ending                      = Column(Date, nullable=False)
    yarn_count_ne                    = Column(Integer, nullable=False)
    spinning_method                  = Column(String(50), nullable=False)
    fibre_type                       = Column(String(50), nullable=False)
    cotton_pct                       = Column(Numeric(5, 2))

    # --- Primary signal ---
    price_per_kg_inr                 = Column(Numeric(10, 4), nullable=False)
    price_change_vs_prior_week_inr   = Column(Numeric(10, 4))  # absolute change
    price_change_vs_prior_week_pct   = Column(Numeric(6, 4))   # % change
    price_change_vs_4w_avg_pct       = Column(Numeric(6, 4))   # vs 4-week moving average

    # --- ICE cotton context at observation (for correlation computation) ---
    # These are populated at ingestion by joining the Cotton table.
    # ice_cotton_near_inr_kg is the most direct comparison point.
    ice_cotton_near_cents_lb_at_obs  = Column(Numeric(10, 4))  # ICE spot at same week (USD ¢/lb)
    ice_cotton_near_inr_kg_at_obs    = Column(Numeric(10, 4))  # ICE spot converted to INR/kg
    # ICE price 6 weeks prior — the baseline for the seeded 6-week transmission lag
    ice_cotton_near_inr_kg_6w_prior  = Column(Numeric(10, 4))
    # The actual implied spread: yarn_price - ice_cotton_inr_kg_at_obs
    # This includes spinning premium + dyeing/processing costs + margin
    implied_yarn_premium_over_cotton_inr = Column(Numeric(10, 4))
    # Set by a calibration job after sufficient data: how many weeks back does the
    # ICE move that best-predicts this week's yarn price?
    observed_transmission_lag_weeks  = Column(Integer)

    # --- Data quality ---
    # 'verified_transaction' | 'market_indicative' | 'survey_based' | 'manual_entry'
    data_quality                     = Column(String(50), nullable=False)
    source                           = Column(String(100), nullable=False)
    source_url                       = Column(String(500))
    notes                            = Column(Text)

    as_of_date                       = Column(Date, nullable=False)
    pulled_at                        = Column(DateTime, nullable=False, server_default=func.now())
    is_latest                        = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at                       = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                       = Column(DateTime, server_default=func.now(),
                                              onupdate=func.now(), nullable=False)

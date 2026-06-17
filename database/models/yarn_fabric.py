from sqlalchemy import Boolean, Column, Date, Integer, String, Numeric, DateTime, Text
from sqlalchemy.sql import func
from database.base import Base


class Yarn(Base):
    __tablename__ = "yarn"
    yarn_id                      = Column(Integer, primary_key=True)
    fibre_type                   = Column(String(100))
    count                        = Column(String(50))
    spinning_method              = Column(String(100))
    grade                        = Column(String(50))
    origin_city                  = Column(String(100))
    origin_country               = Column(String(100))
    price_per_kg                 = Column(Numeric(10, 4))
    price_per_kg_inr             = Column(Numeric(10, 4))
    price_per_kg_usd             = Column(Numeric(10, 4))
    price_per_kg_usd_rate_date   = Column(Date)
    local_currency               = Column(String(10))
    availability_signal          = Column(String(100))
    confidence_score             = Column(Numeric(4, 2))
    source                       = Column(String(100), nullable=False, server_default="unknown")
    data_source_url              = Column(String(500), nullable=False, server_default="unknown")
    status                       = Column(String(50))
    fibre_content_pct_cotton     = Column(Numeric(5, 2))
    fibre_content_pct_polyester  = Column(Numeric(5, 2))
    fibre_content_pct_modal      = Column(Numeric(5, 2))
    fibre_content_pct_viscose    = Column(Numeric(5, 2))
    fibre_content_pct_spandex    = Column(Numeric(5, 2))
    colour                       = Column(String(50))
    is_melange                   = Column(Boolean, nullable=False, default=False, server_default="0")
    is_recycled                  = Column(Boolean, nullable=False, default=False, server_default="0")
    is_bci                       = Column(Boolean, nullable=False, default=False, server_default="0")
    requires_review              = Column(Boolean, nullable=False, default=False, server_default="0")
    supplier_name                = Column(String(255))
    buyer_reference              = Column(String(100))
    po_number                    = Column(String(100))
    grn_number                   = Column(String(100))
    grn_date                     = Column(Date)
    quantity_kg                  = Column(Numeric(12, 4))
    po_rate_inr                  = Column(Numeric(10, 4))
    amount_inr                   = Column(Numeric(12, 4))
    dc_number                    = Column(String(100))
    dc_date                      = Column(Date)
    yarn_type_raw                = Column(Text)
    as_of_date                   = Column(Date)
    cotton_price_at_po_usd_kg    = Column(Numeric(10, 4))
    cotton_price_at_po_inr_kg    = Column(Numeric(10, 4))
    cotton_price_source          = Column(String(50))
    global_cotton_benchmark_usd_kg = Column(Numeric(10, 4))
    global_cotton_benchmark_inr_kg = Column(Numeric(10, 4))
    global_cotton_benchmark_source = Column(String(255))
    global_cotton_benchmark_date = Column(Date)
    spinning_premium_inr_kg      = Column(Numeric(10, 4))
    spinning_premium_usd_kg      = Column(Numeric(10, 4))
    spinning_premium_pct         = Column(Numeric(6, 2))
    spinning_premium_methodology = Column(Text)
    usd_inr_rate_at_po           = Column(Numeric(8, 4))
    data_notes                   = Column(Text)
    pulled_at                    = Column(DateTime, nullable=False, server_default=func.now())
    is_latest                    = Column(Boolean, nullable=False, default=True, server_default="1")
    # World 1 additions — product identity fields missing from original schema
    colour_state                 = Column(String(50))   # greige | yarn_dyed | solution_dyed
    # greige = requires fabric dyeing; yarn_dyed/solution_dyed = FabricDyeing.bypassed=True
    dyeing_step_required         = Column(Boolean)      # derived: True when colour_state='greige'
    # tirupur spot rate on GRN date — enables premium = (po_rate_inr / market_rate_inr) - 1
    tirupur_market_rate_inr_kg   = Column(Numeric(10, 4))
    # expected GSM output range for this yarn (for FabricKnitting target validation)
    expected_gsm_min             = Column(Numeric(8, 2))
    expected_gsm_max             = Column(Numeric(8, 2))
    created_at                   = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                   = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)


class KnitFabric(Base):
    __tablename__ = "knit_fabric"
    fabric_id       = Column(Integer, primary_key=True)
    construction    = Column(String(100))
    weight_gsm      = Column(Numeric(8, 2))
    fibre_content   = Column(String(255))
    finish          = Column(String(100))
    origin_country  = Column(String(100))
    price_per_kg    = Column(Numeric(10, 4))
    created_at      = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at      = Column(DateTime, server_default=func.now(),
                             onupdate=func.now(), nullable=False)

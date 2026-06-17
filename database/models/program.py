from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime, Boolean
from sqlalchemy.sql import func
from database.base import Base


class ProductSpecification(Base):
    __tablename__ = "product_specification"
    spec_id                 = Column(Integer, primary_key=True)
    product_name            = Column(String(255))
    hs_code_id              = Column(Integer)   # FK → hs_codes
    fibre_content           = Column(String(255))
    construction            = Column(String(255))
    weight_gsm              = Column(Numeric(8, 2))
    typical_fob_low         = Column(Numeric(10, 4))
    typical_fob_high        = Column(Numeric(10, 4))
    prototype_corridor_1    = Column(String(100))
    prototype_corridor_2    = Column(String(100))
    prototype_corridor_3    = Column(String(100))
    # World 1 additions — connect to the new product chain
    construction_id         = Column(Integer)   # FK → garment_construction (when spec matures to full build)
    silhouette              = Column(String(50))
    # crew_neck_tee | v_neck_tee | polo | hoodie_pullover | hoodie_zip | sweatshirt | jogger | shorts
    complexity_score        = Column(Integer)   # 1-10 (drives CMT cost via learned_coefficient)
    piece_weight_grams_typical = Column(Numeric(8, 2))  # typical piece weight for this product type
    created_at              = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at              = Column(DateTime, server_default=func.now(),
                                     onupdate=func.now(), nullable=False)


class Program(Base):
    __tablename__ = "program"
    program_id              = Column(Integer, primary_key=True)
    importer_id             = Column(Integer)   # FK → importer
    factory_id              = Column(Integer)   # FK → cmt_factories
    spec_id                 = Column(Integer)   # FK → product_specification
    season                  = Column(String(20))
    quantity_units          = Column(Integer)
    fob_price_agreed        = Column(Numeric(10, 4))
    delivery_date_committed = Column(Date)
    origin_port             = Column(String(255))
    destination_port        = Column(String(255))
    destination_dc          = Column(String(255))
    cmt_start_date          = Column(Date)
    fabric_cut_date         = Column(Date)
    ship_date_planned       = Column(Date)
    ship_date_actual        = Column(Date)
    status                  = Column(String(50))   # See PROGRAM_STATUSES in constants
    commodity_hedge_status  = Column(String(50))   # See HEDGE_STATUSES in constants
    hedge_id                = Column(Integer)       # FK → hedge_portfolio
    freight_booking_id      = Column(Integer)       # FK → ocean_freight_rfq
    customs_clearance_id    = Column(Integer)       # FK → customs_clearance_filing
    otd_risk_score          = Column(Numeric(5, 2))
    landed_cost_estimated   = Column(Numeric(10, 4))
    landed_cost_actual      = Column(Numeric(10, 4))  # Set only after duty confirmed
    # World 2/3 additions — richer commercial context
    construction_id         = Column(Integer)   # FK → garment_construction
    retailer_id             = Column(Integer)   # FK → major_retailers (end customer)
    program_ref             = Column(String(100))   # buyer's internal reference e.g. "CF-AW24-001"
    season_year             = Column(Integer)       # 2024, 2025, …
    season_type             = Column(String(50))    # spring_summer | fall_winter | cruise | resort
    agreed_fob_per_unit_usd = Column(Numeric(10, 4))   # commercial FOB per unit (taxonomy primary field)
    sourcing_type           = Column(String(50))
    # full_package | cmt_only | nominated_fabric | nominated_yarn
    payment_terms           = Column(String(50))
    # lc_at_sight | lc_90 | tt_advance | open_account_30 | open_account_60 | open_account_90 | da_60 | da_90
    created_at              = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at              = Column(DateTime, server_default=func.now(),
                                     onupdate=func.now(), nullable=False)

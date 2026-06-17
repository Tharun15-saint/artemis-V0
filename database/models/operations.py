from sqlalchemy import Boolean, Column, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.sql import func
from database.base import Base


class Sample(Base):
    """
    World 3: A physical sample submitted during the product development cycle.

    The sampling cycle (proto → fit → sealing → top_of_production → shipment_inspection)
    is the primary quality gate before bulk production starts. Each rejection round
    costs the manufacturer time and money and signals quality-pattern risk for the program.

    rejection_reasons_json enables pattern learning across programs:
    e.g. "measurement_deviation in collar width" is a recurring issue for polo constructions.

    review_lag_days (feedback_received - submitted) is a buyer responsiveness signal —
    slow review cycles directly delay bulk start and ship dates.
    """
    __tablename__ = "sample"

    sample_id                   = Column(Integer, primary_key=True)
    program_id                  = Column(Integer, nullable=False)   # FK → program
    variant_id                  = Column(Integer, nullable=True)    # FK → garment_variant
    construction_id             = Column(Integer, nullable=True)    # FK → garment_construction
    sample_type                 = Column(String(50), nullable=False)
    # proto_sample | development_sample | fit_sample | sealing_sample
    # top_of_production | pre_production | shipment_inspection
    round_number                = Column(Integer, default=1)
    submitted_date              = Column(Date)
    feedback_received_date      = Column(Date, nullable=True)
    review_lag_days             = Column(Integer, nullable=True)    # derived: feedback - submitted
    outcome                     = Column(String(50))
    # approved | approved_with_comments | rejected_major | rejected_minor | revision_required | pending
    # rejection_reasons: JSON dict e.g. {"measurement_deviation": true, "colour_off_standard": false}
    rejection_reasons_json      = Column(Text)
    retailer_involved           = Column(Boolean, default=False)
    cost_to_manufacturer_inr    = Column(Numeric(10, 4))
    source_thread_id            = Column(Integer, nullable=True)    # FK → communication_thread
    notes                       = Column(Text)
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)


class PurchaseOrderLine(Base):
    """
    World 3: An individual line item within a buyer's purchase order.

    A Program can have one PO (bulk order) or multiple POs (original + re-orders).
    Each PO has multiple lines — by style, colour, and size. Tracking at line level
    enables granular OTD (on-time delivery) performance by SKU, not just by program.

    ship_date_delta_days (actual_ship_date - required_ship_date) is the primary
    on-time delivery signal. Negative = early (rare), positive = late.
    """
    __tablename__ = "purchase_order_line"

    po_line_id                  = Column(Integer, primary_key=True)
    program_id                  = Column(Integer, nullable=False)   # FK → program
    variant_id                  = Column(Integer, nullable=True)    # FK → garment_variant
    po_number                   = Column(String(100), nullable=False)
    po_date                     = Column(Date)
    line_number                 = Column(Integer)
    size                        = Column(String(20))
    colour_code                 = Column(String(100))
    quantity_units              = Column(Integer)
    agreed_unit_price_usd       = Column(Numeric(10, 4))
    agreed_line_value_usd       = Column(Numeric(12, 4))            # = quantity × unit_price
    required_ship_date          = Column(Date)
    required_delivery_date      = Column(Date, nullable=True)
    actual_ship_date            = Column(Date, nullable=True)
    actual_delivery_date        = Column(Date, nullable=True)
    ship_date_delta_days        = Column(Integer, nullable=True)    # derived: actual - required
    status                      = Column(String(50))
    # confirmed | in_production | shipped | delivered | cancelled
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)


class ProductionOrder(Base):
    """
    World 4: A discrete manufacturing run assigned to a specific production unit.

    RRK has 5 production units. This table tracks which unit ran which program,
    enabling capacity planning and unit-level performance comparison.

    completion_delta_days (actual_completion - planned_completion) is the OTD signal.
    production_yield_pct (qc_passed / ordered) is the inline quality signal.
    Both feed into the bottleneck detection and predictive delay models.
    """
    __tablename__ = "production_order"

    production_order_id         = Column(Integer, primary_key=True)
    program_id                  = Column(Integer, nullable=False)   # FK → program
    construction_id             = Column(Integer, nullable=True)    # FK → garment_construction
    manufacturing_unit_ref      = Column(String(50))                # "Unit 1" … "Unit 5" for RRK
    planned_start_date          = Column(Date)
    actual_start_date           = Column(Date, nullable=True)
    start_date_delta_days       = Column(Integer, nullable=True)    # derived
    planned_completion_date     = Column(Date)
    actual_completion_date      = Column(Date, nullable=True)
    completion_delta_days       = Column(Integer, nullable=True)    # derived: actual - planned
    total_units_ordered         = Column(Integer)
    total_units_qc_passed       = Column(Integer, nullable=True)
    production_yield_pct        = Column(Numeric(5, 2), nullable=True)  # derived
    rejection_root_cause        = Column(String(100), nullable=True)
    notes                       = Column(Text)
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)


class ProcessStep(Base):
    """
    World 4: A single processing step within a production order.

    Covers every stage from yarn procurement to export documentation.
    parallel_group links concurrent steps (same value = run simultaneously, e.g. dyeing + printing).

    bottleneck_flag=True marks steps that caused the overall program to fall behind plan.
    This is the primary learning signal for the intelligence layer's bottleneck prediction.

    sourcing_type distinguishes who nominated the supplier — this is a critical power-dynamic
    signal. retailer_nominated suppliers cannot be changed without buyer approval, even if
    they are underperforming. This constraint drives a class of cost and timeline events.

    quality_outcome = passed_first_time is the gold standard; any rework or scrap is a
    cost event that must be reflected in the ProcessStep cost fields and in ProgramPnl.
    """
    __tablename__ = "process_step"

    step_id                     = Column(Integer, primary_key=True)
    production_order_id         = Column(Integer, nullable=False)   # FK → production_order
    step_type                   = Column(String(50), nullable=False)
    # yarn_procurement | knitting | dyeing | printing | embroidery | special_wash | finishing
    # cutting | sewing | quality_inspection_inline | quality_inspection_final
    # packing | inland_logistics | export_documentation | customs_examination
    step_sequence               = Column(Integer)
    parallel_group              = Column(Integer, nullable=True)    # same value = concurrent steps
    sourcing_type               = Column(String(50))
    # rrk_self_sourced | importer_nominated | retailer_nominated | joint_decision
    supplier_id                 = Column(Integer, nullable=True)
    supplier_type               = Column(String(50), nullable=True)
    # spinning_mill | knitting_mill | dyeing_unit | printing_unit | embroidery_unit | washing_unit
    in_house                    = Column(Boolean, default=False)
    planned_start_date          = Column(Date)
    actual_start_date           = Column(Date, nullable=True)
    planned_duration_days       = Column(Integer)
    actual_duration_days        = Column(Integer, nullable=True)
    duration_delta_days         = Column(Integer, nullable=True)    # derived
    cost_inr                    = Column(Numeric(12, 4))
    cost_usd                    = Column(Numeric(12, 4), nullable=True)
    quantity_in                 = Column(Numeric(12, 4), nullable=True)
    quantity_out                = Column(Numeric(12, 4), nullable=True)
    unit_of_measure             = Column(String(20))                # kg | pcs | dozen | metres
    quality_outcome             = Column(String(50))
    # passed_first_time | passed_after_rework | failed_scrapped | pending
    rejection_reason            = Column(String(255), nullable=True)
    bottleneck_flag             = Column(Boolean, default=False, nullable=False)
    notes                       = Column(Text)
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)

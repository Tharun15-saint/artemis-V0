from sqlalchemy import Boolean, Column, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.sql import func
from database.base import Base


class InternalEvent(Base):
    """
    World 7: A discrete event within a specific program's lifecycle.

    Internal events are the atomic facts that explain WHY program outcomes deviated from plan.
    They are the bridge between the communication corpus (what was said) and the financial record
    (what was the impact). Without this table, margin erosion and delivery delays are unexplained
    numbers — with it, they become patterns that the model can predict.

    what_it_meant_for_program and what_it_meant_for_relationship are the most important
    fields — they must capture causality, not just description. These are the training signals
    for the causal reasoning capability.

    triggered_by determines accountability: manufacturer_action vs importer_action vs
    supplier_failure events have very different implications for the relationship score.
    """
    __tablename__ = "internal_event"

    event_id                    = Column(Integer, primary_key=True)
    program_id                  = Column(Integer, nullable=False)   # FK → program
    process_step_id             = Column(Integer, nullable=True)    # FK → process_step
    event_type                  = Column(String(100), nullable=False)
    # quality_rejection_production | quality_rejection_buyer | delivery_delay
    # order_cancellation_partial | order_cancellation_full | spec_change_post_sealing
    # price_renegotiation | payment_delay | lc_discrepancy | strike | machine_breakdown
    # power_outage | raw_material_shortage | capacity_conflict | force_majeure
    event_date                  = Column(Date, nullable=False)
    triggered_by                = Column(String(50))
    # manufacturer_action | importer_action | retailer_requirement_change
    # supplier_failure | planning_error | external_event | force_majeure
    severity                    = Column(String(20))                # low | medium | high | critical
    financial_impact_inr        = Column(Numeric(14, 4), nullable=True)
    timeline_impact_days        = Column(Integer, nullable=True)
    resolution_type             = Column(String(50), nullable=True)
    # resolved_no_cost | resolved_with_rework_cost | resolved_with_delay
    # unresolved_absorbed | disputed | ongoing
    source_thread_id            = Column(Integer, nullable=True)    # FK → communication_thread
    ext_event_id                = Column(Integer, nullable=True)    # FK → external_event
    what_it_meant_for_program   = Column(Text)
    what_it_meant_for_relationship = Column(Text)
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)


class ExternalEvent(Base):
    """
    World 7: A macro-level event (geopolitical, commodity, regulatory, logistics) affecting
    one or more programs.

    historical_precedent_ids_json is the key field for the intelligence layer's analogical
    reasoning: "The 2025 US tariff shock on India resembles the 2019 Bangladesh duty hike —
    the supply chain response was [X] and margins compressed by [Y]."

    This table links to InternalEvent when an external shock caused a specific internal effect
    on a program. The link is: ExternalEvent → InternalEvent → Program → ProgramPnl,
    forming the full causal chain from macro event to margin impact.
    """
    __tablename__ = "external_event"

    ext_event_id                = Column(Integer, primary_key=True)
    event_category              = Column(String(50), nullable=False)
    # geopolitical | trade_policy | commodity_market | weather_climate | logistics_disruption
    # regulatory_change | retailer_financial | labour_regulation | currency_crisis
    # pandemic_epidemic | port_congestion | shipping_lane_disruption | raw_material_shortage
    event_name                  = Column(String(255), nullable=False)
    event_date                  = Column(Date)
    event_end_date              = Column(Date, nullable=True)
    # affected_corridors: JSON array e.g. ["India", "Bangladesh", "Vietnam"]
    affected_corridors_json     = Column(Text)
    # affected_commodities: JSON array e.g. ["cotton", "polyester", "ocean_freight"]
    affected_commodities_json   = Column(Text)
    supply_chain_impact         = Column(Text)
    price_impact_direction      = Column(String(20))                # up | down | volatile | none
    programs_affected_count     = Column(Integer, nullable=True)
    # historical_precedent_ids: JSON array of ext_event_ids of similar past events
    historical_precedent_ids_json = Column(Text)
    source_reference            = Column(String(500))
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.sql import func
from database.base import Base


class LearnedCoefficient(Base):
    """
    World 9: A calibrated numerical relationship derived from operational data.

    confidence_tier tracks the provenance of every coefficient:
    - industry_prior: published benchmarks, no RRK calibration (use cautiously)
    - rrk_provisional: estimated from limited RRK observations (n < 10)
    - rrk_measured: calibrated from actual RRK records (10 ≤ n < 50)
    - rrk_high_confidence: fully calibrated from RRK data (n ≥ 50)

    The intelligence engine must weight cost estimates by confidence_tier and surface
    uncertainty to the user. An industry_prior coefficient should always carry a wider
    confidence interval than an rrk_high_confidence one.

    Critical coefficients seeded at system start (industry_prior):
    - yarn_to_fabric_ratio_*: kg yarn per kg greige fabric by knit structure
    - cmt_minutes_per_dozen_complexity_*: CMT minutes by complexity score (1-10)
    - cutting_wastage_pct_*: fabric lost to cutting by construction type
    - dark_colour_dye_premium_pct: cost premium for dark colours vs standard
    - cotton_transmission_lag_weeks: weeks from cotton price move to yarn price move (Tirupur)
    - piece_weight_*: typical piece weight by silhouette and GSM tier
    """
    __tablename__ = "learned_coefficient"

    coefficient_id              = Column(Integer, primary_key=True)
    coefficient_name            = Column(String(255), nullable=False, unique=True)
    description                 = Column(Text)
    scope_construction_type     = Column(String(100), nullable=True)
    scope_gsm_min               = Column(Numeric(8, 2), nullable=True)
    scope_gsm_max               = Column(Numeric(8, 2), nullable=True)
    scope_corridor              = Column(String(100), nullable=True)     # "India/Tirupur"
    value                       = Column(Numeric(12, 6), nullable=False)
    unit                        = Column(String(50))                     # ratio | minutes | pct | weeks | grams
    confidence_tier             = Column(String(50), nullable=False)
    # industry_prior | rrk_provisional | rrk_measured | rrk_high_confidence
    calibration_sample_count    = Column(Integer, default=0)
    calibration_date_from       = Column(Date, nullable=True)
    calibration_date_to         = Column(Date, nullable=True)
    last_calibrated_at          = Column(DateTime, nullable=True)
    # source_evidence_ids: JSON dict e.g. {"yarn_ids": [1,2,3], "process_step_ids": [10,11]}
    source_evidence_ids_json    = Column(Text)
    is_active                   = Column(Boolean, default=True, nullable=False)
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)


class ObservedPattern(Base):
    """
    World 9: A recurring pattern across programs, actors, or time periods.

    historical_accuracy_rate is back-tested: of N observations where trigger_conditions were met,
    how often did predicted_outcome occur? This must be recomputed as new programs close.

    why_it_matters is the most important field — it must articulate the causal mechanism,
    not just the correlation. A pattern without a causal explanation cannot be safely applied
    to novel situations.

    Example pattern:
    - description: "Spec changes received after sealing sample approval add 7-12 days to ship date"
    - trigger_conditions: {"event_type": "spec_change_post_sealing"}
    - predicted_outcome: "ship_date_delta_days between 7 and 12"
    - historical_accuracy_rate: 0.84
    - why_it_matters: "Post-sealing spec changes require re-cutting fabric, disrupting the already
      committed sewing line schedule. The 7-day floor reflects minimum re-cut + re-inspection time."
    """
    __tablename__ = "observed_pattern"

    pattern_id                  = Column(Integer, primary_key=True)
    pattern_description         = Column(Text, nullable=False)
    pattern_domain              = Column(String(50))
    # product | process | relationship | financial | market | communication | event_sequence
    # trigger_conditions: JSON dict describing what circumstances activate this pattern
    trigger_conditions_json     = Column(Text)
    predicted_outcome           = Column(Text)
    historical_accuracy_rate    = Column(Numeric(4, 3))     # 0–1
    observation_count           = Column(Integer, default=0)
    first_observed_date         = Column(Date, nullable=True)
    last_observed_date          = Column(Date, nullable=True)
    why_it_matters              = Column(Text)              # causal narrative — most important field
    # supporting_evidence_ids: JSON dict e.g. {"internal_event_ids": [5,9], "program_ids": [2,7]}
    supporting_evidence_ids_json = Column(Text)
    is_active                   = Column(Boolean, default=True)
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)


class DecisionRecord(Base):
    """
    World 9: A recorded decision with its context, reasoning, and eventual outcome.

    reasoning is the critical field — it must capture WHY the decision was made, including
    the information available at decision time, the alternatives considered, and any constraints.

    outcome_quality is assessed retrospectively, comparing what happened against what was expected.
    The decision records with strong_negative outcomes are the most valuable training examples —
    they teach the model what to avoid in similar future situations.

    thread_id links to the email thread where the decision was communicated — enabling the model
    to learn: "this class of decision correlates with these communication patterns."
    """
    __tablename__ = "decision_record"

    decision_id                 = Column(Integer, primary_key=True)
    program_id                  = Column(Integer, nullable=True)    # FK → program
    thread_id                   = Column(Integer, nullable=True)    # FK → communication_thread
    decision_type               = Column(String(100))
    # price_acceptance | price_rejection | spec_change_acceptance | spec_change_rejection
    # supplier_nomination | supplier_change | delivery_date_extension | order_cancellation
    # payment_term_change | quality_hold | quality_release | freight_mode_change
    decision_date               = Column(Date)
    decided_by_actor_id         = Column(Integer)
    decided_by_actor_type       = Column(String(50))
    context_summary             = Column(Text)
    decision_made               = Column(Text, nullable=False)
    reasoning                   = Column(Text)                      # WHY — most important field
    expected_outcome            = Column(Text)
    actual_outcome              = Column(Text, nullable=True)       # filled in retrospectively
    outcome_quality             = Column(String(50))
    # strong_positive | positive | neutral | negative | strong_negative | unknown
    outcome_explanation         = Column(Text, nullable=True)
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)


class KnowledgeGap(Base):
    """
    World 9: A documented gap in the system's knowledge.

    This table makes uncertainty explicit. The intelligence engine must check KnowledgeGap
    before making any recommendation and surface relevant open gaps to the user.
    A recommendation based on a gap with severity=blocks_reasoning must not be made.

    resolution_path documents exactly what data collection would close the gap —
    this drives the data collection roadmap. If a gap can be closed by ingesting
    a specific type of RRK document, that must be stated here.
    """
    __tablename__ = "knowledge_gap"

    gap_id                      = Column(Integer, primary_key=True)
    gap_description             = Column(Text, nullable=False)
    gap_domain                  = Column(String(50))
    # product_costing | process_timing | relationship | market | regulatory | communication
    gap_severity                = Column(String(50))
    # blocks_reasoning | degrades_accuracy | minor_limitation
    analogous_knowledge         = Column(Text)
    resolution_path             = Column(Text)
    related_coefficient_name    = Column(String(255), nullable=True) # → learned_coefficient.coefficient_name
    status                      = Column(String(50), default="open")
    # open | data_ingestion_in_progress | resolved
    resolved_at                 = Column(Date, nullable=True)
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)


class ReasoningChain(Base):
    """
    World 9: A stored reasoning chain produced by the intelligence engine.

    reasoning_steps_json is an ordered array of reasoning steps:
    [{"step": 1, "observation": "...", "source_type": "invoice", "source_id": 42,
      "confidence": 0.9, "coefficient_used": "yarn_to_fabric_ratio_single_jersey_140gsm"}, ...]

    Storing chains serves three purposes:
    1. Explanation: the user can audit why a recommendation was made
    2. Audit trail: decisions can be traced to specific data points
    3. Quality improvement: low-confidence chains identify where data gaps hurt most

    knowledge_gaps_in_chain_json records which gaps reduced confidence in this specific chain.
    """
    __tablename__ = "reasoning_chain"

    chain_id                    = Column(Integer, primary_key=True)
    conclusion                  = Column(Text, nullable=False)
    # reasoning_steps: JSON array of step objects (see docstring above)
    reasoning_steps_json        = Column(Text)
    overall_confidence          = Column(Numeric(4, 3))     # 0–1
    # knowledge_gaps_in_chain: JSON array of gap_ids that reduced confidence
    knowledge_gaps_in_chain_json = Column(Text)
    program_id                  = Column(Integer, nullable=True)
    decision_record_id          = Column(Integer, nullable=True)
    generated_at                = Column(DateTime)
    engine_version              = Column(String(20))
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)

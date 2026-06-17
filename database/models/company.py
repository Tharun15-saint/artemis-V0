"""Legacy company-profile and purchase-order classes.

These classes were defined in the legacy monolithic database/models.py and are
retained here for backwards compatibility while synthesis.py migrates to the new
actor/program model (World 2-4 schema).

The tables referenced here (company_profile, purchase_order_history, cost_layer_prior,
cost_variable_prior) are from the old architecture and not yet fully replaced.
"""
from sqlalchemy import Boolean, Column, Date, DateTime, Integer, Numeric, String
from sqlalchemy.sql import func

from database.base import Base


class CompanyProfile(Base):
    __tablename__ = "company_profile"
    id                              = Column(Integer, primary_key=True, autoincrement=True)
    company_name                    = Column(String, nullable=False)
    company_type                    = Column(String, nullable=False)
    primary_corridors               = Column(String)
    primary_product_categories      = Column(String)
    typical_quantity_range          = Column(String)
    typical_fob_range_low           = Column(Numeric(10, 4))
    typical_fob_range_high          = Column(Numeric(10, 4))
    primary_retail_relationships    = Column(String)
    annual_volume_estimate_dozens   = Column(Numeric(10, 4))
    risk_profile                    = Column(String)
    intelligence_confidence         = Column(Numeric(10, 4), nullable=False, default=0)
    onboarded_at                    = Column(Date)
    last_intelligence_update        = Column(Date)
    notes                           = Column(String)
    created_at                      = Column(DateTime, server_default=func.now(), nullable=False)


class PurchaseOrderHistory(Base):
    __tablename__ = "purchase_order_history"
    id                          = Column(Integer, primary_key=True, autoincrement=True)
    company_id                  = Column(Integer, nullable=False, index=True)
    po_reference                = Column(String, nullable=False)
    factory_name                = Column(String, nullable=False)
    corridor                    = Column(String, nullable=False)
    product_category            = Column(String)
    fibre_content               = Column(String)
    construction                = Column(String)
    gsm                         = Column(Numeric(10, 4))
    colour_description          = Column(String)
    quantity_dozens             = Column(Numeric(10, 4))
    quoted_fob_per_dozen        = Column(Numeric(10, 4))
    actual_fob_per_dozen        = Column(Numeric(10, 4))
    target_retail_price         = Column(Numeric(10, 4))
    retailer_name               = Column(String)
    committed_delivery_date     = Column(Date)
    actual_delivery_date        = Column(Date)
    days_late                   = Column(Integer)
    quality_issues              = Column(Boolean, default=False, nullable=False)
    quality_issue_description   = Column(String)
    cost_variance_pct           = Column(Numeric(10, 4))
    factors_that_caused_variance = Column(String)
    season                      = Column(String)
    source                      = Column(String)
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)


class CostLayerPrior(Base):
    __tablename__ = "cost_layer_prior"
    id                          = Column(Integer, primary_key=True, autoincrement=True)
    layer_name                  = Column(String, nullable=False, unique=True)
    sequence_order              = Column(Integer, nullable=False)
    is_mandatory                = Column(Boolean, nullable=False, default=True)
    applies_when                = Column(String)
    unit                        = Column(String, nullable=False)
    prior_low                   = Column(Numeric(10, 4), nullable=False)
    prior_high                  = Column(Numeric(10, 4), nullable=False)
    current_low                 = Column(Numeric(10, 4), nullable=False)
    current_high                = Column(Numeric(10, 4), nullable=False)
    update_count                = Column(Integer, nullable=False, default=0)
    last_updated_from_instance  = Column(Date)
    stability                   = Column(String)
    source                      = Column(String, nullable=False)
    notes                       = Column(String)
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)


class CostVariablePrior(Base):
    __tablename__ = "cost_variable_prior"
    id                  = Column(Integer, primary_key=True, autoincrement=True)
    cost_layer_id       = Column(Integer, nullable=False, index=True)
    variable_name       = Column(String, nullable=False)
    variable_value      = Column(String, nullable=False)
    effect_type         = Column(String, nullable=False)
    prior_effect_low    = Column(Numeric(10, 4), nullable=False)
    prior_effect_high   = Column(Numeric(10, 4), nullable=False)
    current_effect_low  = Column(Numeric(10, 4), nullable=False)
    current_effect_high = Column(Numeric(10, 4), nullable=False)
    observation_count   = Column(Integer, nullable=False, default=0)
    confidence          = Column(Numeric(10, 4), nullable=False)
    reasoning           = Column(String)
    source              = Column(String, nullable=False)
    created_at          = Column(DateTime, server_default=func.now(), nullable=False)


class DiscoveredCostFactor(Base):
    __tablename__ = "discovered_cost_factor"
    id                              = Column(Integer, primary_key=True, autoincrement=True)
    layer_name                      = Column(String, nullable=False, index=True)
    condition_description           = Column(String, nullable=False)
    factor_name                     = Column(String, nullable=False)
    effect_direction                = Column(String, nullable=False)
    effect_magnitude_low            = Column(Numeric(10, 4), nullable=False)
    effect_magnitude_high           = Column(Numeric(10, 4), nullable=False)
    effect_unit                     = Column(String, nullable=False)
    applies_to_corridor             = Column(String)
    applies_to_company_id           = Column(Integer, index=True)
    applies_to_factory              = Column(String)
    applies_to_colour_tier          = Column(String)
    applies_to_season               = Column(String)
    discovered_from_instance_count  = Column(Integer, nullable=False, default=1)
    first_observed                  = Column(Date, nullable=False)
    last_observed                   = Column(Date, nullable=False)
    confidence                      = Column(Numeric(10, 4), nullable=False)
    is_active                       = Column(Boolean, default=True, nullable=False)
    reviewed_by_human               = Column(Boolean, default=False, nullable=False)
    notes                           = Column(String)
    created_at                      = Column(DateTime, server_default=func.now(), nullable=False)


class CostReasoningSession(Base):
    __tablename__ = "cost_reasoning_session"
    id                          = Column(Integer, primary_key=True, autoincrement=True)
    session_id                  = Column(String, nullable=False, unique=True, index=True)
    company_id                  = Column(Integer, index=True)
    input_context               = Column(String)
    inferred_spec               = Column(String)
    prior_layers_used           = Column(String)
    company_context_applied     = Column(Boolean, default=False, nullable=False)
    company_factors_used        = Column(String)
    discovered_factors_applied  = Column(String)
    layer_estimates             = Column(String)
    total_low                   = Column(Numeric(10, 4), nullable=False)
    total_mid                   = Column(Numeric(10, 4), nullable=False)
    total_high                  = Column(Numeric(10, 4), nullable=False)
    confidence_overall          = Column(Numeric(10, 4), nullable=False)
    flags                       = Column(String)
    missing_inputs              = Column(String)
    unknown_factors_flagged     = Column(String)
    actual_cost                 = Column(Numeric(10, 4))
    outcome_recorded            = Column(Boolean, default=False, nullable=False)
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)


class CostOutcome(Base):
    __tablename__ = "cost_outcome"
    id                      = Column(Integer, primary_key=True, autoincrement=True)
    reasoning_session_id    = Column(String, nullable=False, index=True)
    company_id              = Column(Integer, index=True)
    po_reference            = Column(String)
    estimated_fob_low       = Column(Numeric(10, 4), nullable=False)
    estimated_fob_mid       = Column(Numeric(10, 4), nullable=False)
    estimated_fob_high      = Column(Numeric(10, 4), nullable=False)
    actual_fob              = Column(Numeric(10, 4), nullable=False)
    actual_date             = Column(Date, nullable=False)
    variance_amount         = Column(Numeric(10, 4), nullable=False)
    variance_pct            = Column(Numeric(10, 4), nullable=False)
    was_within_range        = Column(Boolean, nullable=False)
    variance_explained      = Column(Boolean, default=False, nullable=False)
    variance_cause_layer    = Column(String)
    variance_cause_factor   = Column(String)
    is_known_factor         = Column(Boolean)
    discovered_factor_id    = Column(Integer)
    learnable               = Column(Boolean, default=True, nullable=False)
    learning_note           = Column(String)
    created_at              = Column(DateTime, server_default=func.now(), nullable=False)

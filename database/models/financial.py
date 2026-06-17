from sqlalchemy import Boolean, Column, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.sql import func
from database.base import Base


class Invoice(Base):
    """
    World 5: Every financial transaction in the supply chain — AR and AP alike.

    This is the ground truth for the P&L. ProgramPnl is derived by aggregating all Invoice
    rows for a given program_id. The system must never write ProgramPnl values manually —
    they must always be computed from this table.

    payment_days_delta = actual_payment_date - due_date. Negative = paid early (importer favour),
    positive = paid late (cash flow stress on manufacturer). This is a core relationship health signal
    and feeds into the ActorRelationship.payment_behavior_score.

    invoice_type covers both directions:
    - export_invoice: RRK invoices Classic Fashion (AR to RRK)
    - supplier_invoice: spinning mill / knitting mill / dye house invoices RRK (AP for RRK)
    - freight_invoice: freight forwarder invoices importer or manufacturer
    - customs_duty_invoice: issued by customs / duty drawback refund

    lc_reference tracks letter of credit lifecycle — LC discrepancies are a class of
    financial events that damage the relationship score.
    """
    __tablename__ = "invoice"

    invoice_id                  = Column(Integer, primary_key=True)
    invoice_number              = Column(String(100), nullable=False)
    invoice_type                = Column(String(50), nullable=False)
    # export_invoice | supplier_invoice | sample_invoice | advance_invoice
    # credit_note | debit_note | freight_invoice | customs_duty_invoice
    program_id                  = Column(Integer, nullable=True)    # FK → program
    process_step_id             = Column(Integer, nullable=True)    # FK → process_step
    issuer_actor_id             = Column(Integer)
    issuer_actor_type           = Column(String(50))
    recipient_actor_id          = Column(Integer)
    recipient_actor_type        = Column(String(50))
    amount_original             = Column(Numeric(14, 4), nullable=False)
    currency                    = Column(String(10), nullable=False)    # INR | USD | EUR | GBP
    amount_inr                  = Column(Numeric(14, 4))
    amount_usd                  = Column(Numeric(14, 4))
    fx_rate_used                = Column(Numeric(8, 4))
    invoice_date                = Column(Date, nullable=False)
    due_date                    = Column(Date, nullable=True)
    payment_date_actual         = Column(Date, nullable=True)
    payment_days_delta          = Column(Integer, nullable=True)    # derived: actual - due
    lc_reference                = Column(String(100), nullable=True)
    lc_expiry_date              = Column(Date, nullable=True)
    dispute_flag                = Column(Boolean, default=False, nullable=False)
    dispute_reason              = Column(Text, nullable=True)
    resolution_date             = Column(Date, nullable=True)
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)


class ProgramPnl(Base):
    """
    World 5: Actual (not estimated) profit and loss per program, derived from Invoice aggregation.

    is_complete=False until all invoices are booked (after final payment confirmation and
    duty drawback receipt). The intelligence layer must treat incomplete P&Ls as provisional
    and weight them accordingly in pattern learning.

    margin_erosion_primary_cause is a categorized attribution field — it must be set by the
    intelligence engine by comparing the actual cost breakdown against the original quoted cost.
    This categorical label is the training signal for the margin risk prediction model.

    This table REPLACES ProgramPnlWithLevers in outputs.py as the truth record.
    ProgramPnlWithLevers remains as the forward-looking estimate with lever scenarios.
    """
    __tablename__ = "program_pnl"

    pnl_id                      = Column(Integer, primary_key=True)
    program_id                  = Column(Integer, nullable=False)   # FK → program (unique per program)
    # Revenue
    revenue_inr                 = Column(Numeric(14, 4))
    revenue_usd                 = Column(Numeric(14, 4))
    # Cost breakdown (INR) — each derived from Invoice.amount_inr aggregation
    yarn_cost_inr               = Column(Numeric(14, 4))
    knitting_cost_inr           = Column(Numeric(14, 4))
    dyeing_cost_inr             = Column(Numeric(14, 4))
    finishing_cost_inr          = Column(Numeric(14, 4))
    printing_embroidery_wash_inr = Column(Numeric(14, 4))
    cmt_cost_inr                = Column(Numeric(14, 4))
    trim_cost_inr               = Column(Numeric(14, 4))
    sampling_cost_inr           = Column(Numeric(14, 4))
    rejection_rework_cost_inr   = Column(Numeric(14, 4))
    freight_inland_inr          = Column(Numeric(14, 4))
    other_cost_inr              = Column(Numeric(14, 4))
    # Totals (derived sums)
    total_cost_inr              = Column(Numeric(14, 4))
    gross_margin_inr            = Column(Numeric(14, 4))
    gross_margin_pct            = Column(Numeric(6, 4))
    margin_vs_quoted_delta_pct  = Column(Numeric(6, 4))
    margin_erosion_primary_cause = Column(String(50))
    # commodity_spike | spec_change | quality_rejection | delay_penalty | fx_move | other
    is_complete                 = Column(Boolean, default=False, nullable=False)
    calculated_at               = Column(DateTime)
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)

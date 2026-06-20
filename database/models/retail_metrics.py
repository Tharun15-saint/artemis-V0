"""
Canonical retailer metric store (the god-table refactor + medallion refined/gold layer).

Three tables:
  - MetricDefinition  — the catalog AS DATA: one row per metric_key with its precise
    definition, unit, category, per-retailer XBRL concept map, and which retailer
    archetypes it applies to. Single source of truth for what each number means.
  - RetailerMetric    — tall facts: one row per (retailer, fiscal period, metric_key).
    Carries provenance + confidence + is_latest, and a `certified` flag (the gate sets
    it — certified rows are the model-grade gold layer).
  - MetricInterpretation — archetype-aware reading: (archetype × metric) → direction,
    benchmark, demand implication. Interpretation lives HERE, never baked into the
    raw fact (e.g. inventory up = bearish for full-price, neutral/bullish for off-price).

Adding a metric is a new row in MetricDefinition, never a schema change — so this scales
to any retailer × any metric, and one metric can never carry two definitions.
"""

from sqlalchemy import Boolean, Column, Date, DateTime, Index, Integer, Numeric, String, Text
from sqlalchemy.sql import func

from database.base import Base


class MetricDefinition(Base):
    """The metric catalog as data — the single source of truth for each metric's meaning."""
    __tablename__ = "metric_definition"
    metric_key            = Column(String(60), primary_key=True)   # e.g. 'merchandise_sales_usd'
    label                 = Column(String(120), nullable=False)
    definition            = Column(Text, nullable=False)
    unit                  = Column(String(20), nullable=False)     # usd|pct|ratio|days|count|bps|usd_per_share
    category              = Column(String(40), nullable=False)     # demand|margin|inventory_working_capital|cashflow|balance_sheet|guidance|capital_return|apparel_supply_chain
    investor_grade        = Column(Boolean, nullable=False, server_default="1")
    vision_critical       = Column(Boolean, nullable=False, server_default="0")
    # Default reading; archetype-specific overrides live in MetricInterpretation.
    direction             = Column(String(20))                     # higher_better|lower_better|context
    applies_to_archetypes = Column(Text, nullable=False, server_default="all")  # JSON list or 'all'
    # Per-retailer XBRL concept resolution: {"default": [...], "overrides": {"TGT": [...]}}
    xbrl_concepts_json    = Column(Text)
    derivation            = Column(Text)                           # formula if a computed metric
    source_priority       = Column(String(40))                     # xbrl|8k_release|10k|derived
    notes                 = Column(Text)
    created_at            = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at            = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class RetailerMetric(Base):
    """Tall, append-only-by-period metric facts. `certified` = passed every gate (gold layer)."""
    __tablename__ = "retailer_metric"
    retailer_metric_id = Column(Integer, primary_key=True, autoincrement=True)
    retailer_id        = Column(Integer, nullable=False)   # FK → major_retailers
    metric_key         = Column(String(60), nullable=False)  # FK → metric_definition
    fiscal_year        = Column(Integer, nullable=False)
    fiscal_quarter     = Column(Integer)                   # NULL = annual / full-year metric
    period_end_date    = Column(Date)
    filing_date        = Column(Date)
    calendar_year      = Column(Integer)
    calendar_quarter   = Column(Integer)
    value_numeric      = Column(Numeric(20, 4), nullable=False)
    unit               = Column(String(20), nullable=False)
    source             = Column(String(100), nullable=False, server_default="unknown")
    source_concept     = Column(String(120))              # the exact XBRL concept used, if any
    source_url         = Column(String(500), nullable=False, server_default="unknown")
    confidence         = Column(Numeric(4, 2))
    data_quality       = Column(Text)                     # per-field provenance ledger (JSON)
    certified          = Column(Boolean, nullable=False, server_default="0")  # gate flag → gold layer
    is_latest          = Column(Boolean, nullable=False, server_default="1")
    pulled_at          = Column(DateTime, server_default=func.now(), nullable=False)
    created_at         = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at         = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_retailer_metric_lookup", "retailer_id", "metric_key", "is_latest"),
        Index("ix_retailer_metric_period", "retailer_id", "fiscal_year", "fiscal_quarter"),
    )


class MetricInterpretation(Base):
    """Archetype-aware reading of a metric — keeps type-specific JUDGMENT out of the raw fact.

    e.g. ('off_price', 'inventory_vs_sales_growth_gap') → context/bullish (sector glut = buying
    opportunity) vs ('mass_market', same) → bearish (markdowns + apparel order cuts ahead).
    """
    __tablename__ = "metric_interpretation"
    interpretation_id  = Column(Integer, primary_key=True, autoincrement=True)
    archetype          = Column(String(40), nullable=False)   # off_price|mass_market|warehouse_club|department|specialty|fast_fashion|value_extreme|marketplace
    metric_key         = Column(String(60), nullable=False)   # FK → metric_definition
    direction          = Column(String(20), nullable=False)   # higher_better|lower_better|context
    benchmark_low      = Column(Numeric(20, 4))
    benchmark_high     = Column(Numeric(20, 4))
    demand_implication = Column(Text, nullable=False)         # what a move means for apparel order flow
    created_at         = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at         = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_metric_interpretation_lookup", "archetype", "metric_key"),
    )

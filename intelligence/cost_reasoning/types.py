"""Shared types for the cost reasoning engine."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional


@dataclass
class ProgramSpec:
    """Structured program specification — fields may be inferred with varying confidence."""

    gsm: Optional[float] = None
    construction: Optional[str] = None
    colour_description: Optional[str] = None
    colour_tier: Optional[str] = None
    fibre_content: Optional[str] = None
    count: Optional[str] = None
    grade: Optional[str] = None
    corridor: Optional[str] = None
    quantity_dozens: Optional[float] = None
    target_fob: Optional[Decimal] = None
    factory_name: Optional[str] = None
    style_complexity: Optional[str] = None
    has_print: Optional[bool] = None
    print_type: Optional[str] = None
    season: Optional[str] = None
    retailer: Optional[str] = None
    inferred_fields: dict[str, float] = field(default_factory=dict)


@dataclass
class LayerEstimate:
    layer_name: str
    low: Decimal
    mid: Decimal
    high: Decimal
    confidence: float
    notes: list[str] = field(default_factory=list)
    factors_applied: list[str] = field(default_factory=list)


@dataclass
class CostReasoningResult:
    session_id: str
    reasoning_mode: str
    fob_low: Decimal
    fob_mid: Decimal
    fob_high: Decimal
    confidence_overall: float
    vs_target_fob: str
    flags: list[str]
    unknowns: list[str]
    missing_inputs: list[str]
    layer_estimates: list[LayerEstimate]
    company_context_applied: bool = False
    market_context: dict[str, Any] = field(default_factory=dict)

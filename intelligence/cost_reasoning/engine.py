"""Adaptive, company-centric cost reasoning engine."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session

from database.models import (
    CompanyFactoryRelationship,
    CompanyProfile,
    CostLayerPrior,
    CostOutcome,
    CostReasoningSession,
    CostVariablePrior,
    Cotton,
    CottonSupplyDemand,
    CrudeOil,
    DiscoveredCostFactor,
    PurchaseOrderHistory,
)
from intelligence.cost_reasoning.types import (
    CostReasoningResult,
    LayerEstimate,
    ProgramSpec,
)

logger = logging.getLogger(__name__)

MIN_DISCOVERED_OBSERVATIONS = 3
MIN_DISCOVERED_CONFIDENCE = Decimal("0.60")

DARK_COLOURS = {"navy", "black", "charcoal", "burgundy", "maroon", "dark"}
LIGHT_COLOURS = {"white", "cream", "ivory", "pastel", "sky", "light", "bleach"}
MEDIUM_COLOURS = {"red", "royal", "green", "orange", "purple", "yellow"}


class CostReasoningEngine:
    def __init__(self, db: Session, company_id: Optional[int] = None):
        self.db = db
        self.company_id = company_id
        self.company = self._load_company() if company_id else None
        self.last_session_id: Optional[str] = None

    def reason(self, spec_input: Any) -> CostReasoningResult:
        spec = self._parse_and_infer_spec(spec_input)
        mode = self._determine_reasoning_mode(spec)
        priors = self._load_priors(spec)
        discovered = self._load_discovered_factors(spec)
        company_context = (
            self._load_company_context(spec) if self.company else None
        )
        market_data = self._get_live_market_data(spec)
        layer_estimates = self._reason_all_layers(
            spec, priors, discovered, company_context, market_data
        )
        unknowns = self._identify_unknowns(spec, priors, discovered)
        result = self._compile_result(
            spec, layer_estimates, company_context, mode, unknowns, market_data
        )
        self._log_session(spec, result, mode, company_context, discovered)
        self.last_session_id = result.session_id
        return result

    def learn_from_outcome(
        self,
        session_id: str,
        actual_fob: Decimal,
        po_reference: Optional[str] = None,
    ) -> dict:
        session = (
            self.db.query(CostReasoningSession)
            .filter_by(session_id=session_id)
            .first()
        )
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        variance = actual_fob - session.total_mid
        variance_pct = (
            (variance / session.total_mid * Decimal("100"))
            if session.total_mid
            else Decimal("0")
        )
        within = session.total_low <= actual_fob <= session.total_high

        outcome = CostOutcome(
            reasoning_session_id=session_id,
            company_id=session.company_id,
            po_reference=po_reference,
            estimated_fob_low=session.total_low,
            estimated_fob_mid=session.total_mid,
            estimated_fob_high=session.total_high,
            actual_fob=actual_fob,
            actual_date=date.today(),
            variance_amount=variance,
            variance_pct=variance_pct,
            was_within_range=within,
            variance_explained=within,
            learnable=True,
        )

        new_factor = None
        new_factor_desc = None
        observations_needed = 0

        if not within:
            outcome.variance_explained = False
            outcome.learning_note = (
                f"Actual ${actual_fob} outside estimated range "
                f"${session.total_low}–${session.total_high}. Investigating."
            )
            new_factor = DiscoveredCostFactor(
                layer_name="unknown",
                condition_description=(
                    f"Unexplained variance on session {session_id}"
                    + (f" PO {po_reference}" if po_reference else "")
                ),
                factor_name="unexplained_cost_variance",
                effect_direction="increase" if variance > 0 else "decrease",
                effect_magnitude_low=abs(variance),
                effect_magnitude_high=abs(variance),
                effect_unit="usd_per_dozen",
                applies_to_company_id=session.company_id,
                discovered_from_instance_count=1,
                first_observed=date.today(),
                last_observed=date.today(),
                confidence=Decimal("0.35"),
                is_active=False,
                reviewed_by_human=False,
                notes=outcome.learning_note,
            )
            self.db.add(new_factor)
            self.db.flush()
            outcome.discovered_factor_id = new_factor.id
            outcome.is_known_factor = False
            outcome.variance_cause_factor = "unexplained_cost_variance"
            new_factor_desc = new_factor.condition_description
            observations_needed = MIN_DISCOVERED_OBSERVATIONS - 1
        else:
            outcome.learning_note = "Actual within estimated range — model calibrated."

        session.actual_cost = actual_fob
        session.outcome_recorded = True
        self.db.add(outcome)

        if session.company_id and session.company_id:
            rel = None
            inferred = json.loads(session.inferred_spec or "{}")
            factory = inferred.get("factory_name")
            if factory:
                rel = (
                    self.db.query(CompanyFactoryRelationship)
                    .filter_by(company_id=session.company_id, factory_name=factory)
                    .first()
                )
            if rel and rel.avg_price_vs_market_pct is not None:
                market_mid = session.total_mid
                actual_vs_market = (
                    (actual_fob - market_mid) / market_mid * Decimal("100")
                    if market_mid
                    else Decimal("0")
                )
                n = max(rel.programs_completed, 1)
                rel.avg_price_vs_market_pct = (
                    rel.avg_price_vs_market_pct * Decimal(n) + actual_vs_market
                ) / Decimal(n + 1)
                rel.programs_completed += 1

        self.db.commit()

        return {
            "variance_amount": float(variance),
            "variance_pct": float(variance_pct),
            "was_within_range": within,
            "new_factor_discovered": new_factor is not None,
            "discovered_factor_description": new_factor_desc,
            "factor_confidence": float(new_factor.confidence) if new_factor else None,
            "observations_needed": observations_needed,
            "learning_note": outcome.learning_note,
        }

    def learn_from_company_data(
        self,
        company_id: int,
        historical_pos: list[dict],
    ) -> dict:
        company = self.db.query(CompanyProfile).filter_by(id=company_id).first()
        if not company:
            raise ValueError(f"Company not found: {company_id}")

        created = 0
        corridors: set[str] = set()
        for po in historical_pos:
            row = PurchaseOrderHistory(
                company_id=company_id,
                po_reference=po.get("po_reference", f"PO-{created + 1}"),
                factory_name=po["factory_name"],
                corridor=po["corridor"],
                product_category=po.get("product_category"),
                fibre_content=po.get("fibre_content"),
                construction=po.get("construction"),
                gsm=po.get("gsm"),
                colour_description=po.get("colour_description"),
                quantity_dozens=po.get("quantity_dozens"),
                quoted_fob_per_dozen=po.get("quoted_fob_per_dozen"),
                actual_fob_per_dozen=po.get("actual_fob_per_dozen"),
                retailer_name=po.get("retailer_name"),
                season=po.get("season"),
                source=po.get("source", "erp_import"),
            )
            self.db.add(row)
            corridors.add(po["corridor"])
            created += 1

            rel = (
                self.db.query(CompanyFactoryRelationship)
                .filter_by(company_id=company_id, factory_name=po["factory_name"])
                .first()
            )
            if not rel:
                rel = CompanyFactoryRelationship(
                    company_id=company_id,
                    factory_name=po["factory_name"],
                    factory_corridor=po["corridor"],
                    programs_completed=0,
                )
                self.db.add(rel)
            rel.programs_completed += 1
            if po.get("actual_fob_per_dozen") and po.get("quoted_fob_per_dozen"):
                q = Decimal(str(po["quoted_fob_per_dozen"]))
                a = Decimal(str(po["actual_fob_per_dozen"]))
                if q > 0:
                    vs = (a - q) / q * Decimal("100")
                    if rel.avg_price_vs_market_pct is None:
                        rel.avg_price_vs_market_pct = vs
                    else:
                        rel.avg_price_vs_market_pct = (
                            rel.avg_price_vs_market_pct + vs
                        ) / 2

        company.primary_corridors = ",".join(sorted(corridors))
        company.intelligence_confidence = min(
            Decimal("0.95"),
            company.intelligence_confidence + Decimal(str(created * 0.015)),
        )
        company.last_intelligence_update = date.today()
        self.db.commit()

        return {
            "pos_imported": created,
            "corridors": sorted(corridors),
            "intelligence_confidence": float(company.intelligence_confidence),
        }

    def _load_company(self) -> Optional[CompanyProfile]:
        if not self.company_id:
            return None
        return self.db.query(CompanyProfile).filter_by(id=self.company_id).first()

    def _parse_and_infer_spec(self, input_data: Any) -> ProgramSpec:
        if isinstance(input_data, ProgramSpec):
            spec = input_data
        elif isinstance(input_data, dict):
            spec = ProgramSpec(**{k: v for k, v in input_data.items() if k != "inferred_fields"})
            spec.inferred_fields = input_data.get("inferred_fields", {})
        elif isinstance(input_data, str):
            spec = self._parse_natural_language(input_data)
        else:
            raise TypeError(f"Unsupported spec input type: {type(input_data)}")

        if spec.colour_description and not spec.colour_tier:
            spec.colour_tier = self._infer_colour_tier(spec.colour_description)
            spec.inferred_fields["colour_tier"] = 0.85

        if spec.fibre_content and not spec.grade:
            fc = spec.fibre_content.lower()
            if "combed" in fc:
                spec.grade = "combed"
            elif "carded" in fc or "cotton" in fc:
                spec.grade = "carded"
            if "spandex" in fc or "elastane" in fc:
                spec.inferred_fields["fibre_blend"] = 0.8

        if spec.fibre_content and not spec.count:
            match = re.search(r"(\d{2})s", spec.fibre_content.lower())
            if match:
                spec.count = f"{match.group(1)}s"
            elif "30" in (spec.fibre_content or ""):
                spec.count = "30s"

        if not spec.style_complexity:
            if spec.construction == "fleece":
                spec.style_complexity = "complex"
            elif spec.construction == "pique":
                spec.style_complexity = "moderate"
            else:
                spec.style_complexity = "basic"
            spec.inferred_fields["style_complexity"] = 0.7

        if spec.corridor:
            spec.corridor = self._normalize_corridor(spec.corridor)

        return spec

    def _parse_natural_language(self, text: str) -> ProgramSpec:
        lower = text.lower()
        spec = ProgramSpec(colour_description=text)
        for word in DARK_COLOURS | LIGHT_COLOURS | MEDIUM_COLOURS:
            if word in lower:
                spec.colour_description = word
                break
        if "hoodie" in lower or "fleece" in lower:
            spec.construction = "fleece"
        elif "polo" in lower or "pique" in lower:
            spec.construction = "pique"
        elif "tee" in lower or "t-shirt" in lower or "jersey" in lower:
            spec.construction = "single_jersey"
        if "bangladesh" in lower:
            spec.corridor = "Bangladesh"
        elif "vietnam" in lower:
            spec.corridor = "Vietnam"
        elif "india" in lower or "tirupur" in lower:
            spec.corridor = "India"
        qty = re.search(r"(\d[\d,]*)\s*(?:k\s*)?doz", lower)
        if qty:
            val = qty.group(1).replace(",", "")
            spec.quantity_dozens = float(val)
            if "k" in lower[max(0, qty.start() - 2) : qty.end() + 3]:
                spec.quantity_dozens *= 1000
        return spec

    def _infer_colour_tier(self, description: str) -> str:
        lower = description.lower()
        for word in DARK_COLOURS:
            if word in lower:
                return "dark"
        for word in LIGHT_COLOURS:
            if word in lower:
                return "light"
        return "medium"

    def _normalize_corridor(self, corridor: str) -> str:
        c = corridor.strip().title()
        if c.lower().startswith("bang"):
            return "Bangladesh"
        return c

    def _determine_reasoning_mode(self, spec: ProgramSpec) -> str:
        has_company = self.company is not None
        has_factory_history = False
        has_po_history = False
        if has_company:
            has_po_history = (
                self.db.query(PurchaseOrderHistory)
                .filter_by(company_id=self.company_id)
                .count()
                > 3
            )
            if spec.factory_name:
                has_factory_history = (
                    self.db.query(CompanyFactoryRelationship)
                    .filter_by(
                        company_id=self.company_id,
                        factory_name=spec.factory_name,
                    )
                    .first()
                    is not None
                )

        discovered_count = len(self._load_discovered_factors(spec, min_confidence=False))

        if has_factory_history and has_po_history:
            return "full_company_centric"
        if discovered_count > 0:
            return "prior_plus_learned"
        if has_company:
            return "prior_plus_company"
        return "prior_only"

    def _load_priors(self, spec: ProgramSpec) -> dict[str, CostLayerPrior]:
        layers = (
            self.db.query(CostLayerPrior)
            .order_by(CostLayerPrior.sequence_order)
            .all()
        )
        return {layer.layer_name: layer for layer in layers}

    def _load_discovered_factors(
        self,
        spec: ProgramSpec,
        min_confidence: bool = True,
    ) -> list[DiscoveredCostFactor]:
        query = self.db.query(DiscoveredCostFactor).filter(
            DiscoveredCostFactor.is_active.is_(True)
        )
        if min_confidence:
            query = query.filter(
                DiscoveredCostFactor.confidence >= MIN_DISCOVERED_CONFIDENCE,
                DiscoveredCostFactor.discovered_from_instance_count
                >= MIN_DISCOVERED_OBSERVATIONS,
            )
        factors = query.order_by(desc(DiscoveredCostFactor.confidence)).all()
        relevant = []
        for factor in factors:
            if factor.applies_to_corridor and spec.corridor:
                if factor.applies_to_corridor.lower() != spec.corridor.lower():
                    continue
            if factor.applies_to_company_id and self.company_id:
                if factor.applies_to_company_id != self.company_id:
                    continue
            elif factor.applies_to_company_id and not self.company_id:
                continue
            if factor.applies_to_factory and spec.factory_name:
                if factor.applies_to_factory != spec.factory_name:
                    continue
            if factor.applies_to_colour_tier and spec.colour_tier:
                if factor.applies_to_colour_tier != spec.colour_tier:
                    continue
            relevant.append(factor)
        return relevant

    def _load_company_context(self, spec: ProgramSpec) -> dict:
        assert self.company is not None
        ctx: dict[str, Any] = {
            "company_name": self.company.company_name,
            "intelligence_confidence": float(self.company.intelligence_confidence),
            "factory_relationship": None,
            "similar_programs": [],
            "price_vs_market_pct": None,
        }
        if spec.factory_name:
            rel = (
                self.db.query(CompanyFactoryRelationship)
                .filter_by(
                    company_id=self.company_id,
                    factory_name=spec.factory_name,
                )
                .first()
            )
            if rel:
                ctx["factory_relationship"] = {
                    "factory_name": rel.factory_name,
                    "programs_completed": rel.programs_completed,
                    "avg_otd_rate": float(rel.avg_otd_rate) if rel.avg_otd_rate else None,
                    "avg_price_vs_market_pct": (
                        float(rel.avg_price_vs_market_pct)
                        if rel.avg_price_vs_market_pct is not None
                        else None
                    ),
                }
                ctx["price_vs_market_pct"] = ctx["factory_relationship"][
                    "avg_price_vs_market_pct"
                ]

        similar = (
            self.db.query(PurchaseOrderHistory)
            .filter_by(company_id=self.company_id)
            .order_by(desc(PurchaseOrderHistory.created_at))
            .limit(10)
            .all()
        )
        for po in similar:
            if spec.corridor and po.corridor != spec.corridor:
                continue
            ctx["similar_programs"].append(
                {
                    "po_reference": po.po_reference,
                    "factory": po.factory_name,
                    "actual_fob": float(po.actual_fob_per_dozen)
                    if po.actual_fob_per_dozen
                    else None,
                }
            )
        return ctx

    def _get_live_market_data(self, spec: ProgramSpec) -> dict:
        data: dict[str, Any] = {}
        cotton = (
            self.db.query(Cotton)
            .filter(Cotton.is_latest.is_(True))
            .order_by(desc(Cotton.as_of_date))
            .first()
        )
        if cotton:
            data["cotton_spot_cents_lb"] = float(cotton.spot_price)
        su = (
            self.db.query(CottonSupplyDemand)
            .order_by(desc(CottonSupplyDemand.report_month))
            .first()
        )
        if su and su.world_stocks_to_use_ratio_pct is not None:
            su_pct = float(su.world_stocks_to_use_ratio_pct)
            data["cotton_stocks_to_use_pct"] = su_pct
            data["cotton_market"] = "bearish" if su_pct > 60 else "bullish"
        else:
            data["cotton_market"] = "neutral"

        crude = (
            self.db.query(CrudeOil)
            .filter(CrudeOil.is_latest.is_(True))
            .order_by(desc(CrudeOil.as_of_date))
            .first()
        )
        if crude and crude.wti_spot is not None:
            wti = float(crude.wti_spot)
            data["crude_wti"] = wti
            data["crude_oil"] = "elevated" if wti > 75 else "normal"

        return data

    def _layer_applies(self, layer: CostLayerPrior, spec: ProgramSpec) -> bool:
        if layer.is_mandatory:
            return True
        if layer.applies_when == "has_print":
            return spec.has_print is True
        return False

    def _match_variable(self, var: CostVariablePrior, spec: ProgramSpec, market: dict) -> bool:
        name = var.variable_name
        value = var.variable_value

        if value == "discovered_at_runtime":
            return False

        if name == "count" and spec.count:
            return spec.count.lower() == value.lower()
        if name == "grade" and spec.grade:
            return spec.grade.lower() == value.lower()
        if name == "fibre" and spec.fibre_content:
            if value == "blend_spandex":
                return "spandex" in spec.fibre_content.lower() or "elastane" in spec.fibre_content.lower()
        if name == "corridor" and spec.corridor:
            return spec.corridor.lower() == value.lower()
        if name == "cotton_market":
            return market.get("cotton_market") == value
        if name == "local_signal" and spec.corridor:
            return value == "egypt_export_demand" and spec.corridor == "India"
        if name == "colour_tier" and spec.colour_tier:
            return spec.colour_tier == value
        if name == "crude_oil":
            return market.get("crude_oil") == value
        if name == "style_complexity" and spec.style_complexity:
            return spec.style_complexity == value
        if name == "construction" and spec.construction:
            return spec.construction == value
        if name == "gsm" and spec.gsm is not None:
            if value == "under_160":
                return spec.gsm < 160
            if value == "160_220":
                return 160 <= spec.gsm <= 220
            if value == "over_220":
                return spec.gsm > 220
        if name == "quantity" and spec.quantity_dozens:
            if value == "25000_plus":
                return spec.quantity_dozens >= 25000
            if value == "100000_plus":
                return spec.quantity_dozens >= 100000
        if name == "print_type" and spec.print_type:
            return spec.print_type == value
        if name == "retailer" and spec.retailer:
            return spec.retailer == value
        return False

    def _apply_effect(
        self,
        low: Decimal,
        high: Decimal,
        var: CostVariablePrior,
    ) -> tuple[Decimal, Decimal, str]:
        weight = float(var.confidence)
        if var.effect_type == "pct":
            factor_lo = Decimal("1") + var.current_effect_low / Decimal("100")
            factor_hi = Decimal("1") + var.current_effect_high / Decimal("100")
            if var.current_effect_low == 0 and var.current_effect_high == 0:
                return low, high, ""
            new_low = low * factor_lo
            new_high = high * factor_hi
            label = f"{var.variable_name}={var.variable_value} ({var.current_effect_low}%–{var.current_effect_high}%)"
        else:
            new_low = low + var.current_effect_low
            new_high = high + var.current_effect_high
            if var.current_effect_low == 0 and var.current_effect_high == 0:
                return low, high, ""
            label = f"{var.variable_name}={var.variable_value} (+${var.current_effect_low}–${var.current_effect_high}/doz)"

        if weight < 1.0:
            mid_lo = (low + new_low) / 2
            mid_hi = (high + new_high) / 2
            return mid_lo, mid_hi, label
        return new_low, new_high, label

    def _apply_discovered_factor(
        self,
        low: Decimal,
        high: Decimal,
        factor: DiscoveredCostFactor,
    ) -> tuple[Decimal, Decimal, str]:
        mag_lo = factor.effect_magnitude_low
        mag_hi = factor.effect_magnitude_high
        sign = Decimal("1") if factor.effect_direction == "increase" else Decimal("-1")
        if factor.effect_unit == "pct":
            return (
                low * (Decimal("1") + sign * mag_lo / Decimal("100")),
                high * (Decimal("1") + sign * mag_hi / Decimal("100")),
                factor.factor_name,
            )
        return low + sign * mag_lo, high + sign * mag_hi, factor.factor_name

    def _reason_all_layers(
        self,
        spec: ProgramSpec,
        priors: dict[str, CostLayerPrior],
        discovered: list[DiscoveredCostFactor],
        company_context: Optional[dict],
        market_data: dict,
    ) -> list[LayerEstimate]:
        estimates: list[LayerEstimate] = []

        for layer_name, layer in sorted(
            priors.items(), key=lambda x: x[1].sequence_order
        ):
            if not self._layer_applies(layer, spec):
                continue

            low = layer.current_low
            high = layer.current_high
            notes: list[str] = []
            factors: list[str] = []

            variables = (
                self.db.query(CostVariablePrior)
                .filter_by(cost_layer_id=layer.id)
                .all()
            )
            for var in variables:
                if self._match_variable(var, spec, market_data):
                    if float(var.confidence) <= 0:
                        notes.append(
                            f"Unknown factor slot: {var.variable_name} — not yet characterised"
                        )
                        continue
                    low, high, label = self._apply_effect(low, high, var)
                    if label:
                        factors.append(label)

            for factor in discovered:
                if factor.layer_name == layer.layer_name or factor.layer_name == "unknown":
                    low, high, label = self._apply_discovered_factor(low, high, factor)
                    factors.append(f"discovered:{label}")

            mid = (low + high) / 2
            conf = 0.72 if factors else 0.65
            if any("discovered:" in f for f in factors):
                conf = min(0.88, conf + 0.08)

            estimates.append(
                LayerEstimate(
                    layer_name=layer_name,
                    low=low.quantize(Decimal("0.0001")),
                    mid=mid.quantize(Decimal("0.0001")),
                    high=high.quantize(Decimal("0.0001")),
                    confidence=conf,
                    notes=notes,
                    factors_applied=factors,
                )
            )

        return estimates

    def _identify_unknowns(
        self,
        spec: ProgramSpec,
        priors: dict[str, CostLayerPrior],
        discovered: list[DiscoveredCostFactor],
    ) -> list[str]:
        unknowns: list[str] = []
        if spec.has_print is None:
            unknowns.append(
                "Print cost unknown — if program includes print, add $0.30–0.60/doz"
            )
        if not spec.factory_name:
            unknowns.append(
                "Factory identity unknown — no relationship context or factory-specific premiums"
            )
        if not spec.style_complexity or spec.style_complexity == "basic":
            unknowns.append(
                "Style complexity uncertain — CMT range is wide for unconfirmed styles"
            )
        if spec.factory_name and spec.colour_tier:
            has_factory_colour = any(
                f.applies_to_factory == spec.factory_name
                and f.applies_to_colour_tier == spec.colour_tier
                for f in discovered
            )
            if not has_factory_colour:
                unknowns.append(
                    f"No discovered factory-specific premium for {spec.factory_name} "
                    f"+ {spec.colour_tier} colour"
                )
        if not spec.season:
            unknowns.append(
                "Season unknown — seasonal labour availability factors not applied"
            )
        return unknowns

    def _compile_result(
        self,
        spec: ProgramSpec,
        layer_estimates: list[LayerEstimate],
        company_context: Optional[dict],
        mode: str,
        unknowns: list[str],
        market_data: dict,
    ) -> CostReasoningResult:
        total_low = sum((le.low for le in layer_estimates), Decimal("0"))
        total_high = sum((le.high for le in layer_estimates), Decimal("0"))
        total_mid = sum((le.mid for le in layer_estimates), Decimal("0"))

        if company_context and company_context.get("price_vs_market_pct") is not None:
            adj = Decimal(str(company_context["price_vs_market_pct"])) / Decimal("100")
            total_low *= Decimal("1") + adj
            total_mid *= Decimal("1") + adj
            total_high *= Decimal("1") + adj

        confidence_map = {
            "prior_only": 0.68,
            "prior_plus_learned": 0.78,
            "prior_plus_company": 0.74,
            "full_company_centric": 0.84,
        }
        confidence = confidence_map.get(mode, 0.68)
        confidence -= len(unknowns) * 0.015
        confidence = max(0.55, min(0.92, confidence))
        if mode == "prior_only" and len(unknowns) <= 4:
            confidence = max(confidence, 0.68)

        flags: list[str] = []
        if market_data.get("cotton_market") == "bearish":
            flags.append(
                "Cotton bearish (elevated S/U) — yarn cost relief in estimate"
            )
        elif market_data.get("cotton_market") == "bullish":
            flags.append("Cotton bullish — yarn cost pressure in estimate")
        if market_data.get("crude_oil") == "elevated" and spec.colour_tier == "dark":
            flags.append("Dark dyeing costs elevated (crude oil premium)")
        if spec.corridor == "India" and market_data.get("cotton_market") == "bearish":
            flags.append(
                "Local Tirupur yarn may remain tight despite bearish ICE "
                "(Egypt export demand signal — monitor)"
            )
        if company_context and company_context.get("factory_relationship"):
            rel = company_context["factory_relationship"]
            if rel.get("avg_price_vs_market_pct") is not None:
                pct = rel["avg_price_vs_market_pct"]
                direction = "below" if pct < 0 else "above"
                flags.append(
                    f"Company history with {rel['factory_name']}: typically "
                    f"{abs(pct):.1f}% {direction} market on similar programs"
                )

        if mode == "prior_only":
            flags.insert(
                0,
                "Market benchmark based on Tirupur industry knowledge. "
                "No operator-specific context applied.",
            )
        elif mode == "full_company_centric":
            flags.insert(
                0,
                f"Estimate personalised for {company_context['company_name']} "
                f"using factory and PO history.",
            )

        vs_target = "No target FOB provided."
        if spec.target_fob is not None:
            if spec.target_fob <= total_high:
                margin_lo = spec.target_fob - total_high
                margin_hi = spec.target_fob - total_low
                vs_target = (
                    f"Achievable at current estimate. Target ${spec.target_fob} "
                    f"gives ${margin_lo:.2f}–${margin_hi:.2f}/doz margin vs range."
                )
            else:
                gap = spec.target_fob - total_high
                vs_target = (
                    f"Target ${spec.target_fob} requires ${gap:.2f}/doz above "
                    f"current high estimate — challenging without concessions."
                )

        missing: list[str] = []
        if not spec.factory_name:
            missing.append("factory_name")
        if spec.has_print is None:
            missing.append("has_print")

        return CostReasoningResult(
            session_id=str(uuid.uuid4()),
            reasoning_mode=mode,
            fob_low=total_low.quantize(Decimal("0.01")),
            fob_mid=total_mid.quantize(Decimal("0.01")),
            fob_high=total_high.quantize(Decimal("0.01")),
            confidence_overall=confidence,
            vs_target_fob=vs_target,
            flags=flags,
            unknowns=unknowns,
            missing_inputs=missing,
            layer_estimates=layer_estimates,
            company_context_applied=company_context is not None,
            market_context=market_data,
        )

    def _log_session(
        self,
        spec: ProgramSpec,
        result: CostReasoningResult,
        mode: str,
        company_context: Optional[dict],
        discovered: list[DiscoveredCostFactor],
    ) -> None:
        session = CostReasoningSession(
            session_id=result.session_id,
            company_id=self.company_id,
            input_context=json.dumps(spec.__dict__, default=str),
            inferred_spec=json.dumps(spec.__dict__, default=str),
            prior_layers_used=json.dumps(
                [le.layer_name for le in result.layer_estimates]
            ),
            company_context_applied=result.company_context_applied,
            company_factors_used=json.dumps(company_context) if company_context else None,
            discovered_factors_applied=json.dumps(
                [f.factor_name for f in discovered]
            ),
            layer_estimates=json.dumps(
                [
                    {
                        "layer": le.layer_name,
                        "low": str(le.low),
                        "mid": str(le.mid),
                        "high": str(le.high),
                        "factors": le.factors_applied,
                    }
                    for le in result.layer_estimates
                ]
            ),
            total_low=result.fob_low,
            total_mid=result.fob_mid,
            total_high=result.fob_high,
            confidence_overall=Decimal(str(result.confidence_overall)),
            flags=json.dumps(result.flags),
            missing_inputs=json.dumps(result.missing_inputs),
            unknown_factors_flagged=json.dumps(result.unknowns),
        )
        self.db.add(session)
        self.db.commit()

"""Seed Tirupur benchmark cost layer priors and variables."""

from decimal import Decimal
from typing import Optional

from database.database import SessionLocal
from database.models import CostLayerPrior, CostVariablePrior

SOURCE = "tirupur_benchmark_v1"
DEFAULT_CONFIDENCE = Decimal("0.65")


def _layer(
    name: str,
    seq: int,
    low: str,
    high: str,
    *,
    mandatory: bool = True,
    applies_when: Optional[str] = None,
    unit: str = "usd_per_dozen",
    stability: str = "moderate",
    notes: Optional[str] = None,
) -> dict:
    val_low = Decimal(low)
    val_high = Decimal(high)
    return {
        "layer_name": name,
        "sequence_order": seq,
        "is_mandatory": mandatory,
        "applies_when": applies_when,
        "unit": unit,
        "prior_low": val_low,
        "prior_high": val_high,
        "current_low": val_low,
        "current_high": val_high,
        "update_count": 0,
        "stability": stability,
        "source": SOURCE,
        "notes": notes,
    }


def _var(
    layer_name: str,
    variable_name: str,
    variable_value: str,
    effect_type: str,
    eff_low: str,
    eff_high: str,
    *,
    confidence: Decimal = DEFAULT_CONFIDENCE,
    reasoning: Optional[str] = None,
) -> dict:
    low = Decimal(eff_low)
    high = Decimal(eff_high)
    return {
        "layer_name": layer_name,
        "variable_name": variable_name,
        "variable_value": variable_value,
        "effect_type": effect_type,
        "prior_effect_low": low,
        "prior_effect_high": high,
        "current_effect_low": low,
        "current_effect_high": high,
        "observation_count": 0,
        "confidence": confidence,
        "reasoning": reasoning,
        "source": SOURCE,
    }


LAYERS = [
    _layer(
        "yarn",
        1,
        "7.80",
        "8.70",
        notes="30s carded cotton yarn equivalent per dozen at Tirupur benchmark GSM.",
    ),
    _layer("knitting", 2, "0.32", "0.48"),
    _layer("heat_setting", 3, "0.08", "0.14"),
    _layer("scouring_pfd", 4, "0.18", "0.34"),
    _layer(
        "dyeing",
        5,
        "0.42",
        "0.62",
        notes="Reactive dyeing baseline; colour tier variables adjust from here.",
    ),
    _layer("fixing_softening", 6, "0.10", "0.18"),
    _layer("drying", 7, "0.08", "0.14"),
    _layer("compacting", 8, "0.12", "0.20"),
    _layer(
        "cmt",
        9,
        "2.20",
        "3.10",
        notes="Cut-make-trim for basic-moderate knit; style complexity adjusts.",
    ),
    _layer(
        "printing",
        10,
        "0.30",
        "0.65",
        mandatory=False,
        applies_when="has_print",
    ),
    _layer("trims", 11, "0.42", "0.72"),
    _layer("local_freight", 12, "0.12", "0.24"),
]

VARIABLES = [
    # Yarn
    _var("yarn", "count", "40s", "pct", "8", "14", reasoning="40s finer count premium over 30s."),
    _var("yarn", "count", "30s", "pct", "0", "0", reasoning="Baseline count for Tirupur basics."),
    _var("yarn", "grade", "combed", "pct", "10", "18", reasoning="Combed yarn premium over carded."),
    _var("yarn", "grade", "carded", "pct", "0", "0", reasoning="Baseline carded grade."),
    _var("yarn", "fibre", "blend_spandex", "pct", "12", "22", reasoning="Spandex blend yarn premium."),
    _var("yarn", "corridor", "Bangladesh", "pct", "-2", "2", reasoning="Bangladesh local yarn pricing."),
    _var("yarn", "corridor", "India", "pct", "0", "0", reasoning="Tirupur baseline corridor."),
    _var("yarn", "corridor", "Vietnam", "pct", "3", "8", reasoning="Imported yarn dependency premium."),
    _var(
        "yarn",
        "cotton_market",
        "bearish",
        "pct",
        "-8",
        "-3",
        reasoning="ICE cotton bearish — yarn cost relief.",
    ),
    _var(
        "yarn",
        "cotton_market",
        "bullish",
        "pct",
        "3",
        "8",
        reasoning="ICE cotton bullish — yarn cost pressure.",
    ),
    _var(
        "yarn",
        "local_signal",
        "egypt_export_demand",
        "usd_per_dozen",
        "0.20",
        "0.35",
        reasoning="Tirupur local yarn tightness from Egypt export demand.",
    ),
    _var(
        "yarn",
        "unknown_local_market_factor",
        "discovered_at_runtime",
        "pct",
        "0",
        "0",
        confidence=Decimal("0.0"),
        reasoning=(
            "Placeholder for factors the system will discover from real yarn market "
            "instances. The Egypt export demand signal is one example — there will be others."
        ),
    ),
    # Knitting
    _var("knitting", "gsm", "under_160", "pct", "-8", "-4"),
    _var("knitting", "gsm", "160_220", "pct", "0", "0"),
    _var("knitting", "gsm", "over_220", "pct", "12", "25"),
    _var("knitting", "construction", "fleece", "pct", "20", "35"),
    _var("knitting", "construction", "pique", "pct", "8", "15"),
    _var("knitting", "construction", "single_jersey", "pct", "0", "0"),
    # Dyeing
    _var("dyeing", "colour_tier", "light", "pct", "-22", "-12"),
    _var("dyeing", "colour_tier", "medium", "pct", "0", "0"),
    _var("dyeing", "colour_tier", "dark", "pct", "18", "30"),
    _var(
        "dyeing",
        "crude_oil",
        "elevated",
        "pct",
        "5",
        "12",
        reasoning="Higher crude increases dye chemical and energy costs.",
    ),
    _var(
        "dyeing",
        "factory_specific_colour_premium",
        "discovered_at_runtime",
        "usd_per_dozen",
        "0",
        "0",
        confidence=Decimal("0.0"),
        reasoning=(
            "Some dye houses specialise in certain colours and charge premiums for "
            "colours outside their specialisation. The system will discover these "
            "factory-specific factors from real programs."
        ),
    ),
    # CMT
    _var("cmt", "style_complexity", "basic", "pct", "0", "0"),
    _var("cmt", "style_complexity", "moderate", "pct", "10", "20"),
    _var("cmt", "style_complexity", "complex", "pct", "25", "45"),
    _var("cmt", "construction", "fleece", "pct", "20", "35"),
    _var("cmt", "corridor", "Bangladesh", "pct", "-12", "-5"),
    _var("cmt", "corridor", "India", "pct", "0", "0"),
    _var("cmt", "corridor", "Vietnam", "pct", "-5", "2"),
    _var("cmt", "quantity", "25000_plus", "pct", "-6", "-3"),
    _var("cmt", "quantity", "100000_plus", "pct", "-12", "-6"),
    _var(
        "cmt",
        "seasonal_labour_availability",
        "discovered_at_runtime",
        "pct",
        "0",
        "0",
        confidence=Decimal("0.0"),
        reasoning=(
            "Seasonal factors (Eid, harvest seasons, elections) affect CMT labour "
            "availability and cost in specific corridors. The system will learn these "
            "patterns from real program outcomes."
        ),
    ),
    # Printing
    _var("printing", "print_type", "chest_logo", "usd_per_dozen", "0.30", "0.45"),
    _var("printing", "print_type", "all_over", "usd_per_dozen", "0.55", "0.85"),
    # Trims
    _var("trims", "retailer", "target_compliance", "usd_per_dozen", "0.12", "0.20"),
    _var("trims", "trims_type", "nominated", "pct", "8", "15"),
    # Local freight
    _var("local_freight", "corridor", "Bangladesh", "pct", "-5", "0"),
    _var("local_freight", "corridor", "India", "pct", "0", "0"),
]


def seed_cost_priors() -> None:
    db = SessionLocal()
    try:
        if db.query(CostLayerPrior).first():
            print("Cost priors already seeded — skipping.")
            return

        layer_ids: dict[str, int] = {}
        for layer_data in LAYERS:
            layer = CostLayerPrior(**layer_data)
            db.add(layer)
            db.flush()
            layer_ids[layer.layer_name] = layer.id

        for var_data in VARIABLES:
            layer_name = var_data.pop("layer_name")
            db.add(CostVariablePrior(cost_layer_id=layer_ids[layer_name], **var_data))

        db.commit()
        print(
            f"Seeded {len(LAYERS)} cost layer priors and {len(VARIABLES)} variable priors."
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_cost_priors()

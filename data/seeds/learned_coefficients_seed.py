"""
Seed script: Industry-prior learned coefficients.

Run once after the f0a1b2c3d4e5 migration to populate the learned_coefficient table
with the initial set of industry-prior values. These are the best-available benchmarks
before RRK's own data calibrates the system.

Every coefficient starts at confidence_tier='industry_prior'. As RRK operational records
are ingested (yarn GRNs → FabricKnitting rows, process steps, invoices), the calibration
engine will promote tiers to rrk_provisional → rrk_measured → rrk_high_confidence.

Usage:
    python -m data.seeds.learned_coefficients_seed
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from sqlalchemy.orm import Session
from database.database import engine
from database.models.knowledge import LearnedCoefficient


COEFFICIENTS = [
    # ── yarn_to_fabric_ratio ─────────────────────────────────────────────────
    # kg of yarn consumed per kg of greige fabric produced.
    # Derived from: (yarn_GRN_kg_consumed / greige_fabric_output_kg) per knit run.
    # Source: industry standard; Tirupur mill surveys; ATIRA research.
    {
        "coefficient_name": "yarn_to_fabric_ratio_single_jersey_120_150gsm",
        "description": "kg yarn per kg greige fabric for single jersey 120-150 gsm",
        "scope_construction_type": "single_jersey",
        "scope_gsm_min": 120.0,
        "scope_gsm_max": 150.0,
        "scope_corridor": "India/Tirupur",
        "value": 1.08,
        "unit": "ratio",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "yarn_to_fabric_ratio_single_jersey_150_200gsm",
        "description": "kg yarn per kg greige fabric for single jersey 150-200 gsm",
        "scope_construction_type": "single_jersey",
        "scope_gsm_min": 150.0,
        "scope_gsm_max": 200.0,
        "scope_corridor": "India/Tirupur",
        "value": 1.09,
        "unit": "ratio",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "yarn_to_fabric_ratio_interlock",
        "description": "kg yarn per kg greige fabric for interlock",
        "scope_construction_type": "interlock",
        "scope_corridor": "India/Tirupur",
        "value": 1.14,
        "unit": "ratio",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "yarn_to_fabric_ratio_rib_1x1",
        "description": "kg yarn per kg greige fabric for 1×1 rib",
        "scope_construction_type": "rib_1x1",
        "scope_corridor": "India/Tirupur",
        "value": 1.18,
        "unit": "ratio",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "yarn_to_fabric_ratio_french_terry",
        "description": "kg yarn per kg greige fabric for french terry",
        "scope_construction_type": "french_terry",
        "scope_corridor": "India/Tirupur",
        "value": 1.32,
        "unit": "ratio",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "yarn_to_fabric_ratio_fleece_3thread",
        "description": "kg yarn per kg greige fabric for 3-thread fleece",
        "scope_construction_type": "fleece_3thread",
        "scope_corridor": "India/Tirupur",
        "value": 1.42,
        "unit": "ratio",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "yarn_to_fabric_ratio_fleece_2thread",
        "description": "kg yarn per kg greige fabric for 2-thread fleece",
        "scope_construction_type": "fleece_2thread",
        "scope_corridor": "India/Tirupur",
        "value": 1.38,
        "unit": "ratio",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "yarn_to_fabric_ratio_pique",
        "description": "kg yarn per kg greige fabric for pique",
        "scope_construction_type": "pique",
        "scope_corridor": "India/Tirupur",
        "value": 1.11,
        "unit": "ratio",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "yarn_to_fabric_ratio_waffle",
        "description": "kg yarn per kg greige fabric for waffle",
        "scope_construction_type": "waffle",
        "scope_corridor": "India/Tirupur",
        "value": 1.22,
        "unit": "ratio",
        "confidence_tier": "industry_prior",
    },

    # ── cmt_minutes_per_dozen by complexity score ─────────────────────────────
    # Minutes of direct sewing labour to produce one dozen units, by complexity score.
    # complexity_score 1 = basic tee; 10 = complex multi-technique hoodie with zipper + embroidery.
    # Source: SMV (Standard Minute Value) databases; industry benchmarks from BGMEA/AEPC data.
    {
        "coefficient_name": "cmt_minutes_per_dozen_complexity_1",
        "description": "CMT labour minutes per dozen for complexity score 1 (basic tee, no embellishments)",
        "scope_corridor": "India/Tirupur",
        "value": 42.0,
        "unit": "minutes",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "cmt_minutes_per_dozen_complexity_2",
        "description": "CMT labour minutes per dozen for complexity score 2",
        "scope_corridor": "India/Tirupur",
        "value": 52.0,
        "unit": "minutes",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "cmt_minutes_per_dozen_complexity_3",
        "description": "CMT labour minutes per dozen for complexity score 3 (polo, basic pocket tee)",
        "scope_corridor": "India/Tirupur",
        "value": 65.0,
        "unit": "minutes",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "cmt_minutes_per_dozen_complexity_4",
        "description": "CMT labour minutes per dozen for complexity score 4",
        "scope_corridor": "India/Tirupur",
        "value": 78.0,
        "unit": "minutes",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "cmt_minutes_per_dozen_complexity_5",
        "description": "CMT labour minutes per dozen for complexity score 5 (sweatshirt, jogger)",
        "scope_corridor": "India/Tirupur",
        "value": 92.0,
        "unit": "minutes",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "cmt_minutes_per_dozen_complexity_6",
        "description": "CMT labour minutes per dozen for complexity score 6",
        "scope_corridor": "India/Tirupur",
        "value": 108.0,
        "unit": "minutes",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "cmt_minutes_per_dozen_complexity_7",
        "description": "CMT labour minutes per dozen for complexity score 7 (hoodie pullover with kangaroo pocket)",
        "scope_corridor": "India/Tirupur",
        "value": 125.0,
        "unit": "minutes",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "cmt_minutes_per_dozen_complexity_8",
        "description": "CMT labour minutes per dozen for complexity score 8 (zip hoodie)",
        "scope_corridor": "India/Tirupur",
        "value": 145.0,
        "unit": "minutes",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "cmt_minutes_per_dozen_complexity_9",
        "description": "CMT labour minutes per dozen for complexity score 9",
        "scope_corridor": "India/Tirupur",
        "value": 165.0,
        "unit": "minutes",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "cmt_minutes_per_dozen_complexity_10",
        "description": "CMT labour minutes per dozen for complexity score 10 (multi-technique jacket)",
        "scope_corridor": "India/Tirupur",
        "value": 190.0,
        "unit": "minutes",
        "confidence_tier": "industry_prior",
    },

    # ── cutting_wastage_pct ───────────────────────────────────────────────────
    # % of fabric input that is lost to cutting (markers, off-cuts, selvedge waste).
    # Derived from: (fabric_in_kg - fabric_panels_out_kg) / fabric_in_kg × 100
    {
        "coefficient_name": "cutting_wastage_pct_basic_tee",
        "description": "% of fabric lost to cutting for basic tee silhouette",
        "scope_construction_type": "crew_neck_tee",
        "scope_corridor": "India/Tirupur",
        "value": 10.0,
        "unit": "pct",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "cutting_wastage_pct_polo",
        "description": "% of fabric lost to cutting for polo shirt (higher due to collar/placket)",
        "scope_construction_type": "polo",
        "scope_corridor": "India/Tirupur",
        "value": 12.0,
        "unit": "pct",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "cutting_wastage_pct_hoodie",
        "description": "% of fabric lost to cutting for hoodie (complex panels)",
        "scope_construction_type": "hoodie_pullover",
        "scope_corridor": "India/Tirupur",
        "value": 14.0,
        "unit": "pct",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "cutting_wastage_pct_leggings",
        "description": "% of fabric lost to cutting for leggings (tubular cut, low waste)",
        "scope_construction_type": "leggings",
        "scope_corridor": "India/Tirupur",
        "value": 7.0,
        "unit": "pct",
        "confidence_tier": "industry_prior",
    },

    # ── dyeing cost premiums ──────────────────────────────────────────────────
    {
        "coefficient_name": "dark_colour_dye_premium_pct",
        "description": "% cost premium for dark colours vs standard (navy, black, dark grey) — more dye, more water, more passes",
        "scope_corridor": "India/Tirupur",
        "value": 12.0,
        "unit": "pct",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "melange_yarn_premium_pct_over_standard",
        "description": "% price premium for melange yarn vs standard yarn of same count and fibre",
        "scope_corridor": "India/Tirupur",
        "value": 8.0,
        "unit": "pct",
        "confidence_tier": "industry_prior",
    },

    # ── market transmission lags ──────────────────────────────────────────────
    {
        "coefficient_name": "cotton_to_yarn_price_transmission_lag_weeks_tirupur",
        "description": "Weeks from ICE cotton futures move to yarn mill price change in Tirupur market. Driven by cotton-to-yarn processing cycle + trader inventory buffer.",
        "scope_corridor": "India/Tirupur",
        "value": 6.0,
        "unit": "weeks",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "yarn_to_fabric_price_transmission_lag_weeks_tirupur",
        "description": "Weeks from yarn price move to knitting mill job-work rate change in Tirupur",
        "scope_corridor": "India/Tirupur",
        "value": 3.0,
        "unit": "weeks",
        "confidence_tier": "industry_prior",
    },

    # ── typical piece weights ─────────────────────────────────────────────────
    # Typical finished garment weight in grams for a medium-size unit.
    # This is the most important prior for FOB estimation before actual tech pack.
    # Calibrate against actual piece_weight_grams from GarmentConstruction records.
    {
        "coefficient_name": "piece_weight_grams_crew_neck_tee_140gsm_medium",
        "description": "Typical piece weight (grams) for a crew-neck tee in 140 gsm single jersey, size M",
        "scope_construction_type": "crew_neck_tee",
        "scope_gsm_min": 130.0,
        "scope_gsm_max": 155.0,
        "value": 155.0,
        "unit": "grams",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "piece_weight_grams_crew_neck_tee_180gsm_medium",
        "description": "Typical piece weight (grams) for a crew-neck tee in 180 gsm single jersey, size M",
        "scope_construction_type": "crew_neck_tee",
        "scope_gsm_min": 165.0,
        "scope_gsm_max": 200.0,
        "value": 200.0,
        "unit": "grams",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "piece_weight_grams_polo_220gsm_medium",
        "description": "Typical piece weight (grams) for a pique polo in 220 gsm, size M",
        "scope_construction_type": "polo",
        "scope_gsm_min": 200.0,
        "scope_gsm_max": 240.0,
        "value": 265.0,
        "unit": "grams",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "piece_weight_grams_hoodie_fleece_300gsm_medium",
        "description": "Typical piece weight (grams) for a pullover hoodie in 300 gsm 3-thread fleece, size M",
        "scope_construction_type": "hoodie_pullover",
        "scope_gsm_min": 280.0,
        "scope_gsm_max": 330.0,
        "value": 620.0,
        "unit": "grams",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "piece_weight_grams_sweatshirt_fleece_300gsm_medium",
        "description": "Typical piece weight (grams) for a crewneck sweatshirt in 300 gsm fleece, size M",
        "scope_construction_type": "sweatshirt",
        "scope_gsm_min": 280.0,
        "scope_gsm_max": 330.0,
        "value": 540.0,
        "unit": "grams",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "piece_weight_grams_jogger_fleece_300gsm_medium",
        "description": "Typical piece weight (grams) for jogger pants in 300 gsm fleece, size M",
        "scope_construction_type": "jogger",
        "scope_gsm_min": 280.0,
        "scope_gsm_max": 330.0,
        "value": 580.0,
        "unit": "grams",
        "confidence_tier": "industry_prior",
    },

    # ── spinning premium components ───────────────────────────────────────────
    {
        "coefficient_name": "spinning_premium_pct_ring_spun_30s_tirupur",
        "description": "Spinning conversion premium as % of cotton value for 30s ring spun yarn in Tirupur",
        "scope_corridor": "India/Tirupur",
        "value": 35.0,
        "unit": "pct",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "spinning_premium_pct_compact_30s_tirupur",
        "description": "Spinning conversion premium as % of cotton value for 30s compact yarn (higher than ring spun)",
        "scope_corridor": "India/Tirupur",
        "value": 45.0,
        "unit": "pct",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "spinning_premium_pct_open_end_20s_tirupur",
        "description": "Spinning conversion premium as % of cotton value for 20s open-end (lower, faster process)",
        "scope_corridor": "India/Tirupur",
        "value": 22.0,
        "unit": "pct",
        "confidence_tier": "industry_prior",
    },

    # ── compacting / finishing shrinkage ──────────────────────────────────────
    {
        "coefficient_name": "compacting_shrinkage_pct_single_jersey",
        "description": "GSM gain and width reduction after compacting single jersey (finished GSM / greige GSM - 1)",
        "scope_construction_type": "single_jersey",
        "value": 5.0,
        "unit": "pct",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "dyeing_weight_loss_pct_reactive",
        "description": "Weight loss % during reactive dyeing process (moisture + chemical absorption net of washed-out dye)",
        "scope_corridor": "India/Tirupur",
        "value": 3.0,
        "unit": "pct",
        "confidence_tier": "industry_prior",
    },

    # ── container utilisation ─────────────────────────────────────────────────
    {
        "coefficient_name": "cartons_per_40hq_apparel_standard",
        "description": "Approximate carton count achievable in a 40HQ container for standard apparel carton sizes (~50×40×30 cm)",
        "value": 680.0,
        "unit": "cartons",
        "confidence_tier": "industry_prior",
    },
    {
        "coefficient_name": "cbm_per_40hq_usable",
        "description": "Usable cubic metres in a 40HQ container for general cargo",
        "value": 67.0,
        "unit": "cbm",
        "confidence_tier": "industry_prior",
    },
]


def seed_learned_coefficients() -> None:
    with Session(engine) as session:
        inserted = 0
        skipped = 0
        for data in COEFFICIENTS:
            existing = session.query(LearnedCoefficient).filter_by(
                coefficient_name=data["coefficient_name"]
            ).first()
            if existing:
                skipped += 1
                continue
            coeff = LearnedCoefficient(**data)
            session.add(coeff)
            inserted += 1
        session.commit()
        print(f"Learned coefficients seed complete: {inserted} inserted, {skipped} already present")


if __name__ == "__main__":
    seed_learned_coefficients()

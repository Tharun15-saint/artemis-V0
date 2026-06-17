"""Seed KnowledgeGap records for the synthetic fiber cost chain.

Run once to document that the PX → PTA → polyester chip → yarn chain has
no live data feeds. The intelligence engine must surface these gaps before
making any recommendation involving polyester or viscose programs.

Classic Fashion / Athlux Studio programs known to use synthetic fibers:
  - Polyester fleece (100% polyester): fully in the broken chain
  - Cotton/poly blends (C/P 60/40, 50/50): partially affected
  - Viscose-blend fabrics: fully in the broken chain

RRK Tirupur cluster is cotton-dominant but Classic Fashion programs include
blended and synthetic constructions. This chain gap is non-trivial.

Usage:
    python -m data.seeds.synthetic_fiber_chain_gaps
    python -m data.seeds.synthetic_fiber_chain_gaps --overwrite
"""

import argparse
import logging

from data.ingestion._env import load_project_env
from database.base import SessionLocal
from database.models import KnowledgeGap

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

GAPS = [
    {
        "gap_description": (
            "PX Paraxylene spot price has no live data feed. The px_paraxylene table has "
            "0 rows. Paraxylene is the first petrochemical derivative of crude oil on the "
            "path to polyester fiber: crude → naphtha → PX → PTA → PET chip → polyester yarn. "
            "Without PX prices, the system cannot derive the cost of any polyester-containing "
            "fabric from first principles."
        ),
        "gap_domain": "market",
        "gap_severity": "blocks_reasoning",
        "analogous_knowledge": (
            "Cotton cost chain is fully live (ICE No.2 → Tirupur yarn market rate). "
            "The PX chain is structurally identical but requires ICIS or PCI Fibres data access."
        ),
        "resolution_path": (
            "Option A (preferred): Subscribe to ICIS PX Asia assessment (weekly, USD/MT CFR Asia). "
            "ICIS provides weekly PX, PTA, and polyester chip prices in a single feed — "
            "one subscription closes three gaps simultaneously. "
            "Option B: Use CPCIF (China Petroleum and Chemical Industry Federation) monthly PX "
            "prices available on their public portal. Less frequent but free. "
            "Option C: Derive from crude using a fixed margin model "
            "(PX ≈ crude_usd_per_bbl × 4.5 + spread_factor) as a temporary proxy, clearly flagged "
            "as synthetic until real data is available."
        ),
        "related_coefficient_name": None,
        "status": "open",
    },
    {
        "gap_description": (
            "PTA (Purified Terephthalic Acid) spot price has no live data feed. The pta table "
            "has 0 rows. PTA is one step downstream of PX in the polyester chain: "
            "PX → PTA → PET chip → polyester yarn. PTA prices are published by ICIS and "
            "the China Textile Information Center (CTIC). Chinese PTA prices are the most "
            "relevant for Tirupur importers since most Asian polyester comes from China."
        ),
        "gap_domain": "market",
        "gap_severity": "blocks_reasoning",
        "analogous_knowledge": (
            "The lag structure from crude → PX → PTA → chip is approximately 2–4 weeks per step. "
            "This is a known industry transmission lag, similar to the 6-week cotton→yarn lag "
            "already seeded as a learned_coefficient. Once price data is available, the lag "
            "can be calibrated from actual RRK polyester yarn purchase records."
        ),
        "resolution_path": (
            "ICIS PTA Asia assessment (weekly, USD/MT CFR China main port). "
            "Same subscription as PX above. CTIC also publishes weekly Chinese domestic PTA "
            "spot prices in CNY/MT — cross with fx_rates.usd_cny for USD conversion."
        ),
        "related_coefficient_name": None,
        "status": "open",
    },
    {
        "gap_description": (
            "Polyester PET chip (raw material for polyester yarn spinning) has no live data feed. "
            "The polyester_pet_chips table has 0 rows. PET chip is the direct feedstock for "
            "polyester yarn and is the most operationally relevant price for cost estimation: "
            "it maps directly to the polyester yarn premium over cotton in blended fabrics. "
            "For Classic Fashion programs using CVC (chief value cotton, e.g. 60/40 C/P) "
            "or polyester fleece, PET chip price is the primary cost driver for the synthetic component."
        ),
        "gap_domain": "market",
        "gap_severity": "blocks_reasoning",
        "analogous_knowledge": (
            "The equivalent in the cotton chain is the ICE No.2 futures contract price "
            "materialized in the cotton table. PET chip plays the same role for polyester — "
            "it is the commodity reference price from which spinning mills price their yarn. "
            "Industry rule: polyester yarn price ≈ PET chip × 1.15 + spinning premium "
            "(INR 15–25/kg for 150D POY). This coefficient is in learned_coefficient "
            "as industry_prior and needs calibration from RRK purchase records."
        ),
        "resolution_path": (
            "ICIS Polyester PET Chip China spot (weekly, CNY/MT). Same subscription. "
            "Alternatively, PCI Fibres publishes a free monthly Asian fibre price bulletin "
            "that includes PET chip, polyester staple fibre, and viscose staple fibre — "
            "a useful free starting point before committing to an ICIS subscription."
        ),
        "related_coefficient_name": "polyester_yarn_from_pet_chip_ratio",
        "status": "open",
    },
    {
        "gap_description": (
            "Viscose rayon staple fibre (VSF) has no live data feed. The viscose_rayon table "
            "has 0 rows. Viscose is used in premium blended constructions (viscose/cotton, "
            "viscose/poly) and modal fabrics. Birla Cellulose (Grasim) and Lenzing are the "
            "dominant suppliers to Indian mills. Viscose price is NOT derived from crude — "
            "it is derived from wood pulp (dissolving pulp), a separate commodity chain. "
            "Indian viscose yarn prices are more stable than polyester but subject to "
            "import duty changes and wood pulp supply disruptions."
        ),
        "gap_domain": "market",
        "gap_severity": "degrades_accuracy",
        "analogous_knowledge": (
            "For Tirupur mills, viscose is mostly sourced domestically from Birla VSF "
            "(Nagda, Kharach plants). Birla publishes list prices quarterly that mills "
            "use as reference. The actual transacted price depends on mill volume and "
            "relationship. Blended yarn premiums (viscose 30% blend) are approximately "
            "INR 40–80/kg above equivalent cotton count yarn."
        ),
        "resolution_path": (
            "Option A: PCI Fibres monthly bulletin includes VSF prices (free). "
            "Option B: Track Birla Cellulose quarterly price notifications — "
            "available through industry association (SIMA, CITI) circulars. "
            "Option C: Collect from RRK purchase records for viscose yarn — "
            "if RRK buys viscose-blend yarn, purchase invoices are ground truth."
        ),
        "related_coefficient_name": None,
        "status": "open",
    },
]


def seed(overwrite: bool = False) -> int:
    db = SessionLocal()
    inserted = 0
    skipped = 0
    try:
        for gap_data in GAPS:
            existing = (
                db.query(KnowledgeGap)
                .filter(
                    KnowledgeGap.gap_description.like(
                        gap_data["gap_description"][:80] + "%"
                    )
                )
                .first()
            )
            if existing and not overwrite:
                logger.info(f"Gap already exists (id={existing.gap_id}), skipping.")
                skipped += 1
                continue
            if existing and overwrite:
                for k, v in gap_data.items():
                    setattr(existing, k, v)
                logger.info(f"Updated existing gap id={existing.gap_id}")
            else:
                db.add(KnowledgeGap(**gap_data))
                inserted += 1

        db.commit()
        logger.info(f"Synthetic fiber chain gaps: {inserted} inserted, {skipped} skipped.")
        return inserted
    except Exception as exc:
        logger.critical(f"Gap seed failed: {exc}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed KnowledgeGap records for synthetic fiber chain")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Update existing gap records instead of skipping them.",
    )
    args = parser.parse_args()
    seed(overwrite=args.overwrite)

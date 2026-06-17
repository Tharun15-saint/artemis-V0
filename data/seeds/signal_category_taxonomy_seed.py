"""
Canonical signal taxonomy for the retail intelligence layer — 23 categories.

This is the authoritative registry the FK on retailer_intelligence_extract.canonical_category
enforces, and that the v4.0 earnings-call extraction prompt must emit. Re-run to reseed.

NOTE: an earlier consolidation collapsed this to 17 renamed categories; that was
reverted (the 23-category set with business_segment as a separate dimension is the
authoritative taxonomy). Keep this seed in sync with any taxonomy change.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text

from database.base import SessionLocal, engine

# (category_id, canonical_category, label, description, applies_to_retailer_types, supply_chain_layer)
CANONICAL_TAXONOMY = [
    (1, "apparel_sales_performance", "Apparel Sales Performance",
     "Direct apparel and clothing category revenue signals", "all", "retail_demand"),
    (2, "inventory_positioning", "Inventory Positioning",
     "Retailer inventory levels, days on hand, and positioning signals", "all", "retail_demand"),
    (3, "forward_guidance", "Forward Guidance",
     "Management guidance on future sales, margins, and volumes", "all", "retail_demand"),
    (4, "consumer_demand", "Consumer Demand",
     "Broad consumer spending and demand environment signals", "all", "retail_demand"),
    (5, "margin_pressure", "Margin Pressure",
     "Gross margin, operating margin, and pricing pressure signals", "all", "retail_demand"),
    (6, "channel_mix", "Channel Mix",
     "Digital vs physical, own vs marketplace channel shift signals", "all", "retail_demand"),
    (7, "category_mix_shift", "Category Mix Shift",
     "Shifts between product categories including apparel vs other", "all", "retail_demand"),
    (8, "vendor_supply_chain", "Vendor and Supply Chain",
     "Retailer signals about supplier relationships and sourcing", "all", "supply_chain"),
    (9, "tariff_and_sourcing_geography", "Tariff and Sourcing Geography",
     "Signals about tariff exposure, trade policy, and sourcing shifts", "all", "trade_compliance"),
    (10, "analyst_pressure", "Analyst Pressure",
     "Analyst Q&A pressure signals revealing hidden risks", "all", "retail_demand"),
    (11, "consumer_behavior_language", "Consumer Behavior Language",
     "Language describing consumer value-seeking and trade-down behavior", "all", "retail_demand"),
    (12, "seasonal_sellthrough", "Seasonal Sell-Through",
     "Sell-through rates and seasonal performance signals", "mass_market,department,specialty", "retail_demand"),
    (13, "private_brand_penetration", "Private Brand Penetration",
     "Own-brand vs national brand mix signals", "mass_market,department", "retail_demand"),
    (14, "membership_signals", "Membership and Loyalty Signals",
     "Membership growth, renewal, and loyalty program signals", "warehouse_club,marketplace", "retail_demand"),
    (15, "off_price_buying_signal", "Off-Price Buying Signal",
     "Off-price retailer purchasing activity indicating market oversupply", "off_price", "retail_demand"),
    (16, "ecommerce_penetration", "E-Commerce Penetration",
     "Online channel growth and penetration signals", "all", "retail_demand"),
    (17, "store_expansion", "Store Expansion",
     "Physical store opening, closing, and remodeling signals", "all", "retail_demand"),
    (18, "pricing_and_markdown", "Pricing and Markdown",
     "Promotional intensity, markdown rates, and pricing strategy signals", "all", "retail_demand"),
    (19, "freight_and_logistics", "Freight and Logistics",
     "Retailer comments on freight costs and supply chain logistics", "all", "logistics"),
    (20, "retailer_strategy", "Retailer Strategy and Technology",
     "AI, technology, and strategic initiatives affecting how retailers buy and sell fashion", "all", "retail_demand"),
    (22, "fulfillment_requirements", "Fulfillment Requirements",
     "Speed, in-stock, fill rate, and omnichannel fulfillment standards affecting suppliers", "all", "logistics"),
    (23, "pricing_pressure", "Pricing Pressure",
     "FOB price pressure, markdown intensity, and cost reduction demands from retailers", "all", "commercial"),
    (24, "program_risk", "Program Risk",
     "Signals indicating risk to existing or future apparel programs", "all", "commercial"),
]

CANONICAL_CATEGORY_NAMES = frozenset(row[1] for row in CANONICAL_TAXONOMY)


def reseed() -> None:
    """Replace the taxonomy table contents. FK off so children aren't orphaned mid-swap."""
    now = datetime(2026, 6, 16)
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        conn.execute(text("DELETE FROM signal_category_taxonomy"))
        for cid, cat, label, desc, rtypes, layer in CANONICAL_TAXONOMY:
            conn.execute(
                text(
                    "INSERT INTO signal_category_taxonomy "
                    "(category_id, canonical_category, category_label, category_description, "
                    "applies_to_retailer_types, supply_chain_layer, created_at) "
                    "VALUES (:i, :c, :l, :d, :rt, :sl, :ts)"
                ),
                {"i": cid, "c": cat, "l": label, "d": desc, "rt": rtypes, "sl": layer, "ts": now},
            )
        conn.execute(text("PRAGMA foreign_keys=ON"))
    print(f"reseeded signal_category_taxonomy with {len(CANONICAL_TAXONOMY)} canonical categories")


def verify() -> None:
    from database.models.retail import RetailerIntelligenceExtract as RIE

    db = SessionLocal()
    try:
        used = {r[0] for r in db.query(RIE.signal_category).filter(RIE.is_latest.is_(True)).distinct()}
        print("in-use categories NOT in taxonomy:", used - CANONICAL_CATEGORY_NAMES or "none")
    finally:
        db.close()


if __name__ == "__main__":
    reseed()
    verify()

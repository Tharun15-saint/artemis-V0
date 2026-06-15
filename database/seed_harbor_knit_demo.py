"""Demo seed: Harbor Knit Co — 8 years Bangladesh sourcing via Tex-Knit Gazipur."""

from datetime import date, timedelta
from decimal import Decimal

from database.database import SessionLocal
from database.models import (
    CompanyFactoryRelationship,
    CompanyProfile,
    PurchaseOrderHistory,
)

FACTORY = "Tex-Knit Gazipur"
CORRIDOR = "Bangladesh"
BELOW_MARKET_PCT = Decimal("-3.1")  # negative = below market
OTD_RATE = Decimal("0.91")

# (po_ref, season, colour, qty, market_fob, on_time)
POS = [
    ("HK-2018-0142", "FW18", "navy", 32000, "12.40", True),
    ("HK-2019-0088", "SS19", "white", 45000, "11.85", True),
    ("HK-2019-0311", "FW19", "grey", 38000, "12.15", True),
    ("HK-2020-0055", "SS20", "navy", 52000, "12.65", True),
    ("HK-2020-0290", "FW20", "white", 35000, "12.05", True),
    ("HK-2021-0117", "SS21", "grey", 41000, "12.35", False),  # 7 days late → 91% OTD
    ("HK-2021-0344", "FW21", "navy", 48000, "12.80", True),
    ("HK-2022-0063", "SS22", "white", 55000, "12.20", True),
    ("HK-2023-0198", "FW22", "grey", 33000, "12.55", True),
    ("HK-2023-0401", "SS23", "navy", 60000, "13.10", True),
    ("HK-2024-0125", "FW24", "white", 42000, "12.90", True),
    ("HK-2025-0036", "SS25", "grey", 47000, "13.25", True),
]


def _actual_fob(market: Decimal) -> Decimal:
    return (market * (Decimal("1") + BELOW_MARKET_PCT / Decimal("100"))).quantize(
        Decimal("0.0001")
    )


def seed_harbor_knit_demo() -> int:
    db = SessionLocal()
    try:
        existing = (
            db.query(CompanyProfile).filter_by(company_name="Harbor Knit Co").first()
        )
        if existing:
            print(f"Harbor Knit Co already seeded (id={existing.id}) — skipping.")
            return existing.id

        company = CompanyProfile(
            company_name="Harbor Knit Co",
            company_type="importer",
            primary_corridors="Bangladesh",
            primary_product_categories="knit_basics",
            typical_quantity_range="30k-60k dozen per program",
            typical_fob_range_low=Decimal("11.50"),
            typical_fob_range_high=Decimal("14.00"),
            primary_retail_relationships="Target, TJX, Burlington",
            annual_volume_estimate_dozens=Decimal("480000"),
            risk_profile="moderate",
            intelligence_confidence=Decimal("0.82"),
            onboarded_at=date(2018, 3, 1),
            last_intelligence_update=date.today(),
            notes=(
                "8-year Bangladesh knit basics importer. Primary factory Tex-Knit Gazipur. "
                "Consistent sub-market pricing on volume programs. TJX-category retailer mix."
            ),
        )
        db.add(company)
        db.flush()

        rel = CompanyFactoryRelationship(
            company_id=company.id,
            factory_name=FACTORY,
            factory_location="Gazipur, Bangladesh",
            factory_corridor=CORRIDOR,
            relationship_years=8,
            programs_completed=len(POS),
            avg_otd_rate=OTD_RATE,
            avg_quality_acceptance_rate=Decimal("0.96"),
            avg_price_vs_market_pct=BELOW_MARKET_PCT,
            typical_payment_terms="LC at sight, 30-day post-shipment on repeat programs",
            typical_lead_time_weeks=14,
            known_specialisations="single jersey basics, fleece hoodies, reactive dye darks",
            known_limitations="Q3 capacity tight above 78% utilisation; navy dyeing runs high",
            last_order_date=date(2025, 4, 15),
            notes=(
                "Harbor Knit receives ~3.1% below market from this factory on Bangladesh "
                "programs — volume and 8-year relationship. Monitor Q3 OTD."
            ),
        )
        db.add(rel)

        base_delivery = date(2018, 9, 1)
        for i, (po_ref, season, colour, qty, market_str, on_time) in enumerate(POS):
            market = Decimal(market_str)
            actual = _actual_fob(market)
            committed = base_delivery + timedelta(days=120 * i)
            actual_delivery = committed if on_time else committed + timedelta(days=7)
            days_late = (actual_delivery - committed).days
            tier = "dark" if colour == "navy" else ("light" if colour == "white" else "medium")

            db.add(
                PurchaseOrderHistory(
                    company_id=company.id,
                    po_reference=po_ref,
                    factory_name=FACTORY,
                    corridor=CORRIDOR,
                    product_category="knit_basics",
                    fibre_content="100% cotton",
                    construction="single_jersey",
                    gsm=Decimal("180"),
                    colour_description=colour,
                    quantity_dozens=Decimal(str(qty)),
                    quoted_fob_per_dozen=market,
                    actual_fob_per_dozen=actual,
                    target_retail_price=Decimal("14.99"),
                    retailer_name="TJX",
                    committed_delivery_date=committed,
                    actual_delivery_date=actual_delivery,
                    days_late=days_late,
                    quality_issues=False,
                    cost_variance_pct=BELOW_MARKET_PCT,
                    factors_that_caused_variance='{"relationship_volume_discount": -3.1}',
                    season=season,
                    source="erp_import",
                )
            )
            _ = tier  # reserved for future spec-matching filters

        db.commit()
        print(
            f"Seeded Harbor Knit Co (id={company.id}): "
            f"1 factory relationship, {len(POS)} POs via {FACTORY}."
        )
        return company.id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_harbor_knit_demo()

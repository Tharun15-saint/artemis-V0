"""
Artemis seed data — reference tables that rarely change.
Run once after initial migration, then only when values need updating.
"""

from database.base import SessionLocal
from database.models.costs import FactoryFinancingCost, LabourCostByCountry, EnergyCost, TrimCost
from database.models.trade import HsCodes
from decimal import Decimal


def seed_all():
    db = SessionLocal()
    try:
        seed_financing_costs(db)
        seed_labour_costs(db)
        seed_energy_costs(db)
        seed_trim_costs(db)
        seed_hs_codes(db)
        db.commit()
        print("All reference data seeded successfully.")
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def seed_financing_costs(db):
    if db.query(FactoryFinancingCost).filter(FactoryFinancingCost.is_latest.is_(True)).count() > 0:
        print("Financing costs already seeded — skipping.")
        return
    db.add(FactoryFinancingCost(
        india_rate_pct=Decimal("11.00"),
        bangladesh_rate_pct=Decimal("13.00"),
        vietnam_rate_pct=Decimal("9.00"),
        china_rate_pct=Decimal("6.50"),
        turkey_rate_pct=Decimal("27.00"),
        morocco_rate_pct=Decimal("10.00"),
        cambodia_rate_pct=Decimal("12.00"),
        pakistan_rate_pct=Decimal("16.00"),
        source="IMF / World Bank / Central Banks",
        refresh="quarterly",
    ))
    print("Factory financing costs seeded.")


def seed_labour_costs(db):
    if db.query(LabourCostByCountry).filter(LabourCostByCountry.is_latest.is_(True)).count() > 0:
        print("Labour costs already seeded — skipping.")
        return
    db.add(LabourCostByCountry(
        india_tirupur=Decimal("0.85"),
        india_coimbatore=Decimal("0.78"),
        india_bangalore=Decimal("0.92"),
        bangladesh_dhaka=Decimal("0.40"),
        bangladesh_gazipur=Decimal("0.38"),
        bangladesh_chittagong=Decimal("0.34"),
        vietnam_hcmc=Decimal("0.75"),
        vietnam_hanoi=Decimal("0.72"),
        china_guangdong=Decimal("3.50"),
        china_zhejiang=Decimal("3.20"),
        turkey_istanbul=Decimal("4.20"),
        morocco_casablanca=Decimal("1.85"),
        cambodia_national=Decimal("0.45"),
        pakistan_national=Decimal("0.41"),
        source="ILO ILOSTAT",
        refresh="monthly",
    ))
    print("Labour costs seeded.")


def seed_energy_costs(db):
    if db.query(EnergyCost).filter(EnergyCost.is_latest.is_(True)).count() > 0:
        print("Energy costs already seeded — skipping.")
        return
    db.add(EnergyCost(
        india_kwh_usd=Decimal("0.088"),
        bangladesh_kwh_usd=Decimal("0.072"),
        vietnam_kwh_usd=Decimal("0.078"),
        china_kwh_usd=Decimal("0.083"),
        turkey_kwh_usd=Decimal("0.112"),
        morocco_kwh_usd=Decimal("0.094"),
        cambodia_kwh_usd=Decimal("0.168"),
        pakistan_kwh_usd=Decimal("0.095"),
        update_frequency="quarterly",
    ))
    print("Energy costs seeded.")


def seed_trim_costs(db):
    if db.query(TrimCost).count() > 0:
        print("Trim costs already seeded — skipping.")
        return
    db.add(TrimCost(
        product_type="basic_knit_tee",
        labels_per_doz=Decimal("0.18"),
        buttons_zippers_doz=Decimal("0.00"),
        polybag_packaging_doz=Decimal("0.48"),
        total_trim_cost_doz=Decimal("0.85"),
    ))
    db.add(TrimCost(
        product_type="hoodie_fleece",
        labels_per_doz=Decimal("0.22"),
        buttons_zippers_doz=Decimal("0.35"),
        polybag_packaging_doz=Decimal("0.72"),
        total_trim_cost_doz=Decimal("1.42"),
    ))
    print("Trim costs seeded.")


def seed_hs_codes(db):
    if db.query(HsCodes).count() > 0:
        print("HS codes already seeded — skipping.")
        return
    codes = [
        ("6109.10", "Cotton knit T-shirts and vests"),
        ("6110.20", "Cotton sweaters, pullovers, sweatshirts, hoodies"),
        ("6109.90", "Synthetic knit T-shirts"),
        ("6111",    "Infant knit garments"),
    ]
    for code, desc in codes:
        db.add(HsCodes(code=code, description=desc))
    print("HS codes seeded.")


if __name__ == "__main__":
    seed_all()

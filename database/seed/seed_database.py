from datetime import date
from decimal import Decimal

from database.database import SessionLocal
from database.models import (
    FactoryFinancingCost,
    GovernmentExportIncentive,
    MarineInsurance,
    ProductSpecification,
    ShippingLaneRisk,
    UsImportDutyRate,
)


def seed_database() -> None:
    today = date.today()
    db = SessionLocal()
    try:
        if db.query(ProductSpecification).filter(ProductSpecification.spec_id == 1).first():
            return

        db.add(
            ProductSpecification(
                spec_id=1,
                product_name="180gsm Cotton Jersey T-Shirt",
                hs_code="6109.10",
                fibre_content="100% Cotton",
                construction="Single Jersey",
                weight_gsm=Decimal("180.0"),
                prototype_corridor_1="Bangladesh",
                prototype_corridor_2="India",
                prototype_corridor_3="Vietnam",
                typical_fob_range_low=Decimal("8.50"),
                typical_fob_range_high=Decimal("13.00"),
            )
        )

        db.add(
            UsImportDutyRate(
                ntr_rate_6109_10_pct=Decimal("0.1590"),
                ntr_rate_6110_20_pct=Decimal("0.1600"),
                ntr_rate_6109_90_pct=Decimal("0.3200"),
                ntr_rate_6111_pct=Decimal("0.0850"),
                section_301_china_6109_10_pct=Decimal("0.3990"),
                section_301_china_6110_20_pct=Decimal("0.3990"),
                section_301_china_6109_90_pct=Decimal("0.6490"),
                gsp_status_by_country=(
                    "Bangladesh: no (graduated). India: eligible for select codes."
                ),
                effective_date=today,
                source="USITC HTS Database",
            )
        )

        db.add(
            FactoryFinancingCost(
                india_rate_pct=Decimal("0.1100"),
                bangladesh_rate_pct=Decimal("0.1300"),
                vietnam_rate_pct=Decimal("0.0900"),
                china_rate_pct=Decimal("0.0650"),
                turkey_rate_pct=Decimal("0.3500"),
                morocco_rate_pct=Decimal("0.0600"),
                cambodia_rate_pct=Decimal("0.1000"),
                pakistan_rate_pct=Decimal("0.1800"),
                effective_date=today,
                source="IMF + central bank policy rates + spread",
                update_frequency="quarterly",
            )
        )

        incentives = [
            GovernmentExportIncentive(
                country="India",
                program_name="RoDTEP",
                program_type="duty_drawback",
                applicable_hs_codes="6109.10,6110.20",
                benefit_rate_pct_fob=Decimal("0.0150"),
                benefit_per_dozen_usd_estimate=Decimal("0.18"),
                benefit_recipient="factory_retained",
                is_active=True,
                effective_date=today,
                source="CBIC India cbic.gov.in",
                last_verified=today,
            ),
            GovernmentExportIncentive(
                country="Bangladesh",
                program_name="Export Cash Incentive",
                program_type="cash_incentive",
                applicable_hs_codes="6109.10,6110.20",
                benefit_rate_pct_fob=Decimal("0.0150"),
                benefit_per_dozen_usd_estimate=Decimal("0.18"),
                benefit_recipient="factory_retained",
                is_active=True,
                effective_date=today,
                source="Bangladesh Bank Export Incentive Circular",
                last_verified=today,
            ),
            GovernmentExportIncentive(
                country="China",
                program_name="Export VAT Rebate",
                program_type="vat_rebate",
                applicable_hs_codes="6109.10,6110.20",
                benefit_rate_pct_fob=Decimal("0.1300"),
                benefit_per_dozen_usd_estimate=Decimal("1.30"),
                benefit_recipient="factory_retained",
                is_active=True,
                effective_date=today,
                source="China State Taxation Administration",
                last_verified=today,
            ),
            GovernmentExportIncentive(
                country="Pakistan",
                program_name="DLTL Scheme",
                program_type="duty_drawback",
                applicable_hs_codes="6109.10,6110.20",
                benefit_rate_pct_fob=Decimal("0.0700"),
                benefit_per_dozen_usd_estimate=Decimal("0.75"),
                benefit_recipient="factory_retained",
                is_active=True,
                effective_date=today,
                source="Federal Board of Revenue Pakistan",
                last_verified=today,
            ),
        ]
        db.add_all(incentives)

        corridors_standard = [
            "chittagong_la",
            "chennai_la",
            "hcmc_la",
            "shanghai_la",
            "casablanca_rotterdam",
        ]
        for corridor in corridors_standard:
            db.add(
                MarineInsurance(
                    corridor=corridor,
                    all_risk_rate_pct_cif=Decimal("0.0045"),
                    war_risk_rate_pct_cif=Decimal("0.0000"),
                    total_effective_rate_pct_cif=Decimal("0.0045"),
                    route_risk_level="standard",
                    active_war_risk_surcharge=False,
                    as_of_date=today,
                    source="Standard marine market rate",
                )
            )

        db.add(
            MarineInsurance(
                corridor="istanbul_rotterdam",
                all_risk_rate_pct_cif=Decimal("0.0045"),
                war_risk_rate_pct_cif=Decimal("0.0150"),
                total_effective_rate_pct_cif=Decimal("0.0195"),
                route_risk_level="elevated",
                active_war_risk_surcharge=True,
                as_of_date=today,
                source="Standard marine market rate",
            )
        )

        db.add_all(
            [
                ShippingLaneRisk(
                    lane_name="Red Sea / Suez Canal",
                    corridors_affected="istanbul_rotterdam",
                    current_risk_level="elevated",
                    is_currently_disrupted=True,
                    alternative_route="Cape of Good Hope",
                    additional_transit_days=14,
                    additional_cost_per_40ft_usd=Decimal("800.0"),
                    as_of_date=today,
                    source="BIMCO / Lloyd's List",
                ),
                ShippingLaneRisk(
                    lane_name="Panama Canal",
                    corridors_affected="chittagong_la,chennai_la,hcmc_la,shanghai_la",
                    current_risk_level="normal",
                    is_currently_disrupted=False,
                    as_of_date=today,
                    source="Panama Canal Authority",
                ),
                ShippingLaneRisk(
                    lane_name="Strait of Malacca",
                    corridors_affected="chittagong_la,chennai_la,hcmc_la,shanghai_la",
                    current_risk_level="normal",
                    is_currently_disrupted=False,
                    as_of_date=today,
                    source="IMB Piracy Reporting Centre",
                ),
            ]
        )

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    from database.database import init_db

    init_db()
    seed_database()
    print("Database seeded successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

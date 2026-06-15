from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.database import Base

# ---------------------------------------------------------------------------
# Layer 1 — Raw Materials (7 entities)
# ---------------------------------------------------------------------------


class Cotton(Base):
    __tablename__ = "cotton"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    origin_country: Mapped[str] = mapped_column(String, nullable=False)
    grade: Mapped[str] = mapped_column(String, nullable=False)
    staple_length: Mapped[str] = mapped_column(String, nullable=False)
    spot_price: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    ice_futures_near: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    ice_futures_3m: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    ice_futures_6m: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    usda_price_forecast_next_month: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    crop_year: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    is_curve_real: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class CottonSupplyDemand(Base):
    __tablename__ = "cotton_supply_demand"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_month: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    report_type: Mapped[str] = mapped_column(String, nullable=False)
    forecast_provider: Mapped[str] = mapped_column(String, nullable=False)
    us_planted_area_thousand_acres: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    us_harvested_area_thousand_acres: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    us_production_million_bales: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    us_yield_lbs_per_acre: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    india_production_million_bales: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    china_production_million_bales: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    pakistan_production_million_bales: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    australia_production_million_bales: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    west_africa_production_million_bales: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    brazil_production_million_bales: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    world_production_million_bales: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    world_mill_use_million_bales: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    world_exports_million_bales: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    world_ending_stocks_million_bales: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    world_stocks_to_use_ratio_pct: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    usda_season_avg_price_cents_per_lb: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    cotlook_a_index_cents_per_lb: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    us_pct_planted: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    us_crop_condition_good_excellent_pct: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    source: Mapped[str] = mapped_column(String, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class CrudeOil(Base):
    __tablename__ = "crude_oil"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brent_spot: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    wti_spot: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class Paraxylene(Base):
    __tablename__ = "paraxylene"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asian_spot_price: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class PtaPurifiedTerephthalicAcid(Base):
    __tablename__ = "pta_purified_terephthalic_acid"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chinese_domestic_spot: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    asian_export_price: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class PolyesterPetChips(Base):
    __tablename__ = "polyester_pet_chips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chinese_spot: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    asian_spot: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class ViscoseRayonStaple(Base):
    __tablename__ = "viscose_rayon_staple"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asian_spot_price: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Layer 2 — Intermediate Products (2 entities)
# ---------------------------------------------------------------------------


class Yarn(Base):
    __tablename__ = "yarn"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fibre_type: Mapped[str] = mapped_column(String, nullable=False)
    count: Mapped[str] = mapped_column(String, nullable=False)
    spinning_method: Mapped[str] = mapped_column(String, nullable=False)
    grade: Mapped[str] = mapped_column(String, nullable=False)
    origin_city: Mapped[str] = mapped_column(String, nullable=False)
    price_per_kg: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    availability_signal: Mapped[str] = mapped_column(String, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class KnitFabric(Base):
    __tablename__ = "knit_fabric"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    construction: Mapped[str] = mapped_column(String, nullable=False)
    weight_gsm: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    fibre_content: Mapped[str] = mapped_column(String, nullable=False)
    finish: Mapped[str] = mapped_column(String, nullable=False)
    origin_country: Mapped[str] = mapped_column(String, nullable=False)
    price_per_kg: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Layer 3 — Manufacturing Nodes (5 entities)
# ---------------------------------------------------------------------------


class SpinningMill(Base):
    __tablename__ = "spinning_mill"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    location_country: Mapped[str] = mapped_column(String, nullable=False)
    location_city: Mapped[str] = mapped_column(String, nullable=False)
    location_district: Mapped[str] = mapped_column(String, nullable=False)
    capacity_tons_month: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    utilisation_percent: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    certifications: Mapped[str] = mapped_column(String, nullable=False)
    lead_time_weeks: Mapped[int] = mapped_column(Integer, nullable=False)
    financing_rate: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class KnittingFabricMill(Base):
    __tablename__ = "knitting_fabric_mill"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    location: Mapped[str] = mapped_column(String, nullable=False)
    capacity_tons_month: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    utilisation_percent: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    machine_types: Mapped[str] = mapped_column(String, nullable=False)
    certifications: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    lead_time_weeks: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class DyeingFinishingUnit(Base):
    __tablename__ = "dyeing_finishing_unit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    location: Mapped[str] = mapped_column(String, nullable=False)
    capacity_tons_month: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    chemical_cost_structure: Mapped[str] = mapped_column(String, nullable=False)
    energy_intensity: Mapped[str] = mapped_column(String, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class CmtFactory(Base):
    __tablename__ = "cmt_factory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    location: Mapped[str] = mapped_column(String, nullable=False)
    product_specialisation: Mapped[str] = mapped_column(String, nullable=False)
    capacity_pieces_month: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    utilisation_percent: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    order_book_depth: Mapped[str] = mapped_column(String, nullable=False)
    certifications: Mapped[str] = mapped_column(String, nullable=False)
    on_time_delivery_rate: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    lead_time_weeks: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class PrintingUnit(Base):
    __tablename__ = "printing_unit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    location_country: Mapped[str] = mapped_column(String, nullable=False)
    location_city: Mapped[str] = mapped_column(String, nullable=False)
    print_methods: Mapped[str] = mapped_column(String, nullable=False)
    capacity_pieces_month: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    utilisation_percent: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    screen_print_cost_per_colour_per_dozen: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    digital_print_cost_per_dozen: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    setup_cost_per_design_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    minimum_order_quantity_dozens: Mapped[int] = mapped_column(Integer, nullable=False)
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    certifications: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    on_time_delivery_rate: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    ink_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Layer 4 — Production Cost Components (4 entities)
# ---------------------------------------------------------------------------


class LabourCostByCountry(Base):
    __tablename__ = "labour_cost_by_country"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    india_tirupur_usd_per_hr: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    india_coimbatore_usd_per_hr: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    india_bangalore_usd_per_hr: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    bangladesh_dhaka_usd_per_hr: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    bangladesh_gazipur_usd_per_hr: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    bangladesh_chittagong_usd_per_hr: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    vietnam_hcmc_usd_per_hr: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    vietnam_hanoi_usd_per_hr: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    china_guangdong_usd_per_hr: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    china_zhejiang_usd_per_hr: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    turkey_istanbul_usd_per_hr: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    morocco_casablanca_usd_per_hr: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    cambodia_national_usd_per_hr: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    pakistan_national_usd_per_hr: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    update_frequency: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class EnergyAtFactoryLevel(Base):
    __tablename__ = "energy_at_factory_level"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    india_electricity_usd_per_kwh: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    bangladesh_electricity_usd_per_kwh: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    vietnam_electricity_usd_per_kwh: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    china_electricity_usd_per_kwh: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    turkey_electricity_usd_per_kwh: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    morocco_electricity_usd_per_kwh: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    cambodia_electricity_usd_per_kwh: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    pakistan_electricity_usd_per_kwh: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    highest_impact_processes: Mapped[str] = mapped_column(String, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    update_frequency: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class FactoryFinancingCost(Base):
    __tablename__ = "factory_financing_cost"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    india_rate_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    bangladesh_rate_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    vietnam_rate_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    china_rate_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    turkey_rate_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    morocco_rate_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    cambodia_rate_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    pakistan_rate_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    update_frequency: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class ImporterWorkingCapitalCost(Base):
    __tablename__ = "importer_working_capital_cost"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    annual_borrowing_rate: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    typical_inventory_days: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_of_carry_per_dozen: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    comparison_note: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    update_frequency: Mapped[str] = mapped_column(String, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Layer 5 — Logistics and Freight (4 entities)
# ---------------------------------------------------------------------------


class OceanFreight(Base):
    __tablename__ = "ocean_freight"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chittagong_la_rate_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    chennai_la_rate_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    hcmc_la_rate_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    shanghai_la_rate_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    istanbul_rotterdam_rate_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    casablanca_rotterdam_rate_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    rate_per_40ft_container_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    rate_per_teu: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    transit_days: Mapped[int] = mapped_column(Integer, nullable=False)
    vessel_availability: Mapped[str] = mapped_column(String, nullable=False)
    port_congestion_index: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    update_frequency: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class LocalInlandFreight(Base):
    __tablename__ = "local_inland_freight"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    factory_to_origin_port_cost_by_country: Mapped[str] = mapped_column(String, nullable=False)
    update_frequency: Mapped[str] = mapped_column(String, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class AirFreight(Base):
    __tablename__ = "air_freight"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rate_per_kg: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    demand_signal_driven_replenishment: Mapped[str] = mapped_column(String, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class MarineInsurance(Base):
    __tablename__ = "marine_insurance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    corridor: Mapped[str] = mapped_column(String, nullable=False, index=True)
    all_risk_rate_pct_cif: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    war_risk_rate_pct_cif: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    total_effective_rate_pct_cif: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    route_risk_level: Mapped[str] = mapped_column(String, nullable=False)
    active_war_risk_surcharge: Mapped[bool] = mapped_column(Boolean, nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Layer 5b — Geopolitical Risk (2 entities)
# ---------------------------------------------------------------------------


class GeopoliticalRiskEvent(Base):
    __tablename__ = "geopolitical_risk_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_name: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    affected_region: Mapped[str] = mapped_column(String, nullable=False)
    affected_corridors: Mapped[str] = mapped_column(String, nullable=False)
    freight_impact_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    lead_time_impact_days: Mapped[int] = mapped_column(Integer, nullable=False)
    production_disruption_risk: Mapped[str] = mapped_column(String, nullable=False)
    risk_level: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_resolution_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    actual_resolution_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    analyst_note: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    shipping_lane_risks: Mapped[list["ShippingLaneRisk"]] = relationship(
        back_populates="current_event"
    )


class ShippingLaneRisk(Base):
    __tablename__ = "shipping_lane_risk"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lane_name: Mapped[str] = mapped_column(String, nullable=False)
    corridors_affected: Mapped[str] = mapped_column(String, nullable=False)
    current_risk_level: Mapped[str] = mapped_column(String, nullable=False)
    is_currently_disrupted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    alternative_route: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    additional_transit_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    additional_cost_per_40ft_usd: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    current_event_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("geopolitical_risk_event.id"), nullable=True
    )
    disruption_since: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    current_event: Mapped[Optional["GeopoliticalRiskEvent"]] = relationship(
        back_populates="shipping_lane_risks"
    )


# ---------------------------------------------------------------------------
# Layer 6 — Trade, Customs, and Regulatory (7 entities)
# ---------------------------------------------------------------------------


class FreeTradeAgreement(Base):
    __tablename__ = "free_trade_agreement"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cafta_dr: Mapped[str] = mapped_column(String, nullable=False)
    us_morocco_fta: Mapped[str] = mapped_column(String, nullable=False)
    us_jordan_fta: Mapped[str] = mapped_column(String, nullable=False)
    yarn_forward_rules: Mapped[str] = mapped_column(String, nullable=False)
    cafta_dr_duty_reduction_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    morocco_fta_duty_reduction_pct: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    jordan_fta_duty_reduction_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    qualifying_criteria_summary: Mapped[str] = mapped_column(String, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class HsCode(Base):
    __tablename__ = "hs_code"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hs_6109_10: Mapped[str] = mapped_column(String, nullable=False)
    hs_6110_20: Mapped[str] = mapped_column(String, nullable=False)
    hs_6109_90: Mapped[str] = mapped_column(String, nullable=False)
    hs_6111: Mapped[str] = mapped_column(String, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class UsImportDutyRate(Base):
    __tablename__ = "us_import_duty_rate"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ntr_rate_6109_10_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    ntr_rate_6110_20_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    ntr_rate_6109_90_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    ntr_rate_6111_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    section_301_china_6109_10_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    section_301_china_6110_20_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    section_301_china_6109_90_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    gsp_status_by_country: Mapped[str] = mapped_column(String, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class Uflpa(Base):
    __tablename__ = "uflpa"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    xinjiang_rebuttable_presumption: Mapped[str] = mapped_column(String, nullable=False)
    border_block_risk: Mapped[str] = mapped_column(String, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class EuCsddd(Base):
    __tablename__ = "eu_csddd"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    supply_chain_mapping_requirement: Mapped[str] = mapped_column(String, nullable=False)
    verification_requirement: Mapped[str] = mapped_column(String, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class DeMinimisRule(Base):
    __tablename__ = "de_minimis_rule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    us_800_threshold: Mapped[str] = mapped_column(String, nullable=False)
    duty_free_entry: Mapped[str] = mapped_column(String, nullable=False)
    regulatory_pressure: Mapped[str] = mapped_column(String, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class GovernmentExportIncentive(Base):
    __tablename__ = "government_export_incentive"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    country: Mapped[str] = mapped_column(String, nullable=False)
    program_name: Mapped[str] = mapped_column(String, nullable=False)
    program_type: Mapped[str] = mapped_column(String, nullable=False)
    applicable_hs_codes: Mapped[str] = mapped_column(String, nullable=False)
    benefit_rate_pct_fob: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    benefit_per_dozen_usd_estimate: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    benefit_recipient: Mapped[str] = mapped_column(String, nullable=False)
    eligibility_criteria: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    processing_time_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    annual_cap_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    expiry_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    last_verified: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Layer 7 — Import and Export Trade Flow Data (3 entities)
# ---------------------------------------------------------------------------


class ShipperOriginFactory(Base):
    __tablename__ = "shipper_origin_factory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    factory_name: Mapped[str] = mapped_column(String, nullable=False)
    country: Mapped[str] = mapped_column(String, nullable=False)
    city: Mapped[str] = mapped_column(String, nullable=False)
    hs_codes_shipped: Mapped[str] = mapped_column(String, nullable=False)
    volume_by_month: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    active_buyer_relationships: Mapped[int] = mapped_column(Integer, nullable=False)
    yoy_volume_trend: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class ConsigneeUsImporter(Base):
    __tablename__ = "consignee_us_importer"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_name: Mapped[str] = mapped_column(String, nullable=False)
    sourcing_country_mix: Mapped[str] = mapped_column(String, nullable=False)
    product_categories: Mapped[str] = mapped_column(String, nullable=False)
    monthly_volume: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    yoy_origin_shift: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    new_factory_relationships: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class TradeFlowDerivedSignal(Base):
    __tablename__ = "trade_flow_derived_signal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_share_by_origin_country_mom: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    competitor_sourcing_shifts: Mapped[str] = mapped_column(String, nullable=False)
    new_factory_entrants: Mapped[int] = mapped_column(Integer, nullable=False)
    seasonal_patterns: Mapped[str] = mapped_column(String, nullable=False)
    pricing_inference: Mapped[str] = mapped_column(String, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Layer 8 — Currency and Financial Inputs (2 entities)
# ---------------------------------------------------------------------------


class ForeignExchangeRate(Base):
    __tablename__ = "foreign_exchange_rate"
    __table_args__ = (
        UniqueConstraint("effective_date", name="uq_fx_effective_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    usd_inr: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    usd_bdt: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    usd_vnd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    usd_cny: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    usd_try: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    usd_pkr: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)  # Pakistani Rupee
    update_frequency: Mapped[str] = mapped_column(String, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    data_source_quality: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class CommodityFuturesCurve(Base):
    __tablename__ = "commodity_futures_curve"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ice_cotton_2_spot: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    ice_cotton_2_3m: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    ice_cotton_2_6m: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    ice_cotton_2_9m: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    ice_cotton_2_12m: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    ocean_freight_ffa: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_curve_real: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Layer 9 — Retailer and Demand Signals (4 entities)
# ---------------------------------------------------------------------------


class MajorUsRetailer(Base):
    __tablename__ = "major_us_retailer"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    retailer_name: Mapped[str] = mapped_column(String, nullable=False)
    store_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_sales: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    apparel_revenue: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    gross_margin: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    inventory_turnover: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    forward_guidance: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class DemandSignalDerived(Base):
    __tablename__ = "demand_signal_derived"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_expansion: Mapped[str] = mapped_column(String, nullable=False)
    inventory_turnover_trend: Mapped[str] = mapped_column(String, nullable=False)
    gross_margin_pressure: Mapped[str] = mapped_column(String, nullable=False)
    inventory_build_risk: Mapped[str] = mapped_column(String, nullable=False)
    order_cancellation_risk: Mapped[str] = mapped_column(String, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class SeasonalDemandPattern(Base):
    __tablename__ = "seasonal_demand_pattern"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season_summer: Mapped[str] = mapped_column(String, nullable=False)
    season_winter: Mapped[str] = mapped_column(String, nullable=False)
    commit_windows: Mapped[str] = mapped_column(String, nullable=False)
    delivery_windows: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class RetailerGrowthDemandForecast(Base):
    __tablename__ = "retailer_growth_demand_forecast"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    retailer_name: Mapped[str] = mapped_column(String, nullable=False)
    store_count_trend: Mapped[str] = mapped_column(String, nullable=False)
    buying_volume_signal: Mapped[str] = mapped_column(String, nullable=False)
    unit_growth_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    category_focus: Mapped[str] = mapped_column(String, nullable=False)
    confidence_score: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Intelligence and Output Layer (13 entities)
# ---------------------------------------------------------------------------


class ProductSpecification(Base):
    __tablename__ = "product_specification"
    __table_args__ = (UniqueConstraint("spec_id", name="uq_product_specification_spec_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    spec_id: Mapped[int] = mapped_column(Integer, nullable=False)
    product_name: Mapped[str] = mapped_column(String, nullable=False)
    hs_code: Mapped[str] = mapped_column(String, nullable=False)
    fibre_content: Mapped[str] = mapped_column(String, nullable=False)
    construction: Mapped[str] = mapped_column(String, nullable=False)
    weight_gsm: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    prototype_corridor_1: Mapped[str] = mapped_column(String, nullable=False)
    prototype_corridor_2: Mapped[str] = mapped_column(String, nullable=False)
    prototype_corridor_3: Mapped[str] = mapped_column(String, nullable=False)
    typical_fob_range_low: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    typical_fob_range_high: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class TrimCost(Base):
    __tablename__ = "trim_cost"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_type: Mapped[str] = mapped_column(String, nullable=False)
    labels_per_dozen: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    buttons_zippers_per_dozen: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    polybag_packaging_per_dozen: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    total_trim_cost_per_dozen: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    update_frequency: Mapped[str] = mapped_column(String, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class FobPrice(Base):
    __tablename__ = "fob_price"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    spec_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("product_specification.id"), nullable=False, index=True
    )
    corridor: Mapped[str] = mapped_column(String, nullable=False, index=True)
    fabric_cost_per_dozen: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    cmt_cost_per_dozen: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    trim_cost_per_dozen: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    factory_overhead_per_dozen: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    factory_financing_cost_per_dozen: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    government_incentive_credit_per_dozen: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    fob_price_per_dozen: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    calculation_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    product_specification: Mapped["ProductSpecification"] = relationship()


class CurrentMarketLandedCost(Base):
    __tablename__ = "current_market_landed_cost"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_spec_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("product_specification.id"), nullable=False, index=True
    )
    corridor: Mapped[str] = mapped_column(String, nullable=False, index=True)
    landed_cost_per_dozen: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    data_quality_score: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    product_specification: Mapped["ProductSpecification"] = relationship()


class NinetyDayForwardLandedCost(Base):
    __tablename__ = "ninety_day_forward_landed_cost"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    p10: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    p50: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    p90: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    corridor: Mapped[str] = mapped_column(String, nullable=False, index=True)
    product_spec_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("product_specification.id"), nullable=False, index=True
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    dominant_risk_factor: Mapped[str] = mapped_column(String, nullable=False)
    model_version: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    product_specification: Mapped["ProductSpecification"] = relationship()


class HedgingOpportunity(Base):
    __tablename__ = "hedging_opportunity"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    commodity: Mapped[str] = mapped_column(String, nullable=False)
    tenor_months: Mapped[int] = mapped_column(Integer, nullable=False)
    recommended_action: Mapped[str] = mapped_column(String, nullable=False)
    current_spot_price: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    futures_price: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    basis: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    potential_saving_per_dozen: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    risk_if_unhedged_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    confidence_level: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class CommodityRiskInOpenPrograms(Base):
    __tablename__ = "commodity_risk_open_programs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    program_id: Mapped[str] = mapped_column(String, nullable=False)
    corridor: Mapped[str] = mapped_column(String, nullable=False, index=True)
    product_spec_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("product_specification.id"), nullable=False, index=True
    )
    commodity_exposure_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    cotton_sensitivity_10pct_move: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    freight_sensitivity_10pct_move: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    fx_sensitivity_5pct_move: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    total_risk_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    risk_rating: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    product_specification: Mapped["ProductSpecification"] = relationship()


class MostCostEffectiveCorridor(Base):
    __tablename__ = "most_cost_effective_corridor"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_spec_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("product_specification.id"), nullable=False, index=True
    )
    product_category: Mapped[str] = mapped_column(String, nullable=False)
    best_corridor: Mapped[str] = mapped_column(String, nullable=False)
    cost_differential_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    second_best_corridor: Mapped[str] = mapped_column(String, nullable=False)
    second_best_differential_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    key_driver: Mapped[str] = mapped_column(String, nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    product_specification: Mapped["ProductSpecification"] = relationship()


class TariffExposureAlternativeCorridor(Base):
    __tablename__ = "tariff_exposure_alternative_corridor"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_spec_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("product_specification.id"), nullable=False, index=True
    )
    current_corridor: Mapped[str] = mapped_column(String, nullable=False)
    current_effective_duty_rate_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    alternative_corridor_1: Mapped[str] = mapped_column(String, nullable=False)
    duty_rate_alt_1_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    cost_saving_pct_alt_1: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    alternative_corridor_2: Mapped[str] = mapped_column(String, nullable=False)
    duty_rate_alt_2_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    cost_saving_pct_alt_2: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    product_specification: Mapped["ProductSpecification"] = relationship()


class TopImporterSourcingMove(Base):
    __tablename__ = "top_importer_sourcing_move"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    importer_name: Mapped[str] = mapped_column(String, nullable=False)
    from_corridor: Mapped[str] = mapped_column(String, nullable=False)
    to_corridor: Mapped[str] = mapped_column(String, nullable=False)
    volume_monthly_units: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    volume_shift_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    date_detected: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    confidence_score: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    signal_source: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class FactoryCapacityConstraint(Base):
    __tablename__ = "factory_capacity_constraint"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    country: Mapped[str] = mapped_column(String, nullable=False)
    corridor: Mapped[str] = mapped_column(String, nullable=False, index=True)
    constraint_type: Mapped[str] = mapped_column(String, nullable=False)
    severity_score: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    lead_time_change_weeks: Mapped[int] = mapped_column(Integer, nullable=False)
    affected_product_types: Mapped[str] = mapped_column(String, nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    implication: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class FactoryFinancingImpactAnalysis(Base):
    __tablename__ = "factory_financing_impact_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    country_a: Mapped[str] = mapped_column(String, nullable=False)
    country_b: Mapped[str] = mapped_column(String, nullable=False)
    country_a_financing_rate: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    country_b_financing_rate: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    rate_difference_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    impact_per_dozen_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    annualised_impact_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    implication: Mapped[str] = mapped_column(String, nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class PredictionLog(Base):
    __tablename__ = "prediction_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prediction_type: Mapped[str] = mapped_column(String, nullable=False)
    corridor: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    product_spec_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("product_specification.id"), nullable=True, index=True
    )
    predicted_value: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    p10: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    p50: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    p90: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    prediction_timestamp: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    actual_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    accuracy_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    model_version: Mapped[str] = mapped_column(String, nullable=False)
    data_snapshot_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    product_specification: Mapped[Optional["ProductSpecification"]] = relationship()


# ---------------------------------------------------------------------------
# Cost Reasoning — Learning & Company Intelligence
# ---------------------------------------------------------------------------


class CompanyProfile(Base):
    __tablename__ = "company_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_name: Mapped[str] = mapped_column(String, nullable=False)
    company_type: Mapped[str] = mapped_column(String, nullable=False)
    primary_corridors: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    primary_product_categories: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    typical_quantity_range: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    typical_fob_range_low: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    typical_fob_range_high: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    primary_retail_relationships: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    annual_volume_estimate_dozens: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    risk_profile: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    intelligence_confidence: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("0.0")
    )
    onboarded_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    last_intelligence_update: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class CompanyFactoryRelationship(Base):
    __tablename__ = "company_factory_relationship"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("company_profile.id"), nullable=False, index=True
    )
    factory_name: Mapped[str] = mapped_column(String, nullable=False)
    factory_location: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    factory_corridor: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    relationship_years: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    programs_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_otd_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    avg_quality_acceptance_rate: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    avg_price_vs_market_pct: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    typical_payment_terms: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    typical_lead_time_weeks: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    known_specialisations: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    known_limitations: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_order_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    company: Mapped["CompanyProfile"] = relationship()


class PurchaseOrderHistory(Base):
    __tablename__ = "purchase_order_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("company_profile.id"), nullable=False, index=True
    )
    po_reference: Mapped[str] = mapped_column(String, nullable=False)
    factory_name: Mapped[str] = mapped_column(String, nullable=False)
    corridor: Mapped[str] = mapped_column(String, nullable=False)
    product_category: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    fibre_content: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    construction: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    gsm: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    colour_description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    quantity_dozens: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    quoted_fob_per_dozen: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    actual_fob_per_dozen: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    target_retail_price: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    retailer_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    committed_delivery_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    actual_delivery_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    days_late: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    quality_issues: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    quality_issue_description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    cost_variance_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    factors_that_caused_variance: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    season: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    company: Mapped["CompanyProfile"] = relationship()


class DiscoveredCostFactor(Base):
    __tablename__ = "discovered_cost_factor"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    layer_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    condition_description: Mapped[str] = mapped_column(String, nullable=False)
    factor_name: Mapped[str] = mapped_column(String, nullable=False)
    effect_direction: Mapped[str] = mapped_column(String, nullable=False)
    effect_magnitude_low: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    effect_magnitude_high: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    effect_unit: Mapped[str] = mapped_column(String, nullable=False)
    applies_to_corridor: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    applies_to_company_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("company_profile.id"), nullable=True, index=True
    )
    applies_to_factory: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    applies_to_colour_tier: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    applies_to_season: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    discovered_from_instance_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    first_observed: Mapped[date] = mapped_column(Date, nullable=False)
    last_observed: Mapped[date] = mapped_column(Date, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reviewed_by_human: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class CostLayerPrior(Base):
    __tablename__ = "cost_layer_prior"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    layer_name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    sequence_order: Mapped[int] = mapped_column(Integer, nullable=False)
    is_mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    applies_when: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    unit: Mapped[str] = mapped_column(String, nullable=False)
    prior_low: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    prior_high: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    current_low: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    current_high: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    update_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_updated_from_instance: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    stability: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    variables: Mapped[list["CostVariablePrior"]] = relationship(back_populates="layer")


class CostVariablePrior(Base):
    __tablename__ = "cost_variable_prior"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cost_layer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("cost_layer_prior.id"), nullable=False, index=True
    )
    variable_name: Mapped[str] = mapped_column(String, nullable=False)
    variable_value: Mapped[str] = mapped_column(String, nullable=False)
    effect_type: Mapped[str] = mapped_column(String, nullable=False)
    prior_effect_low: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    prior_effect_high: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    current_effect_low: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    current_effect_high: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    reasoning: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    layer: Mapped["CostLayerPrior"] = relationship(back_populates="variables")


class CostReasoningSession(Base):
    __tablename__ = "cost_reasoning_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    company_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("company_profile.id"), nullable=True, index=True
    )
    input_context: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    inferred_spec: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    prior_layers_used: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    company_context_applied: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    company_factors_used: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    discovered_factors_applied: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    layer_estimates: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    total_low: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    total_mid: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    total_high: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    confidence_overall: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    flags: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    missing_inputs: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    unknown_factors_flagged: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    actual_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    outcome_recorded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class CostOutcome(Base):
    __tablename__ = "cost_outcome"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reasoning_session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    company_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("company_profile.id"), nullable=True, index=True
    )
    po_reference: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    estimated_fob_low: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    estimated_fob_mid: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    estimated_fob_high: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    actual_fob: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    actual_date: Mapped[date] = mapped_column(Date, nullable=False)
    variance_amount: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    variance_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    was_within_range: Mapped[bool] = mapped_column(Boolean, nullable=False)
    variance_explained: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    variance_cause_layer: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    variance_cause_factor: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_known_factor: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    discovered_factor_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("discovered_cost_factor.id"), nullable=True
    )
    learnable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    learning_note: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

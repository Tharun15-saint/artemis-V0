"""
Artemis constants — never hardcode these values elsewhere.
Import from here. Change here. Everything stays consistent.
"""

from decimal import Decimal

# Corridors — the only valid sourcing corridor names
CORRIDORS = [
    "Bangladesh",
    "India",
    "Vietnam",
    "China",
    "Turkey",
    "Morocco",
    "Cambodia",
    "Pakistan",
]

# Intelligence thresholds
HEDGE_CONTANGO_THRESHOLD = Decimal("5.0")   # % above spot to trigger hedge opportunity
FACTORY_UTILISATION_OTD_RISK = Decimal("85.0")  # % above which OTD deteriorates
CRUDE_OIL_DYEING_PRESSURE_THRESHOLD = Decimal("85.0")  # USD/bbl — onset of dye chemical cost pressure

# Crude oil price levels for cost engine and synthesis signals (Brent, USD/bbl)
# "high" at $100+: severe polyester chain pressure, dye chemical risk, diesel freight squeeze
# "elevated" at $80-100: meaningful cost pressure, dark-colour dyeing premium applies
# "normal" at $60-80: baseline operating range
# "low" below $60: deflationary signal, polyester costs ease
CRUDE_OIL_LEVEL_THRESHOLDS = {
    "high":     Decimal("100.0"),
    "elevated": Decimal("80.0"),
    "normal":   Decimal("60.0"),
}

# Data staleness thresholds (days)
STALENESS = {
    "cotton":          2,
    "fx_rates":        2,
    "crude_oil_warn":  5,
    "crude_oil_error": 7,
    "ocean_freight":   8,
    "labour_cost":     90,
    "energy_cost":     90,
    "factory_financing": 90,
}

# FOB calculation constants
PAYMENT_DAYS = {
    "Bangladesh": 90,
    "India":      90,
    "Vietnam":    90,
    "China":      60,
    "Turkey":     60,
    "Morocco":    90,
    "Cambodia":   90,
    "Pakistan":   90,
}

# Revenue types — the only valid values for revenue_transaction.revenue_type
REVENUE_TYPES = [
    "subscription",
    "hedge_share",
    "freight_booking",
    "drayage_booking",
    "intermodal_booking",
    "customs_referral",
    "scf_spread",
    "data_licensing",
]

# Program statuses — the only valid values for program.status
PROGRAM_STATUSES = [
    "PLANNING",
    "COMMITTED",
    "IN_PRODUCTION",
    "SHIPPED",
    "DELIVERED",
    "CLOSED",
]

# Hedge statuses
HEDGE_STATUSES = ["UNHEDGED", "PARTIAL", "FULLY_HEDGED"]

# Crude oil product chain — maps crude to affected Tirupur cost components.
# Used by synthesis.py and cost_reasoning engine to route crude price signals.
# tirupur_transmission_lag_weeks: weeks from crude move to cost impact on RRK programs.
# These are industry_prior estimates seeded in learned_coefficient as
# 'crude_to_dye_chemical_lag_weeks' and 'crude_to_polyester_yarn_lag_weeks'.
CRUDE_OIL_PRODUCT_CHAIN = {
    "dyeing_chemical_cost": {
        "affected_component": "disperse_dye_carrier_reactive_dye_auxiliary",
        "tirupur_transmission_lag_weeks": 6,    # crude → petrochemical → dye chemical → Tirupur
        "cost_sensitivity": "high",             # direct petrochemical derivation
    },
    "freight_energy_surcharge": {
        "affected_component": "diesel_port_drayage_bunker_fuel",
        "tirupur_transmission_lag_weeks": 2,    # fuel surcharges adjust near-immediately
        "cost_sensitivity": "medium",
    },
    "factory_energy_cost": {
        "affected_component": "diesel_generator_compressor_fuel",
        "tirupur_transmission_lag_weeks": 1,    # diesel retail price follows crude closely
        "cost_sensitivity": "medium",
    },
    "polyester_yarn_cost": {
        "affected_component": "crude_px_pta_chip_polyester_yarn",
        "tirupur_transmission_lag_weeks": 14,   # full chain: crude→PX→PTA→chip→yarn→Tirupur
        "cost_sensitivity": "very_high",        # dominant cost driver for CVC/polyester programs
    },
}

# Crude → polyester chain proxy coefficients.
# Industry-calibrated conversion factors used when real ICIS prices are unavailable.
# Source: published petrochemical cost analysis (IHS Markit, ICIS methodology guides).
# is_proxy=True rows in px_paraxylene/pta/polyester_pet_chips use these constants.
# Replace with real ICIS data when subscription obtained — do NOT use these for
# precise cost estimation (±20% accuracy only; useful for direction signals only).
POLYESTER_CHAIN_PROXY = {
    # PX = Brent (USD/bbl) × bbl_to_tonne_factor × processing_yield + cracking_spread
    # 7.33 = barrels per metric tonne for naphtha-range crude
    # 1.12 = crude-to-naphtha yield factor (naphtha is denser/more valuable than crude)
    # 100  = typical aromatics extraction + PX purification spread (USD/tonne), 10yr avg
    "px_from_brent_multiplier": Decimal("8.21"),   # 7.33 × 1.12 ≈ 8.21
    "px_from_brent_constant": Decimal("100.0"),    # USD/tonne processing spread
    # PTA = PX × purified_acid_yield + acid_processing_margin
    # 0.86 = PX → PTA mass conversion (oxidation and purification loss)
    # 95   = acetic acid, catalyst, processing costs embedded in PTA price (USD/tonne)
    "pta_from_px_multiplier": Decimal("0.86"),
    "pta_from_px_constant": Decimal("95.0"),       # USD/tonne
    # PET chip = PTA × polymerisation_yield + MEG_cost_proxy + polymerisation_margin
    # 0.83 = PTA → PET mass conversion (MEG ~33% by weight, absorbed into constant)
    # 135  = MEG cost proxy + catalyst + polymerisation margin (USD/tonne)
    "chip_from_pta_multiplier": Decimal("0.83"),
    "chip_from_pta_constant": Decimal("135.0"),    # USD/tonne
    # Crude-to-PX ratio: px_spot / (brent * 7.33)
    # Normal range: 1.05–1.25. Below 1.0 = refinery margin compression (stress signal).
    "crude_to_px_ratio_normal_low": Decimal("1.05"),
    "crude_to_px_ratio_normal_high": Decimal("1.25"),
}

# Crude futures curve signal thresholds (Brent 12m contango % vs spot)
CRUDE_CURVE_SIGNAL_THRESHOLDS = {
    "contango_threshold_pct": Decimal("3.0"),       # >3% = clear contango
    "backwardation_threshold_pct": Decimal("-3.0"),  # <-3% = clear backwardation
    # Between -3% and +3% = 'flat'
}

# Intelligence model version — increment when model logic changes
INTELLIGENCE_MODEL_VERSION = "v1.0"

# Current intelligence output model version (semver x.y.z)
# Patch (1.0.x): bug fix that does not change output schema
# Minor (1.x.0): new field added to output or new signal category
# Major (x.0.0): fundamental change to computation logic
CURRENT_MODEL_VERSION = "1.0.0"

WALMART_INVENTORY_THRESHOLDS = {
    "walmart_us": {
        "lean": 35,
        "normal_low": 35,
        "normal_high": 45,
        "elevated": 55,
    },
    "sams_club": {
        "lean": 30,
        "normal_low": 30,
        "normal_high": 40,
        "elevated": 50,
    },
}

WALMART_ENTITY_CONTEXT = {
    "walmart_us": {
        "model": "mass_retail",
        "stores": 4600,
        "weekly_customers_millions": 240,
        "apparel_category": "General Merchandise",
        "fob_pressure_sensitivity": "high",
        "cancellation_risk_inventory_days": 55,
        "replenishment_surge_inventory_days": 35,
    },
    "sams_club": {
        "model": "membership_warehouse",
        "locations": 600,
        "paying_members_millions": 17,
        "apparel_category": "Home and Apparel",
        "fob_pressure_sensitivity": "medium",
        "membership_growth_bullish_threshold_pct": Decimal("5.0"),
        "membership_growth_bearish_threshold_pct": Decimal("2.0"),
    },
}

WALMART_US_MODEL_NOTE = (
    "Walmart U.S.: retail mass market, 4600+ stores, 240M weekly customers, "
    "apparel is in General Merchandise category, value-seeking consumer, "
    "FOB price sensitive"
)

SAMS_CLUB_MODEL_NOTE = (
    "Sam's Club: membership warehouse club, 600+ locations, 17M paying members, "
    "Home and Apparel is explicit category, bulk buying consumer, member behaviour "
    "is stickier than retail, membership growth predicts volume"
)

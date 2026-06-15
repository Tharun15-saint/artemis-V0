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
CRUDE_OIL_DYEING_PRESSURE_THRESHOLD = Decimal("85.0")  # USD/bbl

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

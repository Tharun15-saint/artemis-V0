"""
Seed ocean_freight_rates with India-origin market estimates.

Run after migration: python -m database.seed.seed_ocean_freight
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from database.base import SessionLocal, mark_latest
from database.models.logistics import OceanFreightRates
from database.validation.ingestion_validators import validate_freight_rate

DATA_NOTES = (
    "Market estimate — pending Drewry WCI subscription for live weekly rates. "
    "Tuticorin and Mumbai origin rates for India-sourced Classic Fashion programs."
)

ROUTES = [
    {
        "origin_port": "Tuticorin",
        "origin_country": "India",
        "destination_port": "Los Angeles",
        "destination_country": "United States",
        "rate_20ft_usd": Decimal("1800"),
        "rate_40ft_usd": Decimal("3200"),
        "rate_40ft_hc_usd": Decimal("3400"),
        "transit_days": 22,
    },
    {
        "origin_port": "Tuticorin",
        "origin_country": "India",
        "destination_port": "New Jersey",
        "destination_country": "United States",
        "rate_20ft_usd": Decimal("2000"),
        "rate_40ft_usd": Decimal("3500"),
        "rate_40ft_hc_usd": Decimal("3700"),
        "transit_days": 25,
    },
    {
        "origin_port": "Tuticorin",
        "origin_country": "India",
        "destination_port": "Houston",
        "destination_country": "United States",
        "rate_20ft_usd": Decimal("1900"),
        "rate_40ft_usd": Decimal("3300"),
        "rate_40ft_hc_usd": Decimal("3500"),
        "transit_days": 24,
    },
    {
        "origin_port": "Mumbai",
        "origin_country": "India",
        "destination_port": "Los Angeles",
        "destination_country": "United States",
        "rate_20ft_usd": Decimal("1750"),
        "rate_40ft_usd": Decimal("3100"),
        "rate_40ft_hc_usd": Decimal("3300"),
        "transit_days": 20,
    },
    {
        "origin_port": "Mumbai",
        "origin_country": "India",
        "destination_port": "New Jersey",
        "destination_country": "United States",
        "rate_20ft_usd": Decimal("1950"),
        "rate_40ft_usd": Decimal("3400"),
        "rate_40ft_hc_usd": Decimal("3600"),
        "transit_days": 23,
    },
    {
        "origin_port": "Mumbai",
        "origin_country": "India",
        "destination_port": "Houston",
        "destination_country": "United States",
        "rate_20ft_usd": Decimal("1850"),
        "rate_40ft_usd": Decimal("3200"),
        "rate_40ft_hc_usd": Decimal("3400"),
        "transit_days": 22,
    },
]


def _route_label(route: dict) -> str:
    return f"{route['origin_port']}→{route['destination_port']}"


def _validate_route_rates(route: dict) -> list[str]:
    label = _route_label(route)
    failures: list[str] = []
    for field in ("rate_20ft_usd", "rate_40ft_usd", "rate_40ft_hc_usd"):
        value = route.get(field)
        if value is None:
            continue
        is_valid, reason = validate_freight_rate(float(value), label)
        if not is_valid:
            failures.append(f"{field}: {reason}")
    return failures


def seed_ocean_freight() -> int:
    today = date.today()
    pulled_at = datetime.now(timezone.utc)
    db = SessionLocal()
    inserted = 0

    try:
        for route in ROUTES:
            failures = _validate_route_rates(route)
            if failures:
                raise ValueError(
                    f"Validation failed for {_route_label(route)}: "
                    + "; ".join(failures)
                )

            entity_filter = {
                "origin_port": route["origin_port"],
                "origin_country": route["origin_country"],
                "destination_port": route["destination_port"],
                "destination_country": route["destination_country"],
                "as_of_date": today,
            }
            mark_latest(db, OceanFreightRates, entity_filter)

            db.add(
                OceanFreightRates(
                    origin_port=route["origin_port"],
                    origin_country=route["origin_country"],
                    destination_port=route["destination_port"],
                    destination_country=route["destination_country"],
                    rate_20ft_usd=route["rate_20ft_usd"],
                    rate_40ft_usd=route["rate_40ft_usd"],
                    rate_40ft_hc_usd=route["rate_40ft_hc_usd"],
                    transit_days=route["transit_days"],
                    vessel_availability="normal",
                    port_congestion_index=Decimal("3.50"),
                    as_of_date=today,
                    source="manual_estimate",
                    data_source_url="manual_entry",
                    data_notes=DATA_NOTES,
                    pulled_at=pulled_at,
                    is_latest=True,
                )
            )
            inserted += 1

        db.commit()
        print(f"Seeded {inserted} ocean freight route(s) for as_of_date={today}.")
        return inserted
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    seed_ocean_freight()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

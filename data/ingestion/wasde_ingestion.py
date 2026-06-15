import argparse
import logging
import os
import time
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

import requests
from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from data.ingestion.wasde_common import current_marketing_year
from database.database import SessionLocal
from database.models import Cotton

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

USDA_FAS_BASE = "https://api.fas.usda.gov/api/psd"
USDA_FAS_API_KEY = os.getenv("USDA_FAS_API_KEY", "")
COTTON_CODE = "2631000"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 15

CURRENT_MARKETING_YEAR = current_marketing_year()
WASDE_HISTORY_START_MY = 2010
WASDE_HISTORY_END_MY = 2025

HECTARES_TO_ACRES = Decimal("2.47105")
THOUSAND_BALES_TO_MILLION = Decimal("1000")

TARGET_COUNTRIES = {
    "United States": "us",
    "India": "india",
    "China": "china",
    "Pakistan": "pakistan",
    "Australia": "australia",
    "Brazil": "brazil",
    "Mali": "west_africa",
    "Burkina Faso": "west_africa",
    "Benin": "west_africa",
}

ORIGIN_PRODUCTION_FIELDS = {
    "US": "us_production",
    "India": "india_production",
    "China": "china_production",
    "Pakistan": "pakistan_production",
    "Australia": "australia_production",
    "Brazil": "brazil_production",
    "West Africa": "west_africa_production",
}

ICE_GLOBAL_ORIGIN = "ICE No.2 Global"

ATTR_AREA_HARVESTED = "Area Harvested"
ATTR_PRODUCTION = "Production"
ATTR_DOMESTIC_USE = "Domestic Use"  # PSD API v2 name (was "Domestic Consumption")
ATTR_DOMESTIC_USE_LEGACY = "Domestic Consumption"
ATTR_EXPORTS = "Exports"
ATTR_ENDING_STOCKS = "Ending Stocks"
ATTR_BEGINNING_STOCKS = "Beginning Stocks"

WORLD_PROD_MIN_M_BALES = 80.0
WORLD_PROD_MAX_M_BALES = 160.0
WORLD_USE_MIN_M_BALES = 80.0
WORLD_USE_MAX_M_BALES = 140.0
SU_RATIO_MIN_PCT = 20.0
SU_RATIO_MAX_PCT = 100.0

_COUNTRY_NAME_TO_CODE: Optional[dict[str, str]] = None
_COUNTRY_CODE_TO_NAME: Optional[dict[str, str]] = None
_ATTRIBUTES_CACHE: Optional[dict[int, str]] = None


def _api_headers() -> dict[str, str]:
    if not USDA_FAS_API_KEY:
        logger.warning(
            "USDA_FAS_API_KEY not set — register at apps.fas.usda.gov/opendatawebv2"
        )
        return {}
    return {"X-Api-Key": USDA_FAS_API_KEY}


def _get_json(url: str) -> list[dict[str, Any]]:
    response = requests.get(url, headers=_api_headers(), timeout=REQUEST_TIMEOUT)
    if response.status_code == 403:
        raise RuntimeError(
            "USDA FAS returned 403 — check USDA_FAS_API_KEY in .env "
            "(register at apps.fas.usda.gov/opendatawebv2)"
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError(f"Unexpected response from {url}")
    return payload


def get_countries_lookup() -> dict[str, str]:
    """Return countryName → countryCode (validates TARGET_COUNTRIES names)."""
    global _COUNTRY_NAME_TO_CODE, _COUNTRY_CODE_TO_NAME
    if _COUNTRY_NAME_TO_CODE is not None:
        return _COUNTRY_NAME_TO_CODE

    try:
        payload = _get_json(f"{USDA_FAS_BASE}/countries")
        name_to_code = {
            item["countryName"]: str(item["countryCode"])
            for item in payload
            if "countryName" in item and "countryCode" in item
        }
        code_to_name = {code: name for name, code in name_to_code.items()}
        code_to_name["00"] = "World"

        for name in TARGET_COUNTRIES:
            if name not in name_to_code:
                logger.warning(f"TARGET_COUNTRIES name not in FAS API: {name!r}")

        _COUNTRY_NAME_TO_CODE = name_to_code
        _COUNTRY_CODE_TO_NAME = code_to_name
        logger.info(f"FAS countries lookup loaded: {len(name_to_code)} countries")
        return name_to_code
    except requests.RequestException as exc:
        logger.error(f"Failed to fetch FAS countries: {exc}")
        _COUNTRY_NAME_TO_CODE = {}
        _COUNTRY_CODE_TO_NAME = {"00": "World"}
        return _COUNTRY_NAME_TO_CODE


def get_attributes_lookup() -> dict[int, str]:
    global _ATTRIBUTES_CACHE
    if _ATTRIBUTES_CACHE is not None:
        return _ATTRIBUTES_CACHE

    try:
        payload = _get_json(f"{USDA_FAS_BASE}/commodityAttributes")
        _ATTRIBUTES_CACHE = {
            int(item["attributeId"]): item["attributeName"]
            for item in payload
            if "attributeId" in item and "attributeName" in item
        }
        logger.info(f"FAS attributes lookup loaded: {len(_ATTRIBUTES_CACHE)} attributes")
        return _ATTRIBUTES_CACHE
    except requests.RequestException as exc:
        logger.error(f"Failed to fetch FAS attributes: {exc}")
        _ATTRIBUTES_CACHE = {}
        return _ATTRIBUTES_CACHE


def enrich_psd_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    get_countries_lookup()
    attrs = get_attributes_lookup()
    code_to_name = _COUNTRY_CODE_TO_NAME or {"00": "World"}
    enriched: list[dict[str, Any]] = []

    for item in records:
        code = str(item.get("countryCode", ""))
        attr_id = item.get("attributeId")
        month_raw = item.get("month", 0)
        try:
            month = int(month_raw)
        except (TypeError, ValueError):
            month = 0

        enriched.append(
            {
                **item,
                "countryName": code_to_name.get(code, code),
                "attributeName": attrs.get(int(attr_id), str(attr_id))
                if attr_id is not None
                else "",
                "month": month,
            }
        )
    return enriched


def fetch_year_data(market_year: int) -> list[dict[str, Any]]:
    if not USDA_FAS_API_KEY:
        raise RuntimeError(
            "USDA_FAS_API_KEY is empty — register free at apps.fas.usda.gov/OpenData "
            "and add the key to .env"
        )

    url_country = f"{USDA_FAS_BASE}/commodity/{COTTON_CODE}/country/all/year/{market_year}"
    url_world = f"{USDA_FAS_BASE}/commodity/{COTTON_CODE}/world/year/{market_year}"
    last_error: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            country_data = _get_json(url_country)
            world_data = _get_json(url_world)
            data = enrich_psd_records(country_data + world_data)
            logger.info(
                f"Fetched MY {market_year}: {len(country_data)} country + "
                f"{len(world_data)} world PSD records"
            )
            return data
        except Exception as exc:
            last_error = exc
            logger.warning(
                f"FAS fetch MY {market_year} attempt {attempt}/{MAX_RETRIES}: {exc}"
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

    raise RuntimeError(f"FAS fetch failed for MY {market_year}") from last_error


def extract_value(
    data: list[dict[str, Any]],
    country_name: str,
    attribute_name: str,
    unit: str = "bales",
    *,
    attribute_aliases: Optional[list[str]] = None,
) -> Optional[Decimal]:
    names = [attribute_name] + (attribute_aliases or [])
    matches = [
        item
        for item in data
        if item.get("countryName") == country_name
        and item.get("attributeName") in names
        and item.get("value") is not None
    ]
    if not matches:
        return None

    matches.sort(key=lambda x: x.get("month", 0), reverse=True)
    raw = matches[0]["value"]
    try:
        value = Decimal(str(raw))
    except Exception:
        return None

    if unit == "bales":
        return (value / THOUSAND_BALES_TO_MILLION).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
    if unit == "area_hectares":
        return (value * HECTARES_TO_ACRES).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    return None


def build_balance_sheet(data: list[dict[str, Any]], market_year: int) -> dict[str, Any]:
    world_prod = extract_value(data, "World", ATTR_PRODUCTION, "bales")
    world_use = extract_value(
        data,
        "World",
        ATTR_DOMESTIC_USE,
        "bales",
        attribute_aliases=[ATTR_DOMESTIC_USE_LEGACY],
    )
    world_exports = extract_value(data, "World", ATTR_EXPORTS, "bales")
    world_stocks = extract_value(data, "World", ATTR_ENDING_STOCKS, "bales")

    su_ratio: Optional[Decimal] = None
    if world_stocks and world_use and world_use > 0:
        su_ratio = (world_stocks / world_use * Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    else:
        logger.error(f"Cannot compute S/U ratio for MY {market_year}")

    us_area = extract_value(data, "United States", ATTR_AREA_HARVESTED, "area_hectares")
    us_prod = extract_value(data, "United States", ATTR_PRODUCTION, "bales")

    india_prod = extract_value(data, "India", ATTR_PRODUCTION, "bales")
    china_prod = extract_value(data, "China", ATTR_PRODUCTION, "bales")
    pakistan_prod = extract_value(data, "Pakistan", ATTR_PRODUCTION, "bales")
    australia_prod = extract_value(data, "Australia", ATTR_PRODUCTION, "bales")
    brazil_prod = extract_value(data, "Brazil", ATTR_PRODUCTION, "bales")

    wa_parts = [
        extract_value(data, "Mali", ATTR_PRODUCTION, "bales"),
        extract_value(data, "Burkina Faso", ATTR_PRODUCTION, "bales"),
        extract_value(data, "Benin", ATTR_PRODUCTION, "bales"),
    ]
    west_africa_prod = sum(p for p in wa_parts if p) or None

    season_price: Optional[Decimal] = None
    for item in data:
        attr = item.get("attributeName", "")
        if (
            item.get("countryName") == "United States"
            and "price" in attr.lower()
            and "farm" in attr.lower()
        ):
            season_price = (Decimal(str(item["value"])) / Decimal("100")).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
            break

    report_type = "estimate" if market_year >= CURRENT_MARKETING_YEAR - 1 else "actual"

    return {
        "world_production": world_prod,
        "world_mill_use": world_use,
        "world_exports": world_exports,
        "world_ending_stocks": world_stocks,
        "world_su_ratio": su_ratio,
        "us_harvested_area": us_area,
        "us_production": us_prod,
        "india_production": india_prod,
        "china_production": china_prod,
        "pakistan_production": pakistan_prod,
        "australia_production": australia_prod,
        "brazil_production": brazil_prod,
        "west_africa_production": west_africa_prod,
        "season_price": season_price,
        "report_type": report_type,
    }


def validate_balance_sheet(data: dict[str, Any], market_year: int) -> bool:
    required = [
        "world_production",
        "world_mill_use",
        "world_ending_stocks",
        "world_su_ratio",
    ]
    for field in required:
        if data.get(field) is None:
            logger.error(f"MY {market_year}: missing critical field {field}")
            return False

    wp = float(data["world_production"])
    wu = float(data["world_mill_use"])
    su = float(data["world_su_ratio"])

    if not (WORLD_PROD_MIN_M_BALES <= wp <= WORLD_PROD_MAX_M_BALES):
        logger.error(f"MY {market_year}: world production {wp} out of bounds")
        return False
    if not (WORLD_USE_MIN_M_BALES <= wu <= WORLD_USE_MAX_M_BALES):
        logger.error(f"MY {market_year}: world mill use {wu} out of bounds")
        return False
    if not (SU_RATIO_MIN_PCT <= su <= SU_RATIO_MAX_PCT):
        logger.error(f"MY {market_year}: S/U ratio {su} out of bounds")
        return False

    if su < 40:
        logger.info(f"SPIKE RISK — S/U below 40%: {su}%")
    elif su < 50:
        logger.info(f"BULLISH — S/U below 50%: {su}%")
    elif su > 60:
        logger.info(f"BEARISH — S/U above 60%: {su}%")
    else:
        logger.info(f"NEUTRAL — S/U at {su}%")

    return True


def marketing_year_bounds(market_year: int) -> tuple[date, date]:
    """Cotton MY N runs August 1 of year N through July 31 of year N+1."""
    return date(market_year, 8, 1), date(market_year + 1, 7, 31)


def _wasde_forecast_for_origin(origin: str, data: dict[str, Any]) -> Optional[Decimal]:
    if origin == "US" and data.get("season_price") is not None:
        return data["season_price"]

    production_field = ORIGIN_PRODUCTION_FIELDS.get(origin)
    if production_field:
        return data.get(production_field)

    if origin == ICE_GLOBAL_ORIGIN:
        return data.get("season_price") or data.get("world_production")

    return None


def apply_wasde_to_rows_in_marketing_year(
    db: Session,
    data: dict[str, Any],
    market_year: int,
) -> int:
    """Update all cotton rows whose as_of_date falls within the marketing year."""
    start, end = marketing_year_bounds(market_year)
    rows = (
        db.query(Cotton)
        .filter(
            Cotton.as_of_date.isnot(None),
            Cotton.as_of_date >= start,
            Cotton.as_of_date <= end,
        )
        .all()
    )

    updated = 0
    for row in rows:
        forecast = _wasde_forecast_for_origin(row.origin_country, data)
        if forecast is not None:
            row.wasde_forecast = forecast
        row.wasde_ending_stocks = data["world_ending_stocks"]
        row.wasde_su_ratio_pct = data["world_su_ratio"]
        row.crop_year = market_year
        updated += 1

    if updated:
        db.commit()

    return updated


def write_balance_sheet(db: Session, data: dict[str, Any], market_year: int) -> int:
    updated = apply_wasde_to_rows_in_marketing_year(db, data, market_year)
    start, end = marketing_year_bounds(market_year)

    if updated:
        logger.info(
            f"MY {market_year}/{market_year + 1} ({start} to {end}): "
            f"updated {updated} cotton row(s) (type={data['report_type']})"
        )
    else:
        logger.warning(
            f"MY {market_year}/{market_year + 1} ({start} to {end}): "
            "no cotton rows in range — run cotton price backfill first"
        )

    return updated


def backfill_wasde_history(db: Session) -> int:
    """Fetch MY2010–MY2025 WASDE data and stamp each cotton row in-range."""
    get_countries_lookup()
    total_rows = 0
    years_applied = 0

    for market_year in range(WASDE_HISTORY_START_MY, WASDE_HISTORY_END_MY + 1):
        try:
            raw = fetch_year_data(market_year)
            balance = build_balance_sheet(raw, market_year)
            if not validate_balance_sheet(balance, market_year):
                logger.warning(f"MY {market_year}/{market_year + 1}: validation failed — skipped")
                continue

            updated = write_balance_sheet(db, balance, market_year)
            total_rows += updated
            if updated:
                years_applied += 1
        except RuntimeError as exc:
            logger.warning(f"MY {market_year}/{market_year + 1}: fetch failed — {exc}")

        time.sleep(2)

    logger.info(
        f"WASDE history backfill complete: {total_rows} cotton row updates "
        f"across {years_applied} marketing years"
    )
    return total_rows


def run_historical_backfill(db: Session) -> int:
    return backfill_wasde_history(db)


def run_once() -> bool:
    db = SessionLocal()
    try:
        get_countries_lookup()
        current = CURRENT_MARKETING_YEAR
        for year in [current - 1, current]:
            raw = fetch_year_data(year)
            bal = build_balance_sheet(raw, year)
            if validate_balance_sheet(bal, year):
                write_balance_sheet(db, bal, year)
        return True
    except Exception as exc:
        logger.critical(f"WASDE ingestion failed: {exc}", exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


def run_scheduled() -> None:
    logger.info("WASDE scheduler started — monthly cycle (30-day sleep).")
    while True:
        success = run_once()
        logger.info(f"WASDE cycle {'SUCCESS' if success else 'FAILED'}. Sleeping 30 days.")
        time.sleep(30 * 86400)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="USDA WASDE cotton PSD ingestion")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help=f"Backfill WASDE history MY{WASDE_HISTORY_START_MY}–MY{WASDE_HISTORY_END_MY}",
    )
    parser.add_argument("--schedule", action="store_true", help="Run monthly loop")
    args = parser.parse_args()

    if args.backfill:
        db = SessionLocal()
        try:
            count = run_historical_backfill(db)
            logger.info(f"WASDE backfill complete: {count} rows written/updated")
        finally:
            db.close()
    elif args.schedule:
        run_scheduled()
    else:
        raise SystemExit(0 if run_once() else 1)

"""
Cotton-region weather ingestion — NASA POWER Agroclimatology API.

Source: NASA POWER Temporal Daily Point API
  https://power.larc.nasa.gov/api/temporal/daily/point
  Free, no API key required, globally validated from 1981-present.
  Data is satellite-observed + model-assimilated, peer-reviewed (30-year climatology).

Regions tracked (all major global cotton-producing belts):
  India   — Gujarat/Surendranagar, Vidarbha/Akola, Telangana/Adilabad, AP/Guntur
  USA     — West Texas/Lubbock, Mississippi Delta/Greenville, SE Georgia/Tifton
  China   — Xinjiang/Aksu (≈25% of world production)
  Brazil  — Mato Grosso/Sorriso (≈10% of world production)
  Pakistan— Punjab/Multan (≈8% of world production)
  Australia—NSW/Narrabri (≈3% of world production)

Weather stress in Gujarat or Xinjiang during the cotton growing season shows up
in world cotton prices 4-12 weeks later and in Tirupur yarn prices 8-14 weeks later.

Parameters fetched per region:
  T2M      — 2m air temperature (°C)   → avg/max/min
  PRECTOTCORR — precipitation (mm/day)
  ALLSKY_SFC_SW_DWN — surface solar radiation (MJ/m²/day)
  RH2M     — relative humidity at 2m (%)

Growing Degree Days (GDD): base 15.5°C (standard cotton)
  GDD_day = max(0, ((Tmax + Tmin) / 2) - 15.5)
  GDD_week = sum of daily values for that week

Scheduling: weekly (every Sunday, processes Mon–Sun of prior week).
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

import requests
from sqlalchemy.orm import Session

from database.base import SessionLocal, is_duplicate_row, mark_latest
from database.ingestion_context import IngestionContext
from database.models.weather import CottonRegionWeather

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

SCRIPT_VERSION = "1.0.0"
SOURCE_NAME = "nasa_power_cotton_weather"
NASA_POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
REQUEST_TIMEOUT = 60
SCHEDULE_INTERVAL_HOURS = 168

# GDD base temperature for cotton (60°F = 15.5°C)
GDD_BASE_CELSIUS = Decimal("15.5")

# Cotton growing seasons by country (months inclusive)
# India: Kharif (June–November sowing/growing, harvest Dec–Feb)
# US: April–October
# China Xinjiang: April–October (drip-irrigated desert, very hot summers)
# Brazil: January–June (Southern Hemisphere summer)
# Pakistan: April–October (similar to India Kharif)
# Australia: October–April (Southern Hemisphere)
COTTON_SEASON_BY_COUNTRY = {
    "IN": (6, 11),
    "US": (4, 10),
    "CN": (4, 10),
    "BR": (1, 6),
    "PK": (4, 10),
    "AU": (10, 4),   # wraps year-end; handled in _season_assessment
}

REGIONS: list[dict[str, Any]] = [
    # ── India: Kharif cotton belt (world's largest producer) ────────────────
    {
        "region_name": "gujarat_india",
        "country": "IN",
        "latitude": 22.73,
        "longitude": 71.68,
        "display": "Gujarat (Surendranagar)",
    },
    {
        "region_name": "vidarbha_maharashtra_india",
        "country": "IN",
        "latitude": 20.71,
        "longitude": 77.00,
        "display": "Vidarbha/Akola (Maharashtra)",
    },
    {
        "region_name": "telangana_india",
        "country": "IN",
        "latitude": 18.70,
        "longitude": 78.53,
        "display": "Telangana (Adilabad)",
    },
    {
        "region_name": "andhra_pradesh_india",
        "country": "IN",
        "latitude": 16.30,
        "longitude": 80.44,
        "display": "Andhra Pradesh (Guntur)",
    },
    # ── USA: major cotton-producing regions ─────────────────────────────────
    {
        "region_name": "west_texas_us",
        "country": "US",
        "latitude": 33.58,
        "longitude": -101.86,
        "display": "West Texas (Lubbock)",
    },
    {
        "region_name": "mississippi_delta_us",
        "country": "US",
        "latitude": 33.41,
        "longitude": -91.06,
        "display": "Mississippi Delta (Greenville)",
    },
    {
        "region_name": "southeast_georgia_us",
        "country": "US",
        "latitude": 31.45,
        "longitude": -83.52,
        "display": "Southeast Georgia (Tifton)",
    },
    # ── China: Xinjiang (~85% of China's cotton, ~25% of world) ────────────
    # Aksu Prefecture — largest cotton-producing area in China.
    # Extreme continental climate: hot dry summers, cold winters.
    # Drip-irrigated; drought stress here means water management failure.
    {
        "region_name": "xinjiang_china",
        "country": "CN",
        "latitude": 41.17,
        "longitude": 80.26,
        "display": "Xinjiang/Aksu (China)",
    },
    # ── Brazil: Mato Grosso (~60% of Brazil's cotton, #2 exporter) ─────────
    # Sorriso area — cerrado savanna, highly mechanised.
    # Season Jan–Jun (Southern Hemisphere); heat stress rare but drought common.
    {
        "region_name": "mato_grosso_brazil",
        "country": "BR",
        "latitude": -12.55,
        "longitude": -55.72,
        "display": "Mato Grosso/Sorriso (Brazil)",
    },
    # ── Pakistan: Punjab province (Multan district) ─────────────────────────
    # Pakistan's cotton belt; similar Kharif season to India.
    # Flood irrigation from Indus system; excess rainfall = waterlogging risk.
    {
        "region_name": "punjab_pakistan",
        "country": "PK",
        "latitude": 30.19,
        "longitude": 71.48,
        "display": "Punjab/Multan (Pakistan)",
    },
    # ── Australia: New South Wales (Narrabri-Walgett belt) ──────────────────
    # World's highest-yielding cotton; fully irrigated from Namoi River.
    # Southern Hemisphere season Oct–Apr; heat stress during Dec–Feb key risk.
    {
        "region_name": "nsw_narrabri_australia",
        "country": "AU",
        "latitude": -30.32,
        "longitude": 149.78,
        "display": "NSW/Narrabri (Australia)",
    },
]

NASA_PARAMETERS = "T2M,T2M_MAX,T2M_MIN,PRECTOTCORR,ALLSKY_SFC_SW_DWN,RH2M"


# ---------------------------------------------------------------------------
# NASA POWER fetch
# ---------------------------------------------------------------------------

def _fetch_daily_weather(
    lat: float, lon: float, start_date: date, end_date: date
) -> Optional[dict[str, Any]]:
    """
    Fetch daily weather from NASA POWER for a single point and date range.
    Returns the parsed daily data dict or None on failure.
    """
    params = {
        "parameters": NASA_PARAMETERS,
        "community": "AG",
        "longitude": lon,
        "latitude": lat,
        "start": start_date.strftime("%Y%m%d"),
        "end": end_date.strftime("%Y%m%d"),
        "format": "JSON",
    }
    try:
        resp = requests.get(NASA_POWER_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        daily = payload.get("properties", {}).get("parameter", {})
        if not daily:
            logger.warning("NASA POWER: no data in response for %.4f,%.4f", lat, lon)
            return None
        return daily
    except requests.RequestException as exc:
        logger.error("NASA POWER fetch failed (%.4f,%.4f): %s", lat, lon, exc)
        return None


# ---------------------------------------------------------------------------
# Weekly aggregation
# ---------------------------------------------------------------------------

def _week_dates(week_ending: date) -> list[str]:
    """Return the 7 YYYYMMDD strings for Mon–Sun of the week ending on week_ending."""
    return [
        (week_ending - timedelta(days=i)).strftime("%Y%m%d") for i in range(6, -1, -1)
    ]


def _to_decimal(v: Any, places: int = 2) -> Optional[Decimal]:
    if v is None or v == -999 or v == -999.0:
        return None
    try:
        return Decimal(str(round(float(v), places))).quantize(
            Decimal(f"0.{'0'*places}"), rounding=ROUND_HALF_UP
        )
    except (TypeError, ValueError):
        return None


def aggregate_week(daily: dict[str, Any], week_ending: date) -> Optional[dict[str, Any]]:
    """
    Aggregate daily NASA POWER data into weekly statistics for week_ending.
    Returns None if fewer than 5 of 7 days have valid data.
    """
    days = _week_dates(week_ending)
    t2m_vals, tmax_vals, tmin_vals, precip_vals, solar_vals, rh_vals = (
        [], [], [], [], [], []
    )

    for day_str in days:
        t2m = daily.get("T2M", {}).get(day_str)
        tmax = daily.get("T2M_MAX", {}).get(day_str)
        tmin = daily.get("T2M_MIN", {}).get(day_str)
        precip = daily.get("PRECTOTCORR", {}).get(day_str)
        solar = daily.get("ALLSKY_SFC_SW_DWN", {}).get(day_str)
        rh = daily.get("RH2M", {}).get(day_str)

        if t2m is not None and t2m != -999.0:
            t2m_vals.append(float(t2m))
        if tmax is not None and tmax != -999.0:
            tmax_vals.append(float(tmax))
        if tmin is not None and tmin != -999.0:
            tmin_vals.append(float(tmin))
        if precip is not None and precip != -999.0:
            precip_vals.append(float(precip))
        if solar is not None and solar != -999.0:
            solar_vals.append(float(solar))
        if rh is not None and rh != -999.0:
            rh_vals.append(float(rh))

    if len(t2m_vals) < 5:
        logger.warning(
            "Week ending %s: only %d/7 days with valid T2M — skipping", week_ending, len(t2m_vals)
        )
        return None

    avg_temp = _to_decimal(sum(t2m_vals) / len(t2m_vals))
    max_temp = _to_decimal(max(tmax_vals)) if tmax_vals else None
    min_temp = _to_decimal(min(tmin_vals)) if tmin_vals else None
    total_precip = _to_decimal(sum(precip_vals)) if precip_vals else None
    avg_solar = _to_decimal(sum(solar_vals) / len(solar_vals)) if solar_vals else None
    avg_rh = _to_decimal(sum(rh_vals) / len(rh_vals)) if rh_vals else None

    # GDD: accumulated over the week using (Tmax + Tmin) / 2 - 15.5
    gdd = Decimal("0")
    gdd_days = 0
    for i in range(min(len(tmax_vals), len(tmin_vals))):
        tmean = Decimal(str((tmax_vals[i] + tmin_vals[i]) / 2))
        daily_gdd = max(Decimal("0"), tmean - GDD_BASE_CELSIUS)
        gdd += daily_gdd
        gdd_days += 1
    gdd_week = gdd.quantize(Decimal("0.01")) if gdd_days >= 4 else None

    return {
        "avg_temp": avg_temp,
        "max_temp": max_temp,
        "min_temp": min_temp,
        "total_precip": total_precip,
        "avg_solar": avg_solar,
        "avg_rh": avg_rh,
        "gdd": gdd_week,
    }


def _is_cotton_season(country: str, month: int) -> bool:
    start, end = COTTON_SEASON_BY_COUNTRY.get(country, (4, 11))
    if start <= end:
        return start <= month <= end
    # Wrap-around season (e.g. AU: Oct(10) – Apr(4) crosses year boundary)
    return month >= start or month <= end


def _season_assessment(
    avg_temp: Optional[Decimal],
    total_precip: Optional[Decimal],
    gdd: Optional[Decimal],
    country: str,
    month: int,
) -> str:
    """
    Compress the week's weather into a model-actionable signal.
    Uses crop-agnostic agronomic thresholds for cotton across all regions.
    """
    if avg_temp is None:
        return "data_unavailable"

    t = float(avg_temp)
    precip = float(total_precip) if total_precip is not None else None
    gdd_val = float(gdd) if gdd is not None else None
    in_season = _is_cotton_season(country, month)

    if t > 38:
        return "heat_stress"
    if t < 15 and in_season:
        return "cold_stress"
    # Drought: low precipitation during the growing season (not off-season)
    if precip is not None and precip < 5 and in_season:
        return "drought_stress"
    if precip is not None and precip > 80:
        return "excess_moisture"
    if gdd_val is not None and gdd_val > 30 and in_season:
        return "favorable"
    return "normal"


# ---------------------------------------------------------------------------
# Write to DB
# ---------------------------------------------------------------------------

def write_weather_row(
    db: Session,
    ctx: IngestionContext,
    region: dict[str, Any],
    week_ending: date,
    agg: dict[str, Any],
) -> None:
    dup_filter = {
        "region_name": region["region_name"],
        "week_ending": week_ending,
    }
    dup_values = {
        "avg_temp_celsius": agg["avg_temp"],
        "total_rainfall_mm": agg["total_precip"],
    }
    if is_duplicate_row(db, CottonRegionWeather, dup_filter, dup_values):
        ctx.stale()
        return

    mark_latest(db, CottonRegionWeather, dup_filter)

    month = week_ending.month
    assessment = _season_assessment(
        agg["avg_temp"], agg["total_precip"], agg["gdd"], region["country"], month
    )

    db.add(CottonRegionWeather(
        region_name=region["region_name"],
        country=region["country"],
        latitude=Decimal(str(region["latitude"])),
        longitude=Decimal(str(region["longitude"])),
        week_ending=week_ending,
        avg_temp_celsius=agg["avg_temp"],
        max_temp_celsius=agg["max_temp"],
        min_temp_celsius=agg["min_temp"],
        total_rainfall_mm=agg["total_precip"],
        rainfall_vs_normal_pct=None,   # climatological normal comparison requires separate fetch
        solar_radiation_mj_m2=agg["avg_solar"],
        relative_humidity_pct=agg["avg_rh"],
        growing_degree_days=agg["gdd"],
        season_assessment=assessment,
        is_cotton_season=_is_cotton_season(region["country"], month),
        as_of_date=week_ending,
        source="NASA_POWER_AG_DAILY",
        data_source_url=NASA_POWER_URL,
        pulled_at=datetime.now(timezone.utc),
        is_latest=True,
    ))
    db.commit()
    ctx.inserted()
    logger.info(
        "  %s | %s | T=%.1f°C | precip=%.1f mm | GDD=%.1f | %s",
        region["display"], week_ending,
        float(agg["avg_temp"]) if agg["avg_temp"] else 0,
        float(agg["total_precip"]) if agg["total_precip"] else 0,
        float(agg["gdd"]) if agg["gdd"] else 0,
        assessment,
    )


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def _last_sunday() -> date:
    """Return the most recently completed Sunday (week_ending day)."""
    today = date.today()
    days_since_sunday = (today.weekday() + 1) % 7
    return today - timedelta(days=days_since_sunday)


def run_once(week_ending: Optional[date] = None) -> bool:
    if week_ending is None:
        week_ending = _last_sunday()

    # Fetch daily data starting 2 days before Monday so we have Monday in range
    week_start = week_ending - timedelta(days=6)
    logger.info(
        "Weather ingestion: week %s → %s (%d regions)",
        week_start, week_ending, len(REGIONS),
    )

    db = SessionLocal()
    try:
        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=NASA_POWER_URL,
            db=db,
        ) as ctx:
            ctx.set_as_of_date(week_ending)
            for region in REGIONS:
                logger.info("Fetching %s...", region["display"])
                daily = _fetch_daily_weather(
                    region["latitude"], region["longitude"],
                    week_start, week_ending,
                )
                if daily is None:
                    ctx.increment_rejected(f"{region['region_name']}: API failure")
                    continue

                agg = aggregate_week(daily, week_ending)
                if agg is None:
                    ctx.increment_rejected(
                        f"{region['region_name']}: insufficient days"
                    )
                    continue

                write_weather_row(db, ctx, region, week_ending, agg)
                time.sleep(1)   # be polite to NASA POWER (free public API)

        return True
    except Exception as exc:
        logger.critical("Weather ingestion failed: %s", exc, exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


def run_backfill(weeks_back: int = 52) -> None:
    """Backfill up to weeks_back weeks of weekly weather data."""
    last_sunday = _last_sunday()
    logger.info("Weather backfill: %d weeks from %s", weeks_back, last_sunday)
    failed = 0
    for i in range(weeks_back):
        week_ending = last_sunday - timedelta(weeks=i)
        if not run_once(week_ending):
            failed += 1
        time.sleep(2)

    logger.info("Weather backfill complete. Failed weeks: %d/%d", failed, weeks_back)


def run_scheduled() -> None:
    logger.info("Weather scheduler: weekly cycle (every %dh).", SCHEDULE_INTERVAL_HOURS)
    while True:
        run_once()
        time.sleep(SCHEDULE_INTERVAL_HOURS * 3600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="NASA POWER cotton-region weekly weather ingestion."
    )
    parser.add_argument("--backfill", type=int, default=0,
                        help="Number of prior weeks to backfill (max ~2000 for 40yr history)")
    parser.add_argument("--schedule", action="store_true")
    parser.add_argument("--week-ending", type=str, default=None,
                        help="Specific week ending date (YYYY-MM-DD)")
    args = parser.parse_args()

    if args.backfill:
        run_backfill(args.backfill)
    elif args.schedule:
        run_scheduled()
    elif args.week_ending:
        we = date.fromisoformat(args.week_ending)
        raise SystemExit(0 if run_once(we) else 1)
    else:
        raise SystemExit(0 if run_once() else 1)

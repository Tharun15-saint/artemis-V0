import logging
import os
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

import requests
from dotenv import load_dotenv
from sqlalchemy import func
from sqlalchemy.orm import Session

from database.base import mark_latest
from database.database import SessionLocal
from database.models import CommodityFutures, Cotton, FxRates

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

FRED_API_KEY = os.getenv("FRED_API_KEY", "")
NASDAQ_API_KEY = os.getenv("NASDAQ_API_KEY", "")
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")
ALPHA_VANTAGE_BASE = "https://www.alphavantage.co/query"

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
WORLDBANK_BASE = "https://api.worldbank.org/v2"
NASDAQ_BASE = "https://data.nasdaq.com/api/v3/datasets"

BACKFILL_YEARS = 15
START_DATE = date.today().replace(year=date.today().year - BACKFILL_YEARS).strftime("%Y-%m-%d")
END_DATE = date.today().strftime("%Y-%m-%d")

FRED_SERIES = {
    "DEXINUS": "usd_inr",
    "DEXCHUS": "usd_cny",
    "CCUSMA02TRM618N": "usd_try",
}

# Monthly FRED series — fetch at monthly frequency, expand to weekly via interpolation.
FRED_MONTHLY_SERIES = {
    "CCUSMA02TRM618N",
}

WORLDBANK_SERIES = {
    "BGD": "usd_bdt",
    "VNM": "usd_vnd",
    "PAK": "usd_pkr",
}

# Alpha Vantage FX_WEEKLY primary; World Bank annual interpolation before AV history.
ALPHA_VANTAGE_WORLDBANK_FX = {
    "BDT": ("usd_bdt", "BGD"),
    "VND": ("usd_vnd", "VNM"),
    "PKR": ("usd_pkr", "PAK"),
}

FX_RATES_SOURCE = "FRED_INR_CNY+AV_BDT_VND_PKR_MAD+WB_fallback"

COTTON_DATASETS = {
    "CHRIS/ICE_CT1": "spot",
    "CHRIS/ICE_CT2": "3m",
    "CHRIS/ICE_CT3": "6m",
}

FRED_COTTON_SERIES_ID = "PCOTTINDUSDM"
ORIGIN_COTTON_START_DATE = date(2011, 1, 1)
ORIGIN_COTTON_BACKFILL_ORIGINS = (
    "India",
    "China",
    "Pakistan",
    "Australia",
    "Brazil",
    "West Africa",
)
ORIGIN_COTTON_SOURCE = "FRED_PCOTTINDUSDM_historical_baseline"

ORIGIN_SPOT_DIFFERENTIALS_CENTS: dict[str, Decimal] = {
    "India": Decimal("-4"),
    "China": Decimal("2"),
    "Pakistan": Decimal("-6"),
    "Australia": Decimal("5"),
    "Brazil": Decimal("-1"),
    "West Africa": Decimal("-5"),
}

ORIGIN_DIFFERENTIAL_COMMIT_BATCH = 500

REQUEST_TIMEOUT = 30
ALPHA_VANTAGE_RATE_LIMIT_SLEEP_SECS = 15


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def fetch_fred_series(series_id: str) -> list[tuple[date, Decimal]]:
    if not FRED_API_KEY:
        logger.error("FRED_API_KEY is not set.")
        return []

    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "observation_start": START_DATE,
        "observation_end": END_DATE,
        "frequency": "w",
        "aggregation_method": "eop",
    }

    try:
        response = requests.get(FRED_BASE, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        observations = response.json().get("observations", [])
    except (requests.RequestException, KeyError, ValueError) as exc:
        logger.error(f"FRED fetch failed for {series_id}: {exc}")
        return []

    rows: list[tuple[date, Decimal]] = []
    for obs in observations:
        raw_value = obs.get("value")
        if raw_value in (None, ".", ""):
            continue
        try:
            rows.append((date.fromisoformat(obs["date"]), Decimal(str(raw_value))))
        except (ValueError, TypeError):
            continue

    logger.info(f"FRED {series_id}: {len(rows)} weekly observations")
    return rows


def fetch_fred_monthly_interpolated_weekly(series_id: str) -> list[tuple[date, Decimal]]:
    """Fetch monthly FRED observations; expand to weekly via linear interpolation."""
    if not FRED_API_KEY:
        logger.error("FRED_API_KEY is not set.")
        return []

    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "observation_start": START_DATE,
        "observation_end": END_DATE,
        "frequency": "m",
        "aggregation_method": "eop",
    }

    try:
        response = requests.get(FRED_BASE, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        observations = response.json().get("observations", [])
    except (requests.RequestException, KeyError, ValueError) as exc:
        logger.error(f"FRED monthly fetch failed for {series_id}: {exc}")
        return []

    monthly: list[tuple[date, Decimal]] = []
    for obs in observations:
        raw_value = obs.get("value")
        if raw_value in (None, ".", ""):
            continue
        try:
            monthly.append((date.fromisoformat(obs["date"]), Decimal(str(raw_value))))
        except (ValueError, TypeError):
            continue

    start = date.fromisoformat(START_DATE)
    end = date.fromisoformat(END_DATE)
    rows = _interpolate_annual_to_weekly(monthly, start, end)
    logger.info(
        f"FRED {series_id}: {len(monthly)} monthly → {len(rows)} weekly observations"
    )
    return rows


def fetch_alpha_vantage_fx_weekly(to_symbol: str) -> dict[date, Decimal]:
    """Fetch Alpha Vantage FX_WEEKLY USD/{to_symbol} close prices."""
    if not ALPHA_VANTAGE_KEY:
        logger.error("ALPHA_VANTAGE_KEY is not set.")
        return {}

    params = {
        "function": "FX_WEEKLY",
        "from_symbol": "USD",
        "to_symbol": to_symbol,
        "apikey": ALPHA_VANTAGE_KEY,
        "outputsize": "full",
    }

    try:
        response = requests.get(ALPHA_VANTAGE_BASE, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error(f"Alpha Vantage request failed for {to_symbol}: {exc}")
        return {}

    if "Note" in payload or "Information" in payload:
        logger.error(
            f"Alpha Vantage rate limit or info for {to_symbol}: "
            f"{payload.get('Note') or payload.get('Information')}"
        )
        return {}

    series = payload.get("Time Series FX (Weekly)")
    if not series:
        logger.error(
            f"Alpha Vantage missing weekly series for {to_symbol}: keys={list(payload.keys())}"
        )
        return {}

    data: dict[date, Decimal] = {}
    for date_str, values in series.items():
        try:
            week_date = date.fromisoformat(date_str)
            rate = Decimal(str(values["4. close"]))
        except (KeyError, TypeError, ValueError):
            continue
        data[week_date] = rate

    logger.info(f"Alpha Vantage USD/{to_symbol}: {len(data)} weekly observations")
    return data


def _pause_before_alpha_vantage_fetch(to_symbol: str) -> None:
    logger.info(
        f"Next Alpha Vantage fetch: USD/{to_symbol} — "
        f"pausing {ALPHA_VANTAGE_RATE_LIMIT_SLEEP_SECS}s to respect rate limits"
    )
    time.sleep(ALPHA_VANTAGE_RATE_LIMIT_SLEEP_SECS)


def fetch_mad_weekly() -> list[tuple[date, Decimal]]:
    """USD/MAD weekly from Alpha Vantage FX_WEEKLY (history back to ~2005)."""
    av_lookup = fetch_alpha_vantage_fx_weekly("MAD")
    if not av_lookup:
        return []
    rows = sorted(av_lookup.items(), key=lambda item: item[0])
    logger.info(f"USD/MAD Alpha Vantage: {len(rows)} weekly observations")
    return rows


def merge_av_with_worldbank_fallback(
    to_symbol: str,
    av_lookup: dict[date, Decimal],
    worldbank_country: str,
) -> list[tuple[date, Decimal]]:
    """
    USD/{to_symbol} weekly: Alpha Vantage FX_WEEKLY primary, World Bank annual
    interpolation for weeks before Alpha Vantage history begins.
    """
    wb_weekly = fetch_worldbank_series(worldbank_country)
    wb_lookup = _series_to_lookup(wb_weekly)

    if not av_lookup and not wb_lookup:
        return []

    if not av_lookup:
        logger.warning(
            f"Alpha Vantage USD/{to_symbol} unavailable — using World Bank annual only"
        )
        return wb_weekly

    av_start = min(av_lookup.keys())
    merged: dict[date, Decimal] = {
        week_date: rate for week_date, rate in wb_lookup.items() if week_date < av_start
    }
    merged.update(av_lookup)

    rows = sorted(merged.items(), key=lambda item: item[0])
    logger.info(
        f"USD/{to_symbol} combined: {len(rows)} weekly rows "
        f"(World Bank fallback before {av_start}, Alpha Vantage from {av_start})"
    )
    return rows


def _log_fx_series_samples(label: str, rows: list[tuple[date, Decimal]]) -> None:
    if not rows:
        logger.warning(f"{label}: no observations fetched")
        return
    ordered = sorted(rows, key=lambda item: item[0])
    first_three = ordered[:3]
    last_three = ordered[-3:]
    logger.info(f"{label} first 3: {first_three}")
    logger.info(f"{label} last 3: {last_three}")


def _interpolate_annual_to_weekly(
    annual: list[tuple[date, Decimal]],
    start: date,
    end: date,
) -> list[tuple[date, Decimal]]:
    if not annual:
        return []

    annual = sorted(annual, key=lambda x: x[0])
    weeks: list[tuple[date, Decimal]] = []
    current = _monday_of(start)
    end_monday = _monday_of(end)

    while current <= end_monday:
        if current <= annual[0][0]:
            value = annual[0][1]
        elif current >= annual[-1][0]:
            value = annual[-1][1]
        else:
            lower = annual[0]
            upper = annual[-1]
            for idx in range(len(annual) - 1):
                if annual[idx][0] <= current <= annual[idx + 1][0]:
                    lower = annual[idx]
                    upper = annual[idx + 1]
                    break
            span_days = (upper[0] - lower[0]).days
            if span_days == 0:
                value = lower[1]
            else:
                progress = Decimal((current - lower[0]).days) / Decimal(span_days)
                value = lower[1] + progress * (upper[1] - lower[1])

        weeks.append((current, value))
        current += timedelta(days=7)

    return weeks


def fetch_worldbank_series(country_code: str) -> list[tuple[date, Decimal]]:
    start_year = date.today().year - BACKFILL_YEARS
    end_year = date.today().year
    url = f"{WORLDBANK_BASE}/country/{country_code}/indicator/PA.NUS.FCRF"
    params = {"format": "json", "date": f"{start_year}:{end_year}", "per_page": 100}

    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
            logger.error(f"World Bank returned no data for {country_code}")
            return []
        records = payload[1]
    except (requests.RequestException, KeyError, ValueError, IndexError) as exc:
        logger.error(f"World Bank fetch failed for {country_code}: {exc}")
        return []

    annual: list[tuple[date, Decimal]] = []
    for item in records:
        raw_value = item.get("value")
        raw_year = item.get("date")
        if raw_value is None or raw_year is None:
            continue
        try:
            annual.append((date(int(raw_year), 1, 1), Decimal(str(raw_value))))
        except (ValueError, TypeError):
            continue

    weekly = _interpolate_annual_to_weekly(
        annual,
        date.fromisoformat(START_DATE),
        date.fromisoformat(END_DATE),
    )
    logger.info(f"World Bank {country_code}: {len(annual)} annual → {len(weekly)} weekly rows")
    return weekly


def fetch_nasdaq_cotton(dataset: str) -> list[tuple[date, Decimal]]:
    if not NASDAQ_API_KEY:
        logger.error("NASDAQ_API_KEY is not set.")
        return []

    url = f"{NASDAQ_BASE}/{dataset}.json"
    params = {
        "api_key": NASDAQ_API_KEY,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "collapse": "weekly",
        "column_index": 4,
    }
    headers = {"User-Agent": "ArtemisV0/1.0 (historical macro backfill)"}

    try:
        response = requests.get(
            url, params=params, headers=headers, timeout=REQUEST_TIMEOUT
        )
        if response.status_code == 403:
            logger.error(
                f"Nasdaq {dataset}: access denied (403). "
                "CHRIS datasets may require a paid plan or be blocked from this network."
            )
            return []
        response.raise_for_status()
        data = response.json()["dataset"]["data"]
    except (requests.RequestException, KeyError, ValueError) as exc:
        logger.error(f"Nasdaq fetch failed for {dataset}: {exc}")
        return []

    rows: list[tuple[date, Decimal]] = []
    for row in data:
        if len(row) < 2 or row[1] in (None, "NA", ""):
            continue
        try:
            rows.append((date.fromisoformat(row[0]), Decimal(str(row[1]))))
        except (ValueError, TypeError):
            continue

    logger.info(f"Nasdaq {dataset}: {len(rows)} weekly observations")
    return rows


def fetch_fred_pcottindusdm_weekly(
    observation_start: date,
    observation_end: date,
) -> list[tuple[date, Decimal]]:
    """Monthly global cotton price from FRED, interpolated to weekly."""
    if not FRED_API_KEY:
        return []

    params = {
        "series_id": FRED_COTTON_SERIES_ID,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "observation_start": observation_start.isoformat(),
        "observation_end": observation_end.isoformat(),
    }

    try:
        response = requests.get(FRED_BASE, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        observations = response.json().get("observations", [])
    except (requests.RequestException, KeyError, ValueError) as exc:
        logger.error(f"FRED cotton fetch failed for {FRED_COTTON_SERIES_ID}: {exc}")
        return []

    monthly: list[tuple[date, Decimal]] = []
    for obs in observations:
        raw_value = obs.get("value")
        if raw_value in (None, ".", ""):
            continue
        try:
            obs_date = date.fromisoformat(obs["date"])
            monthly.append((date(obs_date.year, obs_date.month, 1), Decimal(str(raw_value))))
        except (ValueError, TypeError):
            continue

    weekly = _interpolate_annual_to_weekly(monthly, observation_start, observation_end)
    logger.info(
        f"FRED {FRED_COTTON_SERIES_ID}: {len(monthly)} monthly → {len(weekly)} weekly "
        f"({observation_start} to {observation_end})"
    )
    return weekly


def fetch_fred_cotton_fallback() -> list[tuple[date, Decimal]]:
    """Monthly global cotton price from FRED when Nasdaq CHRIS is unavailable."""
    return fetch_fred_pcottindusdm_weekly(
        date.fromisoformat(START_DATE),
        date.fromisoformat(END_DATE),
    )


def _series_to_lookup(rows: list[tuple[date, Decimal]]) -> dict[date, Decimal]:
    return {obs_date: value for obs_date, value in rows}


def _value_on_date(
    lookup: dict[date, Decimal],
    target: date,
    sorted_dates: list[date],
) -> Optional[Decimal]:
    if target in lookup:
        return lookup[target]
    prior = [d for d in sorted_dates if d <= target]
    if prior:
        return lookup[prior[-1]]
    return None


def row_exists_fx(db: Session, target_date: date) -> bool:
    return (
        db.query(FxRates)
        .filter(
            FxRates.is_latest.is_(True),
            func.date(FxRates.pulled_at) == target_date,
        )
        .first()
        is not None
    )


def row_exists_cotton(db: Session, target_date: date) -> bool:
    return (
        db.query(Cotton)
        .filter(
            Cotton.as_of_date == target_date,
            Cotton.is_latest.is_(True),
        )
        .first()
        is not None
    )


def row_exists_cotton_origin(db: Session, origin_country: str, target_date: date) -> bool:
    return (
        db.query(Cotton)
        .filter(
            Cotton.origin_country == origin_country,
            Cotton.as_of_date == target_date,
        )
        .first()
        is not None
    )


def row_exists_futures_curve(db: Session, target_date: date) -> bool:
    return (
        db.query(CommodityFutures)
        .filter(
            CommodityFutures.as_of_date == target_date,
            CommodityFutures.is_latest.is_(True),
        )
        .first()
        is not None
    )


def backfill_fx_rates(db: Session) -> int:
    field_series: dict[str, dict[date, Decimal]] = {}

    try_rows: list[tuple[date, Decimal]] = []
    for series_id, field in FRED_SERIES.items():
        if series_id in FRED_MONTHLY_SERIES:
            rows = fetch_fred_monthly_interpolated_weekly(series_id)
            if field == "usd_try":
                try_rows = rows
        else:
            rows = fetch_fred_series(series_id)
        field_series[field] = _series_to_lookup(rows)
        time.sleep(0.5)

    logger.info("Fetching USD/MAD from Alpha Vantage FX_WEEKLY")
    mad_rows = fetch_mad_weekly()
    field_series["usd_mad"] = _series_to_lookup(mad_rows)

    _log_fx_series_samples("USD/TRY (CCUSMA02TRM618N monthly→weekly)", try_rows)
    _log_fx_series_samples("USD/MAD (Alpha Vantage FX_WEEKLY)", mad_rows)

    for to_symbol, (field, wb_country) in ALPHA_VANTAGE_WORLDBANK_FX.items():
        _pause_before_alpha_vantage_fetch(to_symbol)
        av_lookup = fetch_alpha_vantage_fx_weekly(to_symbol)
        av_rows = sorted(av_lookup.items(), key=lambda item: item[0])
        _log_fx_series_samples(f"USD/{to_symbol} (Alpha Vantage FX_WEEKLY)", av_rows)
        merged_rows = merge_av_with_worldbank_fallback(to_symbol, av_lookup, wb_country)
        field_series[field] = _series_to_lookup(merged_rows)

    inr_lookup = field_series.get("usd_inr", {})
    if not inr_lookup:
        logger.error("No INR data from FRED — cannot anchor FX backfill.")
        return 0

    anchor_dates = sorted(inr_lookup.keys())
    required_fields = list(FRED_SERIES.values()) + list(WORLDBANK_SERIES.values())
    written = 0

    for week_date in anchor_dates:
        if row_exists_fx(db, week_date):
            continue

        rates: dict[str, Optional[Decimal]] = {}
        complete = True
        for field in required_fields:
            lookup = field_series.get(field, {})
            sorted_dates = sorted(lookup.keys())
            value = _value_on_date(lookup, week_date, sorted_dates)
            if value is None:
                complete = False
                break
            rates[field] = value

        if not complete:
            continue

        mad_lookup = field_series.get("usd_mad", {})
        mad_sorted = sorted(mad_lookup.keys())
        rates["usd_mad"] = _value_on_date(mad_lookup, week_date, mad_sorted)

        record = FxRates(
            usd_inr=rates["usd_inr"],
            usd_bdt=rates["usd_bdt"],
            usd_vnd=rates["usd_vnd"],
            usd_cny=rates["usd_cny"],
            usd_try=rates["usd_try"],
            usd_mad=rates["usd_mad"],
            usd_pkr=rates["usd_pkr"],
            refresh="historical_weekly",
            pulled_at=datetime.combine(week_date, datetime.min.time()),
            source=FX_RATES_SOURCE,
        )
        db.add(record)
        db.commit()
        written += 1

        if written % 50 == 0:
            logger.info(f"Written {written} FX rows...")

    return written


def fix_try_column(db: Session) -> int:
    """Delete all fx_rates rows and rebuild from corrected FRED + World Bank + AV sources."""
    deleted = db.query(FxRates).delete()
    db.commit()
    logger.info(
        f"Deleted {deleted} fx_rates row(s). Re-running complete FX backfill "
        "(CCUSMA02TRM618N monthly→weekly for TRY, Alpha Vantage BDT/VND/PKR/MAD + WB fallback)."
    )
    written = backfill_fx_rates(db)
    logger.info(f"FX rebuild complete: {written} row(s) written")
    return written


def backfill_cotton(db: Session) -> int:
    merged: dict[date, dict[str, Decimal]] = {}
    source = "NasdaqDataLink_CHRIS_historical"

    for dataset, tenor in COTTON_DATASETS.items():
        rows = fetch_nasdaq_cotton(dataset)
        for obs_date, close in rows:
            merged.setdefault(obs_date, {})[tenor] = close
        time.sleep(0.5)

    if not merged:
        logger.warning(
            "Nasdaq CHRIS unavailable — falling back to FRED PCOTTINDUSDM "
            "(monthly global cotton, interpolated to weekly)."
        )
        source = "FRED_PCOTTINDUSDM_historical_fallback"
        fallback_rows = fetch_fred_cotton_fallback()
        for obs_date, close in fallback_rows:
            merged[obs_date] = {"spot": close, "3m": close, "6m": close}

    written = 0
    for obs_date in sorted(merged.keys()):
        if row_exists_cotton(db, obs_date):
            continue

        tenors = merged[obs_date]
        if not {"spot", "3m", "6m"}.issubset(tenors.keys()):
            continue

        spot = tenors["spot"]
        near = tenors["spot"]
        three_m = tenors["3m"]
        six_m = tenors["6m"]

        cotton_row = Cotton(
            origin_country="US",
            grade="ICE #2",
            staple_length="N/A",
            spot_price=spot,
            ice_futures_near=near,
            ice_futures_3m=three_m,
            ice_futures_6m=six_m,
            crop_year=obs_date.year,
            as_of_date=obs_date,
            source=source,
        )
        db.add(cotton_row)

        if not row_exists_futures_curve(db, obs_date):
            curve_row = CommodityFutures(
                ice_cotton_2_spot=spot,
                ice_cotton_2_3m=three_m,
                ice_cotton_2_6m=six_m,
                ice_cotton_2_9m=six_m,
                ice_cotton_2_12m=six_m,
                ocean_freight_ffa=None,
                as_of_date=obs_date,
                source=source,
            )
            db.add(curve_row)

        db.commit()
        written += 1

        if written % 50 == 0:
            logger.info(f"Written {written} cotton rows...")

    return written


def backfill_cotton_origins(db: Session) -> int:
    """Backfill per-origin cotton rows using FRED global price as a shared baseline."""
    observation_end = date.today()
    weekly_rows = fetch_fred_pcottindusdm_weekly(ORIGIN_COTTON_START_DATE, observation_end)
    if not weekly_rows:
        logger.error(
            "No FRED PCOTTINDUSDM weekly data — cannot backfill origin cotton rows."
        )
        return 0

    written = 0
    for origin in ORIGIN_COTTON_BACKFILL_ORIGINS:
        origin_written = 0
        for obs_date, spot in weekly_rows:
            if row_exists_cotton_origin(db, origin, obs_date):
                continue

            mark_latest(db, Cotton, {"origin_country": origin, "as_of_date": obs_date})
            db.add(
                Cotton(
                    origin_country=origin,
                    grade="Global benchmark placeholder",
                    staple_length="N/A",
                    spot_price=spot,
                    ice_futures_near=spot,
                    ice_futures_3m=spot,
                    ice_futures_6m=spot,
                    crop_year=obs_date.year,
                    as_of_date=obs_date,
                    source=ORIGIN_COTTON_SOURCE,
                    refresh="historical_weekly",
                    pulled_at=datetime.combine(obs_date, datetime.min.time()),
                    is_latest=True,
                )
            )
            db.commit()
            written += 1
            origin_written += 1

            if written % 100 == 0:
                logger.info(f"Written {written} origin cotton rows...")

        logger.info(f"{origin}: {origin_written} weekly cotton rows written")

    return written


def _fred_price_on_or_before(
    lookup: dict[date, Decimal],
    target: date,
) -> Optional[Decimal]:
    if not lookup:
        return None
    target_monday = _monday_of(target)
    if target_monday in lookup:
        return lookup[target_monday]
    prior_dates = [d for d in lookup if d <= target_monday]
    if prior_dates:
        return lookup[max(prior_dates)]
    return None


def apply_origin_differentials(db: Session) -> int:
    """
    Apply origin-specific spot differentials (¢/lb vs FRED global) and recalibrate
    the S/U forward curve on affected rows. US and ICE No.2 Global are unchanged.
    """
    from data.ingestion.cotton_futures_historical_backfill import (
        DEFAULT_SU_RATIO,
        build_su_calibrated_curve,
    )

    observation_end = date.today()
    fred_weekly = fetch_fred_pcottindusdm_weekly(ORIGIN_COTTON_START_DATE, observation_end)
    fred_lookup = {obs_date: price for obs_date, price in fred_weekly}
    if not fred_lookup:
        logger.error("No FRED PCOTTINDUSDM data — cannot apply origin differentials.")
        return 0

    total_updated = 0

    for origin, differential in ORIGIN_SPOT_DIFFERENTIALS_CENTS.items():
        rows = (
            db.query(Cotton)
            .filter(
                Cotton.origin_country == origin,
                Cotton.as_of_date.isnot(None),
            )
            .order_by(Cotton.as_of_date)
            .all()
        )

        origin_updated = 0
        for row in rows:
            if row.as_of_date is None:
                continue

            base_price = _fred_price_on_or_before(fred_lookup, row.as_of_date)
            if base_price is None:
                logger.warning(
                    f"{origin} cotton_id={row.cotton_id} as_of={row.as_of_date}: "
                    "no FRED baseline — skipped"
                )
                continue

            new_spot = (base_price + differential).quantize(Decimal("0.0001"))
            su = (
                Decimal(str(row.wasde_su_ratio_pct))
                if row.wasde_su_ratio_pct is not None
                else DEFAULT_SU_RATIO
            )
            curve = build_su_calibrated_curve(new_spot, su)

            row.spot_price = new_spot
            row.ice_futures_near = curve["near"]
            row.ice_futures_3m = curve["3m"]
            row.ice_futures_6m = curve["6m"]
            row.ice_futures_9m = curve["9m"]
            row.ice_futures_12m = curve["12m"]
            row.contango_signal = curve["contango_signal"]

            origin_updated += 1
            total_updated += 1

            if total_updated % ORIGIN_DIFFERENTIAL_COMMIT_BATCH == 0:
                db.commit()
                logger.info(f"Origin differentials: {total_updated} rows updated so far...")

        db.commit()
        logger.info(f"{origin}: {origin_updated} cotton row(s) updated (differential {differential} ¢/lb)")

    logger.info(f"Origin differential pass complete: {total_updated} row(s) updated")
    return total_updated


def run_backfill() -> None:
    logger.info("=" * 60)
    logger.info("HISTORICAL MACRO BACKFILL STARTING")
    logger.info(f"Date range: {START_DATE} to {END_DATE}")
    logger.info(f"Backfilling {BACKFILL_YEARS} years of history")
    logger.info("=" * 60)

    db = SessionLocal()
    try:
        logger.info("Phase 1/3: FX rates (FRED + World Bank)...")
        fx_rows = backfill_fx_rates(db)
        logger.info(f"FX backfill complete: {fx_rows} rows written")

        logger.info("Phase 2/3: Cotton futures (Nasdaq Data Link)...")
        cotton_rows = backfill_cotton(db)
        logger.info(f"Cotton backfill complete: {cotton_rows} rows written")

        logger.info("Phase 3/3: Cotton origins (FRED PCOTTINDUSDM baseline)...")
        origin_cotton_rows = backfill_cotton_origins(db)
        logger.info(f"Origin cotton backfill complete: {origin_cotton_rows} rows written")

        logger.info("Applying origin-specific spot differentials and S/U futures curve...")
        differential_rows = apply_origin_differentials(db)
        logger.info(f"Origin differentials applied: {differential_rows} rows updated")

        logger.info("=" * 60)
        logger.info("BACKFILL COMPLETE")
        logger.info(
            f"Total rows written: {fx_rows + cotton_rows + origin_cotton_rows} | "
            f"origin differentials updated: {differential_rows}"
        )
        logger.info("Run the live schedulers to keep data current going forward.")
        logger.info("=" * 60)
    except Exception as exc:
        logger.critical(f"Backfill failed: {exc}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Historical macro data backfill")
    parser.add_argument(
        "--fix-try",
        action="store_true",
        help="Delete all fx_rates rows and rebuild FX history with corrected TRY series",
    )
    args = parser.parse_args()

    if args.fix_try:
        db = SessionLocal()
        try:
            fix_try_column(db)
        except Exception as exc:
            logger.critical(f"FX TRY fix failed: {exc}", exc_info=True)
            db.rollback()
            raise SystemExit(1) from exc
        finally:
            db.close()
    else:
        run_backfill()

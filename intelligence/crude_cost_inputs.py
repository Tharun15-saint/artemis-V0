"""CrudeCostInputs — the single interface between crude oil data and the cost engine.

ARCHITECTURE RULE: Nothing in the cost engine reads the crude_oil table directly.
Every crude signal flows through this class. Direct queries are prohibited.

Signal routing:
  brent_t_minus_4w        → dyeing_chemical_premium (cost step 2)
  brent_futures_Nm        → forward_landed_cost_90day (cost step 14)
  crude_market_structure  → hedge_opportunity_recommendation

CRUDE_LINKAGE_PENDING (cost steps 7 and 11):
  Energy overhead (step 7) and ocean freight bunker surcharge (step 11) require
  empirical calibration from RRK energy invoices and Drewry WCI live data.
  Neither dataset is available. No approximations are applied.
  Dyeing premium threshold requires n≥20 validated RRK chemical cost invoice pairs.
  Currently n=0. Signal returns None with calibration_status='PENDING'.

Three entry points:
  get_spot_input(as_of_date)                            → cost step 2: dyeing chemical premium
  get_forward_input(delivery_date, as_of_date)          → cost step 14: forward landed cost
  get_dyeing_pressure(as_of_date)                       → dyeing tier alerts (PENDING calibration)

CONFIDENCE CAPS:
  Brent forward tenors (brent_futures_*) → STEO monthly forecast → confidence capped at 0.60

All methods:
  - Raise CrudeDataStaleError if latest FRED row > STALE_THRESHOLD_DAYS old
  - Log the source_row_id used on every call (audit trail)
  - Never return None silently (raise or return a well-structured dict with an error key)
"""
import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from intelligence.exceptions import CrudeDataStaleError

logger = logging.getLogger(__name__)

STALE_THRESHOLD_DAYS = 14   # crude data older than this blocks cost computations
STEO_CONFIDENCE_CAP = 0.55   # Brent forward tenor from STEO monthly forecast (government outlook)
MARKET_FORWARD_CONFIDENCE = 0.85   # Brent forward tenor from real ICE settlement (yfinance/CME)


def _staleness_check(latest_date: Optional[date], as_of: date) -> None:
    """Raise CrudeDataStaleError if latest_date is too old relative to as_of."""
    if latest_date is None:
        raise CrudeDataStaleError(
            "No crude_oil rows found (source=fred_api). "
            "Run: python -m data.ingestion.crude_oil_ingestion --run-once"
        )
    age_days = (as_of - latest_date).days
    if age_days > STALE_THRESHOLD_DAYS:
        raise CrudeDataStaleError(
            f"Crude oil data stale: latest FRED row is {latest_date} "
            f"({age_days} days old, threshold={STALE_THRESHOLD_DAYS}). "
            f"Run: python -m data.ingestion.crude_oil_ingestion --run-once"
        )


def _confidence(data_age_days: int) -> str:
    if data_age_days < 7:
        return "high"
    if data_age_days <= 14:
        return "medium"
    return "low"


class CrudeCostInputs:
    """
    Facade over the crude_oil table. Instantiate with a live db Session;
    all methods do read-only queries except for audit logging.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    # ──────────────────────────────────────────────────────────────────────────
    # Internal: fetch latest FRED row
    # ──────────────────────────────────────────────────────────────────────────

    def _get_latest_fred_row(self, as_of: date) -> dict:
        """Return the most recent fred_api crude_oil row (is_latest=True) and validate freshness."""
        row = self._db.execute(text("""
            SELECT crude_oil_id, as_of_date,
                   CAST(brent_spot AS REAL),
                   CAST(brent_rolling_4w_avg AS REAL),
                   CAST(brent_t_minus_4w AS REAL),
                   CAST(wti_spot AS REAL)
            FROM crude_oil
            WHERE source = 'fred_api' AND is_latest = 1
            ORDER BY as_of_date DESC
            LIMIT 1
        """)).fetchone()

        if row is None:
            _staleness_check(None, as_of)

        row_id, row_date, brent_spot, rolling_avg, t_minus_4w, wti_spot = row
        latest_date = row_date if isinstance(row_date, date) else date.fromisoformat(str(row_date))
        _staleness_check(latest_date, as_of)

        return {
            "crude_oil_id": row_id,
            "as_of_date": latest_date,
            "brent_spot": Decimal(str(brent_spot)) if brent_spot is not None else None,
            "brent_rolling_4w_avg": Decimal(str(rolling_avg)) if rolling_avg is not None else None,
            "brent_t_minus_4w": Decimal(str(t_minus_4w)) if t_minus_4w is not None else None,
            "wti_spot": Decimal(str(wti_spot)) if wti_spot is not None else None,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Internal: fetch nearest futures row
    # ──────────────────────────────────────────────────────────────────────────

    def _get_futures_row(self, as_of: date) -> Optional[dict]:
        """Return the most recent eia_petroleum_futures row on or before as_of."""
        row = self._db.execute(text("""
            SELECT crude_oil_id, as_of_date,
                   CAST(brent_futures_1m  AS REAL),
                   CAST(brent_futures_3m  AS REAL),
                   CAST(brent_futures_6m  AS REAL),
                   CAST(brent_futures_12m AS REAL),
                   CAST(wti_futures_1m    AS REAL),
                   CAST(wti_futures_3m    AS REAL),
                   CAST(wti_futures_6m    AS REAL),
                   CAST(wti_futures_12m   AS REAL),
                   crude_market_structure,
                   brent_futures_source,
                   brent_futures_is_market_price,
                   brent_futures_delay_minutes
            FROM crude_oil
            WHERE source = 'eia_petroleum_futures'
              AND brent_futures_1m IS NOT NULL
              AND as_of_date <= :as_of
            ORDER BY as_of_date DESC
            LIMIT 1
        """), {"as_of": as_of.isoformat()}).fetchone()

        if row is None:
            return None

        (row_id, row_date,
         b1, b3, b6, b12,
         w1, w3, w6, w12,
         structure, bf_source, bf_is_market, bf_delay) = row

        return {
            "crude_oil_id": row_id,
            "as_of_date": row_date if isinstance(row_date, date) else date.fromisoformat(str(row_date)),
            "brent_futures_1m":  Decimal(str(b1))  if b1  is not None else None,
            "brent_futures_3m":  Decimal(str(b3))  if b3  is not None else None,
            "brent_futures_6m":  Decimal(str(b6))  if b6  is not None else None,
            "brent_futures_12m": Decimal(str(b12)) if b12 is not None else None,
            "wti_futures_1m":    Decimal(str(w1))  if w1  is not None else None,
            "wti_futures_3m":    Decimal(str(w3))  if w3  is not None else None,
            "wti_futures_6m":    Decimal(str(w6))  if w6  is not None else None,
            "wti_futures_12m":   Decimal(str(w12)) if w12 is not None else None,
            "crude_market_structure": structure,
            "brent_futures_source": bf_source or "steo_forecast",
            "brent_futures_is_market_price": bool(bf_is_market) if bf_is_market is not None else False,
            "brent_futures_delay_minutes": bf_delay,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def get_spot_input(self, as_of_date: date) -> dict:
        """
        Returns current crude spot inputs for cost step 2 (dyeing chemical premium).

        Uses brent_t_minus_4w (crude price 28 days before as_of_date) and rolling_4w_avg.

        Returns:
            brent_t4w             — Brent spot 4 weeks prior (the 'crude input price')
            brent_rolling_avg     — 4-week rolling average Brent (smoothed trigger signal)
            dyeing_premium_active — None (PENDING: threshold requires RRK invoice calibration)
            source_row_id         — crude_oil_id used (for audit trail)
            as_of                 — date of the crude row used
        """
        fred = self._get_latest_fred_row(as_of_date)
        row_id = fred["crude_oil_id"]

        logger.info(
            f"CrudeCostInputs.get_spot_input as_of={as_of_date}: "
            f"source_row_id={row_id}, as_of={fred['as_of_date']}, "
            f"brent_t4w={fred['brent_t_minus_4w']}, rolling_avg={fred['brent_rolling_4w_avg']}"
        )

        if fred["brent_t_minus_4w"] is None:
            logger.warning(f"brent_t_minus_4w NULL on row {row_id}")
        if fred["brent_rolling_4w_avg"] is None:
            logger.warning(f"brent_rolling_4w_avg NULL on row {row_id}")

        return {
            "brent_t4w": fred["brent_t_minus_4w"],
            "brent_rolling_avg": fred["brent_rolling_4w_avg"],
            "dyeing_premium_active": None,
            "source_row_id": row_id,
            "as_of": fred["as_of_date"],
        }

    def get_forward_input(self, delivery_date: date, as_of_date: date) -> dict:
        """
        Returns forward crude price inputs for cost step 14 (forward_landed_cost_90day).

        Selects the correct futures tenor based on days_to_delivery:
            ≤ 30  → futures_1m
            ≤ 90  → futures_3m
            ≤ 180 → futures_6m
            > 180 → futures_12m

        BRENT FORWARD PROVENANCE:
            Confidence depends on whether the selected Brent tenor is a real market
            settlement or an EIA STEO forecast:
              - Real ICE settlement (brent_futures_is_market_price=True for the tenor)
                → confidence = MARKET_FORWARD_CONFIDENCE (0.85).
                Currently only the front-month (1m) is sourced real (Yahoo BZ=F);
                the CME path, when reachable, supplies the full 1m/3m/6m/12m curve.
              - STEO forecast (is_market_price=False, or longer tenors not yet real)
                → confidence = STEO_CONFIDENCE_CAP (0.55). STEO is a government energy
                outlook — directionally useful, not suitable for contract pricing.
            WTI tenors (wti_futures_*) use NYMEX settlement prices (RCLC1-4) which are
            exchange-traded — higher precision for short tenors (1m/3m).

        Returns:
            brent_futures          — Brent forward price at selected tenor (USD/bbl)
            wti_futures            — WTI forward price at selected tenor (USD/bbl)
            market_structure       — 'contango' / 'backwardation' / 'flat'
            tenor_used             — '1m' / '3m' / '6m' / '12m'
            brent_forward_source   — 'ice_yfinance' / 'cme_delayed' / 'steo_forecast'
            brent_forward_is_market_price — True if selected tenor is a real settlement
            confidence             — 0.85 real market, 0.55 STEO forecast
            source_row_id          — crude_oil_id used
        """
        # Staleness check against fred_api data
        self._get_latest_fred_row(as_of_date)

        days_to_delivery = (delivery_date - as_of_date).days
        if days_to_delivery <= 30:
            tenor = "1m"
        elif days_to_delivery <= 90:
            tenor = "3m"
        elif days_to_delivery <= 180:
            tenor = "6m"
        else:
            tenor = "12m"

        futures = self._get_futures_row(as_of_date)
        if futures is None:
            raise CrudeDataStaleError(
                f"No eia_petroleum_futures row found for as_of={as_of_date}. "
                "Run: python -m data.ingestion.crude_oil_petroleum_futures_ingestion"
            )

        brent_key = f"brent_futures_{tenor}"
        wti_key   = f"wti_futures_{tenor}"
        brent_fwd = futures.get(brent_key)
        wti_fwd   = futures.get(wti_key)

        # Per-tenor provenance: a row may carry a real front-month (1m) while its
        # 3m/6m/12m tenors remain STEO. Only the 1m tenor is real under the
        # ice_yfinance source; cme_delayed supplies the full real curve.
        row_is_market = futures.get("brent_futures_is_market_price", False)
        row_source = futures.get("brent_futures_source", "steo_forecast")
        if row_is_market and (tenor == "1m" or row_source == "cme_delayed"):
            forward_source = row_source
            forward_is_market = True
            confidence = MARKET_FORWARD_CONFIDENCE
        else:
            forward_source = "steo_forecast"
            forward_is_market = False
            confidence = STEO_CONFIDENCE_CAP

        logger.info(
            f"CrudeCostInputs.get_forward_input delivery={delivery_date} as_of={as_of_date}: "
            f"days_to_delivery={days_to_delivery}, tenor={tenor}, "
            f"brent_futures={brent_fwd} (source={forward_source}, market={forward_is_market}), "
            f"wti_futures={wti_fwd} (NYMEX), confidence={confidence}, "
            f"source_row_id={futures['crude_oil_id']}"
        )

        return {
            "brent_futures": brent_fwd,
            "wti_futures": wti_fwd,
            "market_structure": futures["crude_market_structure"],
            "tenor_used": tenor,
            "brent_forward_source": forward_source,
            "brent_forward_is_market_price": forward_is_market,
            "confidence": confidence,
            "source_row_id": futures["crude_oil_id"],
        }

    def get_dyeing_pressure(self, as_of_date: date) -> dict:
        """
        Returns dyeing cost pressure signals for dark-colour program alerts.

        CALIBRATION STATUS: PENDING
        dyeing_premium_active and dyeing_premium_tier are None until the dyeing premium
        threshold and transmission coefficient are empirically calibrated from RRK chemical
        cost invoices. Minimum n=20 validated invoice pairs required; currently n=0.

        Returns:
            brent_spot            — latest Brent spot price (USD/bbl), actual data
            brent_rolling_4w_avg  — 4-week rolling average Brent (USD/bbl), actual data
            brent_t_minus_4w      — Brent price 4 weeks prior (USD/bbl), actual data
            brent_as_of           — date of the crude row used
            data_age_days         — days since latest crude row
            crude_data_confidence — 'high' (<7d) / 'medium' (7-14d) / 'low' (>14d)
            dyeing_premium_active — None (PENDING calibration)
            dyeing_premium_tier   — None (PENDING calibration)
            dyeing_cost_impact_doz — None (PENDING calibration)
            calibration_status    — 'PENDING'
            calibration_note      — human-readable explanation
            source_row_id         — crude_oil_id used (audit trail)
        """
        fred = self._get_latest_fred_row(as_of_date)
        row_id = fred["crude_oil_id"]
        data_age_days = (as_of_date - fred["as_of_date"]).days
        crude_data_confidence = _confidence(data_age_days)

        logger.info(
            f"CrudeCostInputs.get_dyeing_pressure as_of={as_of_date}: "
            f"source_row_id={row_id}, brent_spot={fred['brent_spot']}, "
            f"rolling_4w={fred['brent_rolling_4w_avg']}, t_minus_4w={fred['brent_t_minus_4w']}, "
            f"data_age={data_age_days}d, crude_data_confidence={crude_data_confidence}, "
            f"calibration_status=PENDING"
        )

        return {
            "brent_spot": fred["brent_spot"],
            "brent_rolling_4w_avg": fred["brent_rolling_4w_avg"],
            "brent_t_minus_4w": fred["brent_t_minus_4w"],
            "brent_as_of": fred["as_of_date"],
            "data_age_days": data_age_days,
            "crude_data_confidence": crude_data_confidence,
            "dyeing_premium_active": None,
            "dyeing_premium_tier": None,
            "dyeing_cost_impact_doz": None,
            "calibration_status": "PENDING",
            "calibration_note": (
                "Dyeing premium threshold and transmission coefficient require empirical "
                "calibration from RRK chemical cost invoices. n=0 validated invoice pairs "
                "currently. Minimum n=20 required."
            ),
            "source_row_id": row_id,
        }


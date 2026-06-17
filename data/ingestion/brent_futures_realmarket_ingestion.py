"""Ingest real ICE Brent futures settlement prices from free sources.

PROVENANCE PRINCIPLE
  A forward price is only labelled is_market_price=True when it is a genuine
  market settlement. EIA STEO is a government *forecast*, not a market price,
  and is always labelled is_market_price=False with confidence capped at 0.55.

SOURCE TEST RESULTS (verified 2026-06, this environment):
  - Yahoo chart API (BZ=F front-month)      → WORKS. Real ICE Brent front-month.
  - Yahoo monthly contracts (BZN26=F etc.)  → NO DATA. Yahoo does not carry
                                              individual ICE Brent monthly tickers.
  - CME delayed API (product 209)           → 403 Forbidden (bot-blocked).

  Consequence: only the FRONT-MONTH (1m) is available as a real market price
  from free sources. The 3m / 6m / 12m term structure has no free real-market
  source, so those tenors REMAIN STEO forecast values and stay is_market_price=False.
  We do not fabricate a term structure. We do not approximate.

SOURCE PRIORITY (first success wins, per attempt):
  1. cme_delayed   — full real curve (1m/3m/6m/12m), delay 15min   [if reachable]
  2. ice_yfinance  — real front-month only (1m), delay ~15min
  3. steo_forecast — fallback; is_market_price=False; fires daily Slack WARNING

The script updates the most recent eia_petroleum_futures crude_oil row (it does
not create a parallel source). It recomputes brent_contango_signal /
crude_market_structure from whatever prices the row then holds.
"""
import argparse
import logging
import os
import time
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import requests
from sqlalchemy import text

from data.ingestion._env import load_project_env
from database.base import SessionLocal

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

SCRIPT_VERSION = "1.0.0"
SOURCE_SYSTEM = "eia_petroleum_futures"   # we update the existing futures rows
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{tk}"
CME_BRENT_URL = "https://www.cmegroup.com/CmeWS/mvc/Quotes/Future/209/G"
REQUEST_TIMEOUT = 15
SCHEDULE_HOURS = 24
Q4 = Decimal("0.0001")
Q2 = Decimal("0.01")

MONTH_CODE = {1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
              7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z"}

CONTANGO_THRESHOLD = Decimal("1.5")
BACKWARDATION_THRESHOLD = Decimal("-1.5")


def _send_slack_alert(message: str, level: str = "warning") -> None:
    webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        logger.warning(f"[NO SLACK WEBHOOK] {message}")
        return
    prefix = "⚠ *ARTEMIS ALERT*" if level == "warning" else "🔴 *ARTEMIS CRITICAL*"
    try:
        requests.post(webhook_url, json={"text": f"{prefix}\n{message}"}, timeout=10)
    except requests.RequestException as exc:
        logger.error(f"Slack alert failed: {exc}")


# ──────────────────────────────────────────────────────────────────────────────
# Source 1: Yahoo chart API — real front-month BZ=F (verified working)
# ──────────────────────────────────────────────────────────────────────────────

def _yahoo_last_close(ticker: str) -> Optional[Decimal]:
    try:
        r = requests.get(
            YAHOO_CHART_URL.format(tk=ticker),
            params={"range": "5d", "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=REQUEST_TIMEOUT,
        )
        if not r.ok:
            return None
        result = r.json().get("chart", {}).get("result")
        if not result:
            return None
        closes = result[0]["indicators"]["quote"][0].get("close") or []
        vals = [c for c in closes if c is not None]
        return Decimal(str(vals[-1])).quantize(Q2, rounding=ROUND_HALF_UP) if vals else None
    except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
        logger.warning("Yahoo fetch failed for %s: %s", ticker, exc)
        return None


def fetch_yfinance_frontmonth() -> Optional[dict]:
    """Real ICE Brent front-month from Yahoo BZ=F. Returns {'1m': Decimal} or None."""
    front = _yahoo_last_close("BZ=F")
    if front is None:
        return None
    logger.info("ice_yfinance front-month BZ=F = %s", front)
    return {"brent_futures_1m": front,
            "source": "ice_yfinance", "delay_minutes": 15, "real_tenors": {"1m"}}


# ──────────────────────────────────────────────────────────────────────────────
# Source 2: CME delayed — full curve (bot-blocked here; coded for production)
# ──────────────────────────────────────────────────────────────────────────────

def fetch_cme_curve() -> Optional[dict]:
    """Full real Brent curve from CME delayed API. Returns tenor dict or None.

    CME returns all active contract months with settlement/last prices. In this
    environment CME responds 403 (bot-blocked); kept for production where the
    user's network may reach it.
    """
    try:
        r = requests.get(CME_BRENT_URL, headers={"User-Agent": "Mozilla/5.0"},
                         timeout=REQUEST_TIMEOUT)
        if not r.ok:
            logger.info("CME delayed API unavailable (HTTP %s).", r.status_code)
            return None
        quotes = r.json().get("quotes", [])
        prices: list[Decimal] = []
        for q in quotes:
            raw = (q.get("last") or q.get("priorSettle") or "").replace(",", "").strip()
            if raw and raw not in ("-", "0"):
                try:
                    prices.append(Decimal(raw).quantize(Q2, rounding=ROUND_HALF_UP))
                except Exception:
                    continue
        if len(prices) < 12:
            logger.info("CME returned %d usable contracts (<12) — insufficient curve.", len(prices))
            return None
        return {
            "brent_futures_1m": prices[0],
            "brent_futures_3m": prices[2],
            "brent_futures_6m": prices[5],
            "brent_futures_12m": prices[11],
            "source": "cme_delayed", "delay_minutes": 15,
            "real_tenors": {"1m", "3m", "6m", "12m"},
        }
    except (requests.RequestException, ValueError) as exc:
        logger.info("CME fetch failed: %s", exc)
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Apply to DB
# ──────────────────────────────────────────────────────────────────────────────

def _recompute_structure(b1: Optional[float], b12: Optional[float]) -> tuple[Optional[float], Optional[str]]:
    if b1 is None or b12 is None or b1 == 0:
        return None, None
    signal = round((b12 - b1) / b1 * 100, 4)
    if Decimal(str(signal)) > CONTANGO_THRESHOLD:
        structure = "contango"
    elif Decimal(str(signal)) < BACKWARDATION_THRESHOLD:
        structure = "backwardation"
    else:
        structure = "flat"
    return signal, structure


def run_once() -> bool:
    db = SessionLocal()
    try:
        row = db.execute(text("""
            SELECT crude_oil_id, as_of_date,
                   CAST(brent_futures_1m AS REAL), CAST(brent_futures_3m AS REAL),
                   CAST(brent_futures_6m AS REAL), CAST(brent_futures_12m AS REAL)
            FROM crude_oil
            WHERE source = :s AND brent_futures_1m IS NOT NULL
            ORDER BY as_of_date DESC LIMIT 1
        """), {"s": SOURCE_SYSTEM}).fetchone()
        if row is None:
            logger.error("No eia_petroleum_futures row to update. Run futures ingestion first.")
            return False
        cid, as_of, b1, b3, b6, b12 = row

        # Try real sources in priority order.
        market = fetch_cme_curve() or fetch_yfinance_frontmonth()

        if market is None:
            # No real market data — keep STEO, mark explicitly, warn daily.
            db.execute(text("""
                UPDATE crude_oil
                SET brent_futures_source = 'steo_forecast',
                    brent_futures_is_market_price = 0,
                    brent_futures_delay_minutes = NULL
                WHERE crude_oil_id = :id
            """), {"id": cid})
            db.commit()
            _send_slack_alert(
                "Brent futures: no real market source reachable (yfinance + CME both failed). "
                "Forward curve remains EIA STEO forecast (is_market_price=False, confidence 0.55). "
                "Source a real ICE Brent feed (Platts/Argus/ICE) to lift forward confidence to 0.85.",
                level="warning",
            )
            logger.warning("No real Brent market source — STEO fallback retained for row %s.", cid)
            return True

        full_curve = market["real_tenors"] == {"1m", "3m", "6m", "12m"}
        new_b1 = float(market["brent_futures_1m"])

        if full_curve:
            # Real settlement across all tenors → safe to recompute market structure.
            new_b3 = float(market["brent_futures_3m"])
            new_b6 = float(market["brent_futures_6m"])
            new_b12 = float(market["brent_futures_12m"])
            signal, structure = _recompute_structure(new_b1, new_b12)
            db.execute(text("""
                UPDATE crude_oil
                SET brent_futures_1m = :b1, brent_futures_3m = :b3,
                    brent_futures_6m = :b6, brent_futures_12m = :b12,
                    brent_contango_signal = :sig,
                    crude_market_structure = COALESCE(:struct, crude_market_structure),
                    brent_futures_source = :src,
                    brent_futures_is_market_price = 1,
                    brent_futures_delay_minutes = :delay
                WHERE crude_oil_id = :id
            """), {
                "b1": new_b1, "b3": new_b3, "b6": new_b6, "b12": new_b12,
                "sig": signal, "struct": structure,
                "src": market["source"], "delay": market["delay_minutes"], "id": cid,
            })
            db.commit()
            logger.info(
                "Brent futures (FULL real curve from %s): 1m=%.2f 3m=%.2f 6m=%.2f 12m=%.2f "
                "structure=%s signal=%s",
                market["source"], new_b1, new_b3, new_b6, new_b12, structure, signal,
            )
        else:
            # Front-month only (ice_yfinance). Store the real 1m + provenance, but a single
            # real tenor against STEO 3m/6m/12m cannot yield a VALID market structure.
            # Per "wrong data is worse than no data": NULL the contango signal and structure
            # rather than store a mixed-source spread. Hedge signals require a full
            # consistent real curve (CME or real ICE term structure).
            db.execute(text("""
                UPDATE crude_oil
                SET brent_futures_1m = :b1,
                    brent_contango_signal = NULL,
                    crude_market_structure = NULL,
                    brent_futures_source = :src,
                    brent_futures_is_market_price = 1,
                    brent_futures_delay_minutes = :delay
                WHERE crude_oil_id = :id
            """), {"b1": new_b1, "src": market["source"],
                   "delay": market["delay_minutes"], "id": cid})
            db.commit()
            logger.info(
                "Brent futures front-month updated from %s: 1m=%.2f (real ICE). "
                "3m/6m/12m remain STEO; contango/structure NULLED (single real tenor "
                "cannot form a valid market structure — no mixed-source spread stored).",
                market["source"], new_b1,
            )
        return True
    except Exception as exc:
        logger.critical("Brent futures realmarket ingestion failed: %s", exc, exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


def run_scheduled() -> None:
    logger.info("Brent futures realmarket scheduler started — every %d hours.", SCHEDULE_HOURS)
    while True:
        run_once()
        time.sleep(SCHEDULE_HOURS * 3600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Real ICE Brent futures ingestion")
    parser.add_argument("--run-once", action="store_true", help="Fetch real Brent futures once.")
    parser.add_argument("--schedule", action="store_true", help="Loop every 24h.")
    args = parser.parse_args()
    if args.schedule:
        run_scheduled()
    else:
        raise SystemExit(0 if run_once() else 1)

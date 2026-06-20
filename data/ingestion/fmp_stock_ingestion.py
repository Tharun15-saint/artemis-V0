"""
Ingest daily SPLIT-ADJUSTED PRICE-RETURN OHLCV from FMP into retailer_stock_prices, for every
tracked retailer.

Why this replaces the prior WMT website export: that export was Yahoo-style "Adjusted Close" —
i.e. TOTAL-RETURN prices (split AND dividends) paired with RAW (unadjusted) volume. That mixed
basis means the historical "close" is not the price Walmart actually traded at, and price×volume
mixes two bases. FMP's historical-price-eod/full returns split-adjusted PRICE-return OHLC plus
split-adjusted volume — one consistent, comparable basis (so is_split_adjusted=true is finally
accurate) — and it covers all 12 tickered retailers (only WMT had any stock data before).

Per retailer:  FMP fetch  ->  capture raw bytes to immutable L1  ->  supersede existing is_latest
               ->  bulk insert.  Idempotent; safe per-retailer (one failure doesn't touch others).

    .venv/bin/python -m data.ingestion.fmp_stock_ingestion
    .venv/bin/python -m data.ingestion.fmp_stock_ingestion --ticker WMT --from 2009-01-01
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import date

import requests
from sqlalchemy import text

from data.ingestion._env import load_project_env
from data.raw.capture import capture_bytes, finish_run, start_run
from database.base import SessionLocal

load_project_env()
logger = logging.getLogger("fmp_stock_ingestion")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

_ENDPOINT = "https://financialmodelingprep.com/stable/historical-price-eod/full"
DEFAULT_FROM = "2009-01-01"
SOURCE = "fmp_historical_price_eod"


def _retailers(db, ticker):
    sql = "SELECT retailer_id, ticker FROM major_retailers WHERE ticker IS NOT NULL AND ticker<>''"
    if ticker:
        sql += " AND ticker=:t"
    sql += " ORDER BY retailer_id"
    return db.execute(text(sql), {"t": ticker.upper()} if ticker else {}).fetchall()


def _num(v):
    return None if v in (None, "") else v


def ingest_retailer(db, run, rid, ticker, key, start_from, to) -> dict:
    url = f"{_ENDPOINT}?symbol={ticker}&from={start_from}&to={to}&apikey={key}"
    r = requests.get(url, timeout=60)
    if r.status_code != 200 or not r.content:
        logger.warning("no FMP data %s (status %s)", ticker, r.status_code)
        return {"ticker": ticker, "rows": 0}
    data = r.json()
    rows = data if isinstance(data, list) else data.get("historical", [])
    if not rows:
        logger.warning("empty FMP series %s", ticker)
        return {"ticker": ticker, "rows": 0}

    # L1: preserve the exact bytes we paid for (key stripped from the recorded URI).
    capture_bytes(
        db, run, r.content,
        artifact_kind="fmp_stock_eod",
        source_system="fmp",
        media_type="application/json",
        source_uri=f"{_ENDPOINT}?symbol={ticker}&from={start_from}&to={to}",
        source_locator={"symbol": ticker, "from": start_from, "to": to},
    )

    payload = []
    for x in rows:
        d = x.get("date")
        if not d or x.get("close") is None:
            continue
        payload.append({
            "rid": rid, "tic": ticker, "d": d[:10],
            "o": _num(x.get("open")), "h": _num(x.get("high")), "l": _num(x.get("low")),
            "c": _num(x.get("close")), "vwap": _num(x.get("vwap")), "vol": _num(x.get("volume")),
            "pct": None,
        })
    if not payload:
        return {"ticker": ticker, "rows": 0}
    # DAY-OVER-DAY return = (close − prior close) / prior close. (FMP's changePercent is the
    # INTRADAY close-vs-open move, not what this column means — compute it ourselves from close.)
    payload.sort(key=lambda p: p["d"])
    prev = None
    for p in payload:
        if prev not in (None, 0) and p["c"] is not None:
            p["pct"] = round((float(p["c"]) - float(prev)) / float(prev) * 100, 4)
        prev = p["c"] if p["c"] is not None else prev

    # supersede the prior series for this retailer, then insert the fresh one
    db.execute(text("UPDATE retailer_stock_prices SET is_latest=False, updated_at=now() "
                    "WHERE retailer_id=:r AND is_latest"), {"r": rid})
    db.execute(text(
        "INSERT INTO retailer_stock_prices (retailer_id, ticker, price_date, open_price, high_price, "
        "low_price, close_price, vwap, volume, pct_change, is_split_adjusted, source, data_source_url, "
        "data_quality, is_latest) VALUES (:rid, :tic, :d, :o, :h, :l, :c, :vwap, :vol, :pct, true, "
        "'fmp_historical_price_eod', :url, 'split-adjusted price-return OHLCV + split-adjusted volume (FMP)', true)"),
        [{**p, "url": f"{_ENDPOINT}?symbol={ticker}"} for p in payload])
    db.commit()
    dates = [p["d"] for p in payload]
    logger.info("%s: %d rows %s..%s", ticker, len(payload), min(dates), max(dates))
    return {"ticker": ticker, "rows": len(payload), "min": min(dates), "max": max(dates)}


def main() -> int:
    p = argparse.ArgumentParser(description="Ingest FMP split-adjusted daily OHLCV for retailers")
    p.add_argument("--ticker")
    p.add_argument("--from", dest="start_from", default=DEFAULT_FROM)
    args = p.parse_args()
    key = os.getenv("FMP_API_KEY")
    if not key:
        print("FMP_API_KEY not set")
        return 1
    to = date.today().isoformat()
    db = SessionLocal()
    run = start_run(db, source_system="fmp", run_kind="fmp_stock_ingestion",
                    parameters={"from": args.start_from, "to": to, "ticker": args.ticker},
                    operator=f"cli:{os.getenv('USER', 'unknown')}")
    results = []
    try:
        for rid, ticker in _retailers(db, args.ticker):
            try:
                results.append(ingest_retailer(db, run, rid, ticker, key, args.start_from, to))
            except Exception as exc:                      # noqa: BLE001 — isolate per retailer
                db.rollback()
                logger.exception("FAILED %s: %s", ticker, exc)
            time.sleep(0.3)
        finish_run(db, run, status="completed")
        total = sum(x["rows"] for x in results)
        print(f"\n✓ FMP stock ingestion: {total} rows across {sum(1 for x in results if x['rows'])} retailer(s)")
        return 0
    except Exception as exc:                               # noqa: BLE001
        finish_run(db, run, status="failed", error_message=str(exc))
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

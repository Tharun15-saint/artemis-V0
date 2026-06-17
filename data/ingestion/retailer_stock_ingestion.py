"""
Daily equity OHLCV ingestion for retailers in the retail intelligence layer.

Loads the market's forward view of a retailer (stock price) so it can be
cross-referenced against fundamentals (retailer_financials), demand signals,
and earnings-call intelligence (retailer_intelligence_extract) by retailer_id.

Primary source format: daily OHLCV table exported per retailer (date, open,
high, low, close, VWAP, volume, % change). Prices are split-adjusted by the
vendor. Tail columns from the export ($ change / trade value / trade count) are
intentionally NOT ingested — they are not cleanly separable in the source and
are derivable, so storing them would fabricate precision we do not have.

Append-only discipline: one is_latest=True row per (retailer_id, price_date).
Re-ingesting a day demotes the prior row rather than overwriting it.

Run:
  - Seed load (committed CSV):  python -m data.ingestion.retailer_stock_ingestion --ticker WMT
  - Re-parse from a PDF export: python -m data.ingestion.retailer_stock_ingestion --ticker WMT --pdf "/path/to/export.pdf"
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from database.base import SessionLocal
from database.models.retail import MajorRetailers, RetailerStockPrices

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

_SEED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "seeds",
    "market_data",
)

# Each retailer's daily OHLCV export. Add new tickers here as their data lands.
TICKER_SEEDS: dict[str, dict[str, str]] = {
    "WMT": {
        "seed_csv": "walmart_stock_prices.csv",
        "source": "Stock historical data export (split-adjusted)",
        "data_source_url": "vendor:walmart_stock_historical_export",
    },
}

# Vendor anomalies we accept-but-flag rather than impute or drop.
# open/vwap can fall marginally outside the day's [low, high] range due to
# pre-market prints or the vendor's VWAP windowing.
_OHLC_TOLERANCE = Decimal("0.01")

_PDF_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s+(N/A|[\d.]+)\s+(N/A|[\d.]+)\s+(N/A|[\d.]+)\s+"
    r"([\d.]+)\s+(N/A|[\d.]+)\s+(--|[\d.]+[mb])\s+(--|-?[\d.]+%)"
)


def _dec(value: Optional[str], places: int = 4) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal(10) ** -places)
    except (InvalidOperation, ValueError):
        return None


def _int(value: Optional[str]) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _parse_volume(token: str) -> Optional[int]:
    """'26.18m' → 26180000, '1.2b' → 1200000000, '--' → None."""
    if token == "--":
        return None
    mult = 1
    if token.endswith("m"):
        mult, token = 1_000_000, token[:-1]
    elif token.endswith("b"):
        mult, token = 1_000_000_000, token[:-1]
    try:
        return int(float(token) * mult)
    except ValueError:
        return None


def parse_pdf(pdf_path: str) -> list[dict]:
    """Parse a daily-OHLCV PDF export into normalized row dicts."""
    try:
        import pypdf
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("pypdf is required to parse PDF exports: pip install pypdf") from exc

    reader = pypdf.PdfReader(pdf_path)
    rows: list[dict] = []
    for page in reader.pages:
        for line in page.extract_text().splitlines():
            m = _PDF_LINE_RE.match(line.strip())
            if not m:
                continue

            def _clean(x: str) -> str:
                return "" if x in ("N/A", "--") else x

            rows.append(
                {
                    "price_date": m.group(1),
                    "open_price": _clean(m.group(2)),
                    "high_price": _clean(m.group(3)),
                    "low_price": _clean(m.group(4)),
                    "close_price": m.group(5),
                    "vwap": _clean(m.group(6)),
                    "volume": str(_parse_volume(m.group(7)) or ""),
                    "pct_change": _clean(m.group(8)).rstrip("%"),
                }
            )
    rows.sort(key=lambda d: d["price_date"])
    return rows


def parse_csv(csv_path: str) -> list[dict]:
    with open(csv_path, newline="") as fh:
        return list(csv.DictReader(fh))


def _assess_quality(
    open_p: Optional[Decimal],
    high_p: Optional[Decimal],
    low_p: Optional[Decimal],
    close_p: Optional[Decimal],
    vwap: Optional[Decimal],
) -> Optional[str]:
    """Return a data-quality flag string for vendor anomalies, else None.
    Never mutates the values — observation only.
    """
    if high_p is None or low_p is None:
        return "partial_ohlc"
    flags: list[str] = []
    if low_p > high_p:
        flags.append("low>high")
    for name, val in (("open", open_p), ("close", close_p), ("vwap", vwap)):
        if val is not None and not (low_p - _OHLC_TOLERANCE <= val <= high_p + _OHLC_TOLERANCE):
            flags.append(f"{name}_outside_range")
    return ";".join(flags) if flags else None


def _normalize_row(raw: dict, retailer_id: int, ticker: str, cfg: dict) -> Optional[dict]:
    try:
        price_date = date.fromisoformat(raw["price_date"])
    except (ValueError, KeyError):
        logger.warning("Skipping row with bad date: %r", raw.get("price_date"))
        return None

    open_p = _dec(raw.get("open_price"))
    high_p = _dec(raw.get("high_price"))
    low_p = _dec(raw.get("low_price"))
    close_p = _dec(raw.get("close_price"))
    vwap = _dec(raw.get("vwap"))

    if close_p is None:
        logger.warning("Skipping %s — no close price", price_date)
        return None

    return {
        "retailer_id": retailer_id,
        "ticker": ticker,
        "price_date": price_date,
        "open_price": open_p,
        "high_price": high_p,
        "low_price": low_p,
        "close_price": close_p,
        "vwap": vwap,
        "volume": _int(raw.get("volume")),
        "pct_change": _dec(raw.get("pct_change"), places=4),
        "is_split_adjusted": True,
        "data_quality": _assess_quality(open_p, high_p, low_p, close_p, vwap),
        "source": cfg["source"],
        "data_source_url": cfg["data_source_url"],
        "is_latest": True,
    }


def _resolve_retailer_id(db: Session, ticker: str) -> Optional[int]:
    retailer = (
        db.query(MajorRetailers)
        .filter(MajorRetailers.ticker == ticker.upper())
        .first()
    )
    if retailer is None:
        logger.error(
            "No retailer in major_retailers with ticker=%s — seed it first.", ticker
        )
        return None
    return retailer.retailer_id


def ingest_ticker(
    db: Session,
    ticker: str,
    pdf_path: Optional[str] = None,
) -> dict[str, int]:
    """Load one retailer's daily OHLCV series, append-only with is_latest demotion."""
    ticker = ticker.upper()
    cfg = TICKER_SEEDS.get(ticker)
    if cfg is None:
        raise SystemExit(f"No stock-data config registered for ticker {ticker}")

    retailer_id = _resolve_retailer_id(db, ticker)
    if retailer_id is None:
        return {"parsed": 0, "written": 0, "demoted": 0, "skipped": 0, "flagged": 0}

    if pdf_path:
        logger.info("Parsing PDF export: %s", pdf_path)
        raw_rows = parse_pdf(pdf_path)
        cfg = {**cfg, "data_source_url": f"file:{os.path.basename(pdf_path)}"}
    else:
        seed_path = os.path.join(_SEED_DIR, cfg["seed_csv"])
        logger.info("Loading seed CSV: %s", seed_path)
        raw_rows = parse_csv(seed_path)

    summary = {"parsed": len(raw_rows), "written": 0, "demoted": 0, "skipped": 0, "flagged": 0}

    # Pre-load existing is_latest dates for this retailer to demote precisely.
    existing_latest = {
        row.price_date: row
        for row in (
            db.query(RetailerStockPrices)
            .filter(
                RetailerStockPrices.retailer_id == retailer_id,
                RetailerStockPrices.is_latest.is_(True),
            )
            .all()
        )
    }

    for raw in raw_rows:
        norm = _normalize_row(raw, retailer_id, ticker, cfg)
        if norm is None:
            summary["skipped"] += 1
            continue
        if norm["data_quality"]:
            summary["flagged"] += 1

        prior = existing_latest.get(norm["price_date"])
        if prior is not None:
            prior.is_latest = False
            summary["demoted"] += 1

        db.add(RetailerStockPrices(**norm))
        summary["written"] += 1

    db.commit()
    return summary


def run_once(ticker: str, pdf_path: Optional[str] = None) -> bool:
    db = SessionLocal()
    try:
        summary = ingest_ticker(db, ticker, pdf_path)
        logger.info(
            "Stock ingestion %s — parsed=%d written=%d demoted=%d skipped=%d flagged=%d",
            ticker,
            summary["parsed"],
            summary["written"],
            summary["demoted"],
            summary["skipped"],
            summary["flagged"],
        )
        print(
            f"{ticker}: wrote {summary['written']}/{summary['parsed']} daily bars "
            f"({summary['demoted']} demoted, {summary['skipped']} skipped, "
            f"{summary['flagged']} quality-flagged)"
        )
        return summary["written"] > 0
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest daily equity OHLCV for a retailer into the retail layer."
    )
    parser.add_argument(
        "--ticker", required=True, help="Retailer ticker (e.g. WMT). Must exist in major_retailers."
    )
    parser.add_argument(
        "--pdf", help="Optional path to a PDF export to re-parse instead of the committed seed CSV."
    )
    args = parser.parse_args()

    success = run_once(args.ticker, args.pdf)
    raise SystemExit(0 if success else 1)

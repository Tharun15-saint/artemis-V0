"""
Fetch public financial data from SEC EDGAR for major US apparel retailers
and write to major_retailers and demand_signals tables.
"""

import argparse
import logging
import re
import time
from decimal import Decimal
from typing import Any, Optional, Union

import requests
from sqlalchemy import func
from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from database.database import SessionLocal
from database.models.retail import DemandSignals, MajorRetailers

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

RETAILERS = [
    {"name": "Target Corporation", "cik": "0000027419", "ticker": "TGT"},
    {"name": "Walmart Inc", "cik": "0000104169", "ticker": "WMT"},
    {"name": "TJX Companies", "cik": "0000109198", "ticker": "TJX"},
    {"name": "Burlington Coat Factory", "cik": "0001579298", "ticker": "BURL"},
    {"name": "Ross Stores", "cik": "0000745732", "ticker": "ROST"},
    {"name": "Kohls Corporation", "cik": "0000885639", "ticker": "KSS"},
    {"name": "Macys Inc", "cik": "0000794367", "ticker": "M"},
    {"name": "Gap Inc", "cik": "0000039911", "ticker": "GPS"},
    {"name": "PVH Corp", "cik": "0000078239", "ticker": "PVH"},
    {"name": "Amazon", "cik": "0001018724", "ticker": "AMZN"},
]

XBRL_CONCEPTS = {
    "revenues": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ],
    "gross_profit": ["GrossProfit"],
    "cogs": [
        "CostOfGoodsSold",
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
    ],
    "inventory": ["InventoryNet"],
    "store_count": ["NumberOfStores", "NumberOfOperatedStores"],
    "net_income": ["NetIncomeLoss"],
    "operating_income": ["OperatingIncomeLoss"],
}

APPAREL_REVENUE_CONCEPTS = [
    "SalesRevenueGoodsNet",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
]

GUIDANCE_PATTERNS = [
    "we expect",
    "we anticipate",
    "outlook",
    "guidance",
    "next quarter",
    "full year",
    "fiscal year",
    "inventory levels",
    "gross margin",
    "comparable store",
    "we are pleased",
    "headwinds",
    "tailwinds",
]

SEC_USER_AGENT = "ArtemisV0/1.0 (retail intelligence ingestion; contact@artemis.local)"
SEC_RATE_LIMIT_SECONDS = 0.1
REQUEST_TIMEOUT = 30
SOURCE_LABEL = "SEC EDGAR XBRL API"
NO_GUIDANCE_TEXT = "No forward guidance extracted from latest filing."

_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_FILING_DOC_URL = (
    "https://www.sec.gov/Archives/edgar/data/{cik_num}/{accession}/{document}"
)

_QUARTER_FRAME_RE = re.compile(r"^CY(\d{4})Q([1-4])")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _sec_get(url: str) -> Optional[Union[dict[str, Any], str]]:
    """Rate-limited GET against SEC EDGAR."""
    headers = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        time.sleep(SEC_RATE_LIMIT_SECONDS)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "json" in content_type or url.endswith(".json"):
            return response.json()
        return response.text
    except requests.RequestException as exc:
        logger.warning("SEC request failed for %s: %s", url, exc)
        return None
    except ValueError as exc:
        logger.warning("SEC response parse failed for %s: %s", url, exc)
        return None


def _parse_quarter_frame(frame: str) -> Optional[tuple[int, int]]:
    match = _QUARTER_FRAME_RE.match(frame.rstrip("I"))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _prior_quarter(year: int, quarter: int) -> tuple[int, int]:
    if quarter == 1:
        return year - 1, 4
    return year, quarter - 1


def _same_quarter_prior_year(year: int, quarter: int) -> tuple[int, int]:
    return year - 1, quarter


def _quarters_back(
    year: int, quarter: int, count: int
) -> list[tuple[int, int]]:
    keys: list[tuple[int, int]] = []
    cy, cq = year, quarter
    for _ in range(count):
        keys.append((cy, cq))
        cy, cq = _prior_quarter(cy, cq)
    return keys


def _safe_div(numerator: Decimal, denominator: Decimal) -> Optional[Decimal]:
    if denominator == 0:
        return None
    return numerator / denominator


def extract_quarterly_values(
    us_gaap: dict[str, Any],
    concept_names: list[str],
    *,
    instant: bool = False,
) -> dict[tuple[int, int], Decimal]:
    """
    Extract quarterly values keyed by (calendar_year, quarter).
    Prefers XBRL frame tags (CY2026Q1 / CY2026Q1I), then 10-Q fp tags.
    Merges across all concept fallbacks so stale primary tags do not block fresher ones.
    """
    merged: dict[tuple[int, int], Decimal] = {}

    for concept_name in concept_names:
        concept = us_gaap.get(concept_name)
        if not concept:
            continue

        units = concept.get("units", {})
        entries: list[dict[str, Any]] = []
        for unit_values in units.values():
            if isinstance(unit_values, list):
                entries.extend(unit_values)

        for entry in entries:
            frame = entry.get("frame") or ""
            quarter_key: Optional[tuple[int, int]] = None

            if frame:
                is_instant = frame.endswith("I")
                if instant and not is_instant:
                    continue
                if not instant and is_instant:
                    continue
                quarter_key = _parse_quarter_frame(frame)
            elif entry.get("form") == "10-Q" and entry.get("fp") in (
                "Q1",
                "Q2",
                "Q3",
                "Q4",
            ):
                end = entry.get("end")
                fp = entry.get("fp")
                if end is not None and fp is not None:
                    try:
                        quarter_key = (int(str(end)[:4]), int(fp[1]))
                    except ValueError:
                        quarter_key = None

            if quarter_key is None or entry.get("val") is None:
                continue
            if quarter_key not in merged:
                merged[quarter_key] = Decimal(str(entry["val"]))

    if not merged:
        logger.warning("No quarterly data for concepts: %s", concept_names)

    return merged


def _latest_quarter(
    *series: dict[tuple[int, int], Decimal],
) -> Optional[tuple[int, int]]:
    keys: set[tuple[int, int]] = set()
    for data in series:
        keys.update(data.keys())
    if not keys:
        return None
    return max(keys)


def _sum_cogs_trailing_four(
    cogs: dict[tuple[int, int], Decimal],
    as_of: tuple[int, int],
) -> Optional[Decimal]:
    consecutive_keys = _quarters_back(as_of[0], as_of[1], 4)
    if all(key in cogs for key in consecutive_keys):
        return sum(cogs[key] for key in consecutive_keys)

    available = sorted(
        [key for key in cogs if key <= as_of],
        reverse=True,
    )
    if len(available) >= 4:
        logger.warning(
            "Using last 4 available COGS quarters (non-consecutive) ending at %s",
            as_of,
        )
        return sum(cogs[key] for key in available[:4])

    if available:
        partial = sum(cogs[key] for key in available[:4])
        logger.warning(
            "Annualizing %d COGS quarter(s) for turnover at %s",
            min(4, len(available)),
            as_of,
        )
        return partial * Decimal("4") / Decimal(str(len(available[:4])))

    return None


def compute_gross_margin_pct(
    gross_profit: Decimal,
    revenues: Decimal,
) -> Optional[Decimal]:
    ratio = _safe_div(gross_profit, revenues)
    if ratio is None:
        return None
    return ratio * Decimal("100")


def compute_inventory_turnover(
    cogs: dict[tuple[int, int], Decimal],
    inventory: dict[tuple[int, int], Decimal],
    as_of: tuple[int, int],
) -> Optional[Decimal]:
    cogs_annual = _sum_cogs_trailing_four(cogs, as_of)
    if cogs_annual is None:
        return None
    inv = inventory.get(as_of)
    if inv is None or inv == 0:
        logger.warning("Missing inventory for quarter %s", as_of)
        return None
    return _safe_div(cogs_annual, inv)


def compute_revenue_growth_pct(
    revenues: dict[tuple[int, int], Decimal],
    current: tuple[int, int],
) -> Optional[Decimal]:
    prior_year = _same_quarter_prior_year(*current)
    current_rev = revenues.get(current)
    prior_rev = revenues.get(prior_year)
    if current_rev is None or prior_rev is None or prior_rev == 0:
        logger.warning(
            "Cannot compute YoY revenue growth for %s (prior %s missing)",
            current,
            prior_year,
        )
        return None
    return (current_rev / prior_rev - Decimal("1")) * Decimal("100")


def compute_turnover_change_pct(
    cogs: dict[tuple[int, int], Decimal],
    inventory: dict[tuple[int, int], Decimal],
    current: tuple[int, int],
) -> Optional[Decimal]:
    turnover_current = compute_inventory_turnover(cogs, inventory, current)
    prior_q = _prior_quarter(*current)
    turnover_prior = compute_inventory_turnover(cogs, inventory, prior_q)
    if turnover_current is None or turnover_prior is None or turnover_prior == 0:
        return None
    return (turnover_current / turnover_prior - Decimal("1")) * Decimal("100")


def compute_margin_change_pct(
    gross_profit: dict[tuple[int, int], Decimal],
    revenues: dict[tuple[int, int], Decimal],
    current: tuple[int, int],
) -> Optional[Decimal]:
    prior_year = _same_quarter_prior_year(*current)
    for quarter in (current, prior_year):
        if quarter not in gross_profit or quarter not in revenues:
            return None
    margin_current = compute_gross_margin_pct(
        gross_profit[current], revenues[current]
    )
    margin_prior = compute_gross_margin_pct(
        gross_profit[prior_year], revenues[prior_year]
    )
    if margin_current is None or margin_prior is None:
        return None
    return margin_current - margin_prior


def compute_store_count_yoy_change(
    store_counts: dict[tuple[int, int], Decimal],
    current: tuple[int, int],
) -> Optional[Decimal]:
    prior_year = _same_quarter_prior_year(*current)
    current_count = store_counts.get(current)
    prior_count = store_counts.get(prior_year)
    if current_count is None or prior_count is None:
        return None
    return current_count - prior_count


def derive_buying_signal(
    store_expansion: str,
    inventory_improving: str,
    margin_compression: str,
    revenue_growth_pct: Optional[Decimal],
) -> str:
    """
    Combine signals into a single buying volume prediction.
    An importer needs to know: will this retailer buy more, same, or less next season?
    """
    score = 0

    if store_expansion == "expanding":
        score += 2
    if store_expansion == "contracting":
        score -= 2

    if inventory_improving == "improving":
        score += 1
    if inventory_improving == "deteriorating":
        score -= 2

    if margin_compression == "compressing":
        score -= 1
    if margin_compression == "expanding":
        score += 1

    if revenue_growth_pct is not None:
        if revenue_growth_pct > Decimal("5"):
            score += 1
        if revenue_growth_pct < Decimal("-5"):
            score -= 1

    if score >= 3:
        return "strongly_increasing"
    if score >= 1:
        return "increasing"
    if score == 0:
        return "stable"
    if score >= -2:
        return "declining"
    return "strongly_declining"


def _strip_html(text: str) -> str:
    plain = _HTML_TAG_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", plain).strip()


def _extract_guidance_sentence(text: str) -> Optional[str]:
    plain = _strip_html(text)
    lower = plain.lower()
    best_sentence: Optional[str] = None
    best_score = 0

    sentences = re.split(r"(?<=[.!?])\s+", plain)
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        sentence_lower = sentence.lower()
        score = sum(1 for pattern in GUIDANCE_PATTERNS if pattern in sentence_lower)
        if score > best_score:
            best_score = score
            best_sentence = sentence

    if not best_sentence:
        for pattern in GUIDANCE_PATTERNS:
            idx = lower.find(pattern)
            if idx == -1:
                continue
            start = max(0, idx - 80)
            end = min(len(plain), idx + 120)
            snippet = plain[start:end].strip()
            if snippet:
                best_sentence = snippet
                break

    if not best_sentence:
        return None

    if len(best_sentence) > 200:
        return best_sentence[:197] + "..."
    return best_sentence


def extract_guidance_from_filing(cik: str, submissions: dict[str, Any]) -> str:
    """Fetch latest 10-Q or 10-K and extract forward-guidance sentence."""
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    documents = recent.get("primaryDocument", [])

    filing_accession: Optional[str] = None
    filing_document: Optional[str] = None
    for form, accn, doc in zip(forms, accessions, documents):
        if form in ("10-Q", "10-K"):
            filing_accession = accn
            filing_document = doc
            break

    if not filing_accession or not filing_document:
        logger.warning("No 10-Q/10-K filing found for CIK %s", cik)
        return NO_GUIDANCE_TEXT

    cik_num = str(int(cik))
    accession_nodash = filing_accession.replace("-", "")
    url = _FILING_DOC_URL.format(
        cik_num=cik_num,
        accession=accession_nodash,
        document=filing_document,
    )
    body = _sec_get(url)
    if not isinstance(body, str) or not body.strip():
        return NO_GUIDANCE_TEXT

    sentence = _extract_guidance_sentence(body)
    if not sentence:
        return NO_GUIDANCE_TEXT
    return sentence[:255]


def _derive_gross_profit(
    revenues: dict[tuple[int, int], Decimal],
    cogs: dict[tuple[int, int], Decimal],
    gross_profit: dict[tuple[int, int], Decimal],
) -> dict[tuple[int, int], Decimal]:
    derived = dict(gross_profit)
    for key, rev in revenues.items():
        if key in cogs:
            derived[key] = rev - cogs[key]
    return derived


def _extract_apparel_revenue(
    us_gaap: dict[str, Any],
    latest_quarter: tuple[int, int],
) -> Optional[Decimal]:
    apparel = extract_quarterly_values(us_gaap, APPAREL_REVENUE_CONCEPTS, instant=False)
    value = apparel.get(latest_quarter)
    if value is None:
        return None
    return value


def fetch_retailer_facts(cik: str) -> Optional[tuple[dict[str, Any], dict[str, Any]]]:
    companyfacts_url = _COMPANYFACTS_URL.format(cik=cik)
    submissions_url = _SUBMISSIONS_URL.format(cik=cik)

    companyfacts = _sec_get(companyfacts_url)
    if not isinstance(companyfacts, dict):
        logger.warning("No companyfacts JSON for CIK %s", cik)
        return None

    submissions = _sec_get(submissions_url)
    if not isinstance(submissions, dict):
        logger.warning("No submissions JSON for CIK %s", cik)
        submissions = {}

    us_gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    if not us_gaap:
        logger.warning("No us-gaap facts for CIK %s", cik)
        return None

    return us_gaap, submissions


def process_retailer(retailer: dict[str, str]) -> Optional[dict[str, Any]]:
    """Fetch SEC data and compute metrics for one retailer."""
    name = retailer["name"]
    cik = retailer["cik"]
    logger.info("Processing %s (CIK %s)", name, cik)

    fetched = fetch_retailer_facts(cik)
    if not fetched:
        return None

    us_gaap, submissions = fetched

    revenues = extract_quarterly_values(us_gaap, XBRL_CONCEPTS["revenues"])
    cogs = extract_quarterly_values(us_gaap, XBRL_CONCEPTS["cogs"])
    gross_profit = _derive_gross_profit(
        revenues,
        cogs,
        extract_quarterly_values(us_gaap, XBRL_CONCEPTS["gross_profit"]),
    )
    inventory = extract_quarterly_values(
        us_gaap, XBRL_CONCEPTS["inventory"], instant=True
    )
    store_counts_raw = extract_quarterly_values(
        us_gaap, XBRL_CONCEPTS["store_count"], instant=True
    )
    store_counts = {k: int(v) for k, v in store_counts_raw.items()}

    latest = _latest_quarter(revenues, cogs, gross_profit, inventory)
    if latest is None:
        logger.warning("No recent quarterly revenue for %s", name)
        return None

    latest_revenue = revenues.get(latest)
    latest_gross_profit = gross_profit.get(latest)
    gross_margin_pct = None
    if latest_revenue is not None and latest_gross_profit is not None:
        gross_margin_pct = compute_gross_margin_pct(latest_gross_profit, latest_revenue)

    inventory_turnover = compute_inventory_turnover(cogs, inventory, latest)
    revenue_growth_pct = compute_revenue_growth_pct(revenues, latest)
    turnover_change_pct = compute_turnover_change_pct(cogs, inventory, latest)
    margin_change_pct = compute_margin_change_pct(gross_profit, revenues, latest)
    store_count_yoy_change = compute_store_count_yoy_change(store_counts, latest)

    latest_store_count = store_counts.get(latest)
    if latest_store_count is None and store_counts:
        latest_store_count = store_counts[max(store_counts.keys())]

    forward_guidance = NO_GUIDANCE_TEXT
    if submissions:
        forward_guidance = extract_guidance_from_filing(cik, submissions)

    apparel_revenue = _extract_apparel_revenue(us_gaap, latest)

    if store_count_yoy_change is not None:
        if store_count_yoy_change > 0:
            store_expansion = "expanding"
        elif store_count_yoy_change < 0:
            store_expansion = "contracting"
        else:
            store_expansion = "stable"
    else:
        store_expansion = "stable"

    if turnover_change_pct is not None:
        if turnover_change_pct > Decimal("2.0"):
            inventory_improving = "improving"
        elif turnover_change_pct < Decimal("-2.0"):
            inventory_improving = "deteriorating"
        else:
            inventory_improving = "stable"
    else:
        inventory_improving = "stable"

    if margin_change_pct is not None:
        if margin_change_pct < Decimal("-1.0"):
            margin_compression = "compressing"
        elif margin_change_pct > Decimal("1.0"):
            margin_compression = "expanding"
        else:
            margin_compression = "stable"
    else:
        margin_compression = "stable"

    buying_volume_signal = derive_buying_signal(
        store_expansion,
        inventory_improving,
        margin_compression,
        revenue_growth_pct,
    )

    return {
        "name": name,
        "cik": cik,
        "ticker": retailer["ticker"],
        "latest_quarter": latest,
        "store_count": latest_store_count,
        "total_sales": latest_revenue,
        "apparel_revenue": apparel_revenue,
        "gross_margin": gross_margin_pct,
        "inventory_turnover": inventory_turnover,
        "forward_guidance": forward_guidance,
        "store_expansion": store_expansion,
        "inventory_improving": inventory_improving,
        "margin_compression": margin_compression,
        "buying_volume_signal": buying_volume_signal,
        "revenue_growth_pct": revenue_growth_pct,
        "turnover_change_pct": turnover_change_pct,
        "margin_change_pct": margin_change_pct,
    }


def _next_retailer_id(db: Session) -> int:
    current_max = db.query(func.max(MajorRetailers.retailer_id)).scalar()
    return (current_max or 0) + 1


def _next_demand_signal_id(db: Session) -> int:
    current_max = db.query(func.max(DemandSignals.demand_signal_id)).scalar()
    return (current_max or 0) + 1


def upsert_major_retailer(db: Session, payload: dict[str, Any]) -> MajorRetailers:
    existing = (
        db.query(MajorRetailers).filter(MajorRetailers.name == payload["name"]).first()
    )

    if existing is None:
        existing = MajorRetailers(
            retailer_id=_next_retailer_id(db),
            name=payload["name"],
        )
        db.add(existing)

    existing.store_count = payload.get("store_count")
    existing.total_sales = payload.get("total_sales")
    existing.apparel_revenue = payload.get("apparel_revenue")
    existing.gross_margin = payload.get("gross_margin")
    existing.inventory_turnover = payload.get("inventory_turnover")
    existing.forward_guidance = payload.get("forward_guidance")
    existing.source = SOURCE_LABEL
    existing.status = "LIVE"

    return existing


def upsert_demand_signal(
    db: Session,
    retailer_id: int,
    payload: dict[str, Any],
) -> DemandSignals:
    existing = (
        db.query(DemandSignals)
        .filter(DemandSignals.retailer_id == retailer_id)
        .first()
    )

    if existing is None:
        existing = DemandSignals(
            demand_signal_id=_next_demand_signal_id(db),
            retailer_id=retailer_id,
        )
        db.add(existing)

    existing.store_expansion = payload["store_expansion"]
    existing.inventory_improving = payload["inventory_improving"]
    existing.margin_compression = payload["margin_compression"]
    existing.buying_volume_signal = payload["buying_volume_signal"]
    existing.status = "LIVE"

    return existing


def write_retailer_to_db(db: Session, payload: dict[str, Any]) -> bool:
    try:
        retailer_row = upsert_major_retailer(db, payload)
        db.flush()
        upsert_demand_signal(db, retailer_row.retailer_id, payload)
        db.commit()
        db.refresh(retailer_row)
        return True
    except Exception as exc:
        logger.error("DB write failed for %s: %s", payload.get("name"), exc)
        db.rollback()
        return False


def ingest_retailers(
    db: Session,
    retailers: list[dict[str, str]],
) -> dict[str, int]:
    summary = {"processed": 0, "written": 0, "failed": 0}

    for retailer in retailers:
        summary["processed"] += 1
        payload = process_retailer(retailer)
        if payload is None:
            summary["failed"] += 1
            continue
        if write_retailer_to_db(db, payload):
            summary["written"] += 1
            logger.info(
                "%s Q%s%s — signal=%s margin=%s turnover=%s",
                payload["name"],
                payload["latest_quarter"][0],
                payload["latest_quarter"][1],
                payload["buying_volume_signal"],
                payload["gross_margin"],
                payload["inventory_turnover"],
            )
        else:
            summary["failed"] += 1

    return summary


def _resolve_retailers(
    retailer_ticker: Optional[str],
    run_all: bool,
) -> list[dict[str, str]]:
    if retailer_ticker:
        ticker = retailer_ticker.upper()
        matches = [r for r in RETAILERS if r["ticker"].upper() == ticker]
        if not matches:
            raise SystemExit(f"Unknown retailer ticker: {retailer_ticker}")
        return matches

    if run_all or not retailer_ticker:
        return RETAILERS

    raise SystemExit("Specify --all or --retailer TICKER")


def run_once(
    retailer_ticker: Optional[str] = None,
    run_all: bool = False,
) -> bool:
    retailers = _resolve_retailers(retailer_ticker, run_all)
    db = SessionLocal()
    try:
        logger.info("Starting retail SEC EDGAR ingestion for %d retailer(s)", len(retailers))
        summary = ingest_retailers(db, retailers)
        logger.info(
            "Retail ingestion complete — processed=%d written=%d failed=%d",
            summary["processed"],
            summary["written"],
            summary["failed"],
        )
        print(
            f"Retail ingestion: {summary['written']}/{summary['processed']} retailers written "
            f"({summary['failed']} failed)"
        )
        return summary["written"] > 0
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fetch SEC EDGAR financial data for major US apparel retailers."
    )
    parser.add_argument(
        "--retailer",
        metavar="TICKER",
        help="Refresh a single retailer by ticker (e.g. TGT, WMT).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Refresh all 10 major apparel retailers.",
    )
    args = parser.parse_args()

    if args.retailer and args.all:
        raise SystemExit("Use either --retailer TICKER or --all, not both.")

    success = run_once(retailer_ticker=args.retailer, run_all=args.all)
    raise SystemExit(0 if success else 1)

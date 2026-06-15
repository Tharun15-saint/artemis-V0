"""
Shared logic for selecting earnings-release 8-K EX-99 URLs from SEC EDGAR submissions.

Used by retailer missing-8k-url finder scripts and tier-1 ingestion helpers.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Callable, Optional

TARGET_COMPANY_ACCESSION_PREFIX = "0000027419-"
WALMART_COMPANY_ACCESSION_PREFIX = "0000104169-"

NEGATIVE_PRIMARY_KEYWORDS = (
    "board",
    "director",
    "officer",
    "appointment",
    "resignation",
    "amendment",
    "definitive",
)

_PRIORITY_1_EXACT_EXHIBIT_RE = re.compile(r"a20\d\dq[1-4]ex-99\.htm$", re.I)
_PRIORITY_1_EXHIBIT_RE = re.compile(r"a20\d\dq[1-4]ex-99", re.I)
_PRIORITY_1_ALT_EXHIBIT_RE = re.compile(r"a\d{4}q[1-4].*(?:ex-99|ex99)", re.I)
_INDEX_HREF_RE = re.compile(r'href="[^"]*/([^"/]+\.htm)"', re.I)
_INDEX_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
_INDEX_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.I | re.S)
_INDEX_TAG_RE = re.compile(r"<[^>]+>")


def _clean_index_cell(raw: str) -> str:
    return " ".join(_INDEX_TAG_RE.sub("", raw).split())


def is_earnings_exhibit_row(row: dict[str, str]) -> bool:
    """Limit scoring to EX-99 exhibits, not primary 8-K documents."""
    name = row.get("name", "")
    lower = name.lower()
    if not lower.endswith(".htm"):
        return False

    row_type = row.get("type", "").upper()
    description = row.get("description", "").lower()
    if row_type == "EX-99" or "exhibit" in description:
        return True
    if "ex-99" in lower or "ex99" in lower:
        return True
    return any(keyword in lower for keyword in ("earnings", "release", "results"))


def earnings_exhibit_documents(parsed_rows: list[dict[str, str]]) -> list[str]:
    return [row["name"] for row in parsed_rows if is_earnings_exhibit_row(row)]


def score_exhibit_filename(
    accession: str,
    exhibit_filename: str,
    company_accession_prefix: str,
) -> Optional[tuple[int, int]]:
    """
    Return earnings exhibit priority as (tier, sub_rank).

    Lower tier and sub_rank are better. Tier 1 = company's own earnings release.
    """
    lower = exhibit_filename.lower()
    if not lower.endswith(".htm"):
        return None

    own_cik = accession.startswith(company_accession_prefix)
    if own_cik and _PRIORITY_1_EXACT_EXHIBIT_RE.search(lower):
        return (1, 0)
    if own_cik and (
        _PRIORITY_1_EXHIBIT_RE.search(lower)
        or _PRIORITY_1_ALT_EXHIBIT_RE.search(lower)
    ):
        return (1, 1)
    if own_cik and any(keyword in lower for keyword in ("earnings", "release", "results")):
        return (2, 0)
    if any(keyword in lower for keyword in ("earnings", "release", "results")):
        return (3, 0)
    if "ex-99" in lower or "ex99" in lower:
        return (4, 0)
    return None


def find_earnings_exhibit_document(
    index_documents: list[str],
    accession: str,
    company_accession_prefix: str,
) -> Optional[tuple[str, tuple[int, int]]]:
    """
    Pick the highest-priority earnings exhibit from a filing index document list.

    Returns (exhibit_filename, (priority_tier, sub_rank)) or None.
    """
    best: Optional[tuple[str, tuple[int, int]]] = None
    for document in index_documents:
        priority = score_exhibit_filename(accession, document, company_accession_prefix)
        if priority is None:
            continue
        if best is None or priority < best[1] or (
            priority == best[1] and len(document) < len(best[0])
        ):
            best = (document, priority)
    return best


def parse_index_htm_documents(index_html: str) -> list[dict[str, str]]:
    """Parse SEC filing index.htm into document rows."""
    documents: list[dict[str, str]] = []
    for row_html in _INDEX_ROW_RE.findall(index_html):
        cells = [
            _clean_index_cell(cell)
            for cell in _INDEX_CELL_RE.findall(row_html)
            if _clean_index_cell(cell)
        ]
        if len(cells) < 3:
            continue
        seq = cells[0]
        if not seq.isdigit():
            continue
        description = cells[1]
        document_match = re.search(r"([\w\-]+\.htm)", cells[2], re.I)
        if not document_match:
            continue
        doc_type = cells[3] if len(cells) > 3 else ""
        documents.append(
            {
                "seq": seq,
                "description": description,
                "name": document_match.group(1),
                "type": doc_type,
            }
        )
    return documents


def primary_document_from_index(documents: list[dict[str, str]]) -> Optional[dict[str, str]]:
    """Return the primary 8-K document row from a parsed index."""
    eight_k_rows = [row for row in documents if row.get("type") == "8-K"]
    if eight_k_rows:
        return sorted(eight_k_rows, key=lambda row: int(row["seq"]))[0]
    for row in sorted(documents, key=lambda row: int(row["seq"])):
        if row["name"].lower().endswith(".htm") and "index" not in row["name"].lower():
            return row
    return None


def is_excluded_corporate_action_8k(primary_description: str) -> bool:
    """Skip 8-K filings whose index primary description is a corporate-action filing."""
    lower = primary_description.lower()
    return any(keyword in lower for keyword in NEGATIVE_PRIMARY_KEYWORDS)


def find_quarter_earnings_8k_url(
    filing_rows: list[dict[str, Any]],
    period_end_date: date,
    *,
    company_accession_prefix: str,
    cik_num: str,
    fetch_index_html: Callable[[str], Optional[str]],
    build_document_url: Callable[[str, str], str],
    window_days: int = 45,
) -> Optional[str]:
    """
    Select the best earnings-release EX-99 URL within the post-period-end window.

    When multiple 8-K filings qualify, choose the highest exhibit priority, then
    the filing date closest to (but after) period_end_date.
    """
    window_end = period_end_date + timedelta(days=window_days)
    candidates: list[tuple[tuple[int, int], int, date, str, str]] = []

    for filing in filing_rows:
        if filing.get("form") != "8-K":
            continue

        filed_date = filing.get("filing_date")
        accession = filing.get("accession")
        if not isinstance(filed_date, date) or not accession:
            continue
        if filed_date < period_end_date or filed_date > window_end:
            continue

        index_html = fetch_index_html(accession)
        if not isinstance(index_html, str):
            continue

        parsed_rows = parse_index_htm_documents(index_html)
        primary = primary_document_from_index(parsed_rows)
        primary_description = primary["description"] if primary else ""
        if is_excluded_corporate_action_8k(primary_description):
            continue

        index_documents = earnings_exhibit_documents(parsed_rows)
        exhibit_match = find_earnings_exhibit_document(
            index_documents,
            accession,
            company_accession_prefix,
        )
        if exhibit_match is None:
            continue

        exhibit_name, priority = exhibit_match
        days_after_period_end = (filed_date - period_end_date).days
        candidates.append(
            (priority, days_after_period_end, filed_date, accession, exhibit_name)
        )

    if not candidates:
        return None

    candidates.sort(key=lambda row: (row[0], row[1], row[2]))
    _, _, _, best_accession, best_exhibit = candidates[0]
    return build_document_url(best_accession, best_exhibit)


def index_htm_url(cik_num: str, accession: str) -> str:
    accession_nodash = accession.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik_num}/"
        f"{accession_nodash}/{accession}-index.htm"
    )


def href_documents_from_index_html(index_html: str) -> list[str]:
    """Document filenames linked from index.htm (legacy helper)."""
    documents: list[str] = []
    seen: set[str] = set()
    for match in _INDEX_HREF_RE.finditer(index_html):
        name = match.group(1)
        lower = name.lower()
        if lower in seen or lower.endswith("-index.htm"):
            continue
        seen.add(lower)
        documents.append(name)
    return documents

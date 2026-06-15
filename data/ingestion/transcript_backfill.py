"""
Backfill confirmed Target and Walmart earnings call transcripts from Motley Fool.

Fetches each transcript URL, extracts plain text, and processes via
earnings_transcript_ingestion.process_transcript().

Requires ANTHROPIC_API_KEY in environment. Do not run in CI.
"""

from __future__ import annotations

import argparse
import html
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

import requests
from anthropic import Anthropic

from data.ingestion import walmart_tier1_ingestion as walmart_tier1
from data.ingestion._env import load_project_env
from data.ingestion.earnings_transcript_ingestion import (
    DOCUMENT_TYPE,
    IngestionStats,
    process_transcript,
)
from database.base import SessionLocal
from database.models.retail import RetailerIntelligenceExtract
from sqlalchemy.orm import Session

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
FETCH_DELAY_SECONDS = 5.0
REQUEST_TIMEOUT = 60

CONFIRMED_TRANSCRIPTS: list[dict[str, int | str]] = [
    {
        "retailer_id": 1,
        "fiscal_year": 2026,
        "fiscal_quarter": 1,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2026/05/20/"
            "target-tgt-q1-2026-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 1,
        "fiscal_year": 2024,
        "fiscal_quarter": 4,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2025/03/04/"
            "target-tgt-q4-2024-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 1,
        "fiscal_year": 2024,
        "fiscal_quarter": 3,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2024/11/20/"
            "target-tgt-q3-2024-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 1,
        "fiscal_year": 2024,
        "fiscal_quarter": 2,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2024/08/21/"
            "target-tgt-q2-2024-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 1,
        "fiscal_year": 2024,
        "fiscal_quarter": 1,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2024/05/22/"
            "target-tgt-q1-2024-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 1,
        "fiscal_year": 2023,
        "fiscal_quarter": 4,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2024/03/05/"
            "target-tgt-q4-2023-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 1,
        "fiscal_year": 2023,
        "fiscal_quarter": 3,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2023/11/15/"
            "target-tgt-q3-2023-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 1,
        "fiscal_year": 2023,
        "fiscal_quarter": 2,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2023/08/16/"
            "target-tgt-q2-2023-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 1,
        "fiscal_year": 2023,
        "fiscal_quarter": 1,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2023/05/17/"
            "target-tgt-q1-2023-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 1,
        "fiscal_year": 2022,
        "fiscal_quarter": 4,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2023/02/28/"
            "target-tgt-q4-2022-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 1,
        "fiscal_year": 2022,
        "fiscal_quarter": 3,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2022/11/16/"
            "target-tgt-q3-2022-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 1,
        "fiscal_year": 2022,
        "fiscal_quarter": 2,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2022/08/17/"
            "target-tgt-q2-2022-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 1,
        "fiscal_year": 2022,
        "fiscal_quarter": 1,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2022/05/18/"
            "target-tgt-q1-2022-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 1,
        "fiscal_year": 2021,
        "fiscal_quarter": 4,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2022/03/02/"
            "target-tgt-q4-2021-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 1,
        "fiscal_year": 2021,
        "fiscal_quarter": 3,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2021/11/17/"
            "target-tgt-q3-2021-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 1,
        "fiscal_year": 2021,
        "fiscal_quarter": 2,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2021/08/19/"
            "target-tgt-q2-2021-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 2,
        "fiscal_year": 2026,
        "fiscal_quarter": 4,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2026/02/19/"
            "walmart-wmt-q4-2026-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 2,
        "fiscal_year": 2027,
        "fiscal_quarter": 1,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2026/05/21/"
            "walmart-wmt-q1-2027-earnings-transcript/"
        ),
    },
    {
        "retailer_id": 2,
        "fiscal_year": 2025,
        "fiscal_quarter": 4,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2025/02/20/"
            "walmart-wmt-q4-2025-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 2,
        "fiscal_year": 2025,
        "fiscal_quarter": 3,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2024/11/19/"
            "walmart-wmt-q3-2025-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 2,
        "fiscal_year": 2025,
        "fiscal_quarter": 2,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2024/08/15/"
            "walmart-wmt-q2-2025-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 2,
        "fiscal_year": 2025,
        "fiscal_quarter": 1,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2024/05/16/"
            "walmart-wmt-q1-2025-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 2,
        "fiscal_year": 2024,
        "fiscal_quarter": 4,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2024/02/20/"
            "walmart-wmt-q4-2024-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 2,
        "fiscal_year": 2024,
        "fiscal_quarter": 3,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2023/11/16/"
            "walmart-wmt-q3-2024-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 2,
        "fiscal_year": 2024,
        "fiscal_quarter": 2,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2023/08/17/"
            "walmart-wmt-q2-2024-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 2,
        "fiscal_year": 2024,
        "fiscal_quarter": 1,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2023/05/18/"
            "walmart-wmt-q1-2024-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 2,
        "fiscal_year": 2023,
        "fiscal_quarter": 4,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2023/02/21/"
            "walmart-wmt-q4-2023-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 2,
        "fiscal_year": 2023,
        "fiscal_quarter": 3,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2022/11/15/"
            "walmart-inc-wmt-q3-2023-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 2,
        "fiscal_year": 2023,
        "fiscal_quarter": 2,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2022/08/16/"
            "walmart-inc-wmt-q2-2023-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 2,
        "fiscal_year": 2023,
        "fiscal_quarter": 1,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2022/05/17/"
            "walmart-inc-wmt-q1-2023-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 2,
        "fiscal_year": 2022,
        "fiscal_quarter": 4,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2022/02/17/"
            "walmart-inc-wmt-q4-2022-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 2,
        "fiscal_year": 2022,
        "fiscal_quarter": 3,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2021/11/16/"
            "walmart-inc-wmt-q3-2022-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 2,
        "fiscal_year": 2022,
        "fiscal_quarter": 2,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2021/08/17/"
            "wal-mart-inc-wmt-q2-2022-earnings-call-transcript/"
        ),
    },
    {
        "retailer_id": 2,
        "fiscal_year": 2022,
        "fiscal_quarter": 1,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2021/05/18/"
            "wal-mart-stores-inc-wmt-q1-2022-earnings-call-tran/"
        ),
    },
    {
        "retailer_id": 2,
        "fiscal_year": 2021,
        "fiscal_quarter": 4,
        "url": (
            "https://www.fool.com/earnings/call-transcripts/2021/02/19/"
            "wal-mart-stores-inc-wmt-q4-2020-earnings-call-tran/"
        ),
    },
]


@dataclass(frozen=True)
class TranscriptJob:
    retailer_id: int
    fiscal_year: int
    fiscal_quarter: int
    url: str


_ARTICLE_BODY_START_RE = re.compile(
    r'<div[^>]+class="[^"]*article-body[^"]*"[^>]*>',
    re.I,
)
_FOOL_ANALYST_INTRO_RE = re.compile(
    r"(?:first|next|following|last)\s+question\s+comes\s+from\s+"
    r"([A-Z][\w\.\'\-]+(?:\s+[A-Z][\w\.\'\-]+)*)\s+with\b",
    re.I,
)
_FOOL_SPEAKER_COLON_RE = re.compile(
    r"(?<=[.!?])\s+([A-Z][\w\.\'\-]+(?:\s+[A-Z][\w\.\'\-]+){0,3}):"
)
_FOOL_QA_START_RE = re.compile(
    r"(?:Operator:\s*\[Operator Instructions\]|"
    r"we(?:'|&#x27;)ll open the line for questions)",
    re.I,
)


def _extract_fool_transcript_text(page_html: str) -> Optional[str]:
    start_match = _ARTICLE_BODY_START_RE.search(page_html)
    if start_match:
        start_pos = start_match.end()
        end_pos = page_html.find("</article>", start_pos)
        if end_pos == -1:
            end_pos = len(page_html)
        body_html = page_html[start_pos:end_pos]
        text = walmart_tier1._strip_html(body_html)
        if text:
            normalized = _normalize_fool_transcript_text(text)
            return normalized

    article_match = re.search(
        r"<article[^>]*>(.*?)</article>",
        page_html,
        re.S | re.I,
    )
    if article_match:
        text = walmart_tier1._strip_html(article_match.group(1))
        if text:
            normalized = _normalize_fool_transcript_text(text)
            return normalized

    stripped = walmart_tier1._strip_html(page_html)
    if stripped:
        normalized = _normalize_fool_transcript_text(stripped)
        return normalized
    return None


def _normalize_fool_transcript_text(text: str) -> str:
    """Adapt Motley Fool inline transcript formatting for the passage parser."""
    normalized = html.unescape(text)
    normalized = normalized.replace("&#x27;", "'").replace("&#39;", "'")

    analyst_names = {
        match.group(1).strip()
        for match in _FOOL_ANALYST_INTRO_RE.finditer(normalized)
    }

    qa_start = _FOOL_QA_START_RE.search(normalized)
    if qa_start:
        normalized = (
            f"{normalized[: qa_start.start()].rstrip()}\n\n"
            "Question-and-Answer Session\n\n"
            f"{normalized[qa_start.start() :].lstrip()}"
        )

    def _speaker_replacement(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        if name.lower() == "operator":
            return "\n\nOperator\n"
        if name in analyst_names:
            return f"\n\n{name} - Analyst\n"
        return f"\n\n{name} - Management\n"

    normalized = _FOOL_SPEAKER_COLON_RE.sub(_speaker_replacement, normalized)
    normalized = re.sub(
        r"^Operator:\s*",
        "Operator\n",
        normalized,
        count=1,
        flags=re.I | re.M,
    )
    return normalized.strip()


def fetch_transcript_text(url: str) -> Optional[str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Encoding": "gzip, deflate",
    }
    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Failed to fetch transcript from %s: %s", url, exc)
        return None

    text = _extract_fool_transcript_text(response.text)
    if not text:
        logger.error("No transcript body extracted from %s", url)
        return None
    return text


def _has_latest_transcript_extracts(
    db: Session,
    *,
    retailer_id: int,
    fiscal_year: int,
    fiscal_quarter: int,
) -> bool:
    """True if this retailer/quarter already has is_latest transcript extracts."""
    return (
        db.query(RetailerIntelligenceExtract.extract_id)
        .filter(
            RetailerIntelligenceExtract.retailer_id == retailer_id,
            RetailerIntelligenceExtract.fiscal_year == fiscal_year,
            RetailerIntelligenceExtract.fiscal_quarter == fiscal_quarter,
            RetailerIntelligenceExtract.document_type == DOCUMENT_TYPE,
            RetailerIntelligenceExtract.is_latest.is_(True),
        )
        .first()
        is not None
    )


def _log_transcript_stats(job: TranscriptJob, stats: IngestionStats) -> None:
    logger.info(
        "retailer_id=%s FY%s Q%s | passages_processed=%s signals_extracted=%s "
        "analyst_pressure_signals=%s contradictions_found=%s | %s",
        job.retailer_id,
        job.fiscal_year,
        job.fiscal_quarter,
        stats.passages_processed,
        stats.signals_extracted,
        stats.analyst_pressure_signals,
        stats.contradictions_found,
        job.url,
    )


def run_backfill(
    jobs: list[TranscriptJob],
    *,
    dry_run: bool = False,
    force_reextract: bool = False,
) -> list[tuple[TranscriptJob, Optional[IngestionStats]]]:
    results: list[tuple[TranscriptJob, Optional[IngestionStats]]] = []
    db = SessionLocal()
    client = Anthropic()

    try:
        for index, job in enumerate(jobs):
            if (
                not force_reextract
                and _has_latest_transcript_extracts(
                    db,
                    retailer_id=job.retailer_id,
                    fiscal_year=job.fiscal_year,
                    fiscal_quarter=job.fiscal_quarter,
                )
            ):
                logger.info(
                    "Skipping retailer_id=%s FY%s Q%s — already processed",
                    job.retailer_id,
                    job.fiscal_year,
                    job.fiscal_quarter,
                )
                results.append((job, None))
                continue

            if index > 0:
                logger.info(
                    "Waiting %.0f seconds before next fetch...",
                    FETCH_DELAY_SECONDS,
                )
                time.sleep(FETCH_DELAY_SECONDS)

            logger.info(
                "Fetching transcript retailer_id=%s FY%s Q%s from %s",
                job.retailer_id,
                job.fiscal_year,
                job.fiscal_quarter,
                job.url,
            )
            transcript_text = fetch_transcript_text(job.url)
            if not transcript_text:
                results.append((job, None))
                continue

            if dry_run:
                logger.info(
                    "Dry run: fetched %s characters for retailer_id=%s FY%s Q%s",
                    len(transcript_text),
                    job.retailer_id,
                    job.fiscal_year,
                    job.fiscal_quarter,
                )
                results.append((job, IngestionStats()))
                continue

            stats = process_transcript(
                transcript_text,
                retailer_id=job.retailer_id,
                fiscal_year=job.fiscal_year,
                fiscal_quarter=job.fiscal_quarter,
                db=db,
                source_url=job.url,
                client=client,
            )
            db.expire_all()
            _log_transcript_stats(job, stats)
            results.append((job, stats))
    finally:
        db.close()

    return results


def _jobs_from_config(config: list[dict[str, int | str]]) -> list[TranscriptJob]:
    return [
        TranscriptJob(
            retailer_id=int(item["retailer_id"]),
            fiscal_year=int(item["fiscal_year"]),
            fiscal_quarter=int(item["fiscal_quarter"]),
            url=str(item["url"]),
        )
        for item in config
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch and process confirmed Motley Fool earnings call transcripts "
            "for Target and Walmart."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch transcripts only; do not call Claude or write to the database.",
    )
    parser.add_argument(
        "--force-reextract",
        action="store_true",
        help=(
            "Re-process all transcripts even when is_latest=1 extract rows exist "
            "(e.g. prompt version upgrade)."
        ),
    )
    args = parser.parse_args()

    jobs = _jobs_from_config(CONFIRMED_TRANSCRIPTS)
    logger.info("Starting transcript backfill for %s confirmed transcripts", len(jobs))
    if args.force_reextract:
        logger.info("Force re-extract enabled — bypassing already-processed skip check")
    results = run_backfill(
        jobs,
        dry_run=args.dry_run,
        force_reextract=args.force_reextract,
    )

    succeeded = sum(1 for _, stats in results if stats is not None)
    failed = len(results) - succeeded
    logger.info(
        "Transcript backfill complete | processed=%s failed=%s dry_run=%s",
        succeeded,
        failed,
        args.dry_run,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

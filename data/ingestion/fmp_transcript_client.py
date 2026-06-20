"""
FMP earnings-call-transcript client.

Single clean entry point to fetch a full transcript by (symbol, fiscal_year, fiscal_quarter).
FMP's stable endpoint uses fiscal year + fiscal quarter directly (verified: WMT year=2025
quarter=3 -> the Nov-2024 FY2025-Q3 call), so it maps to our (fy, fq) with no conversion.

Subscription is flat-rate (no per-call billing), but we still back off politely on 429.
Key is read from FMP_API_KEY in the environment and never logged.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_ENDPOINT = "https://financialmodelingprep.com/stable/earning-call-transcript"
_TIMEOUT = 45


def fetch_fmp_transcript(symbol: str, fiscal_year: int, fiscal_quarter: int,
                         retries: int = 3) -> tuple[str, dict]:
    """Return (transcript_text, metadata_dict). ('', {}) if unavailable. Never raises on
    network errors (logs + returns empty so the caller records an honest coverage gap)."""
    key = os.getenv("FMP_API_KEY")
    if not key:
        raise RuntimeError("FMP_API_KEY not set in environment (.env)")
    url = f"{_ENDPOINT}?symbol={symbol}&year={fiscal_year}&quarter={fiscal_quarter}&apikey={key}"
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=_TIMEOUT)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code == 402:
                logger.error("FMP 402 Restricted — tier does not include transcripts")
                return "", {}
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list) and data and isinstance(data[0], dict):
                rec = data[0]
                return rec.get("content", "") or "", rec
            return "", {}
        except (requests.RequestException, ValueError) as exc:
            if attempt == retries - 1:
                logger.error("FMP fetch failed %s FY%sQ%s: %s", symbol, fiscal_year, fiscal_quarter, exc)
                return "", {}
            time.sleep(2 * (attempt + 1))
    return "", {}


def transcript_url(symbol: str, fiscal_year: int, fiscal_quarter: int) -> str:
    """Provenance URL (key omitted) recorded on every signal."""
    return f"{_ENDPOINT}?symbol={symbol}&year={fiscal_year}&quarter={fiscal_quarter}"

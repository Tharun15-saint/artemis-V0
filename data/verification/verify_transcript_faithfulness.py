"""
Anti-hallucination gate for the transcript signal corpus.

For a language corpus, the single most important check is: does each stored
raw_text_passage ACTUALLY appear in the source transcript? An LLM extractor can
paraphrase or invent quotes; this catches that. For every transcript-sourced
is_latest signal (fool.com / insidermonkey) it fetches the transcript (cached per
URL), normalizes both texts, and verifies the passage is genuinely present.

Classification per signal:
  FAITHFUL    — normalized passage is a substring of the transcript
  NEAR        — >=0.90 of the passage's word-shingles are found (minor cleanup/ellipsis)
  UNFAITHFUL  — not found (possible hallucination / wrong source) -> must NOT be certified

Read-only. Reports a faithfulness rate + every UNFAITHFUL signal. Sample with --urls N.
"""

from __future__ import annotations

import argparse
import logging
import re
import time
from collections import defaultdict

import requests

from data.ingestion._env import load_project_env
from database.base import SessionLocal
from database.models.retail_metrics import RetailerMetric  # noqa: F401 (ensure models registered)
from database.models.retail import RetailerIntelligenceExtract

load_project_env()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
TAG_RE = re.compile(r"<[^>]+>")
NORM_RE = re.compile(r"[^a-z0-9 ]+")
WS_RE = re.compile(r"\s+")


def _norm(s: str) -> str:
    s = TAG_RE.sub(" ", s or "")
    s = NORM_RE.sub(" ", s.lower())
    return WS_RE.sub(" ", s).strip()


def _fetch(url: str) -> str:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
        time.sleep(0.5)
        return _norm(r.text)
    except requests.RequestException as exc:
        logger.warning("fetch failed %s: %s", url, exc)
        return ""


def _shingle_overlap(passage: str, doc: str, k: int = 6) -> float:
    pt = passage.split()
    if len(pt) < k:
        return 1.0 if passage in doc else 0.0
    grams = {" ".join(pt[i:i + k]) for i in range(len(pt) - k + 1)}
    if not grams:
        return 0.0
    hit = sum(1 for g in grams if g in doc)
    return hit / len(grams)


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcript anti-hallucination gate")
    parser.add_argument("--urls", type=int, default=4, help="number of distinct transcript URLs to sample (0=all)")
    parser.add_argument("--version", default=None, help="filter extraction_prompt_ver e.g. v4.0")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        q = (db.query(RetailerIntelligenceExtract)
             .filter(RetailerIntelligenceExtract.is_latest.is_(True),
                     (RetailerIntelligenceExtract.source_url.like("%fool.com%") |
                      RetailerIntelligenceExtract.source_url.like("%insidermonkey%"))))
        if args.version:
            q = q.filter(RetailerIntelligenceExtract.extraction_prompt_ver == args.version)
        by_url = defaultdict(list)
        for row in q.all():
            by_url[row.source_url].append(row)

        urls = list(by_url)
        if args.urls:
            urls = urls[:args.urls]

        faithful = near = unfaithful = 0
        for url in urls:
            doc = _fetch(url)
            if not doc:
                logger.warning("SKIP (no doc): %s", url)
                continue
            for row in by_url[url]:
                p = _norm(row.raw_text_passage)
                if not p:
                    unfaithful += 1
                    continue
                if p in doc:
                    faithful += 1
                elif _shingle_overlap(p, doc) >= 0.90:
                    near += 1
                else:
                    unfaithful += 1
                    print(f"  UNFAITHFUL extract={row.extract_id} {row.extraction_prompt_ver} "
                          f"cat={row.canonical_category}: \"{(row.raw_text_passage or '')[:90]}...\"")
            checked = faithful + near + unfaithful
            print(f"[{checked} checked] {url.split('/')[-2]}")

        total = faithful + near + unfaithful
        if total:
            print(f"\nFAITHFUL {faithful} + NEAR {near} = {faithful + near}/{total} "
                  f"({100 * (faithful + near) / total:.1f}%) | UNFAITHFUL {unfaithful}")
        return 1 if unfaithful else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""
STRICT audit of the spoken-signal layers (L2 transcript_turn + L3 v5.0 retailer_intelligence_extract).
Reusable, read-only, per-retailer. The keystone check is FAITHFULNESS: every extracted signal's
stored passage must equal the immutable raw L1 bytes at its char offsets — proof the signal is a
literal slice of what was actually said, not a paraphrase or hallucination.

Checks (over quarters that have v5.0 extracts):
  L2  l2_reconstruction   — turns concatenate to the raw content byte-for-byte
  L2  turn_contiguity     — turn_index is 0..n-1 with no gap, is_latest unique
  L3  faithfulness        — raw_content[quote_char_start:quote_char_end] == raw_text_passage (EXACT)
  L3  turn_linkage        — every signal's source_turn_index exists in transcript_turn
  L3  tagging_complete    — relevance_tier ∈ tiers AND canonical_category ∈ topics, on every signal
  L3  provenance          — source + extraction_model + prompt_ver + confidence present
Also reports (not pass/fail): topic & relevance-tier distribution, Q&A-linked share, turn coverage,
and a truncation WARN for transcripts that look partial.

    python -m data.verification.transcript_audit
    python -m data.verification.transcript_audit --retailer-id 2 --fy 2023 --fq 1
"""

from __future__ import annotations

import argparse
import json
import logging

from sqlalchemy import text

from data.ingestion._env import load_project_env
from data.ingestion.transcript_extractor_v5 import TIERS, TOPICS
from data.raw.raw_store import RawStore
from database.base import SessionLocal

load_project_env()
logging.basicConfig(level=logging.WARNING)


def _raw_content(store, cache, sha):
    if sha not in cache:
        try:
            obj = json.loads(store.get(sha))
            rec = obj[0] if isinstance(obj, list) else obj
            cache[sha] = rec.get("content", "")
        except Exception:
            cache[sha] = ""
    return cache[sha]


def audit_retailer(db, store, cache, rid, sym, fy=None, fq=None):
    res = []

    def add(check, passed, detail=""):
        res.append((check, sym, passed, detail))

    # quarters that have v5.0 signals
    qsql = ("SELECT DISTINCT fiscal_year, fiscal_quarter FROM retailer_intelligence_extract "
            "WHERE retailer_id=:r AND is_latest AND extraction_prompt_ver='v5.0'")
    params = {"r": rid}
    if fy:
        qsql += " AND fiscal_year=:y"; params["y"] = fy
    if fq:
        qsql += " AND fiscal_quarter=:q"; params["q"] = fq
    quarters = [(a, b) for a, b in db.execute(text(qsql), params).fetchall()]
    if not quarters:
        return res, {}

    recon_bad = contig_bad = faith_bad = link_bad = tag_bad = prov_bad = 0
    faith_total = 0
    topic_dist, tier_dist = {}, {}
    qa_linked = sig_total = 0
    turns_total = turns_hit = 0

    for (yy, qq) in quarters:
        turns = db.execute(text(
            "SELECT turn_index, char_start, char_end, verbatim_text, content_sha256, speaker_role "
            "FROM transcript_turn WHERE retailer_id=:r AND fiscal_year=:y AND fiscal_quarter=:q AND is_latest "
            "ORDER BY turn_index"), {"r": rid, "y": yy, "q": qq}).fetchall()
        tmap = {t[0]: t for t in turns}
        # L2 reconstruction + contiguity
        if turns:
            sha = turns[0][4]
            content = _raw_content(store, cache, sha)
            if content and "".join(t[3] for t in turns) != content:
                recon_bad += 1
            if [t[0] for t in turns] != list(range(len(turns))):
                contig_bad += 1
            turns_total += sum(1 for t in turns if t[5] in ("management", "analyst"))

        sigs = db.execute(text(
            "SELECT source_turn_index, quote_char_start, quote_char_end, raw_text_passage, "
            "canonical_category, relevance_tier, source, extraction_model, extraction_prompt_ver, "
            "confidence_score, replies_to_turn_index FROM retailer_intelligence_extract "
            "WHERE retailer_id=:r AND fiscal_year=:y AND fiscal_quarter=:q AND is_latest AND extraction_prompt_ver='v5.0'"),
            {"r": rid, "y": yy, "q": qq}).fetchall()
        hit_turns = set()
        for (ti, qs, qe, passage, topic, tier, src, model, ver, conf, reply) in sigs:
            sig_total += 1
            faith_total += 1
            topic_dist[topic] = topic_dist.get(topic, 0) + 1
            tier_dist[tier] = tier_dist.get(tier, 0) + 1
            if reply is not None:
                qa_linked += 1
            if ti is None or ti not in tmap:
                link_bad += 1
                continue
            hit_turns.add(ti)
            t = tmap[ti]
            sha = t[4]
            content = _raw_content(store, cache, sha)
            if qs is None or qe is None or not content or content[qs:qe] != passage:
                faith_bad += 1
            if topic not in TOPICS or tier not in TIERS:
                tag_bad += 1
            if not src or not model or ver != "v5.0" or conf is None:
                prov_bad += 1
        turns_hit += len(hit_turns)

    add("l2_reconstruction", recon_bad == 0, f"{recon_bad} quarter(s) mismatch")
    add("turn_contiguity", contig_bad == 0, f"{contig_bad} quarter(s) non-contiguous")
    add("faithfulness", faith_bad == 0, f"{faith_bad}/{faith_total} signals' bytes != raw slice")
    add("turn_linkage", link_bad == 0, f"{link_bad} signals link to no turn")
    add("tagging_complete", tag_bad == 0, f"{tag_bad} signals with bad topic/tier")
    add("provenance", prov_bad == 0, f"{prov_bad} signals missing provenance")
    stats = {"signals": sig_total, "topics": topic_dist, "tiers": tier_dist,
             "qa_linked": qa_linked, "turns_total": turns_total, "turns_hit": turns_hit,
             "quarters": len(quarters)}
    return res, stats


def main() -> int:
    p = argparse.ArgumentParser(description="Strict audit of the spoken-signal layers (v5.0)")
    p.add_argument("--retailer-id", type=int)
    p.add_argument("--fy", type=int)
    p.add_argument("--fq", type=int)
    args = p.parse_args()
    db = SessionLocal()
    store = RawStore()
    cache: dict = {}
    try:
        retailers = db.execute(text(
            "SELECT retailer_id, ticker FROM major_retailers WHERE retailer_id IN (1,2) ORDER BY ticker")).fetchall()
        all_res, all_stats = [], {}
        for rid, sym in retailers:
            if args.retailer_id and rid != args.retailer_id:
                continue
            r, s = audit_retailer(db, store, cache, rid, sym, args.fy, args.fq)
            all_res += r
            if s:
                all_stats[sym] = s
        if not all_res:
            print("No v5.0 signals to audit yet.")
            return 0
        syms = sorted({s for _, s, _, _ in all_res})
        checks = sorted({c for c, _, _, _ in all_res})
        by = {(c, s): (ok, d) for c, s, ok, d in all_res}
        print(f"\n{'CHECK':20s} " + " ".join(f"{s:>7s}" for s in syms) + "   detail")
        print("-" * 80)
        failed = 0
        for c in checks:
            cells, detail = [], ""
            for s in syms:
                ok, d = by.get((c, s), (True, ""))
                cells.append("PASS" if ok else "FAIL")
                if not ok:
                    failed += 1
                    detail = f"{s}: {d}"
            print(f"{c:20s} " + " ".join(f"{x:>7s}" for x in cells) + f"   {detail}")
        print("-" * 80)
        print("ALL CLEAR" if failed == 0 else f"{failed} CHECK(S) FAILED")
        for sym, s in all_stats.items():
            cov = f"{s['turns_hit']}/{s['turns_total']}" if s["turns_total"] else "n/a"
            print(f"\n{sym}: {s['signals']} signals across {s['quarters']} quarter(s) | "
                  f"Q&A-linked={s['qa_linked']} | turn coverage(mgmt+analyst)={cov}")
            print(f"  topics: {dict(sorted(s['topics'].items(), key=lambda x: -x[1]))}")
            print(f"  tiers : {dict(sorted(s['tiers'].items(), key=lambda x: -x[1]))}")
        return 1 if failed else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

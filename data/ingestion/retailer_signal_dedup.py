"""
Spoken-signal L3 dedup tagging — capture-all + filter downstream, NEVER delete.

The extractor deliberately captures every material statement per turn, so the same FACT recurs
across turns (e.g. "inventory up 33%" in prepared remarks and again in two Q&A answers). Those are
distinct utterances with distinct provenance — valuable, not noise — so we do NOT drop them. Instead
we CLUSTER true same-fact near-duplicates and mark the single CANONICAL member; downstream the clean
view filters to `is_canonical`, the full view keeps everything.

Clustering (per retailer-quarter, over is_latest v5.0 signals), via union-find:
  • numeric facts: signals sharing the SAME topic AND the SAME figure (e.g. "33%") — verified the
    dominant duplicate pattern; guarded by a light passage-overlap check to avoid same-figure/
    different-fact false merges.
  • non-numeric: signals in the same topic with near-identical passages (high Jaccard), to catch a
    point restated in almost the same words.

Canonical selection (which duplicate is "best/most precise/sophisticated"):
  source authority (prepared_remarks > qa) + completeness (has number, longer implication) +
  confidence. Highest score wins; the rest are tagged non-canonical and linked by dedup_cluster_id.

Re-runnable (resets the quarter's tags first). Read-only `--dry-run` reports clusters without writing.

    python -m data.ingestion.retailer_signal_dedup                       # all v5.0 quarters
    python -m data.ingestion.retailer_signal_dedup --retailer-id 2 --fy 2023 --fq 1 --dry-run
"""

from __future__ import annotations

import argparse
import logging
import re

from sqlalchemy import text

from data.ingestion._env import load_project_env
from database.base import SessionLocal

load_project_env()
logger = logging.getLogger("retailer_signal_dedup")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

_FIG_RE = re.compile(r"\d+\.?\d*\s?%|\$\s?\d[\d,.]*\s?(?:billion|million|bn|mn|b|m)?\b", re.I)
# Conservative on purpose: only collapse GENUINE restatements, never distinct facts. Two facts may
# share a common figure ("4%") yet be different statements, and formulaic earnings sentences
# ("X sales were strong, up Y%") share generic vocabulary — so a shared figure alone is NOT enough.
# We compare DISTINCTIVE content only (generic earnings words stripped) so the discriminator is the
# subject (inventory / e-commerce / International / Sam's Club), and bias toward under-merging:
# a missed duplicate just leaves minor redundancy in the clean view, a false merge HIDES a real fact.
_TEXT_DUP_OVL = 0.60           # non-numeric (both figure-less): near-identical distinctive wording
_FIG_GUARD_OVL = 0.34          # numeric: same figure AND distinctive-subject overlap
_GENERIC = set((
    "this that with from have been were they their there about which would could into more than over "
    "also will your what when then them other been most some such only very upon were our the and for "
    "are was been has had its also "
    # generic earnings/financial vocabulary — strip so the SUBJECT discriminates
    "sales sale growth grow grew strong quarter quarterly year years annual comp comps comparable "
    "expect expects expected increase increased increases decrease declined decline percent percentage "
    "basis points versus prior continuing deliver good half full range guidance outlook total overall "
    "net gross about approximately roughly around first second third fourth point rate rates level "
    "business results result improvement improve improved continue continued strength weaker stronger "
    "billion million dollars dollar number numbers period periods week weeks month months").split())


def _content(s) -> set[str]:
    return {t for t in re.findall(r"[a-z]+", (s or "").lower()) if len(t) >= 4 and t not in _GENERIC}


def _ovl(a: set, b: set) -> float:
    return len(a & b) / max(1, min(len(a), len(b)))


def _figs(*parts) -> set[str]:
    s = " ".join(p or "" for p in parts).lower()
    return {re.sub(r"\s+", "", f) for f in _FIG_RE.findall(s) if re.search(r"\d", f)}


def _score(r: dict) -> float:
    sec = {"prepared_remarks": 2.0, "qa": 1.0}.get(r["section"], 0.0)
    return sec + (0.5 if r["number"] else 0.0) + len(r["impl"] or "") / 300.0 + float(r["conf"] or 0.0)


class _UF:
    def __init__(self, ids):
        self.p = {i: i for i in ids}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _cluster(rows: list[dict]) -> dict[int, list[int]]:
    """Return {root_extract_id: [member extract_ids]} for clusters of size >= 2."""
    uf = _UF([r["id"] for r in rows])
    by_topic: dict[str, list[dict]] = {}
    for r in rows:
        r["_figs"] = _figs(r["number"], r["passage"])
        r["_con"] = _content(r["passage"])
        by_topic.setdefault(r["topic"], []).append(r)

    for topic, group in by_topic.items():
        # numeric: same (topic, figure) AND distinctive-subject overlap → genuine restatement
        fig_map: dict[str, list[dict]] = {}
        for r in group:
            for f in r["_figs"]:
                fig_map.setdefault(f, []).append(r)
        for f, members in fig_map.items():
            if len(members) < 2:
                continue
            seed = max(members, key=_score)
            for r in members:
                if r is not seed and _ovl(seed["_con"], r["_con"]) >= _FIG_GUARD_OVL:
                    uf.union(seed["id"], r["id"])
        # non-numeric: near-identical distinctive wording, only when NEITHER carries a figure
        # (figured facts handled above; avoids merging distinct "X up Y%" sentences by shape)
        figless = [r for r in group if not r["_figs"]]
        for i in range(len(figless)):
            for j in range(i + 1, len(figless)):
                if _ovl(figless[i]["_con"], figless[j]["_con"]) >= _TEXT_DUP_OVL:
                    uf.union(figless[i]["id"], figless[j]["id"])

    comp: dict[int, list[int]] = {}
    for r in rows:
        comp.setdefault(uf.find(r["id"]), []).append(r["id"])
    return {root: ids for root, ids in comp.items() if len(ids) > 1}


def dedup_quarter(db, rid, fy, fq, dry_run=False) -> dict:
    rows = [dict(id=a, topic=b, section=c, number=d, passage=e, impl=f, conf=g)
            for a, b, c, d, e, f, g in db.execute(text(
                "SELECT extract_id, canonical_category, document_section, number_mentioned, "
                "raw_text_passage, artemis_implication, confidence_score "
                "FROM retailer_intelligence_extract "
                "WHERE retailer_id=:r AND fiscal_year=:y AND fiscal_quarter=:q AND is_latest "
                "AND extraction_prompt_ver='v5.0'"),
                {"r": rid, "y": fy, "q": fq}).fetchall()]
    if not rows:
        return {"signals": 0, "clusters": 0, "demoted": 0}
    by_id = {r["id"]: r for r in rows}
    clusters = _cluster(rows)

    canon, demoted, assignments = [], [], []   # assignments: (extract_id, is_canonical, cluster_id)
    clustered_ids = set()
    for _root, ids in clusters.items():
        cluster_id = min(ids)
        best = max(ids, key=lambda i: _score(by_id[i]))
        for i in ids:
            clustered_ids.add(i)
            is_c = (i == best)
            assignments.append((i, is_c, cluster_id))
            (canon if is_c else demoted).append(i)
    # singletons (and everything else) → canonical, no cluster
    for r in rows:
        if r["id"] not in clustered_ids:
            assignments.append((r["id"], True, None))

    if not dry_run:
        for eid, is_c, cid in assignments:
            db.execute(text("UPDATE retailer_intelligence_extract "
                            "SET is_canonical=:c, dedup_cluster_id=:cid, updated_at=now() "
                            "WHERE extract_id=:e"),
                       {"c": is_c, "cid": cid, "e": eid})
        db.commit()
    logger.info("r%s FY%sQ%s: %d signals, %d fact-clusters, %d demoted → %d canonical%s",
                rid, fy, fq, len(rows), len(clusters), len(demoted), len(rows) - len(demoted),
                " (dry-run)" if dry_run else "")
    return {"signals": len(rows), "clusters": len(clusters), "demoted": len(demoted),
            "cluster_detail": {min(ids): ids for ids in clusters.values()} if dry_run else {}}


def _quarters(db, rid, fy, fq):
    sql = ("SELECT DISTINCT retailer_id, fiscal_year, fiscal_quarter FROM retailer_intelligence_extract "
           "WHERE is_latest AND extraction_prompt_ver='v5.0'")
    if rid:
        sql += f" AND retailer_id={int(rid)}"
    if fy:
        sql += f" AND fiscal_year={int(fy)}"
    if fq:
        sql += f" AND fiscal_quarter={int(fq)}"
    return [(a, b, c) for a, b, c in db.execute(text(sql + " ORDER BY 1,2,3")).fetchall()]


def main() -> int:
    p = argparse.ArgumentParser(description="Dedup-tag v5.0 spoken signals (cluster + canonical, never delete)")
    p.add_argument("--retailer-id", type=int)
    p.add_argument("--fy", type=int)
    p.add_argument("--fq", type=int)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    db = SessionLocal()
    try:
        qs = _quarters(db, args.retailer_id, args.fy, args.fq)
        tot_sig = tot_cl = tot_dem = 0
        for rid, fy, fq in qs:
            r = dedup_quarter(db, rid, fy, fq, dry_run=args.dry_run)
            tot_sig += r["signals"]; tot_cl += r["clusters"]; tot_dem += r["demoted"]
        pct = (100 * tot_dem / tot_sig) if tot_sig else 0
        print(f"\n✓ dedup {'(dry-run) ' if args.dry_run else ''}across {len(qs)} quarter(s): "
              f"{tot_sig} signals, {tot_cl} fact-clusters, {tot_dem} demoted ({pct:.1f}%), "
              f"{tot_sig - tot_dem} canonical")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

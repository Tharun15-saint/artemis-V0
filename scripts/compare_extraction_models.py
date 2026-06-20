"""
Opus-vs-Sonnet v5.0 extraction comparison across a diverse, COMPLETE quarter set
(2 retailers × 3 macro regimes). Dry-run only (no DB writes); dumps every signal per model and
records exact measured token cost, so we can decide the model for the full-corpus run on real
quality + cost data, not estimates.

    .venv/bin/python -m scripts.compare_extraction_models
Outputs: /tmp/cmp_opus.json, /tmp/cmp_sonnet.json  (+ a cost summary on stdout)
"""

from __future__ import annotations

import argparse
import json
import logging

from anthropic import Anthropic

from data.ingestion._env import load_project_env
from data.ingestion.transcript_extractor_v5 import MODELS, _cost, process_quarter
from database.base import SessionLocal

load_project_env()
logging.basicConfig(level=logging.WARNING)

# (retailer_id, fy, fq) — WMT 2023Q1 (inflation/glut), WMT 2026Q1 (recent),
# TGT 2020Q4 (COVID), TGT 2026Q1 (recent). All complete (prepared remarks + ≥8 analysts).
QUARTERS = [(2, 2023, 1), (2, 2026, 1), (1, 2020, 4), (1, 2026, 1)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=list(MODELS), help="run just one model (other dump kept)")
    args = ap.parse_args()
    models = {args.only: MODELS[args.only]} if args.only else MODELS
    db = SessionLocal()
    client = Anthropic(timeout=300, max_retries=0)            # our loop retries; cap per-request hang
    try:
        summary = {}
        for mkey, model in models.items():
            usage = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "calls": 0}
            sigs = []
            per_q = {}
            for rid, fy, fq in QUARTERS:
                r = process_quarter(db, client, rid, fy, fq, model=model, usage=usage, dry_run=True)
                qid = f"{'WMT' if rid == 2 else 'TGT'} FY{fy}Q{fq}"
                per_q[qid] = r.get("signals", 0)
                for s in r.get("dry_signals", []):
                    s["_q"] = qid
                    s["_model"] = mkey
                    sigs.append(s)
                print(f"[{mkey}] {qid}: {r.get('signals', 0)} signals (unanchored={r.get('unanchored', 0)})")
            cost = _cost(model, usage)
            json.dump(sigs, open(f"/tmp/cmp_{mkey}.json", "w"), indent=1, default=str)
            summary[mkey] = {"model": model, "signals": len(sigs), "cost": cost,
                             "usage": usage, "per_q": per_q}
            print(f"  → {mkey}: {len(sigs)} signals, ${cost:.4f}  ({usage['calls']} calls)\n")
        print("=== SUMMARY ===")
        for mkey, s in summary.items():
            ps = f"${s['cost'] / s['signals']:.5f}/sig" if s["signals"] else ""
            print(f"  {mkey:7s} {s['signals']:4d} signals  ${s['cost']:.4f}  {ps}  per-q={s['per_q']}")
        json.dump(summary, open("/tmp/cmp_summary.json", "w"), indent=1, default=str)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

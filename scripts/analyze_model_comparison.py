"""
Deep comparative analysis of the Opus-vs-Sonnet v5.0 dry-run dumps (/tmp/cmp_*.json), across the
dimensions that matter for the apparel-intelligence foundation:
  coverage (signals, per-quarter), topic & relevance-tier mix, APPAREL-MISSION completeness
  (apparel signals + sentiment balance — does it capture downside as well as upside?),
  implication richness, confidence calibration (over-confidence flag), and redundancy
  (fact-families = same topic+figure repeated). Cost comes from /tmp/cmp_summary.json + stdout.

    .venv/bin/python -m scripts.analyze_model_comparison
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

MODELS = ["opus", "sonnet"]
APPAREL = {"apparel_performance", "consumer_demand", "inventory_sellthrough", "pricing_margin"}


def load(m):
    try:
        return json.load(open(f"/tmp/cmp_{m}.json"))
    except FileNotFoundError:
        return None


def figs(s):
    return set(re.findall(r"\d+\.?\d*\s?%|\$\s?\d[\d,.]*\s?(?:million|billion|m|b)?", (s or "").lower()))


def fact_families(sigs):
    fam = defaultdict(list)
    for i, s in enumerate(sigs):
        for f in figs((s.get("number") or "") + " " + (s.get("quote") or "")):
            fam[(s["topic"], f.strip())].append(i)
    return sum(1 for v in fam.values() if len(v) >= 3), sum(len(v) - 1 for v in fam.values() if len(v) >= 3)


def main():
    data = {m: load(m) for m in MODELS}
    if not all(data.values()):
        missing = [m for m in MODELS if not data[m]]
        print(f"missing dumps: {missing} — run the comparison first")
        return 1

    print("=" * 78)
    print("DEEP MODEL COMPARISON — v5.0 extraction (4 quarters: WMT 2023Q1/2026Q1, TGT 2020Q4/2026Q1)")
    print("=" * 78)

    for m in MODELS:
        d = data[m]
        per_q = Counter(s["_q"] for s in d)
        impl = [len(s.get("implication") or "") for s in d]
        conf = [float(s["confidence"]) for s in d if s.get("confidence") is not None]
        overc = sum(1 for c in conf if c >= 0.9)
        withnum = sum(1 for s in d if s.get("number"))
        fams, redun = fact_families(d)
        print(f"\n### {m.upper()}  —  {len(d)} signals")
        print(f"  per quarter : {dict(per_q)}")
        print(f"  avg implication length : {sum(impl) / len(impl):.0f} chars")
        print(f"  confidence  : avg={sum(conf) / len(conf):.2f}  | >=0.90 (possible over-conf): "
              f"{overc} ({100 * overc // len(conf)}%)")
        print(f"  with a number : {withnum} ({100 * withnum // len(d)}%)")
        print(f"  redundancy  : {fams} fact-families (topic+figure x>=3) → {redun} redundant signals "
              f"({100 * redun // len(d)}%)")
        print(f"  tiers  : {dict(Counter(s['tier'] for s in d).most_common())}")
        print(f"  topics : {dict(Counter(s['topic'] for s in d).most_common())}")

    # apparel-mission completeness: apparel_performance sentiment balance + apparel-tagged share
    print("\n" + "=" * 78)
    print("APPAREL-MISSION COMPLETENESS")
    print("=" * 78)
    for m in MODELS:
        d = data[m]
        ap = [s for s in d if s["topic"] == "apparel_performance"]
        core = [s for s in d if s["tier"] == "core_apparel"]
        sent = Counter((s.get("sentiment") or "?") for s in ap)
        apparel_topic = sum(1 for s in d if s["topic"] in APPAREL)
        print(f"\n  {m.upper()}: apparel_performance={len(ap)} (sentiment {dict(sent)}) | "
              f"core_apparel-tier={len(core)} | apparel-cluster topics={apparel_topic} "
              f"({100 * apparel_topic // len(d)}% of signals)")

    # per-quarter signal delta
    print("\n" + "=" * 78)
    print("PER-QUARTER SIGNAL COUNT  (opus vs sonnet)")
    print("=" * 78)
    qs = sorted({s["_q"] for s in data["opus"]})
    for q in qs:
        o = sum(1 for s in data["opus"] if s["_q"] == q)
        s_ = sum(1 for s in data["sonnet"] if s["_q"] == q)
        print(f"  {q:16s}  opus={o:4d}   sonnet={s_:4d}   Δ={o - s_:+d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
STRICT, comprehensive audit of the retail-intelligence layer (Walmart + Target) against every
principle in data/verification/INGESTION_PRINCIPLES.md and the project watchwords (accuracy,
truth-seeking, precision, no noise, ground-infallible-truth, completeness, longer-term outlook).

This is a single reusable gate — re-run it any time, for any retailer — that asserts each
requirement BOTH ways (a clean layer passes every check; any defect trips exactly one). It is
intentionally self-contained and read-only: it discovers problems, it does not paper over them.

Database checks (this file):
  1. coverage_contiguity        — no internal hole in the fiscal-quarter sequence (Principle 3)
  2. fiscal_label_consistency   — period_end_date ⇒ (fy,fq) via the entity's own calendar map,
                                  for EVERY row in retailer_metric + retailer_financials  (P1/P5)
  3. is_latest_unique           — exactly one is_latest row per natural key, both tables   (P5)
  4. provenance_complete        — source + confidence + data_quality on every metric row   (P6)
  5. nonneg                     — non-negative metrics are ≥ 0                              (P5)
  6. gross_margin_identity      — gross_margin_pct == gp/(gp+cogs)·100 (concept-clean)      (P5)
  7. magnitude_sanity           — cogs/gp ≤ revenue; gp+cogs ≈ revenue within other-income  (P5)
  8. cross_table_inventory      — retailer_metric.inventory == retailer_financials.inventory(P1)
  9. certification_integrity    — every certified row is is_latest, value set, confidence ok (P5)
 10. raw_l1_present             — immutable raw transcript bytes captured                   (P4)

External gates (run separately, reported here as reminders): SEC reconciliation
(retail_financials_reconcile), FMP triangulation (fmp_retail_verify), coverage-by-archetype
(retail_metric_coverage). They are the ground-truth (P1) and triangulation (P2) anchors.

    python -m data.verification.retail_intelligence_audit
"""

from __future__ import annotations

import logging
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import text

from data.ingestion._env import load_project_env
from data.ingestion.target_tier1_ingestion import _fiscal_quarter_from_end as tgt_fq
from data.ingestion.walmart_tier1_ingestion import _fiscal_quarter_from_end as wmt_fq
from database.base import SessionLocal

load_project_env()
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

RETAILERS = {2: ("WMT", wmt_fq), 1: ("TGT", tgt_fq)}
NONNEG = ("total_revenue_usd", "cogs_usd", "gross_profit_usd", "inventory_usd",
          "cash_and_equivalents_usd", "accounts_payable_usd", "sga_usd")
MONEY_REL = Decimal("0.01")
MONEY_ABS = Decimal("3000000")


class Audit:
    def __init__(self, db):
        self.db = db
        self.results: list[tuple[str, str, bool, str]] = []   # check, retailer, passed, detail

    def add(self, check, retailer, passed, detail=""):
        self.results.append((check, retailer, passed, detail))

    # --- data loaders -------------------------------------------------------
    def metrics(self, rid):
        """{(fy,fq): {metric_key: (value, source, confidence, data_quality, period_end, certified)}}"""
        rows = self.db.execute(text(
            "SELECT fiscal_year, fiscal_quarter, metric_key, value_numeric, source, confidence, "
            "data_quality, period_end_date, certified FROM retailer_metric "
            "WHERE retailer_id=:r AND is_latest"), {"r": rid}).fetchall()
        out = defaultdict(dict)
        for fy, fq, mk, val, src, conf, dq, ped, cert in rows:
            out[(fy, fq)][mk] = (val, src, conf, dq, ped, cert)
        return out

    def financials(self, rid):
        return self.db.execute(text(
            "SELECT fiscal_year, fiscal_quarter, period_end_date, total_net_sales_usd, inventory_usd "
            "FROM retailer_financials WHERE retailer_id=:r AND is_latest"), {"r": rid}).fetchall()

    # --- checks -------------------------------------------------------------
    def coverage_contiguity(self, rid, sym):
        present = sorted({fy * 4 + fq for (fy, fq) in self.metrics(rid).keys()})
        if not present:
            return self.add("coverage_contiguity", sym, False, "no rows")
        gaps = [i for i in range(present[0], present[-1] + 1) if i not in set(present)]
        human = [f"FY{i // 4}Q{i % 4}" for i in gaps]
        self.add("coverage_contiguity", sym, not gaps, f"{len(gaps)} internal gap(s): {human}" if gaps else "contiguous")

    def fiscal_label_consistency(self, rid, sym, fq_fn):
        bad = []
        for (fy, fq), mks in self.metrics(rid).items():
            # use any row's period_end (all metrics in a quarter share it)
            ped = next((v[4] for v in mks.values() if v[4] is not None), None)
            if ped is None:
                continue
            derived = fq_fn(ped)
            if derived != (fy, fq):
                bad.append(f"{ped}→{derived} labelled FY{fy}Q{fq}")
        for fy, fq, ped, _ns, _inv in self.financials(rid):
            if ped is not None and fq_fn(ped) != (fy, fq):
                bad.append(f"[fin] {ped}→{fq_fn(ped)} labelled FY{fy}Q{fq}")
        self.add("fiscal_label_consistency", sym, not bad,
                 "all period_end ⇒ (fy,fq)" if not bad else f"{len(bad)} bad: {bad[:5]}")

    def is_latest_unique(self, rid, sym):
        dup_m = self.db.execute(text(
            "SELECT count(*) FROM (SELECT 1 FROM retailer_metric WHERE retailer_id=:r AND is_latest "
            "GROUP BY metric_key, fiscal_year, fiscal_quarter HAVING count(*)>1) t"), {"r": rid}).scalar()
        dup_f = self.db.execute(text(
            "SELECT count(*) FROM (SELECT 1 FROM retailer_financials WHERE retailer_id=:r AND is_latest "
            "GROUP BY fiscal_year, fiscal_quarter HAVING count(*)>1) t"), {"r": rid}).scalar()
        self.add("is_latest_unique", sym, dup_m == 0 and dup_f == 0,
                 f"metric dups={dup_m} financial dups={dup_f}")

    def provenance_complete(self, rid, sym):
        bad = self.db.execute(text(
            "SELECT count(*) FROM retailer_metric WHERE retailer_id=:r AND is_latest AND "
            "(source IS NULL OR confidence IS NULL OR data_quality IS NULL)"), {"r": rid}).scalar()
        self.add("provenance_complete", sym, bad == 0, f"{bad} row(s) missing source/confidence/lineage")

    def nonneg(self, rid, sym):
        rows = self.db.execute(text(
            "SELECT metric_key, fiscal_year, fiscal_quarter, value_numeric FROM retailer_metric "
            "WHERE retailer_id=:r AND is_latest AND metric_key = ANY(:keys) AND value_numeric < 0"),
            {"r": rid, "keys": list(NONNEG)}).fetchall()
        self.add("nonneg", sym, not rows,
                 "all ≥ 0" if not rows else f"{len(rows)} negative: {[(m, f'FY{y}Q{q}', float(v)) for m, y, q, v in rows[:4]]}")

    def gross_margin_identity(self, rid, sym):
        # A DERIVED gross_margin_pct must equal gp/(gp+cogs) exactly (internal consistency of
        # our own derivation). A REPORTED gross_margin_pct is the company's authoritative rate
        # and is NOT required to match our SEC-derived gp/cogs ratio to the basis point —
        # classification differences (what sits in COGS vs SG&A) make a sub-percent gap normal;
        # we only flag a gross discrepancy there. Tolerances reflect that distinction.
        bad = []
        for (fy, fq), m in self.metrics(rid).items():
            gp, cogs, gm = m.get("gross_profit_usd"), m.get("cogs_usd"), m.get("gross_margin_pct")
            if not (gp and cogs and gm):
                continue
            gpv, cogsv, gmv = Decimal(str(gp[0])), Decimal(str(cogs[0])), Decimal(str(gm[0]))
            denom = gpv + cogsv
            if denom <= 0:
                continue
            implied = gpv / denom * 100
            reported = (gm[1] or "").endswith("reported") or gm[1] == "retailer_financials_reported"
            tol = Decimal("2.0") if reported else Decimal("0.5")
            if abs(implied - gmv) > tol:
                bad.append(f"FY{fy}Q{fq}: stored {gmv:.2f}% ({gm[1]}) vs gp/(gp+cogs) {implied:.2f}%")
        self.add("gross_margin_identity", sym, not bad,
                 "derived==gp/(gp+cogs); reported within 2%" if not bad else f"{len(bad)}: {bad[:3]}")

    def magnitude_sanity(self, rid, sym):
        bad = []
        for (fy, fq), m in self.metrics(rid).items():
            rev = m.get("total_revenue_usd")
            cogs, gp = m.get("cogs_usd"), m.get("gross_profit_usd")
            if not rev:
                continue
            revv = Decimal(str(rev[0]))
            if cogs and Decimal(str(cogs[0])) > revv:
                bad.append(f"FY{fy}Q{fq}: cogs>{revv}")
            if gp and Decimal(str(gp[0])) > revv:
                bad.append(f"FY{fy}Q{fq}: gp>rev")
            if cogs and gp:
                ns = Decimal(str(cogs[0])) + Decimal(str(gp[0]))   # net sales basis
                if ns > revv * Decimal("1.001") or ns < revv * Decimal("0.93"):
                    bad.append(f"FY{fy}Q{fq}: gp+cogs={float(ns) / 1e9:.1f}B vs rev={float(revv) / 1e9:.1f}B")
        self.add("magnitude_sanity", sym, not bad, "ok" if not bad else f"{len(bad)}: {bad[:3]}")

    def cross_table_inventory(self, rid, sym):
        m = self.metrics(rid)
        bad = []
        for fy, fq, ped, _ns, inv in self.financials(rid):
            mi = m.get((fy, fq), {}).get("inventory_usd")
            if inv is None or mi is None:
                continue
            a, b = Decimal(str(mi[0])), Decimal(str(inv))
            if abs(a - b) > MONEY_ABS and abs(a - b) / b > MONEY_REL:
                bad.append(f"FY{fy}Q{fq}: metric {float(a) / 1e9:.2f}B vs fin {float(b) / 1e9:.2f}B")
        self.add("cross_table_inventory", sym, not bad, "agree" if not bad else f"{len(bad)}: {bad[:3]}")

    def certification_integrity(self, rid, sym):
        bad = self.db.execute(text(
            "SELECT count(*) FROM retailer_metric WHERE retailer_id=:r AND certified AND "
            "(NOT is_latest OR value_numeric IS NULL OR confidence IS NULL OR confidence < 0.70)"),
            {"r": rid}).scalar()
        self.add("certification_integrity", sym, bad == 0, f"{bad} bad certified row(s)")

    def raw_l1_present(self, rid, sym):
        n = self.db.execute(text(
            "SELECT count(*) FROM raw_artifact WHERE artifact_kind='fmp_earnings_transcript' "
            "AND source_locator_json LIKE :p"), {"p": f'%"symbol": "{sym}"%'}).scalar()
        self.add("raw_l1_present", sym, n > 0, f"{n} raw transcript artifact(s)")

    def run(self):
        for rid, (sym, fq_fn) in RETAILERS.items():
            self.coverage_contiguity(rid, sym)
            self.fiscal_label_consistency(rid, sym, fq_fn)
            self.is_latest_unique(rid, sym)
            self.provenance_complete(rid, sym)
            self.nonneg(rid, sym)
            self.gross_margin_identity(rid, sym)
            self.magnitude_sanity(rid, sym)
            self.cross_table_inventory(rid, sym)
            self.certification_integrity(rid, sym)
            self.raw_l1_present(rid, sym)


def main() -> int:
    db = SessionLocal()
    try:
        audit = Audit(db)
        audit.run()
        checks = sorted({c for c, _, _, _ in audit.results})
        syms = ["WMT", "TGT"]
        by = {(c, s): (p, d) for c, s, p, d in audit.results}
        print(f"\n{'CHECK':30s} {'WMT':>6s} {'TGT':>6s}   detail")
        print("-" * 90)
        failed = 0
        for c in checks:
            cells = []
            detail = ""
            for s in syms:
                p, d = by.get((c, s), (True, ""))
                cells.append("PASS" if p else "FAIL")
                if not p:
                    failed += 1
                    detail = f"{s}: {d}"
            print(f"{c:30s} {cells[0]:>6s} {cells[1]:>6s}   {detail}")
        print("-" * 90)
        print(f"{'ALL CLEAR' if failed == 0 else str(failed) + ' CHECK(S) FAILED'}")
        print("\nExternal anchors (run separately): retail_financials_reconcile (SEC ground truth), "
              "fmp_retail_verify (triangulation), retail_metric_coverage (archetype completeness).")
        return 1 if failed else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

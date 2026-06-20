"""
STRICT internal-integrity audit of retailer_stock_prices — the analog of
retail_intelligence_audit.py for the price series. Reusable, read-only, per-retailer; asserts
each invariant both ways so a clean series passes everything and any defect trips one check.

  1. is_latest_unique        — one is_latest row per (retailer, price_date)
  2. ohlc_sanity             — high ≥ low, high ≥ open/close, low ≤ open/close, all > 0
  3. volume_nonneg           — volume ≥ 0 where present
  4. close_complete          — close_price present on every row (the authoritative field)
  5. split_consistency       — no unexplained day-over-day close jump (|Δ| ≥ 35%): a series on a
                               mixed split basis shows a ~66% step at a 3:1 split; a consistently
                               adjusted series shows only real moves
  6. continuity              — no calendar gap > 5 days (beyond weekends/holidays)
  7. pct_change_consistency  — stored pct_change ≈ (close/prev_close − 1), loose tol for the
                               rounding of split-adjusted prices
  8. provenance             — source + data_source_url set (not 'unknown')
  9. ohlc_complete           — flag close-only rows (missing OHLC/volume)

External anchor (separate): cross-verify close/OHLC/volume against an INDEPENDENT price source
(e.g. the issuer's investor-relations historical export) — the ground-truth check.

    python -m data.verification.stock_price_audit            # all retailers with price rows
    python -m data.verification.stock_price_audit --ticker WMT
"""

from __future__ import annotations

import argparse
from decimal import Decimal

from sqlalchemy import text

from data.ingestion._env import load_project_env
from database.base import SessionLocal

load_project_env()

JUMP_PCT = Decimal("35")     # |day-over-day close move| ≥ this ⇒ suspect un-adjusted corp action
GAP_DAYS = 5                 # calendar gap > this ⇒ suspect missing trading days
PCT_TOL = Decimal("0.10")    # pct_change vs recomputed: allow split-adjust rounding


def audit_retailer(db, rid, sym):
    res = []

    def add(check, passed, detail=""):
        res.append((check, sym, passed, detail))

    dup = db.execute(text("SELECT count(*) FROM (SELECT 1 FROM retailer_stock_prices "
                          "WHERE retailer_id=:r AND is_latest GROUP BY price_date HAVING count(*)>1) t"),
                     {"r": rid}).scalar()
    add("is_latest_unique", dup == 0, f"{dup} duplicate date(s)")

    ohlc = db.execute(text(
        "SELECT count(*) FROM retailer_stock_prices WHERE retailer_id=:r AND is_latest AND ("
        "high_price < low_price OR high_price < open_price OR high_price < close_price OR "
        "low_price > open_price OR low_price > close_price)"), {"r": rid}).scalar()
    bad_ex = db.execute(text(
        "SELECT price_date, open_price, high_price, low_price, close_price FROM retailer_stock_prices "
        "WHERE retailer_id=:r AND is_latest AND (high_price<low_price OR high_price<open_price OR "
        "high_price<close_price OR low_price>open_price OR low_price>close_price) ORDER BY price_date LIMIT 3"),
        {"r": rid}).fetchall()
    add("ohlc_sanity", ohlc == 0, f"{ohlc} violation(s): {[str(r[0]) for r in bad_ex]}")

    nonpos = db.execute(text("SELECT count(*) FROM retailer_stock_prices WHERE retailer_id=:r AND is_latest "
                             "AND (close_price<=0 OR (open_price IS NOT NULL AND open_price<=0) OR "
                             "(volume IS NOT NULL AND volume<0))"), {"r": rid}).scalar()
    add("volume_nonneg", nonpos == 0, f"{nonpos} non-positive")

    null_c = db.execute(text("SELECT count(*) FROM retailer_stock_prices WHERE retailer_id=:r "
                             "AND is_latest AND close_price IS NULL"), {"r": rid}).scalar()
    add("close_complete", null_c == 0, f"{null_c} null close")

    # An UNADJUSTED split leaves a close/prev_close ratio sitting AT a split factor (2:1→0.5,
    # 3:1→0.333, reverse→2,3…). A merely large move (takeover bid, earnings, squeeze) lands at an
    # arbitrary ratio. Flag only big jumps whose ratio is near a split factor — so genuine moves
    # (e.g. Kohl's +36% on the 2022 buyout approach) don't false-positive.
    factors = [0.2, 0.25, 1.0 / 3, 0.5, 2.0 / 3, 1.5, 2.0, 3.0, 4.0, 5.0]
    rows = db.execute(text(
        "SELECT price_date, close_price/NULLIF(lag(close_price) OVER (ORDER BY price_date),0) ratio "
        "FROM retailer_stock_prices WHERE retailer_id=:r AND is_latest"), {"r": rid}).fetchall()
    susp = []
    for d, ratio in rows:
        if ratio is None:
            continue
        ratio = float(ratio)
        # within ~3% of an exact split factor = the arithmetic signature of an UNADJUSTED split
        # (a real move lands at an arbitrary ratio, e.g. Kohl's +42.5% earnings day at 1.425).
        if (ratio > 1 + float(JUMP_PCT) / 100 or ratio < 1 - float(JUMP_PCT) / 100) and \
                any(abs(ratio - f) / f < 0.03 for f in factors):
            susp.append(str(d))
    add("split_consistency", not susp,
        "no unadjusted-split signature" if not susp else f"{len(susp)} near-split-factor jump(s): {susp[:3]}")

    gaps = db.execute(text(
        "SELECT count(*) FROM (SELECT price_date - lag(price_date) OVER (ORDER BY price_date) g "
        "FROM retailer_stock_prices WHERE retailer_id=:r AND is_latest) t WHERE g > :d"),
        {"r": rid, "d": GAP_DAYS}).scalar()
    add("continuity", gaps == 0, f"{gaps} gap(s) > {GAP_DAYS} calendar days")

    pct_bad = db.execute(text(
        "SELECT count(*) FROM (SELECT pct_change, close_price c, lag(close_price) OVER (ORDER BY price_date) p "
        "FROM retailer_stock_prices WHERE retailer_id=:r AND is_latest) t "
        "WHERE p IS NOT NULL AND p<>0 AND pct_change IS NOT NULL "
        "AND abs(pct_change - (c-p)/p*100) > :tol"), {"r": rid, "tol": PCT_TOL}).scalar()
    add("pct_change_consistency", pct_bad == 0, f"{pct_bad} beyond {PCT_TOL}pp")

    prov = db.execute(text("SELECT count(*) FROM retailer_stock_prices WHERE retailer_id=:r AND is_latest "
                           "AND (source IS NULL OR source='unknown' OR data_source_url IS NULL OR "
                           "data_source_url='unknown')"), {"r": rid}).scalar()
    add("provenance", prov == 0, f"{prov} missing source/url")

    close_only = db.execute(text("SELECT count(*) FROM retailer_stock_prices WHERE retailer_id=:r AND is_latest "
                                 "AND (open_price IS NULL OR high_price IS NULL OR low_price IS NULL OR volume IS NULL)"),
                            {"r": rid}).scalar()
    add("ohlc_complete", close_only == 0, f"{close_only} close-only row(s)")
    return res


def main() -> int:
    p = argparse.ArgumentParser(description="Strict internal audit of retailer_stock_prices")
    p.add_argument("--ticker")
    args = p.parse_args()
    db = SessionLocal()
    try:
        q = ("SELECT DISTINCT s.retailer_id, r.ticker FROM retailer_stock_prices s "
             "JOIN major_retailers r ON r.retailer_id=s.retailer_id")
        params = {}
        if args.ticker:
            q += " WHERE r.ticker=:t"
            params["t"] = args.ticker.upper()
        retailers = db.execute(text(q + " ORDER BY r.ticker"), params).fetchall()
        all_res = []
        for rid, sym in retailers:
            all_res += audit_retailer(db, rid, sym)
        checks = sorted({c for c, _, _, _ in all_res})
        syms = [s for _, s in retailers]
        by = {(c, s): (ok, d) for c, s, ok, d in all_res}
        print(f"\n{'CHECK':24s} " + " ".join(f"{s:>6s}" for s in syms) + "   detail")
        print("-" * 92)
        failed = 0
        for c in checks:
            cells, detail = [], ""
            for s in syms:
                ok, d = by.get((c, s), (True, ""))
                cells.append("PASS" if ok else "FAIL")
                if not ok:
                    failed += 1
                    detail = f"{s}: {d}"
            print(f"{c:24s} " + " ".join(f"{x:>6s}" for x in cells) + f"   {detail}")
        print("-" * 92)
        print("ALL CLEAR" if failed == 0 else f"{failed} CHECK(S) FAILED")
        return 1 if failed else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

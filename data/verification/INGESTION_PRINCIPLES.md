# Artemis Ingestion Principles — the constitution every gate enforces

The retail-intelligence corpus is training ground truth. These principles are the *rules*
the ingestion gates encode. They exist to do one thing: **get to the truth and maximize
completeness, with zero fabrication.** Every gate below cites the principle(s) it enforces.

## 1. Anchor on the ground, infallible truth — never on convenience or implementation order
- Financial **numbers** → anchored to **SEC filings** (audited, legally-binding, the upstream
  source from which vendors like FMP derive). A number is trusted only when it reconciles to SEC.
- **Spoken signals** (transcripts) → no single authority exists, so anchored to SEC for the
  *figures they cite* (date in the SEC filing window; EPS/revenue/comp match SEC) and to the
  **verbatim source text** for the *words*.
- We never treat a derived/convenience source, or "the thing we built first," as truth.
  *(Enforced by: `retail_financials_reconcile.py`, `verify_transcript_source.py`.)*

## 2. Triangulate; do not fixate on one source
- Cross-check independent sources. Where the SAME economic fact differs, adjudicate against the
  ground truth (SEC); where there is no single authority (transcript text), compare sources and
  keep provenance. A source is verified against an *independent* authority, never against itself.
- Per-entity calibration: the same field maps to different source concepts per company
  (Walmart net sales = `RevenueFromContract…`; Target = merchandise `SalesRevenueGoodsNet`).
  *(Enforced by: per-retailer concept maps; `concept_probe.py`; FMP-vs-existing comparison.)*

## 3. Maximize completeness; never downgrade; never fabricate
- Per record, choose the **most complete** verified source. Never replace a fuller version with a
  thinner one (a full call > a truncated transcript > an 8-K bullet).
- A missing datum is an **honest gap** — NULL it, flag it, surface it. Never impute, never
  substitute a neighbouring period, never pass a partial off as whole.
  *(Enforced by: completeness gate in `fmp_transcript_backfill.py`; `retail_metric_coverage.py`;
  the recompute that never nulls a value it can't verify.)*

## 4. Preserve the raw, untouched bytes (immutable Layer 1)
- Capture the exact source payload before any parsing, content-addressed + immutable, so every
  downstream fact traces to its bytes and re-derivation is deterministic and offline.
  *(Enforced by: `data/raw/capture.py` + `raw_capture` tables; `fmp_transcript_capture.py`.)*

## 5. Model-grade is certified BY CONSTRUCTION
- The gold (training) layer is the SUBSET that passes EVERY machine-checkable gate. A bad value
  cannot reach it because the gate excludes it. Every new defect class becomes a new permanent
  check (the ratchet) — errors trend monotonically to zero.
  *(Enforced by: `retail_metric_certify.py` (`certified` flag); partial-unique `is_latest` indexes.)*

## 6. Every datum carries provenance + confidence
- source, source_concept, source_uri, confidence, lineage on every row — so any value is
  re-checkable, and so source-reliability can be *learned later* (weighting at the signal/model
  layer, by predictive calibration — NOT hard-coded now).
  *(Enforced by: `data_quality` ledgers, `source`/`source_concept`/`confidence` columns.)*

## 7. Prove every gate both ways
- A gate is trustworthy only if it ACCEPTS the true AND REJECTS the false. Each is verified on a
  known-correct case and a deliberately-wrong case before we rely on it.
  *(Practice: the accept-correct / reject-wrong tests run for every gate change.)*

---
**One-line creed:** *anchor to the infallible truth, triangulate don't fixate, take the most
complete, preserve the raw, certify by construction, fabricate nothing.*

# Costing Data Model — extraction blueprint for RRK costing sheets

**Purpose.** Reverse-engineered from a real RRK costing workbook
(`costing sheet with formulas.xlsx`, 25 sheets, 176 style-size records, validated at
the cell-formula level). This is the canonical set of data points to capture so that
when RRK's full 30-year costing history lands, we ingest it into a structured,
trainable form — and so the cost engine can be reconstructed and calibrated.

This document defines **7 data-organization layers** + the validated cost engine +
the "process-route-from-cost-lines" logic. Every field notes its source and its
causal meaning (how cash flows through it).

---

## The validated cost engine (per garment → FOB)

```
PIECE WEIGHT (g)      = Σ panels [ (width_in[+allow]) × (length_in[+allow]) × pieces × 2.54²/10000 × panel_GSM ]
                        + flat-gram panels (neck tape, rib, pocket …)
FABRIC COST /kg       = Σ process ₹/kg (yarn + knitting + dyeing + washing + stentering + brushing + compacting)
FABRIC PRICE /kg      = FABRIC COST /kg × (1 + process_loss%)        ← loss is a %-uplift on ₹/kg, NOT on weight
FABRIC COST /garment  = FABRIC PRICE /kg × PIECE WEIGHT(kg)
GARMENT SUBTOTAL      = FABRIC COST/garment + CMT(₹/gmt) + TRIMS(₹/gmt)
+ REJECTION (≈1% of subtotal)
+ PROFIT  (≈20% of subtotal)
= GARMENT PRICE (₹)
FOB (USD)             = GARMENT PRICE ÷ FX (≈₹90/USD)
```
Cutting wastage is **absorbed in the panel allowances** (the `+1/+3` inch), not a
separate line. Each loss is applied exactly once.

---

## Layer 1 — Style / Product
| Field | Source | Causal note |
|---|---|---|
| style_id, style_name | row "Style" | identity (e.g. 16350 RFD) |
| garment_type | derived (tee/tank/crew/hoody/fleece) | drives panel set + CMT |
| construction | tubular vs side-seam | → finishing route + CMT + allowance |
| fit | regular/tall/big/youth | → measurements + size curve |
| customer / program | external (PO) | who it's for; pricing context |

## Layer 2 — Fabric(s)  *(a garment can have several)*
| Field | Source | Causal note |
|---|---|---|
| composition | row "Fabric" (100% cotton / 60-40 OE / 80-20 PC / 52-43-5 tri-blend) | the yarn-class fork |
| yarn_class | derived (ring-spun vs OE) | OE → cheap + no-dye + low-loss |
| gsm | row "Gsm" | × area → weight (linear) |
| color | white/black/… | → dyeing ₹/kg (white≈40, black≈90) |
| knit_structure | jersey/rib/interlock/fleece | → knitting ₹/kg + machine |

## Layer 3 — Panels (consumption geometry)
| Field | Source | Causal note |
|---|---|---|
| panel_name | rows (Body, Sleeve, Neck tape, Rib, Hood, Pocket) | each summed into piece weight |
| fabric_ref | → Layer 2 | panel may use a different fabric/GSM (hood 510 vs body 350) |
| dims (chest, full_length, sleeve_length, bicep, sl_open, hood_ht, hood_length) | rows 5-11 | the rectangle |
| length_allowance, width_allowance | embedded in formula (+1/+3) | **tacit technician knowledge — LEARN from history** |
| pieces (×2 front+back / ×2 sleeves) | formula | |
| conversion | 2.54² (body) / 2.54×2.4 (sleeve taper) | inch²→m² |
| panel_gsm | per-panel | multi-fabric support |
| computed_weight_g OR flat_gram | formula or constant | neck tape 6, rib 6/85, pocket 35 = constants |

## Layer 4 — Size curve
| Field | Source | Causal note |
|---|---|---|
| size (S/M/L/XL/2XL/3XL…) | columns/rows | each size = own measurements → own piece weight |
| per_size_piece_weight | "Total pc wt" per size | scales ~linearly with size |
| program_size_ratio | external (PO) e.g. 1-2-2-1-1 | **program piece wt = qty-weighted avg across sizes** |

## Layer 5 — Fabric-cost (₹/kg) — the PROCESS ROUTE
| Field | Source | Causal note (presence = route taken) |
|---|---|---|
| yarn_per_kg | row "Yarn" | biggest + most volatile; ring-spun≫OE |
| knitting_per_kg | row "Knitting" | jersey 10, fleece/heavy 14, spandex 40 |
| dyeing_per_kg | row "Dyeing" | **absent for pre-colored OE**; color-driven (white 40 / black 90) |
| washing_per_kg | row "Washing" | garment/OE wash route |
| stentering_per_kg | row "Stentering" | **open-width only** (side-seam) |
| brushing_per_kg | row "Brushing" | **fleece only** |
| compacting_per_kg | row "Compacting" | tubular 5 / open-width 9 |
| process_loss_pct | row "Process loss" formula | by blend/color/route: seen 3 (OE) / 6 (cotton tubular) / 8 (cotton std) / 10 (tri-blend) — **LEARN, don't hard-code** |

## Layer 6 — Garment-cost & margin (per garment)
| Field | Source | Causal note |
|---|---|---|
| fabric_cost_per_garment | = price/kg × pcwt | the kg→piece bridge |
| cmt_per_garment | row "Cmt" | tank 8 / tee 12-15 / crew-fleece 20 / hoody 35-45; +₹4 for side-seam |
| trims_per_garment | row "Trims" | 4.35-10 |
| rejection_pct | row "Rejection" | ≈1% |
| profit_pct | row "Profit" | ≈20% (relationship/volume/target-driven) |
| garment_price_inr | row "Garment Price" | |
| fx_rate, fob_usd | row "Fob" | ≈₹90/USD assumption |

## Layer 7 — Provenance / context
date, source_sheet, costed_by, quantity, importer, season — for time-series &
calibration (so we can track how rates moved and tie to the macro layer).

---

## The OE-vs-ring-spun fork, in real numbers (the price-tier divide)
| | Ring-spun cotton | OE / recycled |
|---|---|---|
| yarn ₹/kg | 271-289 | 160-185 |
| dyeing | yes (35-90) | **none (pre-colored)** |
| process loss | 8% | 3% |
This single fork explains most of the FOB spread between "quality" and
"price-conscious" programs — and it's exactly the Layer-1 yarn-class branch.

## What the system must LEARN (not hard-code) from RRK history
- **Panel allowances** (`+1/+3`) — tacit, per style/construction.
- **Process loss %** — actual values vary (3/6/8/10), set by judgment.
- **Flat-gram panels** (rib/neck-tape/pocket) — standard weights per garment type.
- **CMT ₹/garment** — by garment type + construction.
- **Process route** — which ₹/kg lines apply, inferred from fabric + construction.
- **Profit %** — relationship/volume-driven.

## Mapping to the 9-World schema
Layers 1-4 → `product_specification`, `knit_fabric`, `garment_*`; Layer 5-6 →
`fob_price_calculation` (needs these granular fields added); Layer 7 → `program` +
ingestion provenance. The ₹/kg process lines connect to the macro layer
(yarn←cotton/MCX, dyeing←dye-chem/petroleum, etc.) for cross-layer transmission.

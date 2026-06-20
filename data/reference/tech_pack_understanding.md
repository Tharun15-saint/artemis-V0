# Tech Pack Understanding — the input layer to the cost & reasoning engine

**Purpose.** A tech pack is the spec a buyer (importer like Classic Fashion) sends a
manufacturer. It is the **parameter vector** that drives costing *and* every other
form of reasoning. This document defines: (1) the canonical tech-pack schema we
extract into, (2) how each field feeds costing vs other reasoning, (3) the clean
separation of where each cost factor comes from, and (4) the extraction +
learning pipeline. Pairs with `costing_data_model.md` (the output side).

Tech packs are heterogeneous (every buyer's template differs) and multimodal
(PDF/Excel with tables, flat sketches, graded measurement charts, artwork). So
extraction must be **multimodal (vision-LLM) → canonical schema**, with
normalization and human-in-the-loop validation.

---

## The core idea

```
COST(style) = f( TechPack_physical_params ,  Learned_params ,  Market_rates ,  Relationship )
```
- **TechPack_physical_params** — comes FROM the tech pack (fabric, GSM, colour,
  measurements, construction, BOM, print, wash, testing, size range).
- **Learned_params** — NOT in the tech pack; LEARNED from RRK history
  (panel allowances, process-loss %, flat-gram panels, CMT ₹/gmt by type, panel
  decomposition).
- **Market_rates** — from the data layer (yarn ₹/kg←MCX cotton, dyeing←dye-chem/
  petroleum, FX, freight, duty).
- **Relationship/judgment** — profit %, payment-term cash lever, mill choice.

The tech pack supplies the *physical* knobs; the system supplies the rest. The same
structured tech pack also powers feasibility, risk, substitution and lead-time
reasoning — not just cost.

---

## Canonical tech-pack schema (extraction target)

### A. Header / identity
style_no, style_name, brand/customer, importer, season, category, gender/age
(mens/womens/youth), tech_pack_version, date.

### B. Garment classification
garment_type (tee/tank/polo/hoody/crew/fleece), fit/silhouette, sleeve_type
(short/long/raglan/set-in), neck_type (crew/v/polo/hood), construction
(tubular vs side-seam).  → drives panel set, CMT, finishing route.

### C. BOM — fabric(s)  *(list; a garment has several)*
per fabric: placement (body/rib/hood/pocket/contrast), composition (100% cotton /
CVC / PC / blend %), yarn_count, yarn_class (ring-spun/OE), knit_structure
(jersey/rib/interlock/fleece), GSM, colour/pantone, finish (wash/wicking/brushed).
→ cost Layer 2 + process route (Layer 5) + compliance.

### D. BOM — trims
per item: type (main/care/size label, hangtag, button, zipper, drawcord, eyelet,
thread, polybag, carton), spec, qty, placement, nominated_supplier?.
→ trims cost (Layer 6) + lead-time risk (nominated / MOQ / Chinese-New-Year).

### E. Measurement spec (POM — points of measure)
per POM: name (chest/half-chest, body length, sleeve length, bicep, armhole,
neck width, shoulder, hem opening…), value PER SIZE (grading), tolerance;
+ size_range, base_size.  → piece-weight panel geometry (Layer 3) + size curve
(Layer 4). NOTE: buyers name POMs differently → normalization required.

### F. Construction / operations
seam types, stitch type + SPI, hems, neck/rib binding, special ops (coverstitch,
flatlock).  → CMT complexity (operation count) + quality risk (spirality).

### G. Print / embroidery
per placement: technique (screen/sublimation/HD/puff/emb), dimensions (H×W),
#colours, #placements, artwork ref.  → print cost + lead-time.

### H. Colour / colourways
list of colourways; lab-dip / QTX / pantone refs.  → dyeing ₹/kg (colour-driven) +
sampling rounds (time).

### I. Wash / finish
garment wash (enzyme/bio/acid/mineral), softener type.  → process route + cost.

### J. Testing / compliance
buyer test protocol (e.g. Walmart), fibre-content/care, UFLPA/origin declaration,
AATCC tests.  → testing cost + compliance risk.

### K. Packing
fold type, polybag, carton config, size ratio.  → packing cost.

### L. Order context (often a separate PO)
quantity, size_ratio (e.g. 1-2-2-1-1), target_price, delivery_date.

---

## What each section feeds (cost AND beyond)
| Tech-pack section | Costing | Other reasoning |
|---|---|---|
| Fabric BOM (C) | yarn + process ₹/kg, route | feasibility (gauge/structure), compliance (origin), substitution |
| Measurements (E) | piece weight + size curve | grading checks, fit consistency |
| Construction (F) | CMT operation count | lead-time, spirality/quality risk |
| Trims (D) | trims ₹/gmt | lead-time risk (nominated/MOQ/holidays) |
| Print/emb (G) | print ₹/gmt | lead-time, capability |
| Colourways (H) | dyeing ₹/kg | sampling rounds (time), shade-match risk |
| Wash/finish (I) | finishing ₹/kg | hand-feel, shrinkage risk |
| Testing/compliance (J) | testing cost | pass/fail risk, chargeback exposure |

---

## Extraction + learning pipeline
1. **Ingest** — accept PDF/Excel/image tech packs.
2. **Multimodal extract** (vision-LLM / Claude) → canonical schema JSON, with
   **per-field confidence**.
3. **Normalize** — map buyer vocabulary → our ontology (e.g. "½ chest"→chest;
   "100% CMB CTN 30s"→ring-spun/combed/30s; pantone→colour); unify units
   (inch/cm).
4. **Validate / human-in-loop** — low-confidence or missing fields flagged for a
   technician; never silently guess (no-data > wrong-data). Tacit fields
   (allowances) are filled by the **learned** model, clearly tagged as inferred.
5. **Attach costing** — run the cost engine on the extracted params + learned +
   market rates → per-style cost, with every driver traceable.
6. **Learn** — RRK history gives **(tech pack ↔ realized costing sheet ↔ PO/outcome)**
   triples → train: tech-pack features → piece weight, cost, lead-time, quality
   risk, win/loss. Over time, predict cost & feasibility from a fresh tech pack
   alone, and surface value-engineering options.

## Reasoning the structured tech pack unlocks (beyond cost)
- **Feasibility** — can our machines do this gauge/structure/print?
- **Value engineering** — cheaper yarn/route/construction that meets the spec.
- **Risk** — compliance (UFLPA/origin), complexity→delay, spirality/shade-match.
- **Comparison** — nearest past styles → reuse their realized costs/issues.
- **Lead-time estimate** — from construction + trims + sampling + holidays.
- **What-if** — "if cotton/crude moves X%, this style's FOB moves Y%."

## REAL-WORLD GROUNDING — 6 Walmart tech packs (George / Athletic Works, via Classic Fashion → RRK)

Validated against real PLM tech packs. The chain is explicit in the document:
**Brand `George` (Walmart) → Supplier `IMPORT-CLASSIC FASHION APPAREL INDUSTRY LTD` (CO 014110-23, contact Manjunath @cfaiteam.com) → Factory `RK Cotton` (RRK, Factory ID 36214464) → Country India.** Volumes are huge (one style = ~6M units across colorways with % mix).

### A multi-page PLM tech pack has these page types:
1. **Line-sheet / summary** — supplier(importer), factory(RRK), brand, item desc, fabric content, GSM (BY COLOUR: e.g. regular 170 / vivid white 190), colorways + volume %, sizes, units, finish/wash, care, labels, packing, and cost fields (First/Store Cost, Quote ID = **TBD** here — cost is negotiated separately).
2. **Graded measurement / POM page** — the geometry source (see mapping below).
3. **Construction / ISO-stitch page** — seam-by-seam operations + seam allowances.
4. **BOM** — fabrics (body/rib/neck-tape, each composition + GSM + colour; incl. BCI/blends) + trims.
5. **Artwork / branding** — print/embroidery/label placements + construction.
6. **Sketches** (image-only — needs vision).

### POM-code system (buyer-specific) → our canonical panel dims (Walmart/George example)
| Walmart POM | Means | → our dim |
|---|---|---|
| A-09 | Front length HPS→hem | body **length** |
| E-01 | Bust/chest 1″ below armhole | body **chest** (width) |
| J-01 / J-11 | Sweep width / hem height | bottom width / **hem fold allowance** |
| B-01 / B-19 | Neck width / neck-trim height | rib-neck **length / height** |
| D-03 | Across shoulder | shoulder |
| G-10 | Sleeve length from CB neck | **sleeve length** (subtract ½ shoulder for panel) |
| G-52 | Bicep 1″ below armhole | sleeve **width** |
| G-63 / G-81 | Sleeve opening / cuff height | cuff width / **cuff fold allowance** |
| F-05 | Armhole along curve | armhole |
Each POM is **graded per size** with **+/- tolerances**, in separate **REG (S–XL)** and **BIG/TALL (2XL–5XL)** measurement sets. → normalization must map each buyer's POM codes/names to these canonical dims.

### Construction page → CMT *and* the allowance (shrinks the "tacit" gap)
ISO stitch codes (301 lockstitch, 401 chain, 406/407 coverstitch, 504/514/516 overlock/safety, 605 coverstitch…) with a **seam allowance per seam**: side-seam/armhole 1/4″, shoulder/neck-rib 1/8″, back-neck-tape 3/8″, bottom-hem self-turn 1/4″. ⇒
- **CMT** ≈ count & type of operations (this LS crew tee ≈ 8 ops: shoulder, neck-rib attach, back-neck-tape, armhole+topstitch, side+inseam, cuff-rib+topstitch, bottom-hem) → SAM → cost.
- **Consumption allowance** ≈ Σ(seam allowances) + hem/cuff fold heights (J-11, G-81) + a **learned shrinkage/process residual.** So the allowance is *mostly derivable from the tech pack*, with only the residual learned from RRK history — a big precision gain over treating it as fully tacit.

### Linkage & format variation
- Tech pack ↔ costing sheet link by **PLM# / Style#** (cost is TBD on the TP, computed on the costing sheet we already decoded).
- Walmart PLM packs are standardized (POM codes, ISO stitches); other buyers (e.g. the `9146` pack) are freer text ("230GSM STRETCH", "160 GSM NON SLUB", embroidery notes) → the normalizer must handle both.

## Mapping to the 9-World schema
Header/classification → `product_specification`; fabric BOM → `knit_fabric`/fabric;
measurements → product spec + a measurement/POM table; trims/print → BOM tables;
the extracted style links 1:1 to its `fob_price_calculation`. The tech pack is the
World-1/World-3 input; the costing engine produces World-5 financial outputs.

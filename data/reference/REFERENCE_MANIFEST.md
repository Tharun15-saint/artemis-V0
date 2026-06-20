# Reference data manifest

A tracked inventory of the source documents collected for the cost-engine / causal-map
work. **The files themselves are NOT in git** — they are large and buyer-confidential
(several are stamped "Walmart Confidential"), so `.gitignore` excludes them
(`*.xlsx *.xls *.pdf *.jpg *.jpeg *.png *.csv`). This manifest is the durable record of
*what exists*; the bytes live only on the local machine.

> ⚠️ **BACK UP `data/reference/` EXTERNALLY** (encrypted drive / private cloud). These
> are irreplaceable RRK / Walmart source documents and are not version-controlled.

Last updated: 2026-06-20.

## Costing (cost-engine ground truth)
| File | What it is | Analyzed? |
|---|---|---|
| `costing sheet with formulas.xlsx` | RRK costing workbook, 25 styles, live formulas — the validated cost engine | ✅ fully (engine + blind test) |
| `BT CREW FLEECE-MASTER-042623.xlsx` | Costing master — crew fleece | ⏳ not yet |
| `BT CREW TEE-MASTER-042623.xls` | Costing master — crew tee | ⏳ not yet |
| `BT HOOD-MASTER-042623.xls` | Costing master — hood | ⏳ not yet |

## Tech packs (Walmart PLM: brand → Classic Fashion (importer) → RRK)
| File | What it is | Analyzed? |
|---|---|---|
| `D23_GE33100077465_GE SOLID CREW LS TEE_CLASSIC - 11 25 RK.pdf` | George solid crew LS — explicitly Classic Fashion + RK Cotton | ✅ structure |
| `GEORGE GE LS Cotton Tee - 100013591.pdf` | George LS cotton tee — full POM + construction | ✅ structure + POM |
| `GEORGE LS Crew Tee - 100125475-12-26-22.pdf` | George LS crew tee — POM + ISO stitch | ✅ structure |
| `ATHLETIC WORKS AW Tri-Blend Tee - 100096923 -8-22-22.pdf` | Athletic Works tri-blend tee — POM | ✅ structure |
| `PROMO_100139377 CF22454 AW Fleece Crew TP 4.15.pdf` | Athletic Works fleece crew (CF = Classic Fashion) | ✅ partial |
| `9146 HOL21 TO FEB-27.pdf` | Non-Walmart buyer format (free-text) | ✅ partial |

## Spec / measurement sheets (match the costing styles)
| File | What it is | Analyzed? |
|---|---|---|
| `Men's Spec Sheet 7160:7160R:7145:7145RP.pdf` | RRK spec — matches costing cols 7145/7160 (used in the Golden Loop) | ✅ + validated |
| `Men's Spec sheet - l.xlsx` | Spec sheet (Excel) | ⏳ not yet |
| `Style 2000T.pdf` / `Style 2400.pdf` / `Style 5400.pdf` | Gildan blanks — measurements + packaging weights by colour | ✅ structure |
| `SHAGA TECH DETAILS A4302600 1ST.pdf` / `…A4302602 1ST.pdf` | SHAGA BOM + trims (brand Volcom, via NATCO) | ✅ BOM |

## Yarn / market data
| File | What it is | Analyzed? |
|---|---|---|
| `01.04.2026 TEX PRICE LIST-1.pdf` | Tirumalai Textiles — OE recycled 60/40 coloured yarn price list | ✅ |
| `RRK-1.pdf` | Real RRK yarn purchase order (28s black cotton, HSN 52061300, ₹212/kg) | ✅ |
| `wb_cmo_monthly.xlsx` | World Bank commodity monthly (Pink Sheet) | reference |

## Tariff / trade
| File | What it is | Analyzed? |
|---|---|---|
| `hts_2026_revision_10_xls.xlsx` | US Harmonized Tariff Schedule 2026 rev.10 — for the duty layer | reference |

## Images (likely tech-pack photos)
`WhatsApp Image 2026-04-09 at 00.20.11/.12/.13.jpeg` — ⏳ not yet reviewed.

## Blueprints (ours — these ARE in git)
`costing_data_model.md` (costing extraction blueprint) · `tech_pack_understanding.md` (tech-pack extraction blueprint).

## Broader RRK corpus (outside the repo, in ~/Downloads — not yet curated)
- `~/Downloads/06_RRK/03_Products/Yarn Against Order RRK - May 2025 to May 2026.xlsx` — the 1-yr yarn purchase ledger (used in calibration).
- `~/Downloads/09_MISCELLANEOUS_DOCUMENTS/` — RRK customs invoices, import-fabric docs, MOU, pitch deck, yarn sample data, etc.
- The full 30-year RRK database arrives on hard disk (~2026-06-21/22).

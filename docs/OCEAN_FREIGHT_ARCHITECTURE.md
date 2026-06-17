# Ocean Freight Architecture

Status as of the 2026-06 integrity rebuild. This document covers the freight
layer, the crude→freight transmission model, and the staged path to paid
per-lane data.

## Principle

No data is better than wrong data. Every freight number in the database is
either a real published assessment or an explicitly-flagged proxy. We never
store a fabricated or derived-by-constant rate as if it were a market
observation.

## Current state (free path — LIVE)

```
Brent crude (FRED DCOILBRENTEU)
        │  crude→bunker leg: CALIBRATED, R²≈0.95, n=1895, ~0.028 $/gal per $1/bbl
        ▼
Bunker fuel proxy (EIA distillate spot, is_proxy=True)
   • US Gulf Coast ULSD          EER_EPD2DXL0_PF4_RGC_DPG  (2006→)
   • New York Harbor heating oil EER_EPD2F_PF4_Y35NY_DPG   (1990→)
        │  bunker→freight leg: PENDING (needs freight history)
        ▼
Ocean freight (Drewry WCI public page, weekly)
   • Shanghai→LA, Shanghai→NY    (drewry_wci_direct, real)
   • WCI global composite        (drewry_wci_composite, real benchmark series)
```

### Tables
- `ocean_freight_rates` — one append-only row per (origin_port, destination_port);
  `rate_40ft_hc_usd` is the WCI 40HQ unit. `rate_source_tier` ∈
  {`drewry_wci_direct`, `drewry_wci_composite`}. `is_latest` scoped per corridor.
- `bunker_fuel_prices` — distillate proxy now (`is_proxy=True`, `proxy_basis`
  documents the substitution); real VLSFO later (`is_proxy=False`, grade
  `VLSFO`, unit `USD/tonne`) with NO schema change.
- `crude_transmission_calibration` — `crude_to_bunker_fuel_*` rows hold the
  fitted lag/coefficient/R²/n (is_active=1). `freight_energy_surcharge` stays an
  inactive industry-prior until the bunker→freight leg can be fit on real data.

### Scripts
| Script | Purpose |
|---|---|
| `ocean_freight_drewry.py` | Weekly Drewry WCI scrape — real corridors + composite, no derivation, dedup-guarded |
| `ocean_freight_derived_purge.py` | One-time: removed 31 collinear derived + 6 manual rows |
| `bunker_fuel_ingestion.py` | EIA distillate spot, `--backfill` (1990→) / `--run-once` |
| `crude_bunker_calibration.py` | Fits crude→bunker lag/coeff/R², persists calibration |

### Surfaced in intelligence
`intelligence/synthesis.py::get_bunker_fuel_snapshot` exposes latest proxy
prices, current Brent, and the crude→bunker transmission with a plain-English
interpretation. Included in `get_freight_snapshot` and the market brief.

## Why crude→freight goes through bunker

Crude does not move freight directly. The mechanism is:
`crude → refined marine fuel (VLSFO) → Bunker Adjustment Factor surcharge →
spot freight rate`. Modelling the physical chain (with a measurable first leg)
is more honest and more accurate than regressing freight on crude directly,
which would conflate fuel pass-through with capacity/demand cycles.

## Staged roadmap to paid per-lane data

### Stage 1 — Ship & Bunker VLSFO (real marine fuel)
- Replaces the distillate proxy with port-specific VLSFO (Singapore = Asia
  origins, Rotterdam = Europe, Fujairah = Middle East, Houston = US Gulf).
- Currently returns HTTP 403 (Cloudflare) to simple fetches — needs a
  browser-grade client or a licensed feed.
- Schema-ready: insert with `is_proxy=False`, `grade='VLSFO'`,
  `price_unit='USD/tonne'`. Re-run `crude_bunker_calibration.py` to refit on
  real VLSFO. No migration needed.

### Stage 2 — Freightos FBX / Drewry per-lane API (PAID)
Freightos FBX API and Drewry's data feed are **paid** (Freightos "Full
Platform" tier; pricing is quote-only — contact sales). The free tiers give a
webpage + 30-day window, no API, no history. This is what unlocks real
Asian/Indian-subcontinent corridor rates (Chittagong, Chennai, Nhava Sheva,
Tuticorin, Colombo, HCMC) that we refuse to derive.

Planned schema additions (run only when subscribing — keep speculative columns
out until then):
- `ocean_freight_rates.index_component` — which FBX/WCI lane a row maps to
  (e.g. `FBX03`, `FBX11`).
- `ocean_freight_rates.rate_basis` — `spot` | `contract`.
- New `rate_source_tier` values: `fbx_lane_direct`, `drewry_lane_direct`.

Integration points already stubbed:
- `data/ingestion/ocean_freight_fbx_diagnostic.py` — reads `FREIGHTOS_API_KEY`
  from env (never hardcode), probes endpoints, inspects payload shape. This
  becomes the adapter once a key + endpoint contract is confirmed.

### Stage 3 — bunker→freight calibration
Once either (a) enough forward weeks of Drewry composite accumulate, or (b) FBX
historical per-lane data is licensed, fit the second leg
(`bunker → freight rate`) and activate the `freight_energy_surcharge`
calibration. Only then does the full crude→freight coefficient become real.

## What we need from the operator
- Decision + budget for FBX or Drewry subscription (Stage 2). Once obtained:
  the API key (set `FREIGHTOS_API_KEY` in `.env`) and the endpoint/contract docs.
- Optional: a licensed VLSFO source for Stage 1 if Ship & Bunker scraping
  stays blocked.

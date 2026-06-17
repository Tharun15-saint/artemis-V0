# Artemis Data Sources

## Live
| Entity | Source | Frequency | Script |
|---|---|---|---|
| cotton | ICE yfinance | Daily | data/ingestion/cotton.py |
| fx_rates | ExchangeRate-API | Daily | data/ingestion/fx.py |
| cotton.wasde_* | USDA FAS | Monthly | data/ingestion/wasde.py |
| commodity_futures | ICE yfinance | Daily | data/ingestion/futures.py |
| crude_oil | FRED | Daily | data/ingestion/crude.py |
| ocean_freight_rates | Drewry WCI (public page) | Weekly (Thu) | data/ingestion/ocean_freight_drewry.py |
| bunker_fuel_prices | EIA distillate spot (VLSFO proxy) | Weekly | data/ingestion/bunker_fuel_ingestion.py |
| crude→bunker calibration | derived (Brent×EIA distillate) | on-demand | data/ingestion/crude_bunker_calibration.py |

Ocean freight notes (2026-06 integrity rebuild):
- REAL DATA ONLY. Drewry direct corridors (Shanghai→LA/NY) + WCI global composite.
  Derived Shanghai×constant corridors were purged (collinear, zero info).
- Crude→freight runs via crude→bunker(distillate proxy)→freight. The crude→bunker
  leg is calibrated on decades of data (R²≈0.95). The bunker→freight leg is
  pending freight history — see docs/OCEAN_FREIGHT_ARCHITECTURE.md.

## Not Connected — Build Priority
1. Freightos FBX / Drewry per-lane API (PAID) — real Asian/Indian-subcontinent
   corridor spot rates + 5yr history. See docs/OCEAN_FREIGHT_ARCHITECTURE.md.
2. Ship & Bunker VLSFO (real marine fuel, currently 403/Cloudflare) — replaces
   distillate proxy with port-specific VLSFO (Singapore, Rotterdam, Fujairah).
3. yarn — Tirupur 30yr invoice database
4. carrier network — sign up freight forwarders
5. cmt_factories OTD — real factory data

## Staleness Thresholds
cotton:          error if as_of_date > 2 days
fx_rates:        error if as_of_date > 2 days
crude_oil:       warn > 5 days, error > 7 days
ocean_freight:   warn > 8 days
yarn:            use confidence_score weighting (currently 0.68)

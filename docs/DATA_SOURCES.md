# Artemis Data Sources

## Live
| Entity | Source | Frequency | Script |
|---|---|---|---|
| cotton | ICE yfinance | Daily | data/ingestion/cotton.py |
| fx_rates | ExchangeRate-API | Daily | data/ingestion/fx.py |
| cotton.wasde_* | USDA FAS | Monthly | data/ingestion/wasde.py |
| commodity_futures | ICE yfinance | Daily | data/ingestion/futures.py |
| crude_oil | FRED | Daily | data/ingestion/crude.py |

## Not Connected — Build Priority
1. ocean_freight_rates — Drewry WCI Thursday
2. yarn — Tirupur 30yr invoice database
3. carrier network — sign up freight forwarders
4. cmt_factories OTD — real factory data

## Staleness Thresholds
cotton:          error if as_of_date > 2 days
fx_rates:        error if as_of_date > 2 days
crude_oil:       warn > 5 days, error > 7 days
ocean_freight:   warn > 8 days
yarn:            use confidence_score weighting (currently 0.68)

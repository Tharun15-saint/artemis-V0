# ARTEMIS — MASTER SCHEMA
# Source of truth for all database entities.
# Every model, every endpoint, every feature must match this exactly.
# Last updated: June 2026 — Version 3 Final (100% complete)

---

## ZONE 1 — INTELLIGENCE PLANE

### cotton
| Field | Type | Notes |
|---|---|---|
| cotton_id | Integer PK | |
| origin_country | Varchar(100) | US / India / Pakistan / West Africa / Australia |
| grade | Varchar(50) | |
| staple_length | Varchar(30) | e.g. "1-1/16 inch" |
| spot_price | Decimal(10,4) | ICE CT=F |
| ice_futures_near | Decimal(10,4) | Near-month contract |
| ice_futures_3m | Decimal(10,4) | CTN26 equivalent |
| ice_futures_6m | Decimal(10,4) | CTV26 equivalent |
| ice_futures_9m | Decimal(10,4) | CTZ26 equivalent |
| ice_futures_12m | Decimal(10,4) | CTH27 equivalent |
| contango_signal | Decimal(10,4) | (12m - spot) / spot × 100 |
| wasde_forecast | Decimal(10,4) | |
| wasde_ending_stocks | Decimal(14,2) | Million bales |
| wasde_su_ratio_pct | Decimal(6,2) | Stocks-to-use % |
| crop_year | Integer | |
| as_of_date | Date | Market date of data |
| source | Varchar(255) | "ICE yfinance / USDA FAS" |
| refresh | Varchar(50) | "daily" |
| created_at | Timestamp | |
| updated_at | Timestamp | |

Signal rule: contango_signal > 5.0 AND unhedged program → create hedge_opportunity

### crude_oil
| Field | Type | Notes |
|---|---|---|
| crude_oil_id | Integer PK | |
| brent_spot | Decimal(10,4) | eia_daily=RBRTE daily / fred_api=DCOILBRENTEU weekly EOP |
| wti_spot | Decimal(10,4) | eia_daily=RWTC daily / fred_api=DCOILWTICO weekly EOP |
| brent_wti_spread_usd | Decimal(10,4) | Brent − WTI (normally +$2-5) |
| brent_rolling_4w_avg | Decimal(10,4) | 28-day rolling avg Brent — primary dyeing trigger signal |
| brent_rolling_13w_avg | Decimal(10,4) | 91-day rolling avg Brent (eia_daily) — slower trend detection |
| brent_t_minus_4w | Decimal(10,4) | Brent spot 28 days prior — the crude input price for dyeing |
| brent_t_minus_8w | Decimal(10,4) | Brent spot 56 days prior (eia_daily) — 8-week lag hypothesis test |
| brent_yoy_pct | Decimal(6,2) | Year-over-year % change (eia_daily) |
| wti_brent_spread | Decimal(8,4) | WTI − Brent (eia_daily) — corridor basis (US off WTI, Asia off Brent) |
| brent_dyeing_premium_active | Boolean | **DEPRECATED as a signal** — no longer computed at ingest. Dyeing premium is PENDING empirical calibration; CrudeCostInputs returns None until activated. |
| brent_inr_per_barrel | Decimal(14,4) | Brent × INR/USD — Tirupur polyester cost anchor |
| trend_30d_pct | Decimal(6,2) | 30d % change in Brent (materialized) |
| price_anomaly_flag | Boolean | True when > 3σ from 30d rolling mean |
| price_anomaly_sigma | Decimal(8,4) | Z-score at time of anomaly |
| aggregation_period | Varchar(20) | "weekly_eop" / "monthly" etc. |
| as_of_date | Date | Market date of data |
| is_latest | Boolean | True for most-recent row per source |
| source | Varchar(255) | "eia_daily" (primary, daily) / "fred_api" (weekly EOP) / "world_bank_pink_sheet" (monthly anchor) / "eia_petroleum_futures" (futures curve) |
| created_at | Timestamp | |
| updated_at | Timestamp | |
| **Futures curve fields (source = 'eia_petroleum_futures')** | | |
| wti_futures_1m | Decimal(10,4) | EIA RCLC1 — NYMEX WTI 1st nearby (USD/bbl) — exchange-settled |
| wti_futures_3m | Decimal(10,4) | EIA RCLC3 — NYMEX WTI 3rd nearby — exchange-settled |
| wti_futures_6m | Decimal(10,4) | EIA RCLC4 proxy — NYMEX WTI 4th nearby as 6m stand-in |
| wti_futures_12m | Decimal(10,4) | EIA STEO WTIPUUS T+12 — 12-month WTI outlook |
| brent_futures_1m | Decimal(10,4) | EIA STEO BREPUUS T+1 — **STEO forecast, NOT ICE settlement** |
| brent_futures_3m | Decimal(10,4) | EIA STEO BREPUUS T+3 — **STEO forecast, NOT ICE settlement** |
| brent_futures_6m | Decimal(10,4) | EIA STEO BREPUUS T+6 — **STEO forecast, NOT ICE settlement** |
| brent_futures_12m | Decimal(10,4) | EIA STEO BREPUUS T+12 — STEO forecast unless real curve sourced |
| brent_futures_source | Varchar(50) | "ice_yfinance" (real front-month) / "cme_delayed" (real full curve) / "steo_forecast" |
| brent_futures_is_market_price | Boolean | True if a real market settlement, False if STEO forecast |
| brent_futures_delay_minutes | Integer | 0 settlement, 15 delayed, NULL for STEO |

> **NOTE — Brent forward provenance.** Free real-market sources cover only the front-month
> (Yahoo `BZ=F`, real ICE settlement). The 3m/6m/12m term structure has no free real-market
> source (CME bot-blocks; Yahoo carries no monthly Brent contracts), so those tenors remain
> EIA STEO forecast (is_market_price=False). When only the front-month is real, the row's
> brent_contango_signal and crude_market_structure are NULLed — a single real tenor against a
> STEO 12m is not a valid market structure (no mixed-source spread is stored).
> Forward confidence: **0.85** for a real market tenor, **0.55** (STEO_CONFIDENCE_CAP) for forecast.
> WTI tenors (RCLC1-4) are exchange-traded settlement prices and carry no such cap for short tenors.
| **Signal fields** | | |
| wti_contango_signal | Decimal(6,4) | (RCLC4 − RCLC1) / RCLC1 × 100 — authoritative market structure |
| brent_contango_signal | Decimal(6,4) | Proxy = wti_contango_signal (no Brent NYMEX futures from EIA) |
| crude_market_structure | Varchar(20) | "contango" / "backwardation" / "flat" — from NYMEX spread |
| data_quality_flag | Varchar(50) | e.g. "DEVIATION_FLAGGED" |
| data_quality_note | Text | Human-readable note from deviation audit |

Signal rule: PENDING CALIBRATION — crude price data is live and stored. The dyeing cost transmission coefficient requires empirical calibration from RRK invoice data (n≥20, R²≥0.40, p<0.01) before any dyeing signal fires. Until then CrudeCostInputs.get_dyeing_pressure() returns dyeing_premium_active=None with calibration_status='PENDING'.
Signal rule: crude_market_structure = 'contango' AND polyester exposure → hedge_opportunity_recommendation (fires only on a full consistent real or STEO curve; NULL structure on a partial-real curve does not fire)

### px_paraxylene
| Field | Type | Notes |
|---|---|---|
| px_id | Integer PK | |
| asian_spot_price | Decimal(10,4) | |
| pta_price_lag_1_2w | Decimal(10,4) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### pta
| Field | Type | Notes |
|---|---|---|
| pta_id | Integer PK | |
| chinese_spot | Decimal(10,4) | |
| asian_export | Decimal(10,4) | |
| polyester_chip_price | Decimal(10,4) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### polyester_pet_chips
| Field | Type | Notes |
|---|---|---|
| chip_id | Integer PK | |
| chinese_spot | Decimal(10,4) | |
| asian_spot | Decimal(10,4) | |
| polyester_yarn_price_lag_2_4w | Decimal(10,4) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### viscose_rayon
| Field | Type | Notes |
|---|---|---|
| viscose_id | Integer PK | |
| asian_spot_price | Decimal(10,4) | |
| blended_yarn_price | Decimal(10,4) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### yarn

Extended with RRK transaction fields for factory-level yarn purchase records.
Price data is at the factory gate in Tirupur — real transaction prices not market estimates.

| Field | Type | Description | Source | Nullable |
|---|---|---|---|---|
| yarn_id | Integer PK | Primary key | — | NOT NULL |
| fibre_type | Varchar(100) | cotton/polyester/viscose/blended or fabric_not_yarn | rrk_excel_import | YES |
| count | Varchar(50) | Yarn count e.g. 20s/30s/40s parsed from PARTICULARS | rrk_excel_import | YES |
| spinning_method | Varchar(100) | semi_combed/combed/open_end/recycled/vortex/slug | rrk_excel_import | YES |
| grade | Varchar(50) | carded/combed | — | YES |
| origin_city | Varchar(100) | Tirupur/Gazipur/Guangdong etc | rrk_excel_import | YES |
| origin_country | Varchar(100) | Country of origin — 'India' for RRK data | rrk_excel_import | YES |
| price_per_kg | Decimal(10,4) | Validated PO rate in INR per kg (local currency) | rrk_excel_import | YES |
| price_per_kg_usd | Decimal(10,4) | USD equivalent — NULL until FX applied | rrk_excel_import | YES |
| local_currency | Varchar(10) | Currency of price_per_kg — 'INR' for RRK data | rrk_excel_import | YES |
| availability_signal | Varchar(100) | Availability status e.g. available | rrk_excel_import | YES |
| confidence_score | Decimal(4,2) | 0.00–1.00 — 0.90 for RRK transaction data | rrk_excel_import | YES |
| source | Varchar(255) | Data origin e.g. rrk_excel_import | rrk_excel_import | NOT NULL |
| data_source_url | Varchar(500) | Path or URL of ingested Excel file | rrk_excel_import | NOT NULL |
| status | Varchar(50) | LIVE/PARTIAL/NOT_CONNECTED | — | YES |
| fibre_content_pct_cotton | Decimal(5,2) | Cotton % parsed from PARTICULARS | rrk_excel_import | YES |
| fibre_content_pct_polyester | Decimal(5,2) | Polyester % parsed from PARTICULARS | rrk_excel_import | YES |
| fibre_content_pct_modal | Decimal(5,2) | Modal % parsed from PARTICULARS | rrk_excel_import | YES |
| fibre_content_pct_viscose | Decimal(5,2) | Viscose % parsed from PARTICULARS | rrk_excel_import | YES |
| fibre_content_pct_spandex | Decimal(5,2) | Spandex % parsed from PARTICULARS | rrk_excel_import | YES |
| supplier_name | Varchar(200) | Yarn supplier parsed from GRN field | rrk_excel_import | YES |
| buyer_reference | Varchar(200) | REF NO — buyer style/program reference | rrk_excel_import | YES |
| po_number | Varchar(50) | RRK PO number e.g. RRK-POY2425-00004 | rrk_excel_import | YES |
| grn_number | Varchar(50) | GRN number e.g. RRK-GRY2425-00161 | rrk_excel_import | YES |
| grn_date | Date | Date yarn was received at factory | rrk_excel_import | YES |
| quantity_kg | Decimal(12,3) | Kilograms received in this delivery | rrk_excel_import | YES |
| po_rate_inr | Decimal(10,2) | Agreed PO rate in INR per kg | rrk_excel_import | YES |
| amount_inr | Decimal(14,2) | Total INR amount for this delivery | rrk_excel_import | YES |
| dc_number | Varchar(100) | Delivery challan number — may be text not a date | rrk_excel_import | YES |
| dc_date | Date | Delivery challan date — NULL if unparseable | rrk_excel_import | YES |
| colour | Varchar(50) | Normalised colour: grey/black/white/melange etc | rrk_excel_import | YES |
| is_melange | Boolean | True if MELANGE in PARTICULARS | rrk_excel_import | NOT NULL |
| is_recycled | Boolean | True if RECYCLED in PARTICULARS | rrk_excel_import | NOT NULL |
| is_bci | Boolean | True if BCI in PARTICULARS | rrk_excel_import | NOT NULL |
| requires_review | Boolean | True if fabric detected, suspicious fibre %, or rate validation failed | rrk_excel_import | NOT NULL |
| yarn_type_raw | Text | Full PARTICULARS string stored as-is | rrk_excel_import | YES |
| as_of_date | Date | GRN date — the date price was active | rrk_excel_import | YES |
| data_notes | Text | Flags, warnings, special cases e.g. fabric_not_yarn | rrk_excel_import | YES |
| pulled_at | Timestamp | UTC timestamp when row was ingested | rrk_excel_import | NOT NULL |
| is_latest | Boolean | Append-only discipline — only one latest row per entity key | rrk_excel_import | NOT NULL |
| created_at | Timestamp | Row creation timestamp | — | NOT NULL |
| updated_at | Timestamp | Row last update timestamp | — | NOT NULL |

### knit_fabric
| Field | Type | Notes |
|---|---|---|
| fabric_id | Integer PK | |
| construction | Varchar(100) | single jersey/fleece/interlock/piqué |
| weight_gsm | Decimal(8,2) | |
| fibre_content | Varchar(255) | |
| finish | Varchar(100) | greige/dyed/printed |
| origin_country | Varchar(100) | |
| price_per_kg | Decimal(10,4) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### spinning_mills
| Field | Type | Notes |
|---|---|---|
| spinning_mill_id | Integer PK | |
| location_country | Varchar(100) | |
| location_city | Varchar(100) | |
| capacity_tons_month | Decimal(10,2) | |
| utilisation_pct | Decimal(5,2) | |
| certifications | Varchar(255) | GOTS/OEKO-TEX |
| lead_time_weeks | Integer | |
| financing_rate | Decimal(8,4) | Annual % |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### knitting_mills
| Field | Type | Notes |
|---|---|---|
| knitting_mill_id | Integer PK | |
| location | Varchar(255) | |
| capacity_tons_month | Decimal(10,2) | |
| utilisation_pct | Decimal(5,2) | |
| machine_types | Varchar(255) | |
| certifications | Varchar(255) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### dyeing_units
| Field | Type | Notes |
|---|---|---|
| dyeing_unit_id | Integer PK | |
| location | Varchar(255) | |
| capacity_tons_month | Decimal(10,2) | |
| chemical_cost_structure | Varchar(255) | |
| energy_intensity | Varchar(100) | |
| crude_sensitivity_score | Decimal(5,2) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### labour_cost_by_country
| Field | Type | Notes |
|---|---|---|
| labour_cost_id | Integer PK | |
| india_tirupur | Decimal(10,4) | USD/hr |
| india_coimbatore | Decimal(10,4) | USD/hr |
| india_bangalore | Decimal(10,4) | USD/hr |
| bangladesh_dhaka | Decimal(10,4) | USD/hr |
| bangladesh_gazipur | Decimal(10,4) | USD/hr |
| bangladesh_chittagong | Decimal(10,4) | USD/hr |
| vietnam_hcmc | Decimal(10,4) | USD/hr |
| vietnam_hanoi | Decimal(10,4) | USD/hr |
| china_guangdong | Decimal(10,4) | USD/hr |
| china_zhejiang | Decimal(10,4) | USD/hr |
| turkey_istanbul | Decimal(10,4) | USD/hr |
| morocco_casablanca | Decimal(10,4) | USD/hr |
| cambodia_national | Decimal(10,4) | USD/hr |
| pakistan_national | Decimal(10,4) | USD/hr |
| effective_date | Date | |
| source | Varchar(255) | "ILO ILOSTAT" |
| refresh | Varchar(50) | "monthly" |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### energy_cost
| Field | Type | Notes |
|---|---|---|
| energy_cost_id | Integer PK | |
| india_kwh_usd | Decimal(10,4) | |
| bangladesh_kwh_usd | Decimal(10,4) | |
| vietnam_kwh_usd | Decimal(10,4) | |
| china_kwh_usd | Decimal(10,4) | |
| turkey_kwh_usd | Decimal(10,4) | |
| morocco_kwh_usd | Decimal(10,4) | |
| cambodia_kwh_usd | Decimal(10,4) | |
| pakistan_kwh_usd | Decimal(10,4) | |
| effective_date | Date | |
| update | Varchar(50) | "quarterly" |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### factory_financing_cost
| Field | Type | Notes |
|---|---|---|
| financing_cost_id | Integer PK | |
| india_rate_pct | Decimal(6,2) | 11.00 |
| bangladesh_rate_pct | Decimal(6,2) | 13.00 |
| vietnam_rate_pct | Decimal(6,2) | 9.00 |
| china_rate_pct | Decimal(6,2) | 6.50 |
| turkey_rate_pct | Decimal(6,2) | 27.00 |
| morocco_rate_pct | Decimal(6,2) | 10.00 |
| cambodia_rate_pct | Decimal(6,2) | 12.00 |
| pakistan_rate_pct | Decimal(6,2) | 16.00 |
| source | Varchar(255) | "IMF/World Bank" |
| refresh | Varchar(50) | "quarterly" |
| created_at | Timestamp | |
| updated_at | Timestamp | |

CRITICAL: All rates are Decimal, never String. Used in arithmetic.
financing_cost_doz = (fob × rate/100) × (90/365)

### ocean_freight_rates
| Field | Type | Notes |
|---|---|---|
| ocean_rate_id | Integer PK | |
| chittagong_la_usd | Decimal(10,4) | Per 40ft container |
| chennai_la_usd | Decimal(10,4) | |
| hcmc_la_usd | Decimal(10,4) | |
| shanghai_la_usd | Decimal(10,4) | |
| rate_per_40ft_usd | Decimal(10,4) | |
| transit_days | Integer | |
| port_congestion_index | Decimal(5,2) | |
| as_of_date | Date | Weekly — Thursday update |
| source | Varchar(255) | "Drewry WCI / Freightos FBX" |
| status | Varchar(50) | NOT_CONNECTED — 0 rows |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### red_sea_disruption
| Field | Type | Notes |
|---|---|---|
| disruption_id | Integer PK | |
| disruption_name | Varchar(255) | |
| affected_routes | Varchar(255) | |
| severity_score | Decimal(5,2) | |
| extra_transit_days | Integer | |
| extra_cost_usd | Decimal(10,4) | Per container |
| date_resolved | Date | NULL = ongoing |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### local_inland_freight
| Field | Type | Notes |
|---|---|---|
| inland_freight_id | Integer PK | |
| factory_to_port_cost_country | Varchar(255) | JSON map by country |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### air_freight
| Field | Type | Notes |
|---|---|---|
| air_freight_id | Integer PK | |
| rate_per_kg | Decimal(10,4) | |
| key_corridors | Varchar(255) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### hs_codes
| Field | Type | Notes |
|---|---|---|
| hs_code_id | Integer PK | |
| code | Varchar(50) | 6109.10 / 6110.20 / 6109.90 / 6111 |
| description | Varchar(255) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### us_duty_rates
| Field | Type | Notes |
|---|---|---|
| duty_rate_id | Integer PK | |
| hs_code_id | Integer FK → hs_codes | |
| ntr_rate | Decimal(8,4) | |
| section_301_china | Varchar(50) | "25%" / "100%" |
| gsp_status | Varchar(50) | |
| source | Varchar(255) | "USITC HTS" |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### free_trade_agreements
| Field | Type | Notes |
|---|---|---|
| fta_id | Integer PK | |
| agreement_name | Varchar(255) | CAFTA-DR / US-Morocco / US-Jordan |
| beneficiary_countries | Varchar(255) | |
| duty_reduction_pct | Decimal(6,2) | |
| yarn_forward_rule | Varchar(255) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### uflpa
| Field | Type | Notes |
|---|---|---|
| uflpa_id | Integer PK | |
| rebuttable_presumption | Varchar(255) | |
| border_block_risk | Decimal(5,2) | |
| xinjiang_inputs | Varchar(255) | |
| compliance_doc_cost | Decimal(10,4) | Per shipment |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### eu_csddd
| Field | Type | Notes |
|---|---|---|
| csddd_id | Integer PK | |
| supply_chain_mapping_required | Varchar(255) | |
| verification_requirement | Varchar(255) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### de_minimis
| Field | Type | Notes |
|---|---|---|
| de_minimis_id | Integer PK | |
| threshold_amount | Decimal(10,2) | Currently $800 |
| duty_free_entry | Varchar(50) | |
| regulatory_pressure_flag | Varchar(50) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### shipper
| Field | Type | Notes |
|---|---|---|
| shipper_id | Integer PK | |
| factory_name | Varchar(255) | |
| country | Varchar(100) | |
| hs_codes_shipped | Varchar(255) | |
| volume_by_month | Varchar(255) | JSON |
| active_buyers | Varchar(255) | |
| yoy_trend | Decimal(8,2) | |
| as_of_date | Date | |
| source | Varchar(255) | "Panjiva / ImportGenius" |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### consignee
| Field | Type | Notes |
|---|---|---|
| consignee_id | Integer PK | |
| company_name | Varchar(255) | |
| sourcing_country_mix | Varchar(255) | |
| monthly_volume | Varchar(255) | |
| yoy_origin_shift | Varchar(255) | |
| new_factory_relationships | Integer | |
| as_of_date | Date | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### trade_flow_signals
| Field | Type | Notes |
|---|---|---|
| trade_signal_id | Integer PK | |
| market_share_by_origin | Varchar(255) | |
| competitor_shifts | Varchar(255) | |
| new_entrants | Varchar(255) | |
| seasonal_patterns | Varchar(255) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### fx_rates
| Field | Type | Notes |
|---|---|---|
| fx_rate_id | Integer PK | |
| usd_inr | Decimal(10,4) | Indian Rupee |
| usd_bdt | Decimal(10,4) | Bangladeshi Taka |
| usd_vnd | Decimal(10,4) | Vietnamese Dong |
| usd_cny | Decimal(10,4) | Chinese Renminbi |
| usd_try | Decimal(10,4) | Turkish Lira |
| usd_mad | Decimal(10,4) | Moroccan Dirham |
| usd_pkr | Decimal(10,4) | Pakistani Rupee |
| source | Varchar(255) | "ExchangeRate-API" |
| refresh | Varchar(50) | "daily" |
| status | Varchar(50) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

Signal rule: when usd_bdt increases > 3% WoW → flag Bangladesh tailwind

### commodity_futures
| Field | Type | Notes |
|---|---|---|
| commodity_futures_id | Integer PK | |
| ice_cotton_2_spot | Decimal(10,4) | |
| ice_cotton_2_3m | Decimal(10,4) | Separate field — not combined |
| ice_cotton_2_6m | Decimal(10,4) | Separate field — not combined |
| ice_cotton_2_9m | Decimal(10,4) | Separate field — not combined |
| ice_cotton_2_12m | Decimal(10,4) | Separate field — not combined |
| ocean_freight_ffa | Decimal(10,4) | Forward freight agreement |
| as_of_date | Date | |
| source | Varchar(255) | "ICE yfinance" |
| status | Varchar(50) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

CRITICAL: Five separate tenor fields. Never combine into one field.
Contango = (ice_cotton_2_12m - ice_cotton_2_spot) / ice_cotton_2_spot × 100

### importer_working_capital
| Field | Type | Notes |
|---|---|---|
| working_capital_id | Integer PK | |
| importer_id | Integer FK → importer | |
| annual_borrowing_rate | Decimal(8,4) | |
| typical_inventory_days | Integer | |
| cost_of_carry_per_dozen | Decimal(10,4) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### major_retailers
| Field | Type | Notes |
|---|---|---|
| retailer_id | Integer PK | |
| name | Varchar(255) | Target/Walmart/TJX/Burlington etc |
| store_count | Integer | |
| total_sales | Decimal(14,2) | |
| apparel_revenue | Decimal(14,2) | |
| gross_margin | Decimal(6,2) | |
| inventory_turnover | Decimal(8,2) | |
| forward_guidance | Varchar(255) | |
| source | Varchar(255) | "SEC EDGAR 10-Q" |
| status | Varchar(50) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### demand_signals
| Field | Type | Notes |
|---|---|---|
| demand_signal_id | Integer PK | |
| retailer_id | Integer FK → major_retailers | |
| store_expansion | Varchar(255) | |
| inventory_improving | Varchar(255) | |
| margin_compression | Varchar(255) | |
| buying_volume_signal | Varchar(255) | increasing/stable/declining |
| status | Varchar(50) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### retailer_financials
Append-only quarterly financials per retailer (scoped by `is_latest`).

| Field | Type | Notes |
|---|---|---|
| retailer_financials_id | Integer PK | |
| retailer_id | Integer FK → major_retailers | |
| fiscal_year | Integer | Retailer fiscal year |
| fiscal_quarter | Integer | 1–4 |
| period_end_date | Date | Fiscal quarter end |
| calendar_year | Integer | Calendar year of `period_end_date` |
| calendar_quarter | Integer | Calendar quarter (1–4) of `period_end_date` |
| data_quality | Text | JSON per-field provenance: `{field: {source_type, source_url, confidence}}`. `source_type`: xbrl, html_table, regex_8k, regex_10q, mix_derived, income_stmt_derived, carry_forward, manual_fix. `confidence`: high/medium/low |
| total_net_sales_usd | Decimal(14,2) | |
| apparel_revenue_usd | Decimal(14,2) | |
| comparable_sales_growth_pct | Decimal(8,4) | |
| gross_margin_pct | Decimal(6,4) | |
| operating_margin_pct | Decimal(6,4) | |
| inventory_usd | Decimal(14,2) | |
| inventory_days | Decimal(8,2) | |
| store_count_total | Integer | |
| guidance_sales_direction | Varchar(50) | |
| source_10q_url | Varchar(500) | |
| source_8k_url | Varchar(500) | |
| is_latest | Boolean | Current version for this quarter |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### seasonal_patterns
| Field | Type | Notes |
|---|---|---|
| seasonal_pattern_id | Integer PK | |
| ss_factory_commit_window | Varchar(50) | "Sep–Nov" |
| ss_delivery_window | Varchar(50) | "Mar–May" |
| fw_factory_commit_window | Varchar(50) | "Mar–May" |
| fw_delivery_window | Varchar(50) | "Sep–Nov" |
| freight_book_lead_days | Integer | Days before ship date to book |
| hedge_window_days | Integer | Days before commit to hedge |
| created_at | Timestamp | |
| updated_at | Timestamp | |

---

## ZONE 2 — EXECUTION PLANE

### hedge_opportunity
| Field | Type | Notes |
|---|---|---|
| hedge_opportunity_id | Integer PK | |
| program_id | Integer FK → program | |
| commodity | Varchar(50) | cotton/polyester |
| tenor_months | Integer | 3/6/9/12 |
| recommended_quantity | Decimal(10,4) | Bales equivalent |
| spot_price | Decimal(10,4) | |
| futures_price | Decimal(10,4) | |
| basis | Decimal(10,4) | futures - spot |
| potential_saving_doz | Decimal(10,4) | |
| risk_if_unhedged_usd | Decimal(12,2) | |
| recommended_action | Varchar(255) | |
| pillar_quote_id | Varchar(255) | Returned from Pillar HQ API |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### hedge_portfolio
| Field | Type | Notes |
|---|---|---|
| hedge_id | Integer PK | |
| program_id | Integer FK → program | |
| opportunity_id | Integer FK → hedge_opportunity | |
| hedged_commodity | Varchar(50) | |
| strike_price | Decimal(10,4) | |
| quantity_bales | Decimal(10,4) | |
| notional_usd | Decimal(12,2) | |
| premium_usd | Decimal(12,2) | |
| execution_date | Date | |
| expiry_date | Date | |
| settlement_date | Date | When cash settles |
| status | Varchar(50) | ACTIVE/EXPIRED/EXERCISED |
| current_mtm_usd | Decimal(12,2) | Mark-to-market |
| unrealised_pnl | Decimal(12,2) | |
| pillar_hedge_ref | Varchar(255) | Pillar HQ reference |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### ocean_freight_rfq
| Field | Type | Notes |
|---|---|---|
| rfq_id | Integer PK | |
| program_id | Integer FK → program | |
| origin_port | Varchar(255) | Auto from factory location |
| destination_port | Varchar(255) | Auto from program |
| cargo_spec | Varchar(255) | |
| container_size | Varchar(50) | "40ft" / "20ft" |
| ready_to_ship_date | Date | Auto from program.ship_date_planned |
| hs_code_id | Integer FK → hs_codes | |
| estimated_weight_kg | Decimal(12,2) | |
| bid_deadline | Date | |
| status | Varchar(50) | OPEN/AWARDED/CANCELLED |
| awarded_carrier_id | Integer FK → carrier | |
| awarded_rate_usd | Decimal(12,2) | |
| award_timestamp | Timestamp | When auction closed |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### carrier_bid
| Field | Type | Notes |
|---|---|---|
| bid_id | Integer PK | |
| rfq_id | Integer FK → ocean_freight_rfq | NOT NULL |
| carrier_id | Integer FK → carrier | NOT NULL |
| rate_usd | Decimal(12,2) | |
| transit_days | Integer | |
| vessel_schedule | Varchar(255) | |
| validity_hours | Integer | |
| bid_status | Varchar(50) | SUBMITTED/AWARDED/DECLINED |
| bid_timestamp | Timestamp | |
| created_at | Timestamp | |

### us_drayage_rfq
| Field | Type | Notes |
|---|---|---|
| drayage_rfq_id | Integer PK | |
| program_id | Integer FK → program | |
| origin_port | Varchar(255) | |
| destination_dc | Varchar(255) | |
| container_type | Varchar(50) | |
| pickup_date | Date | |
| delivery_deadline | Date | |
| status | Varchar(50) | |
| awarded_carrier_id | Integer FK → carrier | |
| awarded_rate_usd | Decimal(12,2) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### intermodal_rail_rfq
| Field | Type | Notes |
|---|---|---|
| rail_rfq_id | Integer PK | |
| program_id | Integer FK → program | |
| origin_port | Varchar(255) | |
| destination_dc | Varchar(255) | |
| distance_miles | Integer | |
| container_count | Integer | |
| status | Varchar(50) | |
| awarded_carrier_id | Integer FK → carrier | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### origin_drayage_rfq
| Field | Type | Notes |
|---|---|---|
| origin_drayage_id | Integer PK | |
| program_id | Integer FK → program | |
| factory_location | Varchar(255) | |
| origin_port | Varchar(255) | |
| ready_date | Date | |
| status | Varchar(50) | |
| awarded_carrier_id | Integer FK → carrier | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### customs_clearance_filing
| Field | Type | Notes |
|---|---|---|
| filing_id | Integer PK | |
| program_id | Integer FK → program | |
| hs_code_id | Integer FK → hs_codes | |
| importer_of_record | Varchar(255) | |
| declared_value | Decimal(12,2) | |
| quantity_units | Integer | |
| country_of_origin | Varchar(100) | |
| vessel_name | Varchar(255) | |
| bol_number | Varchar(255) | Bill of lading |
| entry_number | Varchar(255) | CBP entry number |
| duty_amount_paid | Decimal(12,2) | Actual duty — feeds landed_cost_actual |
| clearance_date | Date | |
| cbp_response_status | Varchar(50) | FILED/REVIEWING/CLEARED/HELD |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### duty_drawback
| Field | Type | Notes |
|---|---|---|
| drawback_id | Integer PK | |
| program_id | Integer FK → program | |
| eligibility_flag | Boolean | |
| estimated_recovery | Decimal(12,2) | Up to 99% of duties paid |
| drawback_type | Varchar(255) | |
| specialist_referred | Boolean | NOT Varchar |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### supply_chain_finance_offer
| Field | Type | Notes |
|---|---|---|
| scf_offer_id | Integer PK | |
| program_id | Integer FK → program | |
| factory_id | Integer FK → cmt_factories | NOT NULL |
| invoice_value | Decimal(12,2) | |
| advance_rate_pct | Decimal(5,2) | e.g. 95% |
| discount_rate_pct | Decimal(5,2) | e.g. 5% |
| effective_rate_saving | Decimal(10,4) | vs factory's local bank rate |
| offer_date | Date | |
| acceptance_date | Date | NULL until accepted |
| disbursement_date | Date | NULL until funds move — revenue fires here |
| status | Varchar(50) | OFFERED/ACCEPTED/DECLINED/EXPIRED/DISBURSED |
| scf_provider | Varchar(255) | Partner name |
| created_at | Timestamp | |
| updated_at | Timestamp | |

---

## ZONE 3 — NETWORK PLANE

### product_specification
| Field | Type | Notes |
|---|---|---|
| spec_id | Integer PK | |
| product_name | Varchar(255) | "180gsm Cotton Single Jersey T-Shirt" |
| hs_code_id | Integer FK → hs_codes | |
| fibre_content | Varchar(255) | |
| construction | Varchar(255) | |
| weight_gsm | Decimal(8,2) | |
| typical_fob_low | Decimal(10,4) | |
| typical_fob_high | Decimal(10,4) | |
| prototype_corridor_1 | Varchar(100) | |
| prototype_corridor_2 | Varchar(100) | |
| prototype_corridor_3 | Varchar(100) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### fob_price_calculation
| Field | Type | Notes |
|---|---|---|
| fob_calc_id | Integer PK | |
| spec_id | Integer FK → product_specification | |
| corridor | Varchar(100) | "Bangladesh" / "India" / "Vietnam" |
| fabric_cost_doz | Decimal(10,4) | Yarn + knitting + dyeing |
| cmt_cost_doz | Decimal(10,4) | Cut, make, trim labour |
| trim_cost_doz | Decimal(10,4) | Labels, buttons, polybag |
| overhead_doz | Decimal(10,4) | |
| financing_cost_doz | Decimal(10,4) | Invisible local bank rate cost |
| fob_price_doz | Decimal(10,4) | Sum of all components |
| confidence_score | Decimal(5,2) | |
| source | Varchar(255) | "mode_1_priors" / "mode_3_company" |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### trim_cost
| Field | Type | Notes |
|---|---|---|
| trim_id | Integer PK | |
| product_type | Varchar(255) | |
| labels_per_doz | Decimal(10,4) | |
| buttons_zippers_doz | Decimal(10,4) | |
| polybag_packaging_doz | Decimal(10,4) | |
| total_trim_cost_doz | Decimal(10,4) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### program  ← CENTRAL LINKING ENTITY
| Field | Type | Notes |
|---|---|---|
| program_id | Integer PK | |
| importer_id | Integer FK → importer | |
| factory_id | Integer FK → cmt_factories | |
| spec_id | Integer FK → product_specification | |
| season | Varchar(20) | "FW26" / "SS27" |
| quantity_units | Integer | |
| fob_price_agreed | Decimal(10,4) | |
| delivery_date_committed | Date | |
| origin_port | Varchar(255) | Auto from factory location |
| destination_port | Varchar(255) | |
| destination_dc | Varchar(255) | |
| cmt_start_date | Date | |
| fabric_cut_date | Date | |
| ship_date_planned | Date | |
| ship_date_actual | Date | |
| status | Varchar(50) | PLANNING/COMMITTED/IN_PRODUCTION/SHIPPED/DELIVERED/CLOSED |
| commodity_hedge_status | Varchar(50) | UNHEDGED/PARTIAL/FULLY_HEDGED |
| hedge_id | Integer FK → hedge_portfolio | |
| freight_booking_id | Integer | References ocean_freight_rfq.rfq_id |
| customs_clearance_id | Integer | References customs_clearance_filing.filing_id |
| otd_risk_score | Decimal(5,2) | 0.00–1.00 |
| landed_cost_estimated | Decimal(10,4) | From cost model |
| landed_cost_actual | Decimal(10,4) | Set only after customs duty confirmed |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### importer
| Field | Type | Notes |
|---|---|---|
| importer_id | Integer PK | |
| company_name | Varchar(255) | |
| annual_revenue_usd | Decimal(14,2) | |
| primary_corridors | Varchar(255) | |
| subscription_tier | Varchar(50) | STARTER/GROWTH/ENTERPRISE |
| subscription_fee_monthly | Decimal(10,2) | |
| subscribes_to_intelligence | Boolean | |
| executes_hedges | Boolean | |
| books_freight | Boolean | |
| manages_programs | Boolean | |
| discovers_factories | Boolean | |
| account_manager | Varchar(255) | |
| joined_date | Date | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### manufacturer
| Field | Type | Notes |
|---|---|---|
| manufacturer_id | Integer PK | |
| company_name | Varchar(255) | |
| primary_product_type | Varchar(255) | |
| annual_export_usd | Decimal(14,2) | |
| primary_importer_markets | Varchar(255) | |
| platform_tier | Varchar(50) | FREE/VERIFIED/PREMIUM |
| manages_programs | Boolean | |
| receives_pos | Boolean | |
| submits_milestones | Boolean | |
| accesses_scf | Boolean | |
| builds_profile | Boolean | |
| joined_date | Date | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### cmt_factories
| Field | Type | Notes |
|---|---|---|
| factory_id | Integer PK | |
| manufacturer_id | Integer FK → manufacturer | |
| location_country | Varchar(100) | |
| location_city | Varchar(100) | |
| capacity_pieces_month | Integer | |
| utilisation_pct | Decimal(5,2) | |
| order_book_depth_weeks | Integer | |
| certifications | Varchar(255) | WRAP/BSCI/SEDEX |
| on_time_delivery_rate | Decimal(5,2) | |
| lead_time_weeks | Integer | |
| financing_rate_annual_pct | Decimal(8,4) | Used in financing_cost_doz |
| platform_verified | Boolean | |
| active_importer_count | Integer | |
| supply_chain_finance_eligible | Boolean | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

Signal rule: utilisation_pct > 85 → flag OTD_RISK_HIGH on programs at this factory

### manufacturer_profile
| Field | Type | Notes |
|---|---|---|
| profile_id | Integer PK | |
| factory_id | Integer FK → cmt_factories | |
| manufacturer_id | Integer FK → manufacturer | |
| legal_name | Varchar(255) | |
| trade_name | Varchar(255) | |
| factory_type | Varchar(50) | CMT/knitting/spinning/dyeing |
| utilisation_pct_live | Decimal(5,2) | |
| otd_rate_verified | Decimal(5,2) | Verified by platform transactions |
| platform_tier | Varchar(50) | |
| profile_visibility | Varchar(50) | PRIVATE/NETWORK/PUBLIC |
| supply_chain_finance_eligible | Boolean | |
| contact_primary | Varchar(255) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### carrier
| Field | Type | Notes |
|---|---|---|
| carrier_id | Integer PK | |
| carrier_name | Varchar(255) | |
| carrier_type | Varchar(50) | ocean/drayage/intermodal/air |
| api_key | Varchar(255) | For RFQ broadcast |
| operating_routes | Varchar(255) | JSON array |
| certifications | Varchar(255) | |
| reliability_score | Decimal(5,2) | From historical OTD |
| avg_transit_days | Integer | |
| contact_primary | Varchar(255) | |
| active | Boolean | |
| joined_date | Date | |
| status | Varchar(50) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### carrier_network
| Field | Type | Notes |
|---|---|---|
| carrier_network_id | Integer PK | |
| receives_rfqs | Boolean | |
| submits_bids | Boolean | |
| wins_contracts | Boolean | |
| total_carriers | Integer | |
| ocean_carriers | Integer | |
| drayage_carriers | Integer | |
| intermodal_carriers | Integer | |
| status | Varchar(50) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### pillar_hq_partner
| Field | Type | Notes |
|---|---|---|
| partner_id | Integer PK | |
| partner_name | Varchar(255) | "Pillar HQ" |
| integration_type | Varchar(255) | "white-label embedded" |
| api_base_url | Varchar(255) | |
| commodities_covered | Varchar(255) | "cotton, polyester" |
| revenue_share_pct | Decimal(5,2) | Artemis % of Pillar fee |
| contract_signed_date | Date | |
| status | Varchar(50) | ACTIVE/PENDING/INACTIVE |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### customs_broker_partner
| Field | Type | Notes |
|---|---|---|
| partner_id | Integer PK | |
| partner_name | Varchar(255) | |
| api_base_url | Varchar(255) | |
| license_number | Varchar(255) | CHB license |
| files_with_cbp | Boolean | |
| revenue_model | Varchar(255) | "referral_fee" / "revenue_share" |
| fee_per_clearance | Decimal(10,2) | |
| status | Varchar(50) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### scf_provider_partner
| Field | Type | Notes |
|---|---|---|
| partner_id | Integer PK | |
| partner_name | Varchar(255) | |
| api_base_url | Varchar(255) | |
| funds_factories | Boolean | |
| max_advance_rate_pct | Decimal(5,2) | |
| min_invoice_value | Decimal(12,2) | |
| revenue_share_pct | Decimal(5,2) | |
| status | Varchar(50) | |
| created_at | Timestamp | |
| updated_at | Timestamp | |

---

## ZONE 4 — REVENUE PLANE

### revenue_transaction  ← EVERY MONETISATION EVENT
| Field | Type | Notes |
|---|---|---|
| transaction_id | Integer PK | |
| program_id | Integer FK → program | |
| revenue_type | Varchar(100) | See revenue types in .cursorrules |
| partner_name | Varchar(255) | |
| gross_amount | Decimal(12,2) | |
| net_to_artemis | Decimal(12,2) | |
| transaction_date | Date | |
| status | Varchar(50) | PENDING/CONFIRMED/PAID |
| reference_id | Varchar(255) | Partner's reference |
| created_at | Timestamp | |
| updated_at | Timestamp | |

### intelligence_subscription
| Field | Type | Notes |
|---|---|---|
| revenue_id | Integer PK | |
| importer_id | Integer FK → importer | |
| monthly_fee | Decimal(10,2) | |
| tier | Varchar(50) | |
| billing_cycle | Varchar(50) | |
| target_gross_margin_pct | Decimal(5,2) | 70%+ |
| created_at | Timestamp | |
| updated_at | Timestamp | |

(commodity_hedge_revenue_share, ocean_freight_booking_revenue,
domestic_freight_booking_revenue, customs_clearance_revenue,
scf_spread_revenue, manufacturer_premium_profile_revenue,
data_licensing_revenue — all have created_at, updated_at and
their respective fee range / scale_driver fields as defined in schema)

---

## ZONE 5 — OUTPUT PLANE

All 15 intelligence output tables follow this pattern:

| Field | Type | Required |
|---|---|---|
| output_id | Integer PK | always |
| spec_id OR program_id | Integer FK | one or both |
| [domain-specific fields] | Decimal / Varchar | as defined |
| as_of_date | Date | ALWAYS |
| model_version | Varchar(20) | ALWAYS |
| created_at | Timestamp | ALWAYS |
| updated_at | Timestamp | ALWAYS |

The 15 tables with their primary FK:
| Table | Primary FK |
|---|---|
| current_landed_cost_per_dozen | spec_id |
| forward_landed_cost_90day | spec_id |
| most_cost_effective_corridor | spec_id |
| commodity_risk_in_open_programs | program_id |
| hedge_opportunity_recommendation | program_id |
| top5_competitor_sourcing | (none — market-level) |
| retailer_demand_forecast | retailer_id |
| tariff_exposure_analysis | spec_id |
| factory_financing_impact | (none — corridor comparison) |
| factory_capacity_constraints | factory_id |
| otd_risk_score_per_program | program_id |
| freight_booking_window | program_id |
| scf_opportunity_per_factory | factory_id + program_id |
| competitor_factory_intel | (none — market-level) |
| program_pnl_with_levers | program_id |

### prediction_log
| Field | Type | Notes |
|---|---|---|
| prediction_id | Integer PK | |
| program_id | Integer FK → program | |
| spec_id | Integer FK → product_specification | |
| prediction_type | Varchar(100) | landed_cost/otd_risk/hedge_outcome |
| corridor | Varchar(100) | |
| predicted_value | Decimal(12,4) | |
| p10 | Decimal(12,4) | |
| p50 | Decimal(12,4) | |
| p90 | Decimal(12,4) | |
| prediction_timestamp | Datetime | |
| target_date | Date | When outcome is expected |
| actual_value | Decimal(12,4) | Filled retrospectively |
| accuracy_score | Decimal(5,2) | Filled retrospectively |
| model_version | Varchar(100) | |
| data_snapshot_id | Varchar(255) | |
| metadata_json | Text | JSON metadata for synthesis provenance (e.g. `data_freshness_days`, `earliest_data_date`, `latest_data_date`) |
| created_at | Timestamp | |
| updated_at | Timestamp | |

`retailer_demand_forecast` also carries `metadata_json` (same freshness fields) on the latest forecast row per retailer.

---

## CAUSAL CHAIN — COST CALCULATION SEQUENCE

This is the order in which the cost engine must compute. Do not skip steps.

1. cotton.spot_price → yarn.price_per_kg (lag 4–8 weeks, confidence-weighted)
2. crude_oil.brent_spot → dyeing chemical premium (PENDING CALIBRATION — no signal until empirically calibrated from RRK invoices; returns None)
3. yarn.price_per_kg + knitting labour + dyeing premium → knit_fabric.price_per_kg
4. knit_fabric.price_per_kg × GSM conversion → fabric_cost_doz
5. labour_cost_by_country[corridor] × hours_per_dozen → cmt_cost_doz
6. trim_cost.total_trim_cost_doz → trim_cost_doz
7. energy_cost[corridor] × kWh_per_dozen → overhead_doz component  *(CRUDE_LINKAGE_PENDING: no crude multiplier — static reference only until RRK energy invoices calibrated)*
8. factory_financing_cost[corridor] × payment_days / 365 × fob_approx → financing_cost_doz
9. Sum all components → fob_price_doz
10. fx_rates[corridor] → convert all costs to USD
11. ocean_freight_rates[route] / dozens_per_container → freight_cost_doz  *(CRUDE_LINKAGE_PENDING: no crude bunker surcharge — static reference only until Drewry WCI live and BAF empirically calibrated)*
12. us_duty_rates.ntr_rate × fob_price_doz → duty_cost_doz
13. Sum FOB + freight + duty + insurance → landed_cost_doz
14. Write to current_landed_cost_per_dozen with as_of_date and model_version
15. Write to prediction_log

## DATA STATUS AT TIME OF WRITING

| Source | Status | Priority |
|---|---|---|
| Cotton (ICE yfinance) | LIVE | — |
| FX rates (ExchangeRate-API) | LIVE | — |
| WASDE (USDA FAS) | LIVE | — |
| Commodity futures (ICE) | LIVE | — |
| Crude oil spot (EIA daily) | LIVE — daily resolution, 1987→present | primary |
| Crude oil spot (FRED weekly) | LIVE — weekly EOP | secondary |
| Crude oil spot (Pink Sheet) | LIVE — monthly anchor | reference |
| Crude oil futures (Brent ICE) | LIVE — real front-month (yfinance BZ=F); 3m/6m/12m STEO fallback | — |
| Crude oil futures (WTI NYMEX) | LIVE — EIA RCLC series | — |
| Crude transmission calibration | PENDING — 0/20 invoice pairs | activates at n≥20, R²≥0.40, p<0.01 |
| Dyeing premium signal | PENDING CALIBRATION | returns None until activated |
| Energy cost crude linkage | NOT BUILT — pending RRK energy invoices | add after factory data |
| Freight bunker crude linkage | NOT BUILT — pending Drewry WCI | add after live freight data |
| Yarn costs | NOT CONNECTED | #1 priority — Tirupur DB |
| Ocean freight | NOT CONNECTED | #2 priority — Drewry WCI |
| Carrier network | NOT CONNECTED | #3 priority — sign up forwarders |
| Factory OTD data | DEMO ONLY | #4 priority — real factory data |

---

## CRUDE OIL SIGNAL ROUTING

All crude data flows through `intelligence.crude_cost_inputs.CrudeCostInputs`. Direct `crude_oil` table queries are prohibited in the cost engine and intelligence layer.

### Source hierarchy (authority order)
1. **eia_daily** — daily resolution. Use for transmission-calibration joins (exact invoice-date match).
2. **fred_api** — weekly EOP. Use for rolling averages and operational trend signals.
3. **world_bank_pink_sheet** — monthly anchor. Historical analysis only.
4. **eia_petroleum_futures** — forward curve.

### Entry Points (CrudeCostInputs)

| Method | Returns | Cost Engine Use |
|---|---|---|
| `get_spot_input(as_of_date)` | brent_t4w, brent_rolling_avg, dyeing_premium_active=**None**, source_row_id | Cost step 2: dyeing chemical premium |
| `get_forward_input(delivery_date, as_of_date)` | brent_futures, wti_futures, market_structure, brent_forward_source, brent_forward_is_market_price, confidence | Cost step 14: forward landed cost |
| `get_dyeing_pressure(as_of_date)` | raw crude prices + dyeing_premium_active=**None**, calibration_status=**'PENDING'** | Dark-colour program alerts |

**What CrudeCostInputs does NOT compute (pending data — no approximation):**
- Energy cost adjustment (step 7) → requires RRK energy invoices. `# CRUDE_LINKAGE_PENDING` in engine.py.
- Bunker surcharge (step 11) → requires Drewry WCI live feed. `# CRUDE_LINKAGE_PENDING` in engine.py.
- Dyeing premium → requires n≥20 validated invoice pairs. Returns None until calibration activates.

`get_energy_cost_adjustment()` and `get_freight_bunker_adjustment()` were **removed entirely** — they were uncalibrated industry formulas (12% energy sensitivity; $8.50/container BAF). Wrong data is worse than no data.

### Tenor Selection (get_forward_input)
| Days to delivery | Tenor | Confidence |
|---|---|---|
| ≤ 30d | 1m | 0.85 if real ICE (ice_yfinance/cme_delayed), else 0.55 (STEO) |
| ≤ 90d | 3m | 0.55 (STEO) until a real term structure is sourced |
| ≤ 180d | 6m | 0.55 (STEO) |
| > 180d | 12m | 0.55 (STEO) |

### Signal Flow
```
EIA RBRTE/RWTC daily → brent_rolling_4w_avg / rolling_13w / t_minus_4w / t_minus_8w / yoy / wti_brent_spread
EIA NYMEX RCLC1-4    → wti_contango_signal → crude_market_structure
Yahoo BZ=F (real)    → brent_futures_1m (is_market_price=True, conf 0.85)
crude price (live)   → get_dyeing_pressure() → dyeing_premium_active = None (PENDING CALIBRATION)
crude_market_structure = contango (full consistent curve) → hedge_opportunity_recommendation (synthesis)
```

### Quality Gate
`get_blocking_failures()` is called at the start of every `CostReasoningEngine.reason()` call.
It returns the **most recent** result per check and blocks only on the four BLOCKING checks
(`daily_increment_check`, `source_reconciliation_check`, `futures_curve_integrity_check`,
`eia_daily_coverage_check`) whose latest result is an unresolved `fail`. Non-blocking checks
(`sigma_anomaly_check`, `wti_brent_spread_sanity_check`, `calibration_readiness_check`) never block.

### Transmission Calibration (engine: `intelligence/transmission_calibration.py`)
Runs daily, idempotent. Joins fabric_dyeing invoices to **eia_daily** crude at 4w/8w lag.
Activation requires ALL of: **n≥20, R²≥0.40, p<0.01, positive coefficient, 95% CI excludes zero.**
Empirical threshold is found by **Chow test** (structural break), not assumed at $85/bbl.
Status (2026-06-16): **PENDING — 0/20 invoice pairs.**

Until activated: `dyeing_premium_active` returns None.
After activated: `dyeing_premium_active = (brent_t_minus_{lag_weeks}w > empirical_threshold)`,
coefficient and threshold drawn from `crude_transmission_calibration`.

### Crude × Retailer Composite Signal
`synthesis.get_crude_retailer_demand_composite(db, as_of_date)`. Returns a non-predictive composite
of crude pressure level + retailer demand health **only when** eia_daily < 7 days old AND
demand_signals < 90 days old; otherwise components are None and `data_complete=False`.
Does not write to any output table; does not make predictions or recommendations.

### PROHIBITED in cost engine code
- Direct queries to the `crude_oil` table (everything via CrudeCostInputs).
- Any hardcoded threshold ($85 or otherwise).
- Any approximation formula for energy or freight crude adjustment.

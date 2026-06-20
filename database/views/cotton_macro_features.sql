-- Cotton macro model training view.  (PostgreSQL / TimescaleDB native)
--
-- One row per weekly ICE No.2 Global observation (783 rows, 2011-2026).
-- All signals are left-joined with NULLs for missing data — never imputed.
-- The training pipeline must filter on data_quality_tier:
--   'full'     → 4-5 real ICE contracts: trust all futures fields
--   'partial'  → 3   real ICE contracts: trust near/3m/6m, caution on 9m/12m
--   'spot_only'→ FRED spot real, ICE futures NULL (pre-2022 majority)
--
-- Marketing year (India): Oct 1 – Sep 30
--   india_marketing_year 2025 = Oct 2024 – Sep 2025
--
-- PORTING NOTES (SQLite → PostgreSQL):
--   * strftime('%m'|'%Y', d)        → EXTRACT(MONTH|YEAR FROM d)::int
--   * strftime('%W', d)             → Monday-anchored week number, replicated
--                                     exactly (week 0 = days before first Monday)
--   * JULIANDAY(a) - JULIANDAY(b)   → (a - b)   (DATE minus DATE = integer days)
--   * ORDER BY JULIANDAY(col) DESC  → ORDER BY col DESC
--   * is_latest = 1                 → is_latest = true   (real boolean now)
--   Correlated scalar subqueries are valid Postgres; each picks the most-recent
--   match within its filter window via ORDER BY <date> DESC, <pk> DESC LIMIT 1.
--   The trailing primary-key tie-breaker makes the selection deterministic even
--   if is_latest is ever over-set (multiple "latest" rows on one date): the
--   freshest inserted row (highest id) wins. On clean data it is a no-op.

DROP VIEW IF EXISTS cotton_macro_features_v;

CREATE VIEW cotton_macro_features_v AS
SELECT
    -- Time / identity
    c.as_of_date                                                    AS week_date,
    CASE WHEN EXTRACT(MONTH FROM c.as_of_date)::int >= 10
         THEN EXTRACT(YEAR FROM c.as_of_date)::int + 1
         ELSE EXTRACT(YEAR FROM c.as_of_date)::int
    END                                                             AS india_marketing_year,
    EXTRACT(YEAR  FROM c.as_of_date)::int                           AS calendar_year,
    EXTRACT(MONTH FROM c.as_of_date)::int                           AS calendar_month,
    -- strftime('%W') equivalent: weeks start Monday; week 1 begins on the first
    -- Monday of the year; days before it are week 0.
    (FLOOR(
        ( EXTRACT(DOY FROM c.as_of_date)::int
          - (1 + ((8 - EXTRACT(ISODOW FROM date_trunc('year', c.as_of_date))::int) % 7))
        ) / 7.0
     )::int + 1)                                                    AS week_of_year,

    -- Cotton price block (USD cents/lb). Provenance corrected 2026-06-20:
    --   ice_spot_cents_lb = Cotlook A physical SPOT (FRED PCOTTINDUSDM), full history — the
    --                       global physical benchmark. NOT an ICE futures price.
    --   ice_near_cents_lb = REAL ICE No.2 front-month FUTURES (FMP CTUSX); populated 2021-06+,
    --                       NULL before (no fabrication). Sourced from canonical
    --                       cotton_price_observation series ICE_CT_FRONT.
    --   ice_3m/6m/9m/12m + curve_contango = NULL: we hold a real front-month only, not a real
    --                       futures curve. (Prior synthetic S/U-calibrated curve was purged.)
    c.spot_price                                                    AS ice_spot_cents_lb,
    c.ice_futures_near                                              AS ice_near_cents_lb,
    c.ice_futures_3m                                                AS ice_3m_cents_lb,
    c.ice_futures_6m                                                AS ice_6m_cents_lb,
    c.ice_futures_9m                                                AS ice_9m_cents_lb,
    c.ice_futures_12m                                               AS ice_12m_cents_lb,
    c.contango_signal                                               AS curve_contango_pct,

    -- INR cost chain (materialised at ingestion)
    c.spot_price_inr_per_kg                                         AS spot_inr_per_kg,
    c.fx_usd_inr_at_ingestion                                       AS usd_inr,

    -- WASDE balance sheet (stamped on each cotton row)
    c.wasde_su_ratio_pct,
    c.wasde_ending_stocks                                           AS wasde_ending_stocks_m_bales,

    -- CFTC speculative positioning (most recent COT report within 14 days)
    (SELECT cot.noncomm_net_pct_oi FROM cftc_cotton_cot cot
     WHERE ABS(cot.report_date - c.as_of_date) <= 14
     ORDER BY cot.report_date DESC, cot.cot_id DESC LIMIT 1)                         AS spec_net_pct_oi,

    (SELECT cot.noncomm_net FROM cftc_cotton_cot cot
     WHERE ABS(cot.report_date - c.as_of_date) <= 14
     ORDER BY cot.report_date DESC, cot.cot_id DESC LIMIT 1)                         AS spec_net_contracts,

    (SELECT cot.chg_noncomm_net FROM cftc_cotton_cot cot
     WHERE ABS(cot.report_date - c.as_of_date) <= 14
     ORDER BY cot.report_date DESC, cot.cot_id DESC LIMIT 1)                         AS spec_net_change_wow,

    (SELECT cot.open_interest FROM cftc_cotton_cot cot
     WHERE ABS(cot.report_date - c.as_of_date) <= 14
     ORDER BY cot.report_date DESC, cot.cot_id DESC LIMIT 1)                         AS open_interest,

    (SELECT cot.comm_net FROM cftc_cotton_cot cot
     WHERE ABS(cot.report_date - c.as_of_date) <= 14
     ORDER BY cot.report_date DESC, cot.cot_id DESC LIMIT 1)                         AS commercial_net_contracts,

    -- WASDE world supply/demand detail (latest report for that marketing year)
    (SELECT sd.world_production_million_bales FROM cotton_supply_demand sd
     WHERE sd.marketing_year = CASE WHEN EXTRACT(MONTH FROM c.as_of_date)::int >= 10
         THEN EXTRACT(YEAR FROM c.as_of_date)::int + 1
         ELSE EXTRACT(YEAR FROM c.as_of_date)::int END
     ORDER BY sd.report_month DESC, sd.supply_demand_id DESC LIMIT 1)                         AS world_prod_m_bales,

    (SELECT sd.world_mill_use_million_bales FROM cotton_supply_demand sd
     WHERE sd.marketing_year = CASE WHEN EXTRACT(MONTH FROM c.as_of_date)::int >= 10
         THEN EXTRACT(YEAR FROM c.as_of_date)::int + 1
         ELSE EXTRACT(YEAR FROM c.as_of_date)::int END
     ORDER BY sd.report_month DESC, sd.supply_demand_id DESC LIMIT 1)                         AS world_mill_use_m_bales,

    (SELECT sd.india_production_million_bales FROM cotton_supply_demand sd
     WHERE sd.marketing_year = CASE WHEN EXTRACT(MONTH FROM c.as_of_date)::int >= 10
         THEN EXTRACT(YEAR FROM c.as_of_date)::int + 1
         ELSE EXTRACT(YEAR FROM c.as_of_date)::int END
     ORDER BY sd.report_month DESC, sd.supply_demand_id DESC LIMIT 1)                         AS wasde_india_prod_m_bales,

    (SELECT sd.china_production_million_bales FROM cotton_supply_demand sd
     WHERE sd.marketing_year = CASE WHEN EXTRACT(MONTH FROM c.as_of_date)::int >= 10
         THEN EXTRACT(YEAR FROM c.as_of_date)::int + 1
         ELSE EXTRACT(YEAR FROM c.as_of_date)::int END
     ORDER BY sd.report_month DESC, sd.supply_demand_id DESC LIMIT 1)                         AS wasde_china_prod_m_bales,

    -- India harvest signal (PSD annual estimate, most recent)
    (SELECT ihs.estimated_production_lakh_bales FROM india_harvest_signal ihs
     WHERE ihs.marketing_year = CASE WHEN EXTRACT(MONTH FROM c.as_of_date)::int >= 10
         THEN EXTRACT(YEAR FROM c.as_of_date)::int + 1
         ELSE EXTRACT(YEAR FROM c.as_of_date)::int END
     ORDER BY ihs.as_of_date DESC, ihs.harvest_id DESC LIMIT 1)                          AS india_harvest_est_lakh_bales,

    (SELECT ihs.acreage_lakh_hectares FROM india_harvest_signal ihs
     WHERE ihs.marketing_year = CASE WHEN EXTRACT(MONTH FROM c.as_of_date)::int >= 10
         THEN EXTRACT(YEAR FROM c.as_of_date)::int + 1
         ELSE EXTRACT(YEAR FROM c.as_of_date)::int END
     ORDER BY ihs.as_of_date DESC, ihs.harvest_id DESC LIMIT 1)                          AS india_acreage_lakh_ha,

    (SELECT ihs.season_assessment FROM india_harvest_signal ihs
     WHERE ihs.marketing_year = CASE WHEN EXTRACT(MONTH FROM c.as_of_date)::int >= 10
         THEN EXTRACT(YEAR FROM c.as_of_date)::int + 1
         ELSE EXTRACT(YEAR FROM c.as_of_date)::int END
     ORDER BY ihs.as_of_date DESC, ihs.harvest_id DESC LIMIT 1)                          AS india_harvest_season,

    -- Gujarat weather: India's largest cotton belt (nearest week within 7 days)
    (SELECT w.avg_temp_celsius FROM cotton_region_weather w
     WHERE w.region_name = 'gujarat_india'
       AND ABS(w.week_ending - c.as_of_date) <= 7
     ORDER BY w.week_ending DESC, w.weather_id DESC LIMIT 1)                           AS gujarat_avg_temp_c,

    (SELECT w.total_rainfall_mm FROM cotton_region_weather w
     WHERE w.region_name = 'gujarat_india'
       AND ABS(w.week_ending - c.as_of_date) <= 7
     ORDER BY w.week_ending DESC, w.weather_id DESC LIMIT 1)                           AS gujarat_rainfall_mm,

    (SELECT w.growing_degree_days FROM cotton_region_weather w
     WHERE w.region_name = 'gujarat_india'
       AND ABS(w.week_ending - c.as_of_date) <= 7
     ORDER BY w.week_ending DESC, w.weather_id DESC LIMIT 1)                           AS gujarat_gdd,

    (SELECT w.season_assessment FROM cotton_region_weather w
     WHERE w.region_name = 'gujarat_india'
       AND ABS(w.week_ending - c.as_of_date) <= 7
     ORDER BY w.week_ending DESC, w.weather_id DESC LIMIT 1)                           AS gujarat_season,

    -- West Texas weather: US largest cotton belt (nearest week within 7 days)
    (SELECT w.avg_temp_celsius FROM cotton_region_weather w
     WHERE w.region_name = 'west_texas_us'
       AND ABS(w.week_ending - c.as_of_date) <= 7
     ORDER BY w.week_ending DESC, w.weather_id DESC LIMIT 1)                           AS texas_avg_temp_c,

    (SELECT w.total_rainfall_mm FROM cotton_region_weather w
     WHERE w.region_name = 'west_texas_us'
       AND ABS(w.week_ending - c.as_of_date) <= 7
     ORDER BY w.week_ending DESC, w.weather_id DESC LIMIT 1)                           AS texas_rainfall_mm,

    (SELECT w.growing_degree_days FROM cotton_region_weather w
     WHERE w.region_name = 'west_texas_us'
       AND ABS(w.week_ending - c.as_of_date) <= 7
     ORDER BY w.week_ending DESC, w.weather_id DESC LIMIT 1)                           AS texas_gdd,

    (SELECT w.season_assessment FROM cotton_region_weather w
     WHERE w.region_name = 'west_texas_us'
       AND ABS(w.week_ending - c.as_of_date) <= 7
     ORDER BY w.week_ending DESC, w.weather_id DESC LIMIT 1)                           AS texas_season,

    -- Xinjiang, China weather (~25% of world production; nearest week within 7 days)
    (SELECT w.avg_temp_celsius FROM cotton_region_weather w
     WHERE w.region_name = 'xinjiang_china'
       AND ABS(w.week_ending - c.as_of_date) <= 7
     ORDER BY w.week_ending DESC, w.weather_id DESC LIMIT 1)                           AS xinjiang_avg_temp_c,

    (SELECT w.total_rainfall_mm FROM cotton_region_weather w
     WHERE w.region_name = 'xinjiang_china'
       AND ABS(w.week_ending - c.as_of_date) <= 7
     ORDER BY w.week_ending DESC, w.weather_id DESC LIMIT 1)                           AS xinjiang_rainfall_mm,

    (SELECT w.growing_degree_days FROM cotton_region_weather w
     WHERE w.region_name = 'xinjiang_china'
       AND ABS(w.week_ending - c.as_of_date) <= 7
     ORDER BY w.week_ending DESC, w.weather_id DESC LIMIT 1)                           AS xinjiang_gdd,

    (SELECT w.season_assessment FROM cotton_region_weather w
     WHERE w.region_name = 'xinjiang_china'
       AND ABS(w.week_ending - c.as_of_date) <= 7
     ORDER BY w.week_ending DESC, w.weather_id DESC LIMIT 1)                           AS xinjiang_season,

    -- Punjab, Pakistan weather (~8% of world production)
    (SELECT w.growing_degree_days FROM cotton_region_weather w
     WHERE w.region_name = 'punjab_pakistan'
       AND ABS(w.week_ending - c.as_of_date) <= 7
     ORDER BY w.week_ending DESC, w.weather_id DESC LIMIT 1)                           AS pakistan_gdd,

    (SELECT w.season_assessment FROM cotton_region_weather w
     WHERE w.region_name = 'punjab_pakistan'
       AND ABS(w.week_ending - c.as_of_date) <= 7
     ORDER BY w.week_ending DESC, w.weather_id DESC LIMIT 1)                           AS pakistan_season,

    -- Mato Grosso, Brazil weather (~10% of world production)
    (SELECT w.growing_degree_days FROM cotton_region_weather w
     WHERE w.region_name = 'mato_grosso_brazil'
       AND ABS(w.week_ending - c.as_of_date) <= 7
     ORDER BY w.week_ending DESC, w.weather_id DESC LIMIT 1)                           AS brazil_gdd,

    (SELECT w.season_assessment FROM cotton_region_weather w
     WHERE w.region_name = 'mato_grosso_brazil'
       AND ABS(w.week_ending - c.as_of_date) <= 7
     ORDER BY w.week_ending DESC, w.weather_id DESC LIMIT 1)                           AS brazil_season,

    -- NSW/Narrabri, Australia weather (~3% of world production)
    (SELECT w.growing_degree_days FROM cotton_region_weather w
     WHERE w.region_name = 'nsw_narrabri_australia'
       AND ABS(w.week_ending - c.as_of_date) <= 7
     ORDER BY w.week_ending DESC, w.weather_id DESC LIMIT 1)                           AS australia_gdd,

    (SELECT w.season_assessment FROM cotton_region_weather w
     WHERE w.region_name = 'nsw_narrabri_australia'
       AND ABS(w.week_ending - c.as_of_date) <= 7
     ORDER BY w.week_ending DESC, w.weather_id DESC LIMIT 1)                           AS australia_season,

    -- Crude oil signal (nearest is_latest row within 7 days of the cotton observation).
    --
    -- Why crude belongs in the cotton training view:
    --   1. Polyester substitution: when crude rises, polyester yarn costs rise → cotton demand
    --      increases → cotton price gets a structural bid. The model must see both sides.
    --   2. Dye chemical costs: disperse dyes (polyester) and reactive dye carriers (cotton)
    --      are petroleum derivatives. crude_brent_usd directly predicts dyeing cost premium.
    --   3. Local freight: diesel-linked factory-to-port costs move with crude.
    --   4. Cotton-vs-polyester spread: cotton programs benefit when polyester is expensive.
    --      crude_brent_trend_30d_pct is the leading signal for that spread.
    --
    -- NULLs are correct: crude data only available from 2011-01-01 onwards in this DB.
    -- The training pipeline must handle NULLs in these columns for pre-2011 cotton rows.
    (SELECT cr.brent_spot FROM crude_oil cr
     WHERE cr.is_latest = true
       AND ABS(cr.as_of_date - c.as_of_date) <= 7
     ORDER BY cr.as_of_date DESC, cr.crude_oil_id DESC LIMIT 1)                           AS crude_brent_usd,

    (SELECT cr.wti_spot FROM crude_oil cr
     WHERE cr.is_latest = true
       AND ABS(cr.as_of_date - c.as_of_date) <= 7
     ORDER BY cr.as_of_date DESC, cr.crude_oil_id DESC LIMIT 1)                           AS crude_wti_usd,

    -- trend_30d_pct: (brent_now - brent_30d_ago) / brent_30d_ago * 100
    -- Materialized at ingestion — the key leading signal for polyester cost direction.
    (SELECT cr.trend_30d_pct FROM crude_oil cr
     WHERE cr.is_latest = true
       AND ABS(cr.as_of_date - c.as_of_date) <= 7
     ORDER BY cr.as_of_date DESC, cr.crude_oil_id DESC LIMIT 1)                           AS crude_brent_trend_30d_pct,

    -- Brent-WTI spread: normally +$2–5. Widening (>$10) = tighter Asian/EU crude supply.
    -- Spread inversion (<0) is a structural anomaly, historically brief.
    (SELECT cr.brent_wti_spread_usd FROM crude_oil cr
     WHERE cr.is_latest = true
       AND ABS(cr.as_of_date - c.as_of_date) <= 7
     ORDER BY cr.as_of_date DESC, cr.crude_oil_id DESC LIMIT 1)                           AS crude_brent_wti_spread,

    -- INR-denominated crude: the direct entry point for synthetic fiber cost chain in India.
    -- crude_brent_inr → PX paraxylene → PTA → polyester chip → polyester yarn INR/kg.
    -- NULL for pre-2004 rows (no FX history); populated for 2004-onwards rows.
    (SELECT cr.brent_inr_per_barrel FROM crude_oil cr
     WHERE cr.is_latest = true
       AND ABS(cr.as_of_date - c.as_of_date) <= 7
     ORDER BY cr.as_of_date DESC, cr.crude_oil_id DESC LIMIT 1)                           AS crude_brent_inr,

    -- aggregation_period: 'monthly' (World Bank Pink Sheet) | 'weekly' (FRED EOP).
    -- Training models must not treat these rows as equivalent: a monthly average has
    -- lower variance than a weekly end-of-period reading of the same month.
    (SELECT cr.aggregation_period FROM crude_oil cr
     WHERE cr.is_latest = true
       AND ABS(cr.as_of_date - c.as_of_date) <= 7
     ORDER BY cr.as_of_date DESC, cr.crude_oil_id DESC LIMIT 1)                           AS crude_aggregation_period,

    -- Crude oil forward curve — Brent EIA STEO forecasts at 3m/6m/9m/12m tenors.
    -- Source: EIA Short-Term Energy Outlook (BREPUUS), updated monthly.
    -- NULL for cotton dates where no futures row exists within ±35 days.
    -- brent_12m_contango_pct > 3%  = EIA expects cost pressure to persist
    -- brent_12m_contango_pct < -3% = EIA expects crude decline (polyester costs ease)
    (SELECT cf.brent_3m_fwd FROM commodity_futures cf
     WHERE cf.is_latest = true
       AND cf.brent_3m_fwd IS NOT NULL
       AND ABS(cf.as_of_date - c.as_of_date) <= 35
     ORDER BY cf.as_of_date DESC, cf.commodity_futures_id DESC LIMIT 1)                           AS brent_3m_fwd_usd,

    (SELECT cf.brent_6m_fwd FROM commodity_futures cf
     WHERE cf.is_latest = true
       AND cf.brent_6m_fwd IS NOT NULL
       AND ABS(cf.as_of_date - c.as_of_date) <= 35
     ORDER BY cf.as_of_date DESC, cf.commodity_futures_id DESC LIMIT 1)                           AS brent_6m_fwd_usd,

    (SELECT cf.brent_9m_fwd FROM commodity_futures cf
     WHERE cf.is_latest = true
       AND cf.brent_9m_fwd IS NOT NULL
       AND ABS(cf.as_of_date - c.as_of_date) <= 35
     ORDER BY cf.as_of_date DESC, cf.commodity_futures_id DESC LIMIT 1)                           AS brent_9m_fwd_usd,

    (SELECT cf.brent_12m_fwd FROM commodity_futures cf
     WHERE cf.is_latest = true
       AND cf.brent_12m_fwd IS NOT NULL
       AND ABS(cf.as_of_date - c.as_of_date) <= 35
     ORDER BY cf.as_of_date DESC, cf.commodity_futures_id DESC LIMIT 1)                           AS brent_12m_fwd_usd,

    (SELECT cf.brent_3m_contango_pct FROM commodity_futures cf
     WHERE cf.is_latest = true
       AND cf.brent_3m_contango_pct IS NOT NULL
       AND ABS(cf.as_of_date - c.as_of_date) <= 35
     ORDER BY cf.as_of_date DESC, cf.commodity_futures_id DESC LIMIT 1)                           AS brent_3m_contango_pct,

    (SELECT cf.brent_9m_contango_pct FROM commodity_futures cf
     WHERE cf.is_latest = true
       AND cf.brent_9m_contango_pct IS NOT NULL
       AND ABS(cf.as_of_date - c.as_of_date) <= 35
     ORDER BY cf.as_of_date DESC, cf.commodity_futures_id DESC LIMIT 1)                           AS brent_9m_contango_pct,

    (SELECT cf.brent_12m_contango_pct FROM commodity_futures cf
     WHERE cf.is_latest = true
       AND cf.brent_12m_contango_pct IS NOT NULL
       AND ABS(cf.as_of_date - c.as_of_date) <= 35
     ORDER BY cf.as_of_date DESC, cf.commodity_futures_id DESC LIMIT 1)                           AS brent_12m_contango_pct,

    (SELECT cf.crude_curve_signal FROM commodity_futures cf
     WHERE cf.is_latest = true
       AND cf.crude_curve_signal IS NOT NULL
       AND ABS(cf.as_of_date - c.as_of_date) <= 35
     ORDER BY cf.as_of_date DESC, cf.commodity_futures_id DESC LIMIT 1)                           AS crude_curve_signal,

    -- PX Paraxylene proxy price (USD/tonne) — derived from crude, is_proxy=True.
    -- This is the first step in crude → polyester yarn cost chain.
    -- Accuracy ±20% vs real ICIS prices; directionally correct.
    -- The crude_to_px_ratio column tracks whether the processing spread is normal.
    -- NULL for rows before 2011 (no crude basis for proxy).
    (SELECT px.spot_usd_tonne FROM px_paraxylene px
     WHERE px.is_latest = true
       AND ABS(px.as_of_date - c.as_of_date) <= 35
     ORDER BY px.as_of_date DESC, px.px_id DESC LIMIT 1)                           AS px_spot_usd_tonne,

    (SELECT px.crude_to_px_ratio FROM px_paraxylene px
     WHERE px.is_latest = true
       AND ABS(px.as_of_date - c.as_of_date) <= 35
     ORDER BY px.as_of_date DESC, px.px_id DESC LIMIT 1)                           AS crude_to_px_ratio,

    -- PET chip proxy price (USD/tonne) — the direct feedstock for polyester yarn.
    -- Most operationally relevant price for CVC/polyester fabric cost estimation.
    (SELECT ch.spot_usd_tonne FROM polyester_pet_chips ch
     WHERE ch.is_latest = true
       AND ABS(ch.as_of_date - c.as_of_date) <= 35
     ORDER BY ch.as_of_date DESC, ch.chip_id DESC LIMIT 1)                           AS chip_spot_usd_tonne,

    -- Data quality flags — training pipeline must check these before using ICE futures
    c.data_quality_tier,
    c.is_real_futures_data                                          AS has_real_ice_futures,
    c.futures_contracts_available                                   AS ice_contracts_available

FROM cotton c
WHERE c.origin_country = 'ICE No.2 Global'
  AND c.is_latest = true
ORDER BY c.as_of_date DESC;

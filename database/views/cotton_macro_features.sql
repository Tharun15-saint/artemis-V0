-- Cotton macro model training view.
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
-- SQLite correlated subquery constraint: ORDER BY inside scalar subqueries
-- cannot reference outer query columns. We use ORDER BY JULIANDAY(col) DESC
-- to get the most-recent match within the filter window.

DROP VIEW IF EXISTS cotton_macro_features_v;

CREATE VIEW cotton_macro_features_v AS
SELECT
    -- Time / identity
    c.as_of_date                                                    AS week_date,
    CASE WHEN CAST(strftime('%m', c.as_of_date) AS INT) >= 10
         THEN CAST(strftime('%Y', c.as_of_date) AS INT) + 1
         ELSE CAST(strftime('%Y', c.as_of_date) AS INT)
    END                                                             AS india_marketing_year,
    CAST(strftime('%Y', c.as_of_date) AS INT)                       AS calendar_year,
    CAST(strftime('%m', c.as_of_date) AS INT)                       AS calendar_month,
    CAST(strftime('%W', c.as_of_date) AS INT)                       AS week_of_year,

    -- ICE price curve (USD cents/lb) — NULL when is_real_futures_data=0
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
     WHERE ABS(JULIANDAY(cot.report_date) - JULIANDAY(c.as_of_date)) <= 14
     ORDER BY JULIANDAY(cot.report_date) DESC LIMIT 1)             AS spec_net_pct_oi,

    (SELECT cot.noncomm_net FROM cftc_cotton_cot cot
     WHERE ABS(JULIANDAY(cot.report_date) - JULIANDAY(c.as_of_date)) <= 14
     ORDER BY JULIANDAY(cot.report_date) DESC LIMIT 1)             AS spec_net_contracts,

    (SELECT cot.chg_noncomm_net FROM cftc_cotton_cot cot
     WHERE ABS(JULIANDAY(cot.report_date) - JULIANDAY(c.as_of_date)) <= 14
     ORDER BY JULIANDAY(cot.report_date) DESC LIMIT 1)             AS spec_net_change_wow,

    (SELECT cot.open_interest FROM cftc_cotton_cot cot
     WHERE ABS(JULIANDAY(cot.report_date) - JULIANDAY(c.as_of_date)) <= 14
     ORDER BY JULIANDAY(cot.report_date) DESC LIMIT 1)             AS open_interest,

    (SELECT cot.comm_net FROM cftc_cotton_cot cot
     WHERE ABS(JULIANDAY(cot.report_date) - JULIANDAY(c.as_of_date)) <= 14
     ORDER BY JULIANDAY(cot.report_date) DESC LIMIT 1)             AS commercial_net_contracts,

    -- WASDE world supply/demand detail (latest report for that marketing year)
    (SELECT sd.world_production_million_bales FROM cotton_supply_demand sd
     WHERE sd.marketing_year = CASE WHEN CAST(strftime('%m', c.as_of_date) AS INT) >= 10
         THEN CAST(strftime('%Y', c.as_of_date) AS INT) + 1
         ELSE CAST(strftime('%Y', c.as_of_date) AS INT) END
     ORDER BY JULIANDAY(sd.report_month) DESC LIMIT 1)             AS world_prod_m_bales,

    (SELECT sd.world_mill_use_million_bales FROM cotton_supply_demand sd
     WHERE sd.marketing_year = CASE WHEN CAST(strftime('%m', c.as_of_date) AS INT) >= 10
         THEN CAST(strftime('%Y', c.as_of_date) AS INT) + 1
         ELSE CAST(strftime('%Y', c.as_of_date) AS INT) END
     ORDER BY JULIANDAY(sd.report_month) DESC LIMIT 1)             AS world_mill_use_m_bales,

    (SELECT sd.india_production_million_bales FROM cotton_supply_demand sd
     WHERE sd.marketing_year = CASE WHEN CAST(strftime('%m', c.as_of_date) AS INT) >= 10
         THEN CAST(strftime('%Y', c.as_of_date) AS INT) + 1
         ELSE CAST(strftime('%Y', c.as_of_date) AS INT) END
     ORDER BY JULIANDAY(sd.report_month) DESC LIMIT 1)             AS wasde_india_prod_m_bales,

    (SELECT sd.china_production_million_bales FROM cotton_supply_demand sd
     WHERE sd.marketing_year = CASE WHEN CAST(strftime('%m', c.as_of_date) AS INT) >= 10
         THEN CAST(strftime('%Y', c.as_of_date) AS INT) + 1
         ELSE CAST(strftime('%Y', c.as_of_date) AS INT) END
     ORDER BY JULIANDAY(sd.report_month) DESC LIMIT 1)             AS wasde_china_prod_m_bales,

    -- India harvest signal (PSD annual estimate, most recent)
    (SELECT ihs.estimated_production_lakh_bales FROM india_harvest_signal ihs
     WHERE ihs.marketing_year = CASE WHEN CAST(strftime('%m', c.as_of_date) AS INT) >= 10
         THEN CAST(strftime('%Y', c.as_of_date) AS INT) + 1
         ELSE CAST(strftime('%Y', c.as_of_date) AS INT) END
     ORDER BY JULIANDAY(ihs.as_of_date) DESC LIMIT 1)              AS india_harvest_est_lakh_bales,

    (SELECT ihs.acreage_lakh_hectares FROM india_harvest_signal ihs
     WHERE ihs.marketing_year = CASE WHEN CAST(strftime('%m', c.as_of_date) AS INT) >= 10
         THEN CAST(strftime('%Y', c.as_of_date) AS INT) + 1
         ELSE CAST(strftime('%Y', c.as_of_date) AS INT) END
     ORDER BY JULIANDAY(ihs.as_of_date) DESC LIMIT 1)              AS india_acreage_lakh_ha,

    (SELECT ihs.season_assessment FROM india_harvest_signal ihs
     WHERE ihs.marketing_year = CASE WHEN CAST(strftime('%m', c.as_of_date) AS INT) >= 10
         THEN CAST(strftime('%Y', c.as_of_date) AS INT) + 1
         ELSE CAST(strftime('%Y', c.as_of_date) AS INT) END
     ORDER BY JULIANDAY(ihs.as_of_date) DESC LIMIT 1)              AS india_harvest_season,

    -- Gujarat weather: India's largest cotton belt (nearest week within 7 days)
    (SELECT w.avg_temp_celsius FROM cotton_region_weather w
     WHERE w.region_name = 'gujarat_india'
       AND ABS(JULIANDAY(w.week_ending) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(w.week_ending) DESC LIMIT 1)               AS gujarat_avg_temp_c,

    (SELECT w.total_rainfall_mm FROM cotton_region_weather w
     WHERE w.region_name = 'gujarat_india'
       AND ABS(JULIANDAY(w.week_ending) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(w.week_ending) DESC LIMIT 1)               AS gujarat_rainfall_mm,

    (SELECT w.growing_degree_days FROM cotton_region_weather w
     WHERE w.region_name = 'gujarat_india'
       AND ABS(JULIANDAY(w.week_ending) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(w.week_ending) DESC LIMIT 1)               AS gujarat_gdd,

    (SELECT w.season_assessment FROM cotton_region_weather w
     WHERE w.region_name = 'gujarat_india'
       AND ABS(JULIANDAY(w.week_ending) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(w.week_ending) DESC LIMIT 1)               AS gujarat_season,

    -- West Texas weather: US largest cotton belt (nearest week within 7 days)
    (SELECT w.avg_temp_celsius FROM cotton_region_weather w
     WHERE w.region_name = 'west_texas_us'
       AND ABS(JULIANDAY(w.week_ending) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(w.week_ending) DESC LIMIT 1)               AS texas_avg_temp_c,

    (SELECT w.total_rainfall_mm FROM cotton_region_weather w
     WHERE w.region_name = 'west_texas_us'
       AND ABS(JULIANDAY(w.week_ending) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(w.week_ending) DESC LIMIT 1)               AS texas_rainfall_mm,

    (SELECT w.growing_degree_days FROM cotton_region_weather w
     WHERE w.region_name = 'west_texas_us'
       AND ABS(JULIANDAY(w.week_ending) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(w.week_ending) DESC LIMIT 1)               AS texas_gdd,

    (SELECT w.season_assessment FROM cotton_region_weather w
     WHERE w.region_name = 'west_texas_us'
       AND ABS(JULIANDAY(w.week_ending) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(w.week_ending) DESC LIMIT 1)               AS texas_season,

    -- Xinjiang, China weather (~25% of world production; nearest week within 7 days)
    (SELECT w.avg_temp_celsius FROM cotton_region_weather w
     WHERE w.region_name = 'xinjiang_china'
       AND ABS(JULIANDAY(w.week_ending) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(w.week_ending) DESC LIMIT 1)               AS xinjiang_avg_temp_c,

    (SELECT w.total_rainfall_mm FROM cotton_region_weather w
     WHERE w.region_name = 'xinjiang_china'
       AND ABS(JULIANDAY(w.week_ending) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(w.week_ending) DESC LIMIT 1)               AS xinjiang_rainfall_mm,

    (SELECT w.growing_degree_days FROM cotton_region_weather w
     WHERE w.region_name = 'xinjiang_china'
       AND ABS(JULIANDAY(w.week_ending) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(w.week_ending) DESC LIMIT 1)               AS xinjiang_gdd,

    (SELECT w.season_assessment FROM cotton_region_weather w
     WHERE w.region_name = 'xinjiang_china'
       AND ABS(JULIANDAY(w.week_ending) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(w.week_ending) DESC LIMIT 1)               AS xinjiang_season,

    -- Punjab, Pakistan weather (~8% of world production)
    (SELECT w.growing_degree_days FROM cotton_region_weather w
     WHERE w.region_name = 'punjab_pakistan'
       AND ABS(JULIANDAY(w.week_ending) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(w.week_ending) DESC LIMIT 1)               AS pakistan_gdd,

    (SELECT w.season_assessment FROM cotton_region_weather w
     WHERE w.region_name = 'punjab_pakistan'
       AND ABS(JULIANDAY(w.week_ending) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(w.week_ending) DESC LIMIT 1)               AS pakistan_season,

    -- Mato Grosso, Brazil weather (~10% of world production)
    (SELECT w.growing_degree_days FROM cotton_region_weather w
     WHERE w.region_name = 'mato_grosso_brazil'
       AND ABS(JULIANDAY(w.week_ending) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(w.week_ending) DESC LIMIT 1)               AS brazil_gdd,

    (SELECT w.season_assessment FROM cotton_region_weather w
     WHERE w.region_name = 'mato_grosso_brazil'
       AND ABS(JULIANDAY(w.week_ending) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(w.week_ending) DESC LIMIT 1)               AS brazil_season,

    -- NSW/Narrabri, Australia weather (~3% of world production)
    (SELECT w.growing_degree_days FROM cotton_region_weather w
     WHERE w.region_name = 'nsw_narrabri_australia'
       AND ABS(JULIANDAY(w.week_ending) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(w.week_ending) DESC LIMIT 1)               AS australia_gdd,

    (SELECT w.season_assessment FROM cotton_region_weather w
     WHERE w.region_name = 'nsw_narrabri_australia'
       AND ABS(JULIANDAY(w.week_ending) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(w.week_ending) DESC LIMIT 1)               AS australia_season,

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
     WHERE cr.is_latest = 1
       AND ABS(JULIANDAY(cr.as_of_date) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(cr.as_of_date) DESC LIMIT 1)               AS crude_brent_usd,

    (SELECT cr.wti_spot FROM crude_oil cr
     WHERE cr.is_latest = 1
       AND ABS(JULIANDAY(cr.as_of_date) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(cr.as_of_date) DESC LIMIT 1)               AS crude_wti_usd,

    -- trend_30d_pct: (brent_now - brent_30d_ago) / brent_30d_ago * 100
    -- Materialized at ingestion — the key leading signal for polyester cost direction.
    (SELECT cr.trend_30d_pct FROM crude_oil cr
     WHERE cr.is_latest = 1
       AND ABS(JULIANDAY(cr.as_of_date) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(cr.as_of_date) DESC LIMIT 1)               AS crude_brent_trend_30d_pct,

    -- Brent-WTI spread: normally +$2–5. Widening (>$10) = tighter Asian/EU crude supply.
    -- Spread inversion (<0) is a structural anomaly, historically brief.
    (SELECT cr.brent_wti_spread_usd FROM crude_oil cr
     WHERE cr.is_latest = 1
       AND ABS(JULIANDAY(cr.as_of_date) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(cr.as_of_date) DESC LIMIT 1)               AS crude_brent_wti_spread,

    -- INR-denominated crude: the direct entry point for synthetic fiber cost chain in India.
    -- crude_brent_inr → PX paraxylene → PTA → polyester chip → polyester yarn INR/kg.
    -- NULL for pre-2004 rows (no FX history); populated for 2004-onwards rows.
    (SELECT cr.brent_inr_per_barrel FROM crude_oil cr
     WHERE cr.is_latest = 1
       AND ABS(JULIANDAY(cr.as_of_date) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(cr.as_of_date) DESC LIMIT 1)               AS crude_brent_inr,

    -- aggregation_period: 'monthly' (World Bank Pink Sheet) | 'weekly' (FRED EOP).
    -- Training models must not treat these rows as equivalent: a monthly average has
    -- lower variance than a weekly end-of-period reading of the same month.
    (SELECT cr.aggregation_period FROM crude_oil cr
     WHERE cr.is_latest = 1
       AND ABS(JULIANDAY(cr.as_of_date) - JULIANDAY(c.as_of_date)) <= 7
     ORDER BY JULIANDAY(cr.as_of_date) DESC LIMIT 1)               AS crude_aggregation_period,

    -- Crude oil forward curve — Brent EIA STEO forecasts at 3m/6m/9m/12m tenors.
    -- Source: EIA Short-Term Energy Outlook (BREPUUS), updated monthly.
    -- NULL for cotton dates where no futures row exists within ±35 days.
    -- brent_12m_contango_pct > 3%  = EIA expects cost pressure to persist
    -- brent_12m_contango_pct < -3% = EIA expects crude decline (polyester costs ease)
    (SELECT cf.brent_3m_fwd FROM commodity_futures cf
     WHERE cf.is_latest = 1
       AND cf.brent_3m_fwd IS NOT NULL
       AND ABS(JULIANDAY(cf.as_of_date) - JULIANDAY(c.as_of_date)) <= 35
     ORDER BY JULIANDAY(cf.as_of_date) DESC LIMIT 1)               AS brent_3m_fwd_usd,

    (SELECT cf.brent_6m_fwd FROM commodity_futures cf
     WHERE cf.is_latest = 1
       AND cf.brent_6m_fwd IS NOT NULL
       AND ABS(JULIANDAY(cf.as_of_date) - JULIANDAY(c.as_of_date)) <= 35
     ORDER BY JULIANDAY(cf.as_of_date) DESC LIMIT 1)               AS brent_6m_fwd_usd,

    (SELECT cf.brent_9m_fwd FROM commodity_futures cf
     WHERE cf.is_latest = 1
       AND cf.brent_9m_fwd IS NOT NULL
       AND ABS(JULIANDAY(cf.as_of_date) - JULIANDAY(c.as_of_date)) <= 35
     ORDER BY JULIANDAY(cf.as_of_date) DESC LIMIT 1)               AS brent_9m_fwd_usd,

    (SELECT cf.brent_12m_fwd FROM commodity_futures cf
     WHERE cf.is_latest = 1
       AND cf.brent_12m_fwd IS NOT NULL
       AND ABS(JULIANDAY(cf.as_of_date) - JULIANDAY(c.as_of_date)) <= 35
     ORDER BY JULIANDAY(cf.as_of_date) DESC LIMIT 1)               AS brent_12m_fwd_usd,

    (SELECT cf.brent_3m_contango_pct FROM commodity_futures cf
     WHERE cf.is_latest = 1
       AND cf.brent_3m_contango_pct IS NOT NULL
       AND ABS(JULIANDAY(cf.as_of_date) - JULIANDAY(c.as_of_date)) <= 35
     ORDER BY JULIANDAY(cf.as_of_date) DESC LIMIT 1)               AS brent_3m_contango_pct,

    (SELECT cf.brent_9m_contango_pct FROM commodity_futures cf
     WHERE cf.is_latest = 1
       AND cf.brent_9m_contango_pct IS NOT NULL
       AND ABS(JULIANDAY(cf.as_of_date) - JULIANDAY(c.as_of_date)) <= 35
     ORDER BY JULIANDAY(cf.as_of_date) DESC LIMIT 1)               AS brent_9m_contango_pct,

    (SELECT cf.brent_12m_contango_pct FROM commodity_futures cf
     WHERE cf.is_latest = 1
       AND cf.brent_12m_contango_pct IS NOT NULL
       AND ABS(JULIANDAY(cf.as_of_date) - JULIANDAY(c.as_of_date)) <= 35
     ORDER BY JULIANDAY(cf.as_of_date) DESC LIMIT 1)               AS brent_12m_contango_pct,

    (SELECT cf.crude_curve_signal FROM commodity_futures cf
     WHERE cf.is_latest = 1
       AND cf.crude_curve_signal IS NOT NULL
       AND ABS(JULIANDAY(cf.as_of_date) - JULIANDAY(c.as_of_date)) <= 35
     ORDER BY JULIANDAY(cf.as_of_date) DESC LIMIT 1)               AS crude_curve_signal,

    -- PX Paraxylene proxy price (USD/tonne) — derived from crude, is_proxy=True.
    -- This is the first step in crude → polyester yarn cost chain.
    -- Accuracy ±20% vs real ICIS prices; directionally correct.
    -- The crude_to_px_ratio column tracks whether the processing spread is normal.
    -- NULL for rows before 2011 (no crude basis for proxy).
    (SELECT px.spot_usd_tonne FROM px_paraxylene px
     WHERE px.is_latest = 1
       AND ABS(JULIANDAY(px.as_of_date) - JULIANDAY(c.as_of_date)) <= 35
     ORDER BY JULIANDAY(px.as_of_date) DESC LIMIT 1)               AS px_spot_usd_tonne,

    (SELECT px.crude_to_px_ratio FROM px_paraxylene px
     WHERE px.is_latest = 1
       AND ABS(JULIANDAY(px.as_of_date) - JULIANDAY(c.as_of_date)) <= 35
     ORDER BY JULIANDAY(px.as_of_date) DESC LIMIT 1)               AS crude_to_px_ratio,

    -- PET chip proxy price (USD/tonne) — the direct feedstock for polyester yarn.
    -- Most operationally relevant price for CVC/polyester fabric cost estimation.
    (SELECT ch.spot_usd_tonne FROM polyester_pet_chips ch
     WHERE ch.is_latest = 1
       AND ABS(JULIANDAY(ch.as_of_date) - JULIANDAY(c.as_of_date)) <= 35
     ORDER BY JULIANDAY(ch.as_of_date) DESC LIMIT 1)               AS chip_spot_usd_tonne,

    -- Data quality flags — training pipeline must check these before using ICE futures
    c.data_quality_tier,
    c.is_real_futures_data                                          AS has_real_ice_futures,
    c.futures_contracts_available                                   AS ice_contracts_available

FROM cotton c
WHERE c.origin_country = 'ICE No.2 Global'
  AND c.is_latest = 1
ORDER BY c.as_of_date DESC;

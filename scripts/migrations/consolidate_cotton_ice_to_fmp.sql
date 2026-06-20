-- Consolidate the legacy `cotton` table (which feeds cotton_macro_features_v)
-- to agree with the canonical FMP-sourced cotton price store.
--
-- BEFORE: cotton.spot_price = Cotlook A (~78c) but the row was labelled "ICE No.2
-- Global" and ice_futures_near was a Cotlook fallback (yfinance failed). The
-- 3m/6m/9m/12m curve + contango were SYNTHETIC (S/U-calibrated).
--
-- AFTER (one source of truth, honest, no synthetic):
--   * ice_futures_near  = REAL ICE No.2 front-month, sourced from the canonical
--                         cotton_price_observation series 1 (FMP CTUSX), nearest
--                         trading day <= each weekly as_of_date. NULL before FMP
--                         coverage begins (2021-06) — we do NOT fabricate it.
--   * ice_futures_3m/6m/9m/12m + contango_signal = NULL (we have a real
--                         front-month only, not a real curve — no synthetic).
--   * spot_price        = unchanged Cotlook A physical spot (correctly labelled
--                         in `source`).
--   * flags/tier        = honest: real_front_month where FMP exists, else spot_only.
-- Reversible: full snapshot to cotton_backup_pre_fmp_consolidation.

BEGIN;

DROP TABLE IF EXISTS cotton_backup_pre_fmp_consolidation;
CREATE TABLE cotton_backup_pre_fmp_consolidation AS SELECT * FROM cotton;

UPDATE cotton c SET
    ice_futures_near = (
        SELECT o.price_value
        FROM cotton_price_observation o
        WHERE o.series_id = 1            -- ICE_CT_FRONT (FMP CTUSX, real)
          AND o.as_of_date <= c.as_of_date
        ORDER BY o.as_of_date DESC
        LIMIT 1
    ),
    ice_futures_3m  = NULL,
    ice_futures_6m  = NULL,
    ice_futures_9m  = NULL,
    ice_futures_12m = NULL,
    contango_signal = NULL,
    is_real_futures_data        = (c.as_of_date >= DATE '2021-06-21'),
    futures_contracts_available = CASE WHEN c.as_of_date >= DATE '2021-06-21' THEN 1 ELSE 0 END,
    data_quality_tier           = CASE WHEN c.as_of_date >= DATE '2021-06-21'
                                       THEN 'real_front_month' ELSE 'spot_only' END,
    source          = 'cotlook_a_spot(FRED PCOTTINDUSDM) + ice_no2_front(FMP CTUSX)',
    data_source_url = 'https://financialmodelingprep.com (CTUSX) ; https://fred.stlouisfed.org/series/PCOTTINDUSDM',
    updated_at      = now()
WHERE c.origin_country = 'ICE No.2 Global';

COMMIT;

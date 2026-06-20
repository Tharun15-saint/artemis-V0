"""Re-source ICE Cotton No.2 front-month futures from FMP into the canonical
multi-series cotton store (cotton_price_observation, series ICE_CT_FRONT).

WHY: the existing ICE_CT_FRONT series was not real ICE futures — yfinance CT=F
failed in the build environment and it silently fell back to FRED PCOTTINDUSDM
(= Cotlook A spot), mislabeled as ICE. Its values sat ~15% above the real ICE
futures (the genuine Cotlook-vs-futures basis). FMP (financialmodelingprep.com,
symbol CTUSX) provides the real exchange-sourced ICE No.2 futures, daily.

DISCIPLINE:
  * Real data only — FMP CTUSX close, no synthetic, no interpolation.
  * Reversible — the old series-1 rows are snapshotted before replacement.
  * No data lost — the Cotlook content those rows duplicated is preserved in
    series 2 (COTLOOK_A, full history from 1960).
  * Honest coverage — FMP has CTUSX daily from ~2021; we do NOT fabricate
    pre-2021 ICE. Pre-2021 cotton history remains available via the Cotlook
    series (correctly labeled).
  * Traceable — provenance written to every row + an ingestion_log entry.

Idempotent: re-running replaces series-1 observations with a fresh FMP pull.

Usage:  python data/ingestion/fmp_cotton_ingestion.py
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.request

import psycopg
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

SCRIPT_VERSION = "1.0.0"
SERIES_ID = 1
SERIES_CODE = "ICE_CT_FRONT"
FMP_SYMBOL = "CTUSX"
SOURCE_DOC = "FMP CTUSX — ICE Cotton No.2 front-month futures (daily close)"
SOURCE_URL = "https://financialmodelingprep.com/stable/historical-price-eod/full?symbol=CTUSX"
LB_PER_KG = 2.20462


def fetch_fmp_cotton(key: str) -> list[dict]:
    url = f"https://financialmodelingprep.com/stable/historical-price-eod/full?symbol={FMP_SYMBOL}&apikey={key}"
    raw = urllib.request.urlopen(url, timeout=60).read()
    data = json.loads(raw)
    rows = data if isinstance(data, list) else data.get("historical", [])
    out = []
    for r in rows:
        d = dt.date.fromisoformat(str(r["date"])[:10])
        close = r.get("close")
        if close is None:
            close = r.get("adjClose")
        if close is None:
            continue
        out.append({"as_of_date": d, "close": float(close)})
    out.sort(key=lambda x: x["as_of_date"])
    return out


def conn():
    return psycopg.connect(
        host="localhost", dbname="artemis", user="artemis", password="artemis", port=5432
    )


def main() -> int:
    key = os.getenv("FMP_API_KEY")
    if not key:
        print("FMP_API_KEY not set in environment (.env)")
        return 1

    started = dt.datetime.now()
    obs = fetch_fmp_cotton(key)
    if not obs:
        print("FMP returned no CTUSX data — aborting (no data > wrong data).")
        return 1
    print(f"FMP CTUSX: {len(obs)} daily closes, {obs[0]['as_of_date']} → {obs[-1]['as_of_date']}")

    with conn() as cx:
        cur = cx.cursor()

        # 1) snapshot existing series-1 rows (reversible)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = f"cpo_s1_backup_{stamp}"
        cur.execute(
            f'CREATE TABLE "{backup}" AS '
            f"SELECT * FROM cotton_price_observation WHERE series_id = %s",
            (SERIES_ID,),
        )
        cur.execute("SELECT COUNT(*) FROM cotton_price_observation WHERE series_id=%s", (SERIES_ID,))
        old_n = cur.fetchone()[0]
        print(f"snapshot: {old_n} old series-1 rows backed up to {backup}")

        # 2) replace series-1 observations with the real FMP pull
        cur.execute("DELETE FROM cotton_price_observation WHERE series_id=%s", (SERIES_ID,))
        inserted = 0
        for o in obs:
            cents = round(o["close"], 4)
            per_kg = round(cents / 100.0 * LB_PER_KG, 4)
            cur.execute(
                """
                INSERT INTO cotton_price_observation
                  (series_id, series_code, as_of_date, price_value, price_unit,
                   price_in_usd_cents_per_lb, price_in_usd_per_kg,
                   raw_value_original_unit, original_unit,
                   source_document, source_url, data_quality, is_estimate, is_latest, pulled_at)
                VALUES
                  (%s,%s,%s,%s,'cents_per_lb',%s,%s,%s,'cents_per_lb',
                   %s,%s,'verified', false, true, now())
                """,
                (SERIES_ID, SERIES_CODE, o["as_of_date"], cents, cents, per_kg, cents,
                 SOURCE_DOC, SOURCE_URL),
            )
            inserted += 1

        # 3) correct the series catalogue provenance
        cur.execute(
            """
            UPDATE cotton_price_series
               SET source_name = %s,
                   source_url  = %s,
                   history_available_from = %s,
                   notes = 'Real ICE No.2 front-month futures, exchange-sourced via FMP (CTUSX), '
                           'daily close. Replaces prior yfinance/FRED fallback that was Cotlook spot '
                           'mislabeled as ICE. Pre-2021 ICE not available from FMP; use COTLOOK_A for history.'
             WHERE series_id = %s
            """,
            ("FMP (financialmodelingprep.com) — CTUSX, ICE Cotton No.2",
             "https://financialmodelingprep.com", obs[0]["as_of_date"], SERIES_ID),
        )

        # 4) ingestion_log entry
        cur.execute(
            """
            INSERT INTO ingestion_log
              (source_name, pull_started_at, pull_completed_at, status,
               rows_attempted, rows_inserted, rows_rejected, rows_stale,
               data_as_of_date, data_source_url, script_version, created_at, updated_at)
            VALUES (%s,%s,now(),'success',%s,%s,0,0,%s,%s,%s,now(),now())
            """,
            ("fmp_cotton_ctusx", started, len(obs), inserted,
             obs[-1]["as_of_date"], SOURCE_URL, SCRIPT_VERSION),
        )
        cx.commit()
        print(f"replaced series-1: deleted {old_n}, inserted {inserted} real FMP rows")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

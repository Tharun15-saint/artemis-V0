"""
Paid-tier ocean freight adapter — architecture stub for FBX and Drewry API.

CURRENT STATE (free tier)
─────────────────────────
• ocean_freight_rates: 2 real Drewry WCI direct corridors + 1 WCI composite
  (weekly scrape, date: 2026-06-11)
• bunker_fuel_prices: 2,942 weekly EIA distillate rows (2006-2026),
  used as VLSFO proxy (is_proxy=True)
• crude_transmission_calibration: Brent→distillate fitted,
  coeff=1.163, R²=0.88, n=1043 (freight_energy_surcharge row)

WHAT UNLOCKS WITH PAID DATA
────────────────────────────
1. FBX (Freightos Baltic Index) API
   • 12 global lane indices with weekly history back to 2012
   • Per-lane: FBX01 (China/East Asia→North America West Coast),
               FBX03 (China/East Asia→North America East Coast), etc.
   • Unlocks: distillate→freight correlation (currently deferred)
   • Unlocks: per-lane rate history for 4wk/13wk trend in synthesis
   • Pricing: contact freight.freightos.com/fbx-api

2. Drewry Supply Chain Advisors API
   • Full WCI 8-corridor weekly history back to 2011
   • Direct per-lane: SHA→LA, SHA→NY, SHA→RTM, RTM→SHA, etc.
   • Same corridors as the current scraper but with deep history
   • Pricing: contact drewry.co.uk

INTEGRATION PLAN (zero migration required)
───────────────────────────────────────────
Both sources write into the existing ocean_freight_rates table using
rate_source_tier = 'fbx_api' or 'drewry_api' (already a free-text column).
The synthesis get_freight_snapshot() already reads any is_latest=True row
regardless of source tier — no code changes needed in synthesis or models.

After backfilling history, run the distillate→freight correlation:
    python -m data.ingestion.ocean_freight_paid_adapter --calibrate

SCHEMA SLOTS THAT WILL FILL IN
────────────────────────────────
ocean_freight_rates.corridor_differential_pct  — % vs WCI composite benchmark
ocean_freight_rates.base_corridor              — which corridor is the base

crude_transmission_calibration (new row):
    cost_component = 'distillate_to_freight'
    transmission_coeff = TBD (fitted after history available)
    r_squared          = TBD
    lag_weeks_empirical= TBD (expected 0–2 weeks, distillate is fast-moving)

HOW TO ACTIVATE THIS MODULE
─────────────────────────────
Step 1. Obtain API key (FBX or Drewry).
Step 2. Set environment variable:
    export FBX_API_KEY=your_key          # for FBX
    export DREWRY_API_KEY=your_key       # for Drewry
Step 3. Backfill history:
    python -m data.ingestion.ocean_freight_paid_adapter --source fbx --backfill
    python -m data.ingestion.ocean_freight_paid_adapter --source drewry --backfill
Step 4. Run distillate→freight calibration:
    python -m data.ingestion.ocean_freight_paid_adapter --calibrate
Step 5. Weekly cron (same cadence as bunker_fuel_ingestion):
    python -m data.ingestion.ocean_freight_paid_adapter --source fbx

LANE MAP (FBX → ocean_freight_rates columns)
─────────────────────────────────────────────
FBX01: China/East Asia → North America West Coast  (SHA→LA proxy)
FBX03: China/East Asia → North America East Coast  (SHA→NY proxy)
FBX11: China/East Asia → North Europe              (SHA→RTM proxy)
FBX13: China/East Asia → Mediterranean             (SHA→GEN proxy)
FBX21: North Europe → North America East Coast     (RTM→NYC proxy)
FBX23: North Europe → China/East Asia              (RTM→SHA proxy)
(full list: 12 lanes)

LANE MAP (Drewry WCI → ocean_freight_rates columns)
─────────────────────────────────────────────────────
WCI-1: Shanghai → Rotterdam
WCI-2: Rotterdam → Shanghai
WCI-3: Shanghai → Genoa
WCI-4: Shanghai → Los Angeles
WCI-5: Los Angeles → Shanghai
WCI-6: Shanghai → New York
WCI-7: New York → Rotterdam
WCI-8: Rotterdam → New York
WCI Composite: global 8-lane average (already scraped weekly)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Stub ingestion functions — replace with real API calls when keys are obtained
# ---------------------------------------------------------------------------

FBX_API_BASE = "https://api.freightos.com/api/v1"      # placeholder — verify with provider
DREWRY_API_BASE = "https://api.drewry.co.uk/v1"        # placeholder — verify with provider


def _get_fbx_lanes(api_key: str, as_of: Optional[date] = None) -> list[dict]:
    """
    Fetch FBX lane indices from Freightos API.

    Returns list of dicts matching ocean_freight_rates columns:
        origin_port, origin_country, destination_port, destination_country,
        rate_40ft_hc_usd, as_of_date, rate_source_tier='fbx_api', source='fbx'

    Replace this stub with real API call once key is available.
    Endpoint pattern (verify with provider docs):
        GET /fbx/indices?date=YYYY-MM-DD
    """
    raise NotImplementedError(
        "FBX API key not configured. Set FBX_API_KEY env var and implement real fetch. "
        "Contact: freight.freightos.com/fbx-api"
    )


def _get_drewry_lanes(api_key: str, as_of: Optional[date] = None) -> list[dict]:
    """
    Fetch Drewry WCI per-lane rates from Drewry API.

    Returns list of dicts matching ocean_freight_rates columns.

    Replace this stub with real API call once key is available.
    Contact: drewry.co.uk for API access.
    """
    raise NotImplementedError(
        "Drewry API key not configured. Set DREWRY_API_KEY env var and implement real fetch."
    )


def _upsert_freight_rows(rows: list[dict], db_path: str = "artemis.db") -> dict:
    """
    Write paid-tier freight rows into ocean_freight_rates.
    Uses the same append-only + is_latest discipline as the free scraper.
    Zero migration needed — rate_source_tier distinguishes the source.
    """
    import sqlite3
    db = sqlite3.connect(db_path)
    c = db.cursor()

    inserted = 0
    skipped = 0
    for row in rows:
        existing = c.execute(
            "SELECT ocean_rate_id FROM ocean_freight_rates "
            "WHERE origin_port=? AND destination_port=? AND as_of_date=? AND rate_source_tier=?",
            (row["origin_port"], row["destination_port"], row["as_of_date"], row["rate_source_tier"])
        ).fetchone()
        if existing:
            skipped += 1
            continue

        # Mark previous is_latest=False for this corridor+source
        c.execute(
            "UPDATE ocean_freight_rates SET is_latest=0 "
            "WHERE origin_port=? AND destination_port=? AND rate_source_tier=? AND is_latest=1",
            (row["origin_port"], row["destination_port"], row["rate_source_tier"])
        )

        now = datetime.now(timezone.utc).isoformat()
        c.execute(
            """INSERT INTO ocean_freight_rates
               (origin_port, origin_country, destination_port, destination_country,
                rate_40ft_hc_usd, as_of_date, source, rate_source_tier,
                is_latest, pulled_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,1,?,?,?)""",
            (
                row["origin_port"], row.get("origin_country", ""),
                row["destination_port"], row.get("destination_country", ""),
                row["rate_40ft_hc_usd"], row["as_of_date"],
                row.get("source", row["rate_source_tier"]),
                row["rate_source_tier"],
                now, now, now,
            )
        )
        inserted += 1

    db.commit()
    db.close()
    return {"inserted": inserted, "skipped_duplicates": skipped}


def _calibrate_distillate_to_freight(db_path: str = "artemis.db") -> None:
    """
    Fit distillate→freight correlation once paid history is available.
    Writes a new 'distillate_to_freight' row into crude_transmission_calibration.

    Requires: ≥52 weekly ocean_freight_rates rows per corridor (1 year minimum).
    Currently deferred — free tier has only 1 date.
    """
    import sqlite3
    db = sqlite3.connect(db_path)
    freight_count = db.execute(
        "SELECT COUNT(DISTINCT as_of_date) FROM ocean_freight_rates "
        "WHERE rate_source_tier IN ('fbx_api','drewry_api','drewry_wci_direct')"
    ).fetchone()[0]
    db.close()

    if freight_count < 52:
        print(
            f"[calibrate] Only {freight_count} distinct freight dates — need ≥52 for a "
            "meaningful regression. Backfill paid history first, then re-run --calibrate."
        )
        return

    # TODO: implement regression once history is available.
    # Pattern mirrors the Brent→distillate fit in bunker_fuel_ingestion.py:
    #   1. Join weekly bunker_fuel_prices (ULSD) with ocean_freight_rates (WCI composite)
    #   2. Lag search 0–4 weeks, pick best R²
    #   3. Write fitted row to crude_transmission_calibration with
    #      cost_component='distillate_to_freight', data_source='paid_api_regression'
    raise NotImplementedError("Calibration ready to implement once paid history is backfilled.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Paid-tier ocean freight adapter (FBX / Drewry API)")
    parser.add_argument("--source", choices=["fbx", "drewry"], help="Which paid source to pull")
    parser.add_argument("--backfill", action="store_true", help="Pull full available history")
    parser.add_argument("--calibrate", action="store_true", help="Fit distillate→freight correlation")
    args = parser.parse_args()

    if args.calibrate:
        _calibrate_distillate_to_freight()
        return

    if not args.source:
        parser.print_help()
        sys.exit(1)

    if args.source == "fbx":
        api_key = os.environ.get("FBX_API_KEY")
        if not api_key:
            print("ERROR: FBX_API_KEY not set. Contact freight.freightos.com/fbx-api for access.")
            sys.exit(1)
        rows = _get_fbx_lanes(api_key)
    else:
        api_key = os.environ.get("DREWRY_API_KEY")
        if not api_key:
            print("ERROR: DREWRY_API_KEY not set. Contact drewry.co.uk for API access.")
            sys.exit(1)
        rows = _get_drewry_lanes(api_key)

    result = _upsert_freight_rows(rows)
    print(f"[{args.source}] inserted={result['inserted']} skipped={result['skipped_duplicates']}")


if __name__ == "__main__":
    main()

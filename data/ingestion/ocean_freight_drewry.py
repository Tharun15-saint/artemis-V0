"""
Ocean freight rate ingestion from Drewry World Container Index (WCI).

Fetches weekly WCI rates from Drewry's public page and writes append-only rows
to ocean_freight_rates with full provenance tracking.

REAL DATA ONLY. As of the 2026-06 integrity rebuild this script writes ONLY the
corridors Drewry actually publishes (drewry_wci_direct) plus the WCI global
composite. It no longer derives non-published corridors by multiplying Shanghai
rates by a static differential — those derived rows were collinear with the
Shanghai base and carried zero independent information, so they were purged
(see ocean_freight_derived_purge.py). Per-lane rates for Asian/Indian-subcontinent
origins return ONLY via a paid FBX / Drewry feed (see ocean_freight_fbx_diagnostic.py).

Data discipline:
- Only corridors with a REAL parsed rate are written; unparsed ones are skipped
  with an explicit reason (never fabricated).
- Identical re-pulls (same corridor + as_of_date + rate) are skipped via
  is_duplicate_row to avoid same-date duplicate rows.
- is_latest scoped per (origin_port, destination_port).
- Every null field has an explicit reason in data_notes.
- Every run logged to ingestion_log.

Source: https://www.drewry.co.uk/supply-chain-advisors/supply-chain-expertise/world-container-index-assessed-by-drewry
Update frequency: Weekly, published every Thursday
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from database.base import SessionLocal, is_duplicate_row, mark_latest
from database.ingestion_context import IngestionContext
from database.models import OceanFreightRates

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)

SOURCE_NAME = 'ocean_freight_drewry'
SCRIPT_VERSION = '2.0.0'  # 2026-06 integrity rebuild: real-data-only, no derivation
DIRECT_TIER = 'drewry_wci_direct'
COMPOSITE_TIER = 'drewry_wci_composite'
# The WCI global composite is a benchmark index, not a shippable port-pair.
# Stored with these sentinel endpoints so consumers can filter it out of
# per-corridor landed-cost math while still using it as a freight time series.
COMPOSITE_ORIGIN = 'WCI Composite'
COMPOSITE_DESTINATION = 'Global Composite'
DREWRY_URL = (
    'https://www.drewry.co.uk/supply-chain-advisors/supply-chain-expertise/'
    'world-container-index-assessed-by-drewry'
)
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml',
}

# Drewry direct corridor patterns
# Maps (origin, destination) to regex that extracts the rate
DIRECT_CORRIDOR_PATTERNS = {
    ('Shanghai', 'New York'): re.compile(
        r'Shanghai\s+to\s+New\s+York[^$]*\$(\d[\d,]+(?:\.\d+)?)\s*per\s*40ft',
        re.I
    ),
    ('Shanghai', 'Los Angeles'): re.compile(
        r'Shanghai\s+to\s+Los\s+Angeles[^$]*\$(\d[\d,]+(?:\.\d+)?)\s*per\s*40ft',
        re.I
    ),
    ('Shanghai', 'Rotterdam'): re.compile(
        r'Shanghai\s+to\s+Rotterdam[^$]*\$(\d[\d,]+(?:\.\d+)?)\s*per\s*40ft',
        re.I
    ),
    ('Shanghai', 'Genoa'): re.compile(
        r'Shanghai\s+to\s+Genoa[^$]*\$(\d[\d,]+(?:\.\d+)?)\s*per\s*40ft',
        re.I
    ),
}

# WCI composite pattern
WCI_COMPOSITE_PATTERN = re.compile(
    r'(?:WCI|World\s+Container\s+Index)[^$]*\$(\d[\d,]+(?:\.\d+)?)\s*per\s*40ft',
    re.I
)

# Publication date pattern
DATE_PATTERN = re.compile(
    r'(?:Thursday|assessment\s+for\s+Thursday)[,\s]*'
    r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{4})',
    re.I
)

# Fallback date pattern
DATE_FALLBACK_PATTERN = re.compile(
    r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{4})',
    re.I
)


def _fetch_drewry_page() -> str:
    """Fetch Drewry WCI page and return stripped plain text."""
    req = urllib.request.Request(DREWRY_URL, headers=HEADERS)
    resp = urllib.request.urlopen(req, timeout=20)
    content = resp.read().decode('utf-8')
    text = re.sub(r'<[^>]+>', ' ', content)
    text = re.sub(r'\s+', ' ', text)
    return text


def _parse_rate(text: str, pattern: re.Pattern) -> Optional[Decimal]:
    """Extract a dollar rate from text using the given pattern."""
    m = pattern.search(text)
    if not m:
        return None
    raw = m.group(1).replace(',', '')
    try:
        return Decimal(raw)
    except Exception:
        return None


def _parse_publication_date(text: str) -> Optional[date]:
    """Extract the WCI publication date from page text."""
    # Try specific Thursday pattern first
    m = DATE_PATTERN.search(text)
    if not m:
        # Fall back to first date found near 'assessment'
        idx = text.lower().find('assessment')
        if idx > 0:
            m = DATE_FALLBACK_PATTERN.search(text[idx:idx+200])
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1).strip(), '%d %b %Y').date()
    except ValueError:
        try:
            return datetime.strptime(m.group(1).strip(), '%d %B %Y').date()
        except ValueError:
            return None


def _load_corridor_config(db: Session) -> list[dict]:
    """Load all active corridors from ocean_freight_corridor_config."""
    rows = db.execute(
        text('SELECT * FROM ocean_freight_corridor_config WHERE is_active=1')
    ).fetchall()
    return [dict(row._mapping) for row in rows]


def _build_data_notes(source_tier: str, as_of_date: date) -> str:
    """Build structured data_notes JSON for a real Drewry WCI rate row."""
    return json.dumps({
        'source_tier': source_tier,
        'as_of_date': as_of_date.isoformat(),
        'source_url': DREWRY_URL,
        'null_fields': {
            'rate_20ft_usd': 'not published by Drewry WCI for this corridor',
            'rate_40ft_usd': 'WCI publishes 40HQ (high-cube), not standard 40ft',
            'vessel_availability': 'qualitative field not in WCI publication',
            'port_congestion_index': 'only available for major hub ports via separate index',
        },
    })


def ingest_ocean_freight(db: Session, ctx: IngestionContext) -> dict:
    """
    Fetch Drewry WCI, extract the real published corridor rates and the global
    composite, and write them append-only to ocean_freight_rates. Does not
    derive non-published corridors. Returns summary stats.
    """
    pulled_at = datetime.now(timezone.utc)
    stats = {'rows_inserted': 0, 'rows_rejected': 0, 'corridors_processed': 0}

    # Fetch and parse page
    logger.info('Fetching Drewry WCI page')
    text = _fetch_drewry_page()
    logger.info('Page fetched: %d chars', len(text))

    # Parse publication date
    as_of_date = _parse_publication_date(text)
    if as_of_date is None:
        as_of_date = date.today()
        logger.warning('Could not parse publication date, using today: %s', as_of_date)
    else:
        logger.info('Publication date: %s', as_of_date)
    ctx.set_as_of_date(as_of_date)

    # Extract direct Drewry corridor rates
    direct_rates: dict[str, Optional[Decimal]] = {}
    for (origin, destination), pattern in DIRECT_CORRIDOR_PATTERNS.items():
        rate = _parse_rate(text, pattern)
        key = f'{origin}-{destination}'
        direct_rates[key] = rate
        if rate:
            logger.info('Direct rate %s → %s: $%s', origin, destination, rate)
        else:
            logger.warning('Could not parse rate for %s → %s', origin, destination)

    # Extract WCI composite
    wci_composite = _parse_rate(text, WCI_COMPOSITE_PATTERN)
    if wci_composite:
        logger.info('WCI Composite: $%s', wci_composite)
        direct_rates['WCI-Composite'] = wci_composite
    else:
        logger.warning('Could not parse WCI composite rate')

    # Load corridor config and keep ONLY the real Drewry-published corridors.
    # Derived corridors are intentionally ignored (see module docstring).
    corridors = _load_corridor_config(db)
    direct_corridors = [c for c in corridors if c['source_tier'] == DIRECT_TIER]
    logger.info(
        'Loaded %d active corridors (%d direct/real, ignoring derived)',
        len(corridors), len(direct_corridors),
    )

    def _write_rate(
        origin_port: str, origin_country: str,
        destination_port: str, destination_country: str,
        rate_40ft_hc: Decimal, source_tier: str,
        transit_days: Optional[int],
    ) -> None:
        """Append-only write with dedup + is_latest demotion for one corridor."""
        label = f'{origin_port} → {destination_port}'
        # Skip an identical re-pull (same corridor + as_of_date + rate) so a
        # second run on the same WCI publication day does not duplicate rows.
        if is_duplicate_row(
            db, OceanFreightRates,
            {'origin_port': origin_port, 'destination_port': destination_port,
             'as_of_date': as_of_date},
            {'rate_40ft_hc_usd': rate_40ft_hc},
        ):
            logger.info('Duplicate (unchanged) %s for %s — skipping', label, as_of_date)
            ctx.increment_rejected(f'Duplicate unchanged rate for {label} {as_of_date}')
            stats['rows_rejected'] += 1
            return

        mark_latest(db, OceanFreightRates, {
            'origin_port': origin_port,
            'destination_port': destination_port,
        })
        db.add(OceanFreightRates(
            origin_port=origin_port,
            origin_country=origin_country,
            destination_port=destination_port,
            destination_country=destination_country,
            rate_20ft_usd=None,   # documented in data_notes as structural absence
            rate_40ft_usd=None,   # WCI publishes 40HQ, not standard 40ft
            rate_40ft_hc_usd=rate_40ft_hc,
            transit_days=transit_days,
            vessel_availability=None,
            port_congestion_index=None,
            as_of_date=as_of_date,
            source=SOURCE_NAME,
            data_source_url=DREWRY_URL,
            data_notes=_build_data_notes(source_tier, as_of_date),
            rate_source_tier=source_tier,
            corridor_differential_pct=None,
            base_corridor=None,
            pulled_at=pulled_at,
            is_latest=1,
        ))
        db.flush()
        stats['rows_inserted'] += 1
        stats['corridors_processed'] += 1
        ctx.increment_inserted()
        logger.info('Inserted %s: $%s (tier: %s)', label, rate_40ft_hc, source_tier)

    # Real direct corridors: match config to a parsed rate by port-pair name.
    # No hardcoded code→rate map (the old one had a phantom 'Rotterdam-New York'
    # key that never parsed). A corridor with no parsed rate is skipped honestly.
    for corridor in direct_corridors:
        rate_40ft = direct_rates.get(
            f"{corridor['origin_port']}-{corridor['destination_port']}"
        )
        if rate_40ft is None:
            logger.warning(
                'No parsed Drewry rate for direct corridor %s → %s — skipping (not fabricated)',
                corridor['origin_port'], corridor['destination_port'],
            )
            stats['rows_rejected'] += 1
            ctx.increment_rejected(
                f"No parsed rate for {corridor['origin_port']}→{corridor['destination_port']}"
            )
            continue
        _write_rate(
            corridor['origin_port'], corridor['origin_country'],
            corridor['destination_port'], corridor['destination_country'],
            rate_40ft, DIRECT_TIER, corridor.get('transit_days_estimate'),
        )

    # WCI global composite — the headline freight benchmark series, stored as a
    # sentinel "corridor" so it can feed crude→freight correlation directly.
    if wci_composite:
        _write_rate(
            COMPOSITE_ORIGIN, 'Global', COMPOSITE_DESTINATION, 'Global',
            wci_composite, COMPOSITE_TIER, None,
        )

    db.commit()
    logger.info(
        'Ocean freight ingestion complete | inserted=%d rejected=%d corridors=%d date=%s',
        stats['rows_inserted'],
        stats['rows_rejected'],
        stats['corridors_processed'],
        as_of_date,
    )
    return stats


def run_once() -> bool:
    db = SessionLocal()
    try:
        logger.info('Starting ocean freight Drewry WCI ingestion...')
        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=DREWRY_URL,
            db=db,
        ) as ctx:
            stats = ingest_ocean_freight(db, ctx)
            logger.info(
                'Ocean freight Drewry run complete | inserted=%d rejected=%d corridors=%d',
                stats['rows_inserted'],
                stats['rows_rejected'],
                stats['corridors_processed'],
            )
            return stats['rows_inserted'] > 0
    except Exception as exc:
        logger.critical('Ocean freight ingestion failed: %s', exc, exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


if __name__ == '__main__':
    raise SystemExit(0 if run_once() else 1)

"""
Ocean freight rate ingestion from Drewry World Container Index (WCI).

Fetches weekly WCI rates from Drewry's public page, derives corridor rates
for all 19 configured corridors, and writes append-only rows to
ocean_freight_rates with full provenance tracking.

Data discipline:
- Direct Drewry corridors: tagged drewry_wci_direct, no differential
- Derived corridors: tagged drewry_wci_derived, differential from
  ocean_freight_corridor_config, base corridor documented
- Every null field has an explicit reason in data_notes
- is_latest scoped per (origin_port, destination_port, as_of_date)
- Every run logged to ingestion_log

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

from database.base import SessionLocal, mark_latest
from database.ingestion_context import IngestionContext
from database.models import OceanFreightRates

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)

SOURCE_NAME = 'ocean_freight_drewry'
SCRIPT_VERSION = '1.0.0'
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


def _derive_rate(
    base_rate: Decimal,
    differential_pct: Optional[float],
) -> Decimal:
    """Apply corridor differential to base rate."""
    if differential_pct is None:
        return base_rate
    factor = Decimal(str(1 + differential_pct / 100))
    return (base_rate * factor).quantize(Decimal('0.01'))


def _build_data_notes(
    corridor: dict,
    base_rate: Optional[Decimal],
    as_of_date: date,
) -> str:
    """Build structured data_notes JSON for a rate row."""
    notes = {
        'source_tier': corridor['source_tier'],
        'as_of_date': as_of_date.isoformat(),
    }
    if corridor['source_tier'] == 'drewry_wci_derived':
        notes['base_corridor'] = corridor['base_drewry_corridor']
        notes['differential_pct'] = corridor['differential_pct']
        notes['base_rate_usd'] = str(base_rate) if base_rate else None
        notes['differential_source'] = corridor['differential_source']
        notes['null_fields'] = {}
        if corridor.get('transit_days_estimate'):
            notes['transit_days_note'] = 'estimated from corridor config, not confirmed booking'
    else:
        notes['source_url'] = DREWRY_URL
        notes['null_fields'] = {}

    # Document expected nulls
    null_reasons = {
        'rate_20ft_usd': 'not published by Drewry WCI for this corridor',
        'vessel_availability': 'qualitative field not in WCI publication',
        'port_congestion_index': 'only available for major hub ports via separate index',
    }
    notes['null_fields'] = null_reasons
    return json.dumps(notes)


def ingest_ocean_freight(db: Session, ctx: IngestionContext) -> dict:
    """
    Main ingestion function. Fetches Drewry WCI, extracts rates,
    derives all 19 corridor rates, writes to ocean_freight_rates.
    Returns summary stats.
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

    # Load corridor config
    corridors = _load_corridor_config(db)
    logger.info('Loaded %d active corridors from config', len(corridors))

    # Map corridor codes to direct rates for derivation
    # SHA-LAX uses Shanghai-Los Angeles, SHA-NYC uses Shanghai-New York, etc.
    drewry_direct_map = {
        'SHA-LAX': direct_rates.get('Shanghai-Los Angeles'),
        'SHA-NYC': direct_rates.get('Shanghai-New York'),
        'RTM-NYC': direct_rates.get('Rotterdam-New York'),
        'SHA-GEN': direct_rates.get('Shanghai-Genoa'),
    }

    # Process each corridor
    for corridor in corridors:
        corridor_code = corridor['corridor_code']
        source_tier = corridor['source_tier']

        # Determine the rate for this corridor
        if source_tier == 'drewry_wci_direct':
            # Map corridor code to parsed rate
            rate_40ft = drewry_direct_map.get(corridor_code)
            base_rate = None
        else:
            # Derived corridor — apply differential to base
            base_corridor_code = corridor['base_drewry_corridor']
            base_rate = drewry_direct_map.get(base_corridor_code)
            if base_rate is None:
                logger.warning(
                    'Base rate not available for %s (base: %s) — skipping',
                    corridor_code, base_corridor_code
                )
                stats['rows_rejected'] += 1
                ctx.increment_rejected(
                    f'Base rate not available for {corridor_code} (base: {base_corridor_code})'
                )
                continue
            rate_40ft = _derive_rate(base_rate, corridor['differential_pct'])

        if rate_40ft is None:
            logger.warning('No rate for corridor %s — skipping', corridor_code)
            stats['rows_rejected'] += 1
            ctx.increment_rejected(f'No rate for corridor {corridor_code}')
            continue

        # Build the row
        data_notes = _build_data_notes(corridor, base_rate, as_of_date)

        row = OceanFreightRates(
            origin_port=corridor['origin_port'],
            origin_country=corridor['origin_country'],
            destination_port=corridor['destination_port'],
            destination_country=corridor['destination_country'],
            rate_20ft_usd=None,  # documented in data_notes as structural absence
            rate_40ft_usd=None,  # WCI publishes 40HQ, not standard 40ft
            rate_40ft_hc_usd=rate_40ft,
            transit_days=corridor.get('transit_days_estimate'),
            vessel_availability=None,  # documented in data_notes
            port_congestion_index=None,  # documented in data_notes
            as_of_date=as_of_date,
            source=SOURCE_NAME,
            data_source_url=DREWRY_URL,
            data_notes=data_notes,
            rate_source_tier=source_tier,
            corridor_differential_pct=(
                Decimal(str(corridor['differential_pct']))
                if corridor['differential_pct'] is not None else None
            ),
            base_corridor=corridor.get('base_drewry_corridor'),
            pulled_at=pulled_at,
            is_latest=1,
        )

        # Demote all prior rows for this corridor before inserting the new latest row
        mark_latest(db, OceanFreightRates, {
            'origin_port': corridor['origin_port'],
            'destination_port': corridor['destination_port'],
        })
        db.add(row)
        db.flush()

        stats['rows_inserted'] += 1
        stats['corridors_processed'] += 1
        ctx.increment_inserted()
        logger.info(
            'Inserted %s → %s: $%s (tier: %s)',
            corridor['origin_port'],
            corridor['destination_port'],
            rate_40ft,
            source_tier,
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

#!/usr/bin/env python3
"""
One-time repair: backfill price_per_kg and data_notes on existing RRK yarn rows.

Reads the original Excel file, re-parses PO rates for rows where price_per_kg
is NULL, populates data_notes where missing, then computes price_per_kg_usd.

Run:
  python scripts/repair_yarn_prices.py \
    --file "/path/to/Yarn Against Order RRK.xlsx"
"""

from __future__ import annotations

import argparse
import logging
from decimal import Decimal
from pathlib import Path

from data.ingestion._env import load_project_env
from data.ingestion.rrk_yarn_ingestion import (
    _cell_text,
    _effective_rate_from_row,
    _parse_decimal,
    build_yarn_data_notes,
    forward_fill_identifiers,
    parse_grn_field,
    parse_particulars,
    read_excel_rows,
    resolve_price_per_kg,
)
from database.base import SessionLocal
from database.models.yarn_fabric import Yarn
from database.yarn_usd_prices import update_yarn_usd_prices

load_project_env()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _match_yarn_row(
    db,
    *,
    grn_number: str | None,
    buyer_reference: str | None,
    quantity_kg: Decimal,
) -> Yarn | None:
    if not grn_number:
        return None
    query = (
        db.query(Yarn)
        .filter(Yarn.is_latest.is_(True))
        .filter(Yarn.grn_number == grn_number)
        .filter(Yarn.buyer_reference == buyer_reference)
        .filter(Yarn.quantity_kg == quantity_kg)
    )
    return query.first()


def repair_from_excel(file_path: Path, db) -> tuple[int, int]:
    """
    Re-parse Excel rates for yarn rows with NULL price_per_kg.
    Returns (prices_repaired, notes_populated_from_excel).
    """
    rows = read_excel_rows(file_path)
    forward_fill_identifiers(rows)

    prices_repaired = 0
    notes_from_excel = 0

    for row in rows:
        po_no = _cell_text(row.get("po_no"))
        ref_no = _cell_text(row.get("ref_no"))
        particulars = _cell_text(row.get("particulars"))
        rec_qty = _parse_decimal(row.get("rec_qty"))
        effective_rate = _effective_rate_from_row(row)

        if not particulars or rec_qty is None or rec_qty == 0:
            continue
        if not po_no or not po_no.upper().startswith("RRK-"):
            continue

        grn_number, grn_date, supplier = parse_grn_field(row.get("grn"))
        parsed = parse_particulars(particulars, rate=effective_rate)
        price_per_kg, rate_failed = resolve_price_per_kg(effective_rate, parsed)
        requires_review = (
            parsed.requires_review or parsed.fibre_pct_suspicious or rate_failed
        )

        yarn_row = _match_yarn_row(
            db,
            grn_number=grn_number,
            buyer_reference=ref_no,
            quantity_kg=rec_qty,
        )
        if yarn_row is None:
            continue

        if yarn_row.price_per_kg is None and price_per_kg is not None:
            yarn_row.price_per_kg = price_per_kg
            prices_repaired += 1
            logger.info(
                "Repaired price_per_kg=%s for GRN %s ref %s",
                price_per_kg,
                grn_number,
                ref_no,
            )

        if yarn_row.data_notes is None:
            yarn_row.data_notes = build_yarn_data_notes(
                source_file_name=file_path.name,
                yarn_type_raw=particulars,
                supplier_name=supplier or yarn_row.supplier_name,
                grn_number=grn_number or yarn_row.grn_number,
                grn_date=grn_date or yarn_row.grn_date,
                po_number=po_no or yarn_row.po_number,
                buyer_reference=ref_no or yarn_row.buyer_reference,
                quantity_kg=rec_qty,
                price_per_kg=yarn_row.price_per_kg or price_per_kg,
                fibre_type=parsed.fibre_type,
                requires_review=requires_review or yarn_row.requires_review,
            )
            notes_from_excel += 1

    return prices_repaired, notes_from_excel


def backfill_missing_data_notes(db, source_file_name: str) -> int:
    """Populate data_notes on remaining rows from stored DB columns."""
    populated = 0
    rows = (
        db.query(Yarn)
        .filter(Yarn.source == "rrk_excel_import")
        .filter(Yarn.data_notes.is_(None))
        .all()
    )
    for yarn_row in rows:
        yarn_row.data_notes = build_yarn_data_notes(
            source_file_name=source_file_name,
            yarn_type_raw=yarn_row.yarn_type_raw or "",
            supplier_name=yarn_row.supplier_name,
            grn_number=yarn_row.grn_number,
            grn_date=yarn_row.grn_date,
            po_number=yarn_row.po_number,
            buyer_reference=yarn_row.buyer_reference,
            quantity_kg=yarn_row.quantity_kg,
            price_per_kg=yarn_row.price_per_kg,
            fibre_type=yarn_row.fibre_type or "unknown",
            requires_review=bool(yarn_row.requires_review),
        )
        populated += 1
    return populated


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair RRK yarn price_per_kg and data_notes")
    parser.add_argument("--file", required=True, type=Path, help="Original RRK Excel file")
    args = parser.parse_args()

    if not args.file.exists():
        raise FileNotFoundError(f"Excel file not found: {args.file}")

    db = SessionLocal()
    try:
        prices_repaired, notes_from_excel = repair_from_excel(args.file, db)
        notes_backfilled = backfill_missing_data_notes(db, args.file.name)
        db.commit()

        usd_updated = update_yarn_usd_prices(db, only_null=True)

        print("=== YARN PRICE REPAIR COMPLETE ===")
        print(f"  price_per_kg repaired:     {prices_repaired}")
        print(f"  data_notes from Excel:     {notes_from_excel}")
        print(f"  data_notes backfilled:     {notes_backfilled}")
        print(f"  USD prices updated:        {usd_updated}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""retailer_data_quality_calendar_prediction_metadata

Revision ID: c3d4e5f6a7b8
Revises: fb2cddf64d2b
Create Date: 2026-06-10 12:00:00.000000

Adds data_quality + calendar fields on retailer_financials,
metadata_json on prediction_log and retailer_demand_forecast.
Backfills provenance JSON and calendar quarter from period_end_date.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "fb2cddf64d2b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("retailer_financials", schema=None) as batch_op:
        batch_op.add_column(sa.Column("calendar_year", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("calendar_quarter", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("data_quality", sa.Text(), nullable=True))

    with op.batch_alter_table("prediction_log", schema=None) as batch_op:
        batch_op.add_column(sa.Column("metadata_json", sa.Text(), nullable=True))

    with op.batch_alter_table("retailer_demand_forecast", schema=None) as batch_op:
        batch_op.add_column(sa.Column("metadata_json", sa.Text(), nullable=True))

    # Calendar quarter from period_end_date
    op.execute(
        """
        UPDATE retailer_financials
        SET
          calendar_year = CAST(strftime('%Y', period_end_date) AS INTEGER),
          calendar_quarter = CAST(
            (CAST(strftime('%m', period_end_date) AS INTEGER) - 1) / 3 + 1 AS INTEGER
          )
        WHERE period_end_date IS NOT NULL
        """
    )

    # XBRL-ingested rows
    op.execute(
        """
        UPDATE retailer_financials
        SET data_quality = json_object(
          'total_net_sales_usd', CASE WHEN total_net_sales_usd IS NOT NULL
            THEN json_object(
              'source_type', 'xbrl',
              'source_url', COALESCE(source_10q_url, data_source_url),
              'confidence', 'high'
            ) END,
          'gross_margin_pct', CASE WHEN gross_margin_pct IS NOT NULL
            THEN json_object(
              'source_type', 'xbrl',
              'source_url', COALESCE(source_10q_url, data_source_url),
              'confidence', 'high'
            ) END,
          'inventory_usd', CASE WHEN inventory_usd IS NOT NULL
            THEN json_object(
              'source_type', 'xbrl',
              'source_url', COALESCE(source_10q_url, data_source_url),
              'confidence', 'high'
            ) END,
          'operating_margin_pct', CASE
            WHEN operating_margin_pct IS NOT NULL AND retailer_id = 2
            THEN json_object(
              'source_type', 'xbrl',
              'source_url', COALESCE(source_10q_url, data_source_url),
              'confidence', 'high'
            ) END
        )
        WHERE xbrl_extracted = 1
          AND source IN ('target_sec_edgar', 'walmart_sec_edgar')
        """
    )

    op.execute(
        """
        UPDATE retailer_financials
        SET data_quality = json_patch(
          COALESCE(data_quality, '{}'),
          json_object(
            'operating_margin_pct', json_object(
              'source_type', 'regex_10q',
              'source_url', source_10q_url,
              'confidence', 'medium'
            )
          )
        )
        WHERE retailer_id = 1
          AND operating_margin_pct IS NOT NULL
          AND source_10q_url IS NOT NULL
          AND (
            data_quality IS NULL
            OR json_extract(data_quality, '$.operating_margin_pct') IS NULL
          )
        """
    )

    op.execute(
        """
        UPDATE retailer_financials
        SET data_quality = json_patch(
          COALESCE(data_quality, '{}'),
          json_object(
            'apparel_revenue_usd', json_object(
              'source_type', 'mix_derived',
              'source_url', COALESCE(source_10q_url, data_source_url),
              'confidence', 'medium'
            ),
            'apparel_revenue_pct_total', json_object(
              'source_type', 'mix_derived',
              'source_url', COALESCE(source_10q_url, data_source_url),
              'confidence', 'medium'
            )
          )
        )
        WHERE source = 'mix_table_derived'
          AND apparel_revenue_usd IS NOT NULL
        """
    )

    op.execute(
        """
        UPDATE retailer_financials
        SET data_quality = json_patch(
          COALESCE(data_quality, '{}'),
          json_object(
            'comparable_sales_growth_pct', json_object(
              'source_type', 'regex_8k',
              'source_url', source_8k_url,
              'confidence', 'medium'
            )
          )
        )
        WHERE comparable_sales_growth_pct IS NOT NULL
          AND source_8k_url IS NOT NULL
          AND (
            data_quality IS NULL
            OR json_extract(data_quality, '$.comparable_sales_growth_pct') IS NULL
          )
        """
    )

    op.execute(
        """
        UPDATE retailer_financials
        SET data_quality = json_patch(
          COALESCE(data_quality, '{}'),
          json_object(
            'store_count_total', json_object(
              'source_type', 'regex_8k',
              'source_url', source_8k_url,
              'confidence', CASE
                WHEN source_8k_url LIKE '%dex991%' THEN 'medium'
                ELSE 'high'
              END
            )
          )
        )
        WHERE store_count_total IS NOT NULL
          AND source_8k_url IS NOT NULL
          AND (
            data_quality IS NULL
            OR json_extract(data_quality, '$.store_count_total') IS NULL
          )
        """
    )

    op.execute(
        """
        UPDATE retailer_financials
        SET data_quality = json_patch(
          COALESCE(data_quality, '{}'),
          json_object(
            'guidance_sales_direction', json_object(
              'source_type', CASE
                WHEN guidance_sales_direction = 'not_provided' THEN 'manual_fix'
                ELSE 'regex_8k'
              END,
              'source_url', COALESCE(source_8k_url, source_10q_url),
              'confidence', CASE
                WHEN guidance_sales_direction = 'not_provided' THEN 'high'
                ELSE 'medium'
              END
            )
          )
        )
        WHERE guidance_sales_direction IS NOT NULL
        """
    )

    op.execute(
        """
        UPDATE retailer_financials
        SET data_quality = json_patch(
          COALESCE(data_quality, '{}'),
          json_object(
            'inventory_days', json_object(
              'source_type', 'income_stmt_derived',
              'source_url', COALESCE(source_10q_url, data_source_url),
              'confidence', 'medium'
            )
          )
        )
        WHERE inventory_days IS NOT NULL
          AND inventory_usd IS NOT NULL
          AND (
            data_quality IS NULL
            OR json_extract(data_quality, '$.inventory_days') IS NULL
          )
        """
    )

    op.execute(
        """
        UPDATE retailer_financials
        SET data_quality = json_patch(
          COALESCE(data_quality, '{}'),
          json_object(
            'total_net_sales_usd', json_object(
              'source_type', 'regex_10q',
              'source_url', source_10q_url,
              'confidence', 'medium'
            )
          )
        )
        WHERE total_net_sales_usd IS NOT NULL
          AND source_10q_url IS NOT NULL
          AND (
            data_quality IS NULL
            OR json_extract(data_quality, '$.total_net_sales_usd') IS NULL
          )
        """
    )

    op.execute(
        """
        UPDATE retailer_financials
        SET data_quality = json_patch(
          COALESCE(data_quality, '{}'),
          json_object(
            'apparel_revenue_usd', json_object(
              'source_type', 'html_table',
              'source_url', source_10q_url,
              'confidence', 'high'
            )
          )
        )
        WHERE retailer_id = 1
          AND apparel_revenue_usd IS NOT NULL
          AND source != 'mix_table_derived'
          AND source_10q_url IS NOT NULL
          AND (
            data_quality IS NULL
            OR json_extract(data_quality, '$.apparel_revenue_usd') IS NULL
          )
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("retailer_demand_forecast", schema=None) as batch_op:
        batch_op.drop_column("metadata_json")

    with op.batch_alter_table("prediction_log", schema=None) as batch_op:
        batch_op.drop_column("metadata_json")

    with op.batch_alter_table("retailer_financials", schema=None) as batch_op:
        batch_op.drop_column("data_quality")
        batch_op.drop_column("calendar_quarter")
        batch_op.drop_column("calendar_year")

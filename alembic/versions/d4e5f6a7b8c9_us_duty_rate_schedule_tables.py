"""us_duty_rate_schedule_tables

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-15 17:25:00.000000

USITC HTS duty rate schedule and per-country effective rate layer.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "us_duty_rate_schedule",
        sa.Column("rate_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("hts_number", sa.String(length=20), nullable=False),
        sa.Column("hts_description", sa.Text(), nullable=True),
        sa.Column("chapter", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(length=10), nullable=False),
        sa.Column("indent_level", sa.Integer(), nullable=True),
        sa.Column("ntr_rate_pct", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("ntr_rate_text", sa.String(length=100), nullable=True),
        sa.Column("ntr_rate_is_compound", sa.Boolean(), server_default=sa.text("0"), nullable=True),
        sa.Column("fta_free_countries", sa.Text(), nullable=True),
        sa.Column("jusfta_jordan_free", sa.Boolean(), server_default=sa.text("0"), nullable=True),
        sa.Column("korus_korea_free", sa.Boolean(), server_default=sa.text("0"), nullable=True),
        sa.Column("morocco_fta_free", sa.Boolean(), server_default=sa.text("0"), nullable=True),
        sa.Column("cafta_dr_free", sa.Boolean(), server_default=sa.text("0"), nullable=True),
        sa.Column("column2_rate_text", sa.String(length=100), nullable=True),
        sa.Column("additional_duties_text", sa.String(length=200), nullable=True),
        sa.Column("section_301_china_applies", sa.Boolean(), server_default=sa.text("0"), nullable=True),
        sa.Column("section_301_china_rate_pct", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("section_301_list", sa.String(length=20), nullable=True),
        sa.Column(
            "ieepa_universal_rate_pct",
            sa.Numeric(precision=8, scale=4),
            server_default=sa.text("10.0"),
            nullable=True,
        ),
        sa.Column("ieepa_universal_notes", sa.Text(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("hts_revision", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("data_source_url", sa.String(length=500), nullable=True),
        sa.Column("last_verified", sa.Date(), nullable=False),
        sa.Column("is_latest", sa.Boolean(), server_default=sa.text("1"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.PrimaryKeyConstraint("rate_id"),
    )
    with op.batch_alter_table("us_duty_rate_schedule", schema=None) as batch_op:
        batch_op.create_index("ix_duty_hts", ["hts_number", "is_latest"], unique=False)
        batch_op.create_index("ix_duty_chapter", ["chapter", "is_latest"], unique=False)
        batch_op.create_index("ix_duty_jordan", ["jusfta_jordan_free", "chapter"], unique=False)
        batch_op.create_index("ix_duty_heading", ["heading", "is_latest"], unique=False)

    op.create_table(
        "us_duty_country_effective_rate",
        sa.Column("effective_rate_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("hts_number", sa.String(length=20), nullable=False),
        sa.Column("origin_country", sa.String(length=100), nullable=False),
        sa.Column("origin_iso2", sa.String(length=2), nullable=False),
        sa.Column("ntr_rate_pct", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("fta_rate_pct", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("fta_program", sa.String(length=50), nullable=True),
        sa.Column("section_301_additional_pct", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("ieepa_additional_pct", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("effective_rate_pct", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("effective_rate_notes", sa.Text(), nullable=True),
        sa.Column("yarn_forward_required", sa.Boolean(), server_default=sa.text("0"), nullable=True),
        sa.Column("uflpa_risk", sa.Boolean(), server_default=sa.text("0"), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("is_latest", sa.Boolean(), server_default=sa.text("1"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.PrimaryKeyConstraint("effective_rate_id"),
    )
    with op.batch_alter_table("us_duty_country_effective_rate", schema=None) as batch_op:
        batch_op.create_index(
            "ix_effective_rate_hts_country",
            ["hts_number", "origin_iso2", "is_latest"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("us_duty_country_effective_rate", schema=None) as batch_op:
        batch_op.drop_index("ix_effective_rate_hts_country")
    op.drop_table("us_duty_country_effective_rate")

    with op.batch_alter_table("us_duty_rate_schedule", schema=None) as batch_op:
        batch_op.drop_index("ix_duty_heading")
        batch_op.drop_index("ix_duty_jordan")
        batch_op.drop_index("ix_duty_chapter")
        batch_op.drop_index("ix_duty_hts")
    op.drop_table("us_duty_rate_schedule")

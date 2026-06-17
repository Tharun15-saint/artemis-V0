"""Add quality_check_log table for crude oil data quality gate.

Every quality check run writes a row here. get_blocking_failures() queries
unresolved 'fail' rows to block cost engine outputs.

Revision ID: r2s3t4u5v6w7
Revises: q1r2s3t4u5v6
"""
from alembic import op
import sqlalchemy as sa

revision = "r2s3t4u5v6w7"
down_revision = "q1r2s3t4u5v6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quality_check_log",
        sa.Column("check_id",       sa.Integer, primary_key=True),
        sa.Column("check_name",     sa.String(100), nullable=False),
        sa.Column("check_date",     sa.Date,         nullable=False),
        sa.Column("result",         sa.String(20),   nullable=False),  # pass / warn / fail
        sa.Column("details",        sa.Text),
        sa.Column("resolved",       sa.Boolean, nullable=False, server_default="0"),
        sa.Column("resolved_by",    sa.String(100)),
        sa.Column("resolved_at",    sa.DateTime),
        sa.Column("resolution_note", sa.Text),
        sa.Column("created_at",     sa.DateTime, server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("quality_check_log")

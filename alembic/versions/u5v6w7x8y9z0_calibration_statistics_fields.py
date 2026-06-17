"""Add statistical-rigor fields to crude_transmission_calibration.

The calibration engine (intelligence/transmission_calibration.py) records the
full statistical basis of every activation decision:

  p_value               — two-tailed p-value of the transmission coefficient (t-test)
  coeff_ci_low/high      — 95% confidence interval bounds on the coefficient
  calibrated_from        — provenance tag, e.g. 'rrk_invoice_empirical'
  empirical_threshold    — Chow-test structural-break price ($/bbl); may differ from $85
  threshold_f_statistic  — F-statistic at the empirical breakpoint
  threshold_p_value      — p-value for the structural break

Revision ID: u5v6w7x8y9z0
Revises: t4u5v6w7x8y9
"""
from alembic import op
import sqlalchemy as sa

revision = "u5v6w7x8y9z0"
down_revision = "t4u5v6w7x8y9"
branch_labels = None
depends_on = None

_COLS = [
    ("p_value", sa.Numeric(8, 6)),
    ("coeff_ci_low", sa.Numeric(12, 6)),
    ("coeff_ci_high", sa.Numeric(12, 6)),
    ("calibrated_from", sa.String(50)),
    ("empirical_threshold", sa.Numeric(8, 2)),
    ("threshold_f_statistic", sa.Numeric(12, 4)),
    ("threshold_p_value", sa.Numeric(8, 6)),
]


def upgrade() -> None:
    with op.batch_alter_table("crude_transmission_calibration") as batch_op:
        for name, coltype in _COLS:
            batch_op.add_column(sa.Column(name, coltype, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("crude_transmission_calibration") as batch_op:
        for name, _ in _COLS:
            batch_op.drop_column(name)

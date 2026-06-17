"""Add crude-to-cost transmission calibration table.

Purpose: when RRK's dyeing cost invoices are ingested, this table stores the
empirically measured transmission coefficients between crude price (at T-lag) and
dyeing chemical cost (at observation date). Until then, it holds the industry-prior
values seeded from LearnedCoefficient (ids 41,42) as uncalibrated placeholders.

crude_transmission_calibration columns:
  transmission_id        — PK
  cost_component         — which cost is being modelled: 'dyeing_chemical' | 'freight_energy' | 'polyester_yarn'
  data_source            — 'rrk_invoices' when real data; 'industry_prior' until then
  obs_count              — number of (crude, cost) paired observations used to fit
  lag_weeks_empirical    — measured lag (weeks between crude move and cost response)
  lag_weeks_ci_low/high  — 90% confidence interval on the lag
  transmission_coeff     — dCost/dCrude — how much cost moves per $1 brent move
  r_squared              — goodness-of-fit (0 = no relationship, 1 = perfect)
  brent_series_used      — which crude_oil source was used: 'fred_api' | 'world_bank_pink_sheet'
  calibration_date       — when this calibration was computed
  invoice_date_range_start/end — the invoice corpus date range used to fit
  notes                  — narrative explanation

Revision ID: v4w5x6y7z8a9
Revises: u3v4w5x6y7z8
"""
from alembic import op
import sqlalchemy as sa

revision = "v4w5x6y7z8a9"
down_revision = "u3v4w5x6y7z8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crude_transmission_calibration",
        sa.Column("transmission_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("cost_component", sa.String(50), nullable=False),        # 'dyeing_chemical' etc.
        sa.Column("data_source", sa.String(50), nullable=False),           # 'industry_prior' | 'rrk_invoices'
        sa.Column("obs_count", sa.Integer, nullable=True),                 # NULL until calibrated
        sa.Column("lag_weeks_empirical", sa.Numeric(6, 2), nullable=True), # NULL until calibrated
        sa.Column("lag_weeks_ci_low", sa.Numeric(6, 2), nullable=True),
        sa.Column("lag_weeks_ci_high", sa.Numeric(6, 2), nullable=True),
        sa.Column("transmission_coeff", sa.Numeric(10, 6), nullable=True), # dCost/dBrent
        sa.Column("r_squared", sa.Numeric(6, 4), nullable=True),
        sa.Column("brent_series_used", sa.String(50), nullable=True),      # 'fred_api' or 'world_bank_pink_sheet'
        sa.Column("calibration_date", sa.Date, nullable=True),
        sa.Column("invoice_date_range_start", sa.Date, nullable=True),
        sa.Column("invoice_date_range_end", sa.Date, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
    )

    # Seed the 3 uncalibrated industry priors — linked to LearnedCoefficient ids 41,42
    op.execute("""
        INSERT INTO crude_transmission_calibration
            (cost_component, data_source, lag_weeks_empirical, brent_series_used, is_active, notes)
        VALUES
            ('dyeing_chemical', 'industry_prior', 6, 'fred_api', 0,
             'Industry-prior 6-week lag from IHS Markit. Not yet calibrated against RRK invoices. Awaiting dyeing cost invoice corpus ingestion.'),
            ('freight_energy_surcharge', 'industry_prior', 2, 'fred_api', 0,
             'Industry-prior 2-week lag. Not yet calibrated. Awaiting RRK freight invoice corpus.'),
            ('polyester_yarn', 'industry_prior', 14, 'fred_api', 0,
             'Industry-prior 14-week lag via PX→PTA→chip chain. Not yet calibrated. Awaiting RRK yarn purchase invoice corpus.')
    """)


def downgrade() -> None:
    op.drop_table("crude_transmission_calibration")

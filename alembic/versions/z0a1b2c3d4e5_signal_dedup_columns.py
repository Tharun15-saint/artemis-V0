"""Spoken-signal L3 dedup tagging — retailer_intelligence_extract.dedup_cluster_id + is_canonical.

Principle: capture-all + filter downstream, NEVER delete. The extractor captures every material
statement per turn, so the same FACT (e.g. "inventory up 33%", "e-commerce grew ~1%") legitimately
recurs across turns — distinct utterances, each with its own provenance. Rather than drop these,
we CLUSTER true near-duplicates (same topic + figure / same fact) and mark the single CANONICAL
member (chosen by source authority prepared_remarks>qa, completeness, confidence). Downstream the
clean view = is_canonical (no perceived duplication); the full view = all rows (nothing lost).

  dedup_cluster_id  BIGINT  NULL  — groups members of one fact-cluster (NULL = singleton)
  is_canonical      BOOL    true  — the best/most-authoritative member of its cluster (singletons true)

Revision ID: z0a1b2c3d4e5
Revises: y9z0a1b2c3d4
"""
from alembic import op
import sqlalchemy as sa

revision = "z0a1b2c3d4e5"
down_revision = "y9z0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("retailer_intelligence_extract", sa.Column("dedup_cluster_id", sa.BigInteger))
    op.add_column("retailer_intelligence_extract",
                  sa.Column("is_canonical", sa.Boolean, nullable=False, server_default=sa.text("true")))
    op.create_index("ix_rie_dedup_cluster", "retailer_intelligence_extract", ["dedup_cluster_id"])
    # fast clean-view: canonical signals of the current extraction
    op.create_index("ix_rie_canonical_latest", "retailer_intelligence_extract",
                    ["retailer_id", "fiscal_year", "fiscal_quarter"],
                    postgresql_where=sa.text("is_canonical AND is_latest"))


def downgrade() -> None:
    op.drop_index("ix_rie_canonical_latest", table_name="retailer_intelligence_extract")
    op.drop_index("ix_rie_dedup_cluster", table_name="retailer_intelligence_extract")
    op.drop_column("retailer_intelligence_extract", "is_canonical")
    op.drop_column("retailer_intelligence_extract", "dedup_cluster_id")

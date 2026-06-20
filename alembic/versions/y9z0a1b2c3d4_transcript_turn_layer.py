"""Spoken-signal Layer 2 — structured transcript: transcript_turn + extract turn-anchoring.

The keystone of the strongest-possible earnings-transcript foundation. One row per speaker
turn = the COMPLETE, addressable transcript, built deterministically from the immutable raw L1
(raw_artifact). Each turn carries exact char offsets into the raw `content`, so concatenating
turns in order reconstructs the raw byte-for-byte, and any extracted signal can prove it is a
literal slice of the raw (faithfulness BY CONSTRUCTION).

Also turn-anchors retailer_intelligence_extract: relevance_tier (capture-all + tag, never
delete), source_turn_index + quote char offsets (provenance to raw), replies_to_turn_index
(Q↔A context linkage).

Revision ID: y9z0a1b2c3d4
Revises: x8y9z0a1b2c3
"""
from alembic import op
import sqlalchemy as sa

revision = "y9z0a1b2c3d4"
down_revision = "x8y9z0a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transcript_turn",
        sa.Column("transcript_turn_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("retailer_id", sa.Integer, sa.ForeignKey("major_retailers.retailer_id"), nullable=False),
        sa.Column("fiscal_year", sa.Integer, nullable=False),
        sa.Column("fiscal_quarter", sa.Integer, nullable=False),
        sa.Column("period_end_date", sa.Date),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("content_sha256", sa.String(64)),       # → raw_artifact (immutable L1)
        sa.Column("source_url", sa.String(500)),
        sa.Column("turn_index", sa.Integer, nullable=False),   # 0-based order within the call
        sa.Column("section", sa.String(20)),               # prepared_remarks | qa
        sa.Column("speaker_name", sa.String(160)),
        sa.Column("speaker_role", sa.String(20)),          # CEO|CFO|IR|management|analyst|operator|other
        sa.Column("speaker_firm", sa.String(160)),         # analyst's firm, when known
        sa.Column("speaker_title", sa.String(200)),
        sa.Column("char_start", sa.Integer, nullable=False),   # offset into raw content
        sa.Column("char_end", sa.Integer, nullable=False),
        sa.Column("verbatim_text", sa.Text, nullable=False),   # == raw_content[char_start:char_end]
        sa.Column("word_count", sa.Integer),
        sa.Column("is_question", sa.Boolean),
        sa.Column("replies_to_turn_index", sa.Integer),    # analyst Q this management turn answers
        sa.Column("source_format", sa.String(30)),
        sa.Column("is_latest", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("pulled_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "uq_transcript_turn_latest", "transcript_turn",
        ["retailer_id", "fiscal_year", "fiscal_quarter", "source", "turn_index"],
        unique=True, postgresql_where=sa.text("is_latest"),
    )
    op.create_index("ix_transcript_turn_quarter", "transcript_turn",
                    ["retailer_id", "fiscal_year", "fiscal_quarter"])
    op.create_index("ix_transcript_turn_hash", "transcript_turn", ["content_sha256"])

    # turn-anchor the extracted-signal layer (capture-all + tag + provenance to raw)
    op.add_column("retailer_intelligence_extract", sa.Column("relevance_tier", sa.String(30)))
    op.add_column("retailer_intelligence_extract", sa.Column("source_turn_index", sa.Integer))
    op.add_column("retailer_intelligence_extract", sa.Column("quote_char_start", sa.Integer))
    op.add_column("retailer_intelligence_extract", sa.Column("quote_char_end", sa.Integer))
    op.add_column("retailer_intelligence_extract", sa.Column("replies_to_turn_index", sa.Integer))


def downgrade() -> None:
    for col in ("replies_to_turn_index", "quote_char_end", "quote_char_start",
                "source_turn_index", "relevance_tier"):
        op.drop_column("retailer_intelligence_extract", col)
    op.drop_index("ix_transcript_turn_hash", table_name="transcript_turn")
    op.drop_index("ix_transcript_turn_quarter", table_name="transcript_turn")
    op.drop_index("uq_transcript_turn_latest", table_name="transcript_turn")
    op.drop_table("transcript_turn")

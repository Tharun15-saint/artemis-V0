"""Medallion Layer 1 — immutable raw capture: raw_ingest_run, raw_artifact.

The bottom of the data stack. Captures the exact source payload (SEC JSON/HTML today,
RRK Excel/PDF/scan/email tomorrow) byte-for-byte before any parsing, so every downstream
fact traces to the precise bytes and re-derivation is deterministic and offline. Payload
bytes live in a content-addressable store on disk; these tables hold the manifest +
provenance. raw_artifact has no updated_at by design — raw is append-only and immutable.

Revision ID: x8y9z0a1b2c3
Revises: w7x8y9z0a1b2

NOTE: originally drafted as w7x8y9z0a1b2; rebased to x8y9z0a1b2c3 after a concurrent
session landed w7x8y9z0a1b2 (retail is_latest unique indexes) as the new head.
"""
from alembic import op
import sqlalchemy as sa

revision = "x8y9z0a1b2c3"
down_revision = "w7x8y9z0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_ingest_run",
        sa.Column("raw_ingest_run_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_uuid", sa.String(36), nullable=False, unique=True),
        sa.Column("source_system", sa.String(80), nullable=False),
        sa.Column("run_kind", sa.String(60), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("completed_at", sa.DateTime),
        sa.Column("artifact_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_bytes", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("operator", sa.String(160)),
        sa.Column("tool_version", sa.String(64)),
        sa.Column("parameters_json", sa.Text),
        sa.Column("error_message", sa.Text),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "raw_artifact",
        sa.Column("raw_artifact_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "ingest_run_id",
            sa.Integer,
            sa.ForeignKey("raw_ingest_run.raw_ingest_run_id"),
            nullable=False,
        ),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger, nullable=False),
        sa.Column("media_type", sa.String(120)),
        sa.Column("artifact_kind", sa.String(60), nullable=False),
        sa.Column("source_system", sa.String(80), nullable=False),
        sa.Column("source_uri", sa.Text),
        sa.Column("original_filename", sa.String(500)),
        sa.Column("source_locator_json", sa.Text),
        sa.Column("storage_backend", sa.String(20), nullable=False, server_default="local_cas"),
        sa.Column("storage_path", sa.Text, nullable=False),
        sa.Column("fetched_at", sa.DateTime, nullable=False),
        sa.Column("captured_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        # NOTE: intentionally no updated_at — raw artifacts are immutable.
        sa.UniqueConstraint("ingest_run_id", "content_sha256", name="uq_raw_artifact_run_hash"),
    )
    op.create_index("ix_raw_artifact_hash", "raw_artifact", ["content_sha256"])
    op.create_index("ix_raw_artifact_source", "raw_artifact", ["source_system", "artifact_kind"])
    op.create_index("ix_raw_artifact_run", "raw_artifact", ["ingest_run_id"])


def downgrade() -> None:
    op.drop_index("ix_raw_artifact_run", table_name="raw_artifact")
    op.drop_index("ix_raw_artifact_source", table_name="raw_artifact")
    op.drop_index("ix_raw_artifact_hash", table_name="raw_artifact")
    op.drop_table("raw_artifact")
    op.drop_table("raw_ingest_run")

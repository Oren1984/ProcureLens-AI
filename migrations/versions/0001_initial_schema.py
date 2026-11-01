"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-01 19:17:46.460379
"""

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


APPEND_ONLY_FN = """
CREATE OR REPLACE FUNCTION procurelens_reject_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'audit_events is append-only (% rejected)', TG_OP;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    # pgvector is enabled here, inside the controlled migration (owner role).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_name", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.String(length=140), nullable=True),
        sa.Column("dataset", sa.String(length=10), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "exceptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset", sa.String(length=10), nullable=False),
        sa.Column("exception_key", sa.String(length=400), nullable=False),
        sa.Column("exception_type", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("supplier", sa.String(length=140), nullable=False),
        sa.Column("po_id", sa.String(length=140), nullable=True),
        sa.Column("po_line_id", sa.String(length=140), nullable=True),
        sa.Column("item_code", sa.String(length=140), nullable=True),
        sa.Column("title", sa.String(length=400), nullable=False),
        sa.Column("facts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("calculation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("related", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("notes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("exposure", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("days_overdue", sa.Integer(), nullable=False),
        sa.Column("critical_item", sa.Boolean(), nullable=False),
        sa.Column("priority_score", sa.Integer(), nullable=False),
        sa.Column("priority_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("condition_active", sa.Boolean(), nullable=False),
        sa.Column("source_changed", sa.Boolean(), nullable=False),
        sa.Column(
            "first_detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("dataset IN ('demo','live')", name="ck_exceptions_dataset"),
        sa.CheckConstraint(
            "exception_type IN ('qty_exceeds_receipt','price_variance',"
            "'overdue_delivery','manual_review')",
            name="ck_exceptions_type",
        ),
        sa.CheckConstraint(
            "status IN ('open','in_review','resolved')", name="ck_exceptions_status"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset", "exception_key", name="uq_exceptions_key"),
    )
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset", sa.String(length=10), nullable=False),
        sa.Column("filename", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("doc_type", sa.String(length=40), nullable=False),
        sa.Column("scope", sa.String(length=10), nullable=False),
        sa.Column("supplier_source_id", sa.String(length=140), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("uploaded_by", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(scope = 'supplier') = (supplier_source_id IS NOT NULL)", name="ck_knowledge_supplier"
        ),
        sa.CheckConstraint("dataset IN ('demo','live')", name="ck_knowledge_dataset"),
        sa.CheckConstraint("scope IN ('global','supplier')", name="ck_knowledge_scope"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dataset", "filename", "version", name="uq_knowledge_documents_version"
        ),
    )
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_login_attempts_user_time", "login_attempts", ["username", "attempted_at"], unique=False
    )
    op.create_table(
        "source_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset", sa.String(length=10), nullable=False),
        sa.Column("doctype", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.String(length=140), nullable=False),
        sa.Column("supplier", sa.String(length=140), nullable=False),
        sa.Column("docstatus", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("doc_date", sa.Date(), nullable=True),
        sa.Column("is_return", sa.Boolean(), nullable=False),
        sa.Column("source_modified", sa.String(length=40), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("last_sync_run_id", sa.String(length=36), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("dataset IN ('demo','live')", name="ck_source_documents_dataset"),
        sa.CheckConstraint(
            "doctype IN ('Purchase Order','Purchase Receipt','Purchase Invoice')",
            name="ck_source_documents_doctype",
        ),
        sa.CheckConstraint("docstatus IN (0,1,2)", name="ck_source_documents_docstatus"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset", "doctype", "source_id", name="uq_source_documents_source"),
    )
    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset", sa.String(length=10), nullable=False),
        sa.Column("source_id", sa.String(length=140), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("last_sync_run_id", sa.String(length=36), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset", "source_id", name="uq_suppliers_source"),
    )
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("dataset", sa.String(length=10), nullable=False),
        sa.Column("adapter", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_by", sa.String(length=64), nullable=False),
        sa.Column("counts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint("dataset IN ('demo','live')", name="ck_sync_runs_dataset"),
        sa.CheckConstraint(
            "status IN ('running','succeeded','succeeded_with_warnings','failed')",
            name="ck_sync_runs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "password_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('admin','demo_viewer')", name="ck_users_role"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(op.f("ix_auth_sessions_user_id"), "auth_sessions", ["user_id"], unique=False)
    op.create_table(
        "exception_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("exception_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=True),
        sa.Column("actor_name", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("evidence_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["exception_id"], ["exceptions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_exception_events_exception_id"), "exception_events", ["exception_id"], unique=False
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(length=300), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=384), nullable=True),
        sa.Column("embedding_model", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_knowledge_chunks_index"),
    )
    op.create_table(
        "source_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("line_id", sa.String(length=140), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("item_code", sa.String(length=140), nullable=False),
        sa.Column("item_name", sa.String(length=255), nullable=False),
        sa.Column("qty", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("received_qty", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("rejected_qty", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("rate", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("uom", sa.String(length=40), nullable=False),
        sa.Column("schedule_date", sa.Date(), nullable=True),
        sa.Column("po_id", sa.String(length=140), nullable=True),
        sa.Column("po_line_id", sa.String(length=140), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["source_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "line_id", name="uq_source_lines_line"),
    )
    op.create_index("ix_source_lines_po_ref", "source_lines", ["po_id", "po_line_id"], unique=False)
    # Append-only audit trail: UPDATE/DELETE rejected for every role that does not disable
    # triggers. A superuser/DB administrator can still disable or drop it (documented).
    op.execute(APPEND_ONLY_FN)
    op.execute(
        "CREATE TRIGGER audit_events_append_only BEFORE UPDATE OR DELETE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION procurelens_reject_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS procurelens_reject_mutation()")
    op.drop_index("ix_source_lines_po_ref", table_name="source_lines")
    op.drop_table("source_lines")
    op.drop_table("knowledge_chunks")
    op.drop_index(op.f("ix_exception_events_exception_id"), table_name="exception_events")
    op.drop_table("exception_events")
    op.drop_index(op.f("ix_auth_sessions_user_id"), table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_table("users")
    op.drop_table("sync_runs")
    op.drop_table("suppliers")
    op.drop_table("source_documents")
    op.drop_index("ix_login_attempts_user_time", table_name="login_attempts")
    op.drop_table("login_attempts")
    op.drop_table("knowledge_documents")
    op.drop_table("exceptions")
    op.drop_table("audit_events")
    op.drop_table("app_settings")

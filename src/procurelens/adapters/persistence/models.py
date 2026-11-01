"""SQLAlchemy ORM tables. Schema changes go through Alembic migrations only."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIM = 384  # BAAI/bge-small-en-v1.5; changing the model dimension needs a migration
DATASETS = ("demo", "live")


class Base(DeclarativeBase):
    pass


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin','demo_viewer')", name="ck_users_role"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = _ts()
    password_changed_at: Mapped[datetime] = _ts()


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = _ts()
    last_seen_at: Mapped[datetime] = _ts()
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user: Mapped[User] = relationship()


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_user_time", "username", "attempted_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    attempted_at: Mapped[datetime] = _ts()
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = _ts()
    updated_by: Mapped[str | None] = mapped_column(String(64))


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (
        CheckConstraint("dataset IN ('demo','live')", name="ck_sync_runs_dataset"),
        CheckConstraint(
            "status IN ('running','succeeded','succeeded_with_warnings','failed')",
            name="ck_sync_runs_status",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(10), nullable=False)
    adapter: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    started_at: Mapped[datetime] = _ts()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    triggered_by: Mapped[str] = mapped_column(String(64), nullable=False)
    counts: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    warnings: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)


class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("dataset", "source_id", name="uq_suppliers_source"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset: Mapped[str] = mapped_column(String(10), nullable=False)
    source_id: Mapped[str] = mapped_column(String(140), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    last_sync_run_id: Mapped[str | None] = mapped_column(String(36))
    updated_at: Mapped[datetime] = _ts()


class SourceDocument(Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint("dataset", "doctype", "source_id", name="uq_source_documents_source"),
        CheckConstraint("dataset IN ('demo','live')", name="ck_source_documents_dataset"),
        CheckConstraint("docstatus IN (0,1,2)", name="ck_source_documents_docstatus"),
        CheckConstraint(
            "doctype IN ('Purchase Order','Purchase Receipt','Purchase Invoice')",
            name="ck_source_documents_doctype",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset: Mapped[str] = mapped_column(String(10), nullable=False)
    doctype: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[str] = mapped_column(String(140), nullable=False)
    supplier: Mapped[str] = mapped_column(String(140), nullable=False)
    docstatus: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    doc_date: Mapped[date | None] = mapped_column(Date)
    is_return: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_modified: Mapped[str | None] = mapped_column(String(40))
    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    last_sync_run_id: Mapped[str | None] = mapped_column(String(36))
    first_seen_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()
    lines: Mapped[list[SourceLine]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="SourceLine.idx"
    )


class SourceLine(Base):
    __tablename__ = "source_lines"
    __table_args__ = (
        UniqueConstraint("document_id", "line_id", name="uq_source_lines_line"),
        Index("ix_source_lines_po_ref", "po_id", "po_line_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False
    )
    line_id: Mapped[str] = mapped_column(String(140), nullable=False)
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    item_code: Mapped[str] = mapped_column(String(140), nullable=False)
    item_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    received_qty: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0, nullable=False)
    rejected_qty: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0, nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0, nullable=False)
    uom: Mapped[str] = mapped_column(String(40), nullable=False)
    schedule_date: Mapped[date | None] = mapped_column(Date)
    po_id: Mapped[str | None] = mapped_column(String(140))
    po_line_id: Mapped[str | None] = mapped_column(String(140))
    document: Mapped[SourceDocument] = relationship(back_populates="lines")


class ProcurementException(Base):
    __tablename__ = "exceptions"
    __table_args__ = (
        UniqueConstraint("dataset", "exception_key", name="uq_exceptions_key"),
        CheckConstraint("status IN ('open','in_review','resolved')", name="ck_exceptions_status"),
        CheckConstraint(
            "exception_type IN ('qty_exceeds_receipt','price_variance','overdue_delivery',"
            "'manual_review')",
            name="ck_exceptions_type",
        ),
        CheckConstraint("dataset IN ('demo','live')", name="ck_exceptions_dataset"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset: Mapped[str] = mapped_column(String(10), nullable=False)
    exception_key: Mapped[str] = mapped_column(String(400), nullable=False)
    exception_type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    supplier: Mapped[str] = mapped_column(String(140), nullable=False)
    po_id: Mapped[str | None] = mapped_column(String(140))
    po_line_id: Mapped[str | None] = mapped_column(String(140))
    item_code: Mapped[str | None] = mapped_column(String(140))
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    facts: Mapped[dict] = mapped_column(JSONB, nullable=False)
    calculation: Mapped[list] = mapped_column(JSONB, nullable=False)
    related: Mapped[list] = mapped_column(JSONB, nullable=False)
    notes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    exposure: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    days_overdue: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    critical_item: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    priority_score: Mapped[int] = mapped_column(Integer, nullable=False)
    priority_reasons: Mapped[list] = mapped_column(JSONB, nullable=False)
    condition_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source_changed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    first_detected_at: Mapped[datetime] = _ts()
    last_detected_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    events: Mapped[list[ExceptionEvent]] = relationship(
        back_populates="exception", order_by="ExceptionEvent.id"
    )
    __mapper_args__ = {"version_id_col": version}


class ExceptionEvent(Base):
    """Investigation history. Insert-only for the application runtime role."""

    __tablename__ = "exception_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    exception_id: Mapped[int] = mapped_column(
        ForeignKey("exceptions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    actor_name: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    evidence_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = _ts()
    exception: Mapped[ProcurementException] = relationship(back_populates="events")


class AuditEvent(Base):
    """Append-only for the runtime role (grants + trigger). DB administrators can still alter it."""

    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    occurred_at: Mapped[datetime] = _ts()
    actor_name: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(140))
    dataset: Mapped[str | None] = mapped_column(String(10))
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(36))


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("dataset", "filename", "version", name="uq_knowledge_documents_version"),
        CheckConstraint("scope IN ('global','supplier')", name="ck_knowledge_scope"),
        CheckConstraint("dataset IN ('demo','live')", name="ck_knowledge_dataset"),
        CheckConstraint(
            "(scope = 'supplier') = (supplier_source_id IS NOT NULL)", name="ck_knowledge_supplier"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset: Mapped[str] = mapped_column(String(10), nullable=False)
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(40), nullable=False)
    scope: Mapped[str] = mapped_column(String(10), nullable=False)
    supplier_source_id: Mapped[str | None] = mapped_column(String(140))
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _ts()
    chunks: Mapped[list[KnowledgeChunk]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="KnowledgeChunk.chunk_index",
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_knowledge_chunks_index"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str] = mapped_column(String(300), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = _ts()
    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")

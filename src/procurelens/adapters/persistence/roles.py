"""Least-privilege runtime database role, managed by the owner during bootstrap.

The runtime role cannot UPDATE/DELETE audit_events or exception_events, cannot TRUNCATE,
and cannot run DDL. A database owner/superuser still can — that is documented, not hidden.
"""

from __future__ import annotations

from psycopg import sql
from sqlalchemy import Engine

GRANTS: dict[str, str] = {
    "users": "SELECT, UPDATE",
    "auth_sessions": "SELECT, INSERT, UPDATE",
    "login_attempts": "SELECT, INSERT",
    "app_settings": "SELECT, INSERT, UPDATE",
    "sync_runs": "SELECT, INSERT, UPDATE",
    "suppliers": "SELECT, INSERT, UPDATE",
    "source_documents": "SELECT, INSERT, UPDATE",
    "source_lines": "SELECT, INSERT, UPDATE, DELETE",
    "exceptions": "SELECT, INSERT, UPDATE",
    "exception_events": "SELECT, INSERT",
    "audit_events": "SELECT, INSERT",
    "knowledge_documents": "SELECT, INSERT, UPDATE",
    "knowledge_chunks": "SELECT, INSERT, UPDATE",
    "alembic_version": "SELECT",
}


def ensure_runtime_role(engine: Engine, role: str, password: str) -> None:
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
        verb = "ALTER" if cur.fetchone() else "CREATE"
        cur.execute(
            sql.SQL("{} ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
                sql.SQL(verb), sql.Identifier(role), sql.Literal(password)
            )
        )
        raw.commit()
    finally:
        raw.close()


def apply_grants(engine: Engine, role: str, database: str) -> None:
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        ident = sql.Identifier(role)
        cur.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), ident)
        )
        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(ident))
        cur.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(ident))
        for table, privileges in GRANTS.items():
            cur.execute(
                sql.SQL("GRANT {} ON TABLE {} TO {}").format(
                    sql.SQL(privileges), sql.Identifier(table), ident
                )
            )
        cur.execute(
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(ident)
        )
        raw.commit()
    finally:
        raw.close()

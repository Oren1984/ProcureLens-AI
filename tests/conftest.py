"""Shared fixtures. Database tests use a real PostgreSQL + pgvector instance (never SQLite):
set TEST_DATABASE_URL to an owner-role URL of a disposable database (see scripts/test.sh)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from procurelens.adapters.embeddings import HashingEmbedder
from procurelens.config import Settings
from procurelens.security.permissions import Role

ROOT = Path(__file__).resolve().parents[1]
TEST_DB = os.environ.get("TEST_DATABASE_URL")


def make_settings(url: str = "postgresql+psycopg://unused@localhost/unused", **kw) -> Settings:
    base = dict(
        database_url=url,
        public_url="http://localhost:8089",
        private_dir=Path(tempfile.gettempdir()) / "procurelens-test-private",
        knowledge_dir=ROOT / "knowledge",
        base_currency="EUR",
        session_idle_minutes=30,
        session_absolute_hours=8,
        login_max_failures=5,
        login_window_minutes=15,
        max_upload_bytes=256 * 1024,
        erpnext_base_url=None,
        erpnext_api_key=None,
        erpnext_api_secret=None,
    )
    base.update(kw)
    return Settings(**base)


@pytest.fixture(scope="session")
def migrated_db() -> str:
    if not TEST_DB:
        pytest.skip("TEST_DATABASE_URL not set (database integration tests need PostgreSQL)")
    from alembic import command
    from alembic.config import Config

    eng = create_engine(TEST_DB)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    eng.dispose()
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["url"] = TEST_DB
    command.upgrade(cfg, "head")
    return TEST_DB


@pytest.fixture
def container(migrated_db):
    from procurelens.services.container import build_container

    eng = create_engine(migrated_db)
    with eng.begin() as c:
        tables = c.execute(
            text(
                "SELECT string_agg(quote_ident(tablename), ',') FROM pg_tables "
                "WHERE schemaname='public' AND tablename <> 'alembic_version'"
            )
        ).scalar()
        c.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    eng.dispose()
    c = build_container(make_settings(migrated_db), embedder_factory=HashingEmbedder)
    yield c
    c.db.engine.dispose()


@pytest.fixture
def admin(container):
    container.auth.create_user("admin", Role.ADMIN, "Admin-Test-Pass-1", actor="test")
    _token, principal = container.auth.login("admin", "Admin-Test-Pass-1")
    return principal


@pytest.fixture
def viewer(container):
    container.auth.create_user("demo", Role.DEMO_VIEWER, "Demo-Test-Pass-1", actor="test")
    _token, principal = container.auth.login("demo", "Demo-Test-Pass-1")
    return principal

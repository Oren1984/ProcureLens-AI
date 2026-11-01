"""Alembic environment. Migrations run as the schema-owner role, never the runtime role."""

from alembic import context
from sqlalchemy import create_engine, pool

from procurelens.adapters.persistence.models import Base
from procurelens.config import read_secret

target_metadata = Base.metadata


def _url() -> str:
    url = context.config.attributes.get("url") or read_secret("MIGRATION_DATABASE_URL")
    if not url:
        raise RuntimeError("MIGRATION_DATABASE_URL (or MIGRATION_DATABASE_URL_FILE) must be set")
    return url


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()

"""Engine and transaction helpers. All SQL goes through SQLAlchemy (parameterized)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


class Database:
    def __init__(self, url: str, *, pool_size: int = 5) -> None:
        self.engine: Engine = create_engine(
            url, pool_size=pool_size, max_overflow=5, pool_pre_ping=True, future=True
        )
        self._factory = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """One unit of work: commit on success, roll back on any error."""
        session = self._factory()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def read(self) -> Iterator[Session]:
        session = self._factory()
        try:
            yield session
        finally:
            # close() ends the transaction without expiring loaded objects (unlike rollback),
            # so returned ORM objects stay readable after the session is gone.
            session.close()

"""Runtime configuration read from environment variables.

Secrets may be supplied as ``NAME`` or as ``NAME_FILE`` (path to a file holding the
value, e.g. a Docker secret). Secret values are never logged or included in ``repr``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def read_secret(name: str) -> str | None:
    file_path = os.environ.get(f"{name}_FILE")
    if file_path:
        p = Path(file_path)
        if p.is_file():
            return p.read_text(encoding="utf-8").strip() or None
        return None
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    public_url: str
    private_dir: Path
    knowledge_dir: Path
    base_currency: str
    session_idle_minutes: int
    session_absolute_hours: int
    login_max_failures: int
    login_window_minutes: int
    max_upload_bytes: int
    erpnext_base_url: str | None
    erpnext_api_key: str | None = field(repr=False)
    erpnext_api_secret: str | None = field(repr=False)
    erpnext_timeout_seconds: float = 15.0
    erpnext_max_retries: int = 3
    erpnext_page_size: int = 100
    erpnext_max_records: int = 2000
    erpnext_sync_from: str | None = None
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: str | None = None
    ollama_base_url: str | None = None
    ollama_model: str = "qwen2.5:3b"
    ollama_timeout_seconds: float = 150.0

    @property
    def erpnext_configured(self) -> bool:
        return bool(self.erpnext_base_url and self.erpnext_api_key and self.erpnext_api_secret)


def load_settings() -> Settings:
    db_url = read_secret("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL (or DATABASE_URL_FILE) must be set")
    return Settings(
        database_url=db_url,
        public_url=_env("PROCURELENS_PUBLIC_URL", "http://localhost:8089"),
        private_dir=Path(_env("PROCURELENS_PRIVATE_DIR", "/run/procurelens-private")),
        knowledge_dir=Path(_env("PROCURELENS_KNOWLEDGE_DIR", "/app/knowledge")),
        base_currency=_env("PROCURELENS_BASE_CURRENCY", "EUR").upper(),
        session_idle_minutes=int(_env("SESSION_IDLE_MINUTES", "30")),
        session_absolute_hours=int(_env("SESSION_ABSOLUTE_HOURS", "8")),
        login_max_failures=int(_env("LOGIN_MAX_FAILURES", "5")),
        login_window_minutes=int(_env("LOGIN_WINDOW_MINUTES", "15")),
        max_upload_bytes=int(_env("KNOWLEDGE_MAX_UPLOAD_BYTES", str(256 * 1024))),
        erpnext_base_url=os.environ.get("ERPNEXT_BASE_URL", "").strip().rstrip("/") or None,
        erpnext_api_key=read_secret("ERPNEXT_API_KEY"),
        erpnext_api_secret=read_secret("ERPNEXT_API_SECRET"),
        erpnext_timeout_seconds=float(_env("ERPNEXT_TIMEOUT_SECONDS", "15")),
        erpnext_max_retries=int(_env("ERPNEXT_MAX_RETRIES", "3")),
        erpnext_page_size=int(_env("ERPNEXT_PAGE_SIZE", "100")),
        erpnext_max_records=int(_env("ERPNEXT_MAX_RECORDS", "2000")),
        erpnext_sync_from=os.environ.get("ERPNEXT_SYNC_FROM", "").strip() or None,
        embedding_model=_env("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
        embedding_cache_dir=os.environ.get("EMBEDDING_CACHE_DIR") or None,
        ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "").strip().rstrip("/") or None,
        ollama_model=_env("OLLAMA_MODEL", "qwen2.5:3b"),
        ollama_timeout_seconds=float(_env("OLLAMA_TIMEOUT_SECONDS", "150")),
    )

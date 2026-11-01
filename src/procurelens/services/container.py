"""Composition root: builds services from settings. Holds no per-user state."""

from __future__ import annotations

from dataclasses import dataclass

from procurelens.adapters.embeddings import FastEmbedProvider
from procurelens.adapters.erpnext.base import ProcurementSource
from procurelens.adapters.erpnext.demo import DemoSource
from procurelens.adapters.erpnext.rest import ERPNextSource
from procurelens.adapters.generation import OllamaGenerator
from procurelens.adapters.persistence.db import Database
from procurelens.config import Settings

from .app_config import ConfigService
from .auth import AuthService
from .exceptions import ExceptionService
from .explanation import ExplanationService
from .knowledge import KnowledgeService
from .sync import SyncService


@dataclass
class Container:
    settings: Settings
    db: Database
    auth: AuthService
    config: ConfigService
    exceptions: ExceptionService
    sync: SyncService
    knowledge: KnowledgeService
    explanation: ExplanationService
    generator: OllamaGenerator | None

    def source(self, dataset: str) -> ProcurementSource:
        if dataset == "demo":
            return DemoSource()
        s = self.settings
        if not s.erpnext_configured:
            raise RuntimeError("ERPNext is not configured (ERPNEXT_BASE_URL / API key / secret).")
        return ERPNextSource(
            s.erpnext_base_url,
            s.erpnext_api_key,
            s.erpnext_api_secret,
            timeout=s.erpnext_timeout_seconds,
            max_retries=s.erpnext_max_retries,
            page_size=s.erpnext_page_size,
            max_records=s.erpnext_max_records,
            sync_from=s.erpnext_sync_from,
        )


def build_container(
    settings: Settings, *, database_url: str | None = None, embedder_factory=None
) -> Container:
    db = Database(database_url or settings.database_url)
    factory = embedder_factory or (
        lambda: FastEmbedProvider(settings.embedding_model, settings.embedding_cache_dir)
    )
    generator = (
        OllamaGenerator(
            settings.ollama_base_url, settings.ollama_model, settings.ollama_timeout_seconds
        )
        if settings.ollama_base_url
        else None
    )
    return Container(
        settings=settings,
        db=db,
        auth=AuthService(
            db,
            idle_minutes=settings.session_idle_minutes,
            absolute_hours=settings.session_absolute_hours,
            max_failures=settings.login_max_failures,
            window_minutes=settings.login_window_minutes,
        ),
        config=ConfigService(db),
        exceptions=ExceptionService(db, settings.base_currency),
        sync=SyncService(db, settings.base_currency),
        knowledge=KnowledgeService(db, factory, settings.max_upload_bytes),
        explanation=ExplanationService(settings.base_currency, generator),
        generator=generator,
    )

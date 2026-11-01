"""Knowledge import (UTF-8 Markdown / plain text), chunking, embedding and scoped retrieval.

Retrieval is supporting evidence only. Similarity never proves a contractual entitlement and
never influences amounts, matching, tolerances or exception status.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from procurelens.adapters.embeddings import EmbeddingProvider
from procurelens.adapters.persistence.db import Database
from procurelens.adapters.persistence.models import (
    DATASETS,
    EMBEDDING_DIM,
    KnowledgeChunk,
    KnowledgeDocument,
)
from procurelens.security.permissions import Permission, Principal

from .app_config import load_business_settings
from .audit import record_audit

log = logging.getLogger(__name__)
ALLOWED_SUFFIXES = {".md", ".txt"}
FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
DOC_TYPES = {"agreement", "policy", "procedure", "other"}
CHUNK_MAX_CHARS = 900
SUPPLIER_SLOTS = 2
MAX_CHUNKS = 400


class KnowledgeError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedDocument:
    filename: str
    title: str
    version: str
    doc_type: str
    supplier: str | None
    text: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Chunk:
    index: int
    section: str
    text: str


@dataclass(frozen=True)
class Evidence:
    chunk_id: int
    filename: str
    title: str
    version: str
    doc_type: str
    scope: str
    supplier: str | None
    section: str
    chunk_index: int
    text: str
    similarity: float

    @property
    def ref(self) -> str:
        return f"{self.filename}@v{self.version}#chunk-{self.chunk_index}"


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    evidence: list[Evidence]
    sufficient: bool
    threshold: float
    best_similarity: float | None
    note: str


def parse_document(filename: str, data: bytes, max_bytes: int) -> ParsedDocument:
    name = Path(filename or "").name  # strip any client-supplied directories
    if not FILENAME_RE.match(name) or Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
        raise KnowledgeError("Only .md or .txt files with a simple file name are accepted.")
    if len(data) > max_bytes:
        raise KnowledgeError(f"File is larger than the {max_bytes // 1024} KB limit.")
    if not data.strip():
        raise KnowledgeError("File is empty.")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise KnowledgeError("File must be UTF-8 encoded text.") from exc
    if "\x00" in text:
        raise KnowledgeError("File contains binary data.")
    text = text.replace("\r\n", "\n").lstrip("﻿")
    meta: dict[str, str] = {}
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end == -1:
            raise KnowledgeError("Front matter is not closed with '---'.")
        for line in text[4:end].splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.strip().strip('"')
        text = text[end + 4 :].lstrip("\n")
    doc_type = meta.get("doc_type", "other").lower()
    if doc_type not in DOC_TYPES:
        raise KnowledgeError(f"doc_type must be one of {sorted(DOC_TYPES)}.")
    version = meta.get("version", "1")[:40]
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", version):
        raise KnowledgeError("version may contain only letters, digits, '.', '_' and '-'.")
    supplier = meta.get("supplier") or None
    if supplier and len(supplier) > 140:
        raise KnowledgeError("supplier identifier is too long.")
    title = (meta.get("title") or Path(name).stem)[:300]
    return ParsedDocument(
        name, title, version, doc_type, supplier, text, hashlib.sha256(data).hexdigest(), len(data)
    )


def chunk_text(text: str, title: str) -> list[Chunk]:
    """Split on Markdown headings, then on paragraphs, keeping each chunk ≤ CHUNK_MAX_CHARS."""
    chunks: list[Chunk] = []
    heading_stack: list[str] = []
    buf: list[str] = []

    def section() -> str:
        return " › ".join(heading_stack) or title

    def flush() -> None:
        para_text = "\n".join(buf).strip()
        buf.clear()
        if not para_text:
            return
        current = ""
        for para in re.split(r"\n\s*\n", para_text):
            para = para.strip()
            while len(para) > CHUNK_MAX_CHARS:  # hard split very long paragraphs
                cut = para.rfind(" ", 0, CHUNK_MAX_CHARS)
                cut = cut if cut > 200 else CHUNK_MAX_CHARS
                if current:
                    chunks.append(Chunk(len(chunks), section(), current))
                    current = ""
                chunks.append(Chunk(len(chunks), section(), para[:cut].strip()))
                para = para[cut:].strip()
            if current and len(current) + len(para) + 2 > CHUNK_MAX_CHARS:
                chunks.append(Chunk(len(chunks), section(), current))
                current = para
            else:
                current = f"{current}\n\n{para}" if current else para
        if current:
            chunks.append(Chunk(len(chunks), section(), current))

    for line in text.splitlines():
        m = re.match(r"^(#{1,4})\s+(.+?)\s*#*\s*$", line)
        if m:
            flush()
            level = len(m.group(1))
            heading_stack[:] = heading_stack[: level - 1]
            heading_stack.append(m.group(2)[:120])
        else:
            buf.append(line)
    flush()
    if len(chunks) > MAX_CHUNKS:
        raise KnowledgeError(f"Document produces more than {MAX_CHUNKS} chunks.")
    return chunks


class KnowledgeService:
    def __init__(self, db: Database, embedder_factory, max_upload_bytes: int) -> None:
        """``embedder_factory`` returns an EmbeddingProvider; called lazily on first use."""
        self.db = db
        self._factory = embedder_factory
        self._embedder: EmbeddingProvider | None = None
        self.max_upload_bytes = max_upload_bytes

    @property
    def embedder(self) -> EmbeddingProvider:
        if self._embedder is None:
            emb = self._factory()
            if emb.dim != EMBEDDING_DIM:
                raise KnowledgeError(
                    f"Embedding model {emb.model_id} has dimension {emb.dim}; the database "
                    f"column is vector({EMBEDDING_DIM}). A migration is required."
                )
            self._embedder = emb
        return self._embedder

    # -- import -----------------------------------------------------------------------------
    def import_document(
        self,
        principal: Principal,
        dataset: str,
        filename: str,
        data: bytes,
        *,
        actor_override: str | None = None,
    ) -> tuple[str, KnowledgeDocument]:
        principal.require(Permission.IMPORT_KNOWLEDGE)
        return self._import(dataset, filename, data, actor=actor_override or principal.username)

    def _import(self, dataset: str, filename: str, data: bytes, *, actor: str):
        if dataset not in DATASETS:
            raise KnowledgeError("Unknown dataset.")
        doc = parse_document(filename, data, self.max_upload_bytes)
        chunks = chunk_text(doc.text, doc.title)
        if not chunks:
            raise KnowledgeError("No text content found.")
        vectors = self.embedder.embed_documents(
            [f"{doc.title} — {c.section}\n{c.text}" for c in chunks]
        )
        with self.db.transaction() as s:
            same = s.scalar(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.dataset == dataset,
                    KnowledgeDocument.filename == doc.filename,
                    KnowledgeDocument.version == doc.version,
                )
            )
            if same is not None:
                if same.content_sha256 == doc.sha256:
                    return "unchanged", same
                raise KnowledgeError(
                    f"{doc.filename} version {doc.version} already exists with different "
                    "content. Increase 'version' in the front matter to import a new version."
                )
            for old in s.scalars(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.dataset == dataset,
                    KnowledgeDocument.filename == doc.filename,
                    KnowledgeDocument.is_active.is_(True),
                )
            ):
                old.is_active = False
            row = KnowledgeDocument(
                dataset=dataset,
                filename=doc.filename,
                title=doc.title,
                version=doc.version,
                doc_type=doc.doc_type,
                scope="supplier" if doc.supplier else "global",
                supplier_source_id=doc.supplier,
                content_sha256=doc.sha256,
                size_bytes=doc.size,
                raw_text=doc.text,
                uploaded_by=actor,
            )
            for c, v in zip(chunks, vectors, strict=True):
                row.chunks.append(
                    KnowledgeChunk(
                        chunk_index=c.index,
                        section=c.section[:300],
                        text=c.text,
                        embedding=v,
                        embedding_model=self.embedder.model_id,
                    )
                )
            s.add(row)
            s.flush()
            record_audit(
                s,
                actor=actor,
                action="knowledge_imported",
                entity_type="knowledge_document",
                entity_id=row.id,
                dataset=dataset,
                details={
                    "filename": doc.filename,
                    "version": doc.version,
                    "sha256": doc.sha256,
                    "chunks": len(chunks),
                    "embedding_model": self.embedder.model_id,
                },
            )
        log.info(
            "knowledge imported",
            extra={"event": "knowledge_imported", "doc": doc.filename, "chunks": len(chunks)},
        )
        return "imported", row

    def seed_directory(self, directory: Path, dataset: str = "demo") -> dict[str, int]:
        """Bootstrap-only import of the bundled synthetic collection (no principal: CLI)."""
        counts = {"imported": 0, "unchanged": 0}
        for path in sorted(directory.glob("*.md")):
            outcome, _ = self._import(dataset, path.name, path.read_bytes(), actor="system")
            counts[outcome] += 1
        return counts

    # -- reindex ----------------------------------------------------------------------------
    def reindex(self, principal: Principal, reason: str, progress=None) -> int:
        principal.require(Permission.IMPORT_KNOWLEDGE)
        return self._reindex(actor=principal.username, reason=reason, progress=progress)

    def _reindex(self, *, actor: str, reason: str, progress=None) -> int:
        with self.db.transaction() as s:
            docs = list(
                s.scalars(select(KnowledgeDocument).options(selectinload(KnowledgeDocument.chunks)))
            )
            total = 0
            for i, d in enumerate(docs):
                if progress:
                    progress(f"Embedding {d.filename}…", i / max(1, len(docs)))
                texts = [f"{d.title} — {c.section}\n{c.text}" for c in d.chunks]
                for c, v in zip(d.chunks, self.embedder.embed_documents(texts), strict=True):
                    c.embedding, c.embedding_model = v, self.embedder.model_id
                    total += 1
            record_audit(
                s,
                actor=actor,
                action="knowledge_reindexed",
                entity_type="knowledge_chunks",
                reason=reason,
                details={"chunks": total, "embedding_model": self.embedder.model_id},
            )
        return total

    # -- status / browse -----------------------------------------------------------------------
    def documents(self, principal: Principal, dataset: str) -> list[KnowledgeDocument]:
        principal.require(Permission.VIEW)
        principal.require_dataset(dataset)
        with self.db.read() as s:
            return list(
                s.scalars(
                    select(KnowledgeDocument)
                    .where(KnowledgeDocument.dataset == dataset)
                    .order_by(KnowledgeDocument.filename, KnowledgeDocument.created_at.desc())
                )
            )

    def index_status(self, principal: Principal, model_id: str) -> dict:
        principal.require(Permission.VIEW)
        with self.db.read() as s:
            rows = s.execute(
                select(KnowledgeChunk.embedding_model, func.count()).group_by(
                    KnowledgeChunk.embedding_model
                )
            ).all()
        by_model = {m or "(none)": n for m, n in rows}
        mismatched = sum(n for m, n in by_model.items() if m != model_id)
        return {"by_model": by_model, "mismatched": mismatched, "active_model": model_id}

    # -- retrieval ------------------------------------------------------------------------------
    def search(
        self,
        principal: Principal,
        dataset: str,
        query: str,
        *,
        supplier: str | None = None,
        k: int = 4,
        min_similarity: float | None = None,
    ) -> RetrievalResult:
        principal.require(Permission.VIEW)
        principal.require_dataset(dataset)
        query = (query or "").strip()[:1000]
        if not query:
            raise KnowledgeError("Query is empty.")
        vec = self.embedder.embed_query(query)
        with self.db.read() as s:
            threshold = (
                min_similarity
                if min_similarity is not None
                else load_business_settings(s).retrieval_min_similarity
            )
            distance = KnowledgeChunk.embedding.cosine_distance(vec)
            k = max(1, min(k, 10))

            def ranked(scope_filter, limit: int):
                # Scope filters are applied BEFORE ranking (exact search over the filtered set).
                q = (
                    select(KnowledgeChunk, KnowledgeDocument, distance.label("d"))
                    .join(KnowledgeDocument)
                    .where(
                        KnowledgeDocument.dataset == dataset,
                        KnowledgeDocument.is_active.is_(True),
                        KnowledgeChunk.embedding_model == self.embedder.model_id,
                        KnowledgeChunk.embedding.is_not(None),
                        scope_filter,
                    )
                    .order_by(distance, KnowledgeChunk.id)
                    .limit(limit)
                )
                return s.execute(q).all()

            # The supplier's own agreement is ranked separately and gets up to
            # SUPPLIER_SLOTS places, so generic policies cannot crowd out supplier clauses.
            supplier_rows = (
                ranked(KnowledgeDocument.supplier_source_id == supplier, min(SUPPLIER_SLOTS, k))
                if supplier
                else []
            )
            global_rows = ranked(KnowledgeDocument.scope == "global", k)
        evidence = [
            Evidence(
                c.id,
                d.filename,
                d.title,
                d.version,
                d.doc_type,
                d.scope,
                d.supplier_source_id,
                c.section,
                c.chunk_index,
                c.text,
                round(1.0 - float(dist), 4),
            )
            for c, d, dist in supplier_rows + global_rows
        ]
        sup_kept = [e for e in evidence if e.scope == "supplier" and e.similarity >= threshold]
        glob = [e for e in evidence if e.scope == "global"]
        evidence = sorted(
            sup_kept
            + glob[: k - len(sup_kept)]
            + [e for e in evidence if e.scope == "supplier" and e.similarity < threshold],
            key=lambda e: -e.similarity,
        )
        best = evidence[0].similarity if evidence else None
        kept = [e for e in evidence if e.similarity >= threshold]
        if not evidence:
            note = "No indexed knowledge matches this scope."
        elif not kept:
            note = (
                f"Insufficient evidence: best similarity {best:.2f} is below the "
                f"{threshold:.2f} threshold."
            )
        else:
            note = (
                f"{len(kept)} excerpt(s) at or above similarity {threshold:.2f}. Similarity "
                "indicates topical relevance only, not a contractual entitlement."
            )
        return RetrievalResult(query, kept, bool(kept), threshold, best, note)

# Architecture

A modular Python monolith with a Streamlit UI. There is no separate API service, queue or worker. Synchronisation and indexing are bounded, user-triggered operations.

## Layers

```
src/procurelens/
  domain/          pure rules — no Streamlit, SQL, HTTP or LLM imports
    numbers.py       Decimal rounding + tolerance (single source of truth)
    models.py        typed entities (frozen dataclasses), exception types/statuses
    reconciliation.py  three-way matching → Finding objects
    priority.py      transparent 0–100 triage score
    lifecycle.py     Open → In review → Resolved (+ reasoned reopen)
  services/        application layer: authorization, transactions, audit
    auth.py          Argon2id, DB sessions, throttling, expiry, logout
    sync.py          fetch-all → apply in one transaction → re-evaluate
    exceptions.py    upsert findings, history, lifecycle transitions
    knowledge.py     import, chunking, embedding, scoped retrieval
    explanation.py   facts / evidence / next steps; optional validated LLM drafting
    app_config.py    admin-editable business settings (audited)
    container.py     composition root
  adapters/
    erpnext/         ProcurementSource interface: DemoSource, ERPNextSource (REST), mapping
    persistence/     SQLAlchemy models, repositories, runtime-role grants
    embeddings/      FastEmbedProvider (local ONNX), HashingEmbedder (tests only)
    generation/      OllamaGenerator (HTTP, JSON-schema output)
  security/        passwords (argon2-cffi), roles/permissions, Principal
  ui/              Streamlit pages — call services only
  cli.py           operator commands (bootstrap, reset-password, reset-demo, reindex, diagnostics)
```

The UI never makes authorization decisions. Every service method takes a `Principal`, which is rebuilt from a server-validated session on every Streamlit run, and calls `principal.require(...)` / `require_dataset(...)`. Hiding buttons is only a convenience.

## Data model (PostgreSQL 17 + pgvector 0.8.2)

| Area | Tables | Notes |
|---|---|---|
| Identity | `users`, `auth_sessions`, `login_attempts` | password hash only; session token stored as SHA-256 |
| Source data | `suppliers`, `source_documents`, `source_lines`, `sync_runs` | unique `(dataset, doctype, source_id)` and `(document_id, line_id)`; raw JSON snapshot + content hash |
| Investigation | `exceptions`, `exception_events` | unique `(dataset, exception_key)`; optimistic version column; evidence snapshots in events |
| Governance | `audit_events`, `app_settings` | audit is append-only for the runtime role (grants + trigger) |
| Knowledge | `knowledge_documents`, `knowledge_chunks` | `vector(384)`, model id per chunk, unique `(dataset, filename, version)` |

`dataset` is `demo` or `live`. It separates synthetic data from ERPNext data in every query and enforces the demo viewer's restriction.

Schema changes go through Alembic (`migrations/`). The migration enables `vector` and creates the append-only trigger. No approximate vector index is created: the collection is a few dozen chunks, and exact search over the scope-filtered set is fast and deterministic. Add HNSW only after measuring a real need.

## Key flows

**Sync.** `SyncService.run` records a `running` run, then fetches all doctypes through the adapter and writes nothing to the database during the fetch. A single transaction (serialised per dataset with an advisory lock) then upserts suppliers and documents (unchanged content hash → no-op; changed → lines replaced), re-runs reconciliation over the whole dataset, updates exceptions and finalises the run with its audit row. A fetch error marks the run `failed` and leaves the previous data intact. Malformed records are skipped as warnings.

**Exception identity.** Keys are built from stable source IDs, e.g. `qty:<PO>:<PO line>` or `price:<PO>:<line>:<invoice>:<invoice line>`. A fingerprint of the material facts detects change. If the facts change, the exception is updated, flagged `source_changed`, gets a `facts_changed` event holding before/after snapshots, and is reopened if it was resolved. If a finding disappears, the exception is flagged `condition_active = false` and is not auto-resolved.

**Retrieval.** The query text is derived from the exception type and item. Filters (dataset, active version, current embedding model, scope) are applied before ranking. The supplier's own agreement is ranked separately and gets up to 2 of the 4 slots, so generic policies can't crowd it out. Results below the similarity threshold are dropped, and the UI then reports *insufficient evidence*.

**Explanation.** Facts are the deterministic calculation lines; evidence is the exact stored chunk text with its reference. Next steps and the draft come from templates. With Ollama enabled and an admin request, the model drafts summary, next steps and draft as JSON matching a schema. The output is validated (fields, lengths, cited references ⊆ retrieved references, no URLs or payment-approval phrases), and any failure falls back to the template.

## Version choices (locked in `requirements.lock` with hashes)

| Component | Version | Reason |
|---|---|---|
| Python | 3.12 (`python:3.12-slim-trixie`) | broad wheel availability (onnxruntime) |
| Streamlit | 1.66.1 | `st.navigation`, badges, dataframe selection |
| SQLAlchemy / Alembic | 2.0.55 / 1.20.1 | typed ORM, migrations |
| psycopg | 3.3.6 | PostgreSQL driver |
| PostgreSQL / pgvector | 17 / 0.8.2 (`pgvector/pgvector:0.8.2-pg17`) | stable data-dir layout; pgvector from official image |
| argon2-cffi | 25.1.0 | Argon2id (RFC 9106 low-memory profile) |
| httpx | 0.28.1 | timeouts, mock transport for contract tests |
| fastembed / onnxruntime | 0.7.4 / 1.31.1 | local CPU embeddings, no torch |
| Embedding model | `BAAI/bge-small-en-v1.5` (fastembed source `qdrant/bge-small-en-v1.5-onnx-q`), 384-d | small, good English retrieval |
| Pillow | 13.0.0 (override, see `overrides.txt`) | fixes advisories in fastembed's capped 11.x |
| Ollama (optional) | `ollama/ollama:0.40.13`, model `qwen2.5:3b` | runs on CPU |
| Terraform / AWS provider | ≥ 1.9 (validated with 1.13.4) / ~> 6.0 (locked 6.71.0) | |

The ERPNext request format follows the official Frappe REST documentation (`docs.frappe.io/framework/user/en/api/rest`): token header `token key:secret`, `fields`/`filters`/`limit_start`/`limit_page_length`/`order_by`, `GET /api/resource/:doctype/:name`, `frappe.auth.get_logged_user`.

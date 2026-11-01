# Testing and limitations

## How to run

```bash
uv venv .venv && uv pip install -e ".[dev]"     # dev environment
./scripts/test.sh -q                            # disposable pgvector container on 127.0.0.1:15432 + pytest
PROCURELENS_CREDENTIALS_FILE=<private>/procurelens-credentials.txt \
  .venv/Scripts/python scripts/e2e_verify.py --reset-demo --screenshots   # browser E2E vs running stack
```

Database tests use a real PostgreSQL 17 + pgvector instance (never SQLite). `SKIP_MODEL_TESTS=1` skips the retrieval evaluation, which needs the embedding model.

## What was actually run (2026-11-01, Windows 11 + Docker Desktop 29.9.2)

| Check | Result |
|---|---|
| `ruff check` + `ruff format --check` (src, tests, scripts, migrations) | pass |
| `pytest` — 121 tests (unit, ERPNext contract, PostgreSQL/pgvector integration, retrieval eval) | **121 passed** |
| Alembic upgrade → downgrade → upgrade | pass |
| Browser E2E (Playwright, headless Edge) vs Compose stack on :8089 | **12/12** |
| Server-side denial of demo-account mutations inside the running stack | sync, transition, import, reindex, settings, live data: all denied |
| Runtime DB role vs audit trail inside the running stack | UPDATE/DELETE `audit_events`, DELETE `exception_events`, TRUNCATE: permission denied |
| Restart persistence (`down` + `up`, volumes kept) | identical row counts; credentials file unchanged; no reseed |
| Backup → restore into isolated DB | identical row counts; pgvector search and append-only trigger work in the restored DB |
| Optional Ollama (`ai` profile container, `qwen2.5:3b`, CPU) | validated drafts, 77–109 s each; unreachable-endpoint fallback verified |
| `pip-audit` (both locks) | no known vulnerabilities |
| Trivy image | 0 fixable HIGH/CRITICAL (46 unfixed Debian base findings, see SECURITY_AND_GOVERNANCE.md) |
| Terraform `fmt -check` + `validate` (AWS) | pass (run in `hashicorp/terraform:1.13.4`, see below) |

### Test coverage by requirement

- **Calculations:** exact Decimal quantities and prices, fractional quantities, half-up rounding, tolerance boundary (exactly 2 % passes), weighted invoice rate, price direction.
- **Source handling:** partial deliveries, multiple receipts and invoices per line, rejected goods, cancelled and draft documents, returns, foreign currency, UOM mismatch, item mismatch, orphan and unlinked lines, cancelled PO, malformed ERP records.
- **Sync:** idempotent repeat sync, unique constraint against duplicates, failed fetch leaves data intact, warnings for skipped records, audit per run.
- **Lifecycle:** reasons required, invalid transitions, event and audit in the same transaction, optimistic-concurrency rejection, material change reopens a resolved exception with history preserved, cleared condition not auto-resolved.
- **Security:** every mutation denied for the demo viewer; no live-data access by list, ID, overview, history, documents or search; demo/live isolation; generic login errors; throttling and window expiry; idle and absolute expiry; logout; isolated sessions; reset revokes sessions; runtime-role privileges; log redaction.
- **ERPNext adapter:** token header, GET-only, filters, pagination, record cap, 401/403 fail-fast, 429 with Retry-After, bounded 5xx retries, timeouts, no redirects, non-JSON responses, secret absent from `repr` and errors.
- **Knowledge:** supplier scope filtering before ranking, supplier slots, exact excerpt and reference, insufficient evidence, versioning, mixed-model exclusion and reindex, dimension mismatch, idempotent seed, upload validation, chunking.
- **AI:** unconfigured, unreachable, timeout, invalid JSON, hallucinated reference, disallowed content, viewer blocked; a valid output keeps facts deterministic; untrusted text is delimited.

### Retrieval evaluation

`tests/eval/retrieval_cases.json` holds 10 questions with known relevant sources and 5 off-topic questions. Results with `BAAI/bge-small-en-v1.5` and threshold 0.60:

- recall@3 = **1.0** (10/10); top-1 similarity for relevant questions 0.63–0.81;
- all 5 off-topic questions are below the threshold (best 0.45–0.55), so they are reported as insufficient evidence.

The margin is narrow at both ends (lowest relevant 0.632, highest off-topic 0.550). The set is small and synthetic, so it is a sanity check, not a benchmark.

## Live integrations: what is and is not verified

| Integration | Status |
|---|---|
| PostgreSQL 17 + pgvector 0.8.2 | **verified live** (Compose and tests) |
| Local embeddings (fastembed, offline in container) | **verified live** |
| Ollama (optional) | **verified live** with the `ai` profile container. The model was mounted from an existing local download because pulling inside the container was blocked by this host's TLS-inspecting antivirus, so `ollama pull` inside the container was not exercised. |
| ERPNext REST | **not verified against a live ERPNext.** Covered by HTTP contract tests with Frappe-shaped fixtures and the official REST documentation. Run the smoke test in OPERATIONS.md before relying on it. |
| AWS Terraform | **validated code only.** `terraform apply` was not run and no cloud resources were created. |

## Environment notes from this build

- The host runs Norton Web/Mail Shield, which re-signs TLS. Python downloads on the host used an exported copy of the Windows root store. The image was built with that bundle as a BuildKit secret (not stored in the image).
- On the host, Norton also broke Terraform's local plugin gRPC channel (`x509: certificate signed by unknown authority`), so `fmt`/`validate` ran in the official `hashicorp/terraform:1.13.4` container. CI runs them natively.
- The CI workflow (`.github/workflows/ci.yml`) is written but has not been executed on GitHub, because the repository was not pushed.

## Known limitations (V1)

- Single base currency; no UOM conversion, returns or debit-note matching, or landed costs (these are flagged for manual review).
- Matching depends on ERPNext line references; invoices created without `po_detail` are flagged, not matched.
- Each sync does a full, capped fetch (no incremental `modified` cursor). Very large ERPNext sites need `ERPNEXT_SYNC_FROM` or a future incremental mode. Documents deleted in ERPNext are not removed locally.
- Demo data is evaluated at a fixed date (2026-10-15). Live data uses today's date in UTC.
- Sessions do not survive a browser refresh (see SECURITY_AND_GOVERNANCE.md). There is one admin account and no user management UI.
- Retrieval quality is measured only on a small synthetic collection. PDF/OCR import is out of scope.
- Local LLM drafts are slow on CPU and can contain inaccurate statements; they are drafts only.
- Synchronisation and AI drafting run inside the Streamlit request. Long runs block that user's page (with progress shown) but not other users.
- The audit trail is append-only for the application role, not tamper-proof against database administrators.

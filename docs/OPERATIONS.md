# Operations

All commands run from the repository root. On Windows, use Git Bash for the `scripts/*.sh` files.

## First run

```bash
./scripts/init-env.sh [PRIVATE_DIR]   # default ~/.procurelens/private; refuses paths inside the repo
./scripts/check-port.sh               # 127.0.0.1:8089 must be free; never stops other processes
docker compose up -d --build
docker compose ps                     # app + db "healthy"; init "exited (0)"
```

`init` is a one-shot container (schema-owner credentials) that:

1. waits for PostgreSQL and applies Alembic migrations, which enable pgvector;
2. creates or updates the least-privilege runtime role and re-applies its grants;
3. on the first run only, creates `admin` with a generated password and `demo` with the documented public demo password (`PROCURELENS_DEMO_PASSWORD`, default `procurelens-demo`), and writes `procurelens-credentials.txt` (mode 0600) to `PROCURELENS_PRIVATE_DIR`;
4. on the first run only, seeds the synthetic demo dataset and indexes `knowledge/`.

The app container runs as UID 10001 with a read-only root filesystem, no capabilities and no owner database credentials. It publishes **only** `127.0.0.1:8089 → 8501`. PostgreSQL publishes no port.

On Windows the `chmod` in the scripts has no effect. To restrict the private folder to your user:
`icacls "%USERPROFILE%\.procurelens\private" /inheritance:r /grant:r "%USERNAME%:(OI)(CI)F"`.
On Linux, the directory must be writable by UID 10001: `sudo install -d -m 0700 -o 10001 -g 10001 <dir>`.

**Downloads.** The image build fetches Python wheels and the embedding model (~67 MB) once. After that, the default stack needs no internet access: `HF_HUB_OFFLINE=1` is set at runtime. The Ollama model is a separate download of ~1.9 GB. This is not a fully offline installation.

**TLS-inspecting networks.** If downloads fail with certificate errors during the build, build with a CA bundle passed as a build secret: `BUILD_CA_FILE=/path/ca.pem ./scripts/start.sh`.

## Daily operation

| Task | Command |
|---|---|
| Start / stop | `docker compose up -d` / `docker compose stop` (data kept) |
| Logs (JSON, no secrets) | `docker compose logs -f app` |
| Diagnostics | `docker compose run --rm init python -m procurelens.cli diagnostics` |
| Reset a password | `docker compose run --rm init python -m procurelens.cli reset-password --user admin` (revokes that user's sessions; new password goes to the private file) |
| Reset **demo** data | `docker compose run --rm init python -m procurelens.cli reset-demo --confirm demo` (deletes and re-seeds only `dataset='demo'` rows; the audit log is kept) |
| Re-embed knowledge | UI → Integrations & knowledge → Knowledge → Reindex, or `… procurelens.cli reindex --reason "…"` |
| Upgrade code | `git pull && docker compose up -d --build` (`init` re-applies migrations) |
| Remove everything | `docker compose down -v` — **deletes the database volume** |

## Backup and restore

```bash
./scripts/backup.sh                                   # backups/procurelens-<UTC>.dump (pg_dump -Fc)
./scripts/restore.sh backups/<file>.dump              # restores into ISOLATED db procurelens_restore_check and prints row counts
```

Compare the printed counts with `scripts/verify-counts.sql` run against the live database. Restoring over the live database is deliberately manual: `docker compose stop app && ./scripts/restore.sh <file> --into-main`. Backups contain business data and password hashes, so store them privately (`backups/` is gitignored). There is no automatic schedule locally; on AWS, see the EBS snapshot policy.

## Optional local AI (Ollama)

1. In `.env`: `OLLAMA_BASE_URL=http://ollama:11434` (optionally `OLLAMA_MODEL`, `OLLAMA_TIMEOUT_SECONDS`, default 150).
2. `docker compose --profile ai up -d`
3. One-time model download: `docker compose exec ollama ollama pull qwen2.5:3b` (~1.9 GB; cold start). After that, the model is cached in the `ollama` volume.
4. Diagnostics should report "Ollama ready". In an exception, choose **Draft with local AI** (administrator only).

Measured on this CPU-only host: about 80–110 s per explanation with `qwen2.5:3b`. Use ≥ 8 GB RAM for the AI profile. If Ollama is unreachable, times out or returns invalid output, the UI shows the reason and the deterministic draft.

## ERPNext connection

**Setup (ERPNext administrator):** create a dedicated integration user and a role with **read-only** permission on Supplier, Purchase Order, Purchase Receipt and Purchase Invoice. Generate an API key and secret for that user. Set `ERPNEXT_BASE_URL` (https), `ERPNEXT_API_KEY` and `ERPNEXT_API_SECRET` in `.env`, then `docker compose up -d`. Optional settings are `ERPNEXT_SYNC_FROM` (date floor), `ERPNEXT_MAX_RECORDS` (default 2000 per doctype), `ERPNEXT_PAGE_SIZE` (100), `ERPNEXT_TIMEOUT_SECONDS` (15) and `ERPNEXT_MAX_RETRIES` (3).

### ERPNext smoke test

Use a test ERPNext site or a copy; ProcureLens never writes, but test permissions first.

1. Create test data in ERPNext: a supplier, a Purchase Order of 100 units, a Purchase Receipt that accepts 80 against that PO line, and a Purchase Invoice of 100 against the PO line. Submit all three. Set the company currency to the `PROCURELENS_BASE_CURRENCY` value.
2. In ProcureLens (admin), select dataset **Live ERPNext**, go to **Integrations & knowledge → Synchronisation → Check connection**. Expect "Connected to ERPNext" with the integration user's ID.
3. Select **Sync from ERPNext**. Expect status `succeeded` and document counts matching what you created.
4. Under **Exceptions**, expect one *Qty exceeds receipt* exception with unmatched qty 20. If the PO line's `schedule_date` plus the grace period has passed, also expect an *Overdue delivery* exception for the 20 undelivered units.
5. Sync again and expect `document_unchanged` counts and no new exceptions (idempotency).
6. Cancel the invoice in ERPNext, sync, and expect the exception to be marked *no longer detected*.
7. Negative checks: use a wrong secret (expect a clear `auth` failure and unchanged data) and remove a read permission (expect `auth`).

Also confirm in step 3 that each synced document includes its item rows (`items`). The adapter relies on `GET /api/resource/<doctype>/<name>` returning child tables, as in the recorded fixtures. The Frappe REST documentation does not state this explicitly.

## Data retention

- Source documents, exceptions, history and audit events are kept until you delete the volume. There is no automatic purge in V1.
- `login_attempts` and revoked `auth_sessions` accumulate slowly. They can be pruned by a DBA (owner role) if needed.
- Knowledge documents keep previous versions (inactive) for traceability of past evidence references.

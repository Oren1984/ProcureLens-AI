## Troubleshooting and recovery

| Symptom | What to check |
|---|---|
| *Sign-in failed* | The demo login is `demo` / `procurelens-demo` unless your administrator changed it. The admin password is in the private credentials file written at first start. After 5 failures in 15 minutes sign-in is temporarily blocked for that account. Reset with `docker compose run --rm init python -m procurelens.cli reset-password --user admin` (or `--user demo`). |
| Signed out after a browser refresh | Expected: the session lives in the browser tab's server-side state. Sign in again. Idle timeout 30 min, absolute 8 h. |
| Stale-data warning | *Integrations & knowledge → Synchronisation* shows the last success, failures and the error. Fix the cause and sync again; previous data stays intact. |
| ERPNext *auth* error | API key or secret wrong, or the integration user lacks read permission. |
| ERPNext *rate_limited* or *timeout* | Retry later, lower `ERPNEXT_MAX_RECORDS` or set `ERPNEXT_SYNC_FROM`. |
| "Insufficient evidence" | No document is relevant enough. Import the supplier agreement, or lower the threshold in Settings (audited). |
| AI shows the template | Ollama not configured, not running, model not pulled, timed out or produced invalid output — the message says which. |
| Chunks needing reindex | Run *Reindex all embeddings*. |

**Diagnostics:** *Integrations & knowledge → Diagnostics*, or `docker compose run --rm init python -m procurelens.cli diagnostics`. Logs: `docker compose logs app` (JSON lines, no secrets).

**Backup and restore:** `./scripts/backup.sh`, then `./scripts/restore.sh backups/<file>.dump` restores into an isolated check database. See `docs/OPERATIONS.md`.

**Reset demo data only:** `docker compose run --rm init python -m procurelens.cli reset-demo --confirm demo` (live data and the audit log are untouched).

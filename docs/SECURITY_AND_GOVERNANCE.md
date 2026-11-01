# Security and governance

The controls below are proportionate to a local, single-tenant POC. No regulatory certification or compliance is claimed.

## Authentication and sessions

- **Passwords:** Argon2id via `argon2-cffi` (RFC 9106 low-memory profile). Only the hash is stored. Hashes are upgraded automatically on login if the parameters change.
- **Bootstrap:** exactly one administrator (`admin`) and one read-only demo account (`demo`) are created once. Passwords come from the OS CSPRNG (24 characters). They are written only to the private credentials file (outside the repo, mode 0600; owner-only ACL on Windows). They are never printed or logged. There are no fixed or default credentials, and no registration. `reset-password` issues a new password and revokes that user's sessions.
- **Sessions:** a 256-bit random token from `secrets.token_urlsafe(32)`. The database stores only its SHA-256 digest, and the session is validated against the database on **every** Streamlit run. Idle expiry is 30 min and absolute expiry 8 h (both configurable). Logout revokes the server-side session.
- **Where the token lives:** in Streamlit's server-side `session_state` for one browser tab's websocket session. It is never put in the URL, a cookie or a cache. Consequence: **a browser refresh or a new tab starts a new session and asks you to sign in again.** This is intentional and verified.
- **Throttling:** 5 failed attempts per username in 15 minutes block sign-in for that username, including with the correct password, until the window passes or a password reset. Messages are generic. A hash verification also runs for unknown usernames to reduce timing differences. Per-username throttling can be used to lock out a known username; acceptable for a local POC.
- Authorization is enforced in services. Each mutation calls `principal.require(...)`, and dataset access is checked on every query, including direct ID lookups. Tests call every mutation as the demo viewer and expect a denial.

## Application and data security

- SQL only through SQLAlchemy and psycopg parameters. Role DDL uses `psycopg.sql.Identifier/Literal`.
- Untrusted text (ERP fields, knowledge excerpts, model output) is escaped before `st.markdown`, so links, images, HTML and formatting are inert. `unsafe_allow_html` is never used. Raw JSON is shown with `st.json`.
- Uploads: `.md`/`.txt` only, a simple file name (client paths stripped), ≤ 256 KB (plus Streamlit's 1 MB cap), strict UTF-8, no NUL bytes, ≤ 400 chunks. The Help Center reads only fixed files from its own directory.
- Outbound requests: only to the configured ERPNext and Ollama base URLs, with timeouts, bounded retries, no redirects and a record cap.
- Streamlit XSRF protection and CORS restrictions stay enabled. Usage stats are off and error details are limited to the error type.
- Secrets: `.env` (gitignored, generated locally) or `*_FILE` variables. Nothing secret is baked into the image. The optional build CA is a BuildKit secret. The app container has no owner database credentials. Logs are JSON with key- and value-based redaction (passwords, tokens, `Authorization`, DSN passwords).
- Containers: non-root (UID 10001), read-only root filesystem, `cap_drop: ALL`, `no-new-privileges`, no Docker socket, not privileged. The UI is published on loopback only, and the database is not published.

## Database privileges and audit

- Migrations and bootstrap run as the **owner** role. The UI runs as **`procurelens_app`**, which has table-level DML only. It has no DDL and no TRUNCATE, and `audit_events` / `exception_events` are SELECT + INSERT only.
- A trigger rejects UPDATE/DELETE on `audit_events` even for the owner role.
- **Limit:** a database superuser or administrator can still disable the trigger, change grants or edit rows. The audit trail is append-only for the application, not tamper-proof storage.
- Every consequential mutation records actor, action, time, entity ID, dataset, reason and run ID. Covered actions: login/logout, sync start/finish/failure, review start/resolve/reopen, settings change, re-evaluation, knowledge import/reindex, password reset and demo reset. Review decisions write the exception event (with evidence snapshot) and the audit row in the **same transaction**.

## AI governance

- Amounts, matching, tolerances, priority and status changes are deterministic code. Neither vector similarity nor model output affects them.
- The model has no tools and cannot change data or statuses, approve payment or send messages. Its output is a draft for a person.
- Retrieved text is labelled untrusted and delimited in the prompt. Output is validated against a schema; references must be among the retrieved ones, and URLs and payment-approval phrases are rejected. Any failure falls back to the template.
- Observed limitation: in testing, `qwen2.5:3b` once added an inaccurate inference to a summary that passed validation. Validation checks structure and references, not truth, so the verified-facts section stays separate and the draft must be reviewed.
- Similarity is shown as topical relevance only, never as proof of a contractual entitlement.

## Human decision ownership and data

- Resolution requires a reasoned human decision. A cleared discrepancy is flagged but never auto-resolved.
- The demo dataset and knowledge collection are fully synthetic (fictional companies and documents). Screenshots use only synthetic data.
- See OPERATIONS.md § Data retention.

## Remote deployment

Before any non-local use: terminate TLS in front of the app (see `infra/terraform/aws/README.md`), restrict network access to approved CIDRs or SSM port forwarding, provision secrets on the host (not via Terraform), and review session lifetimes. The AWS code is validated but has not been deployed.

## Scanning (CI and locally)

- `pip-audit` on both lock files: **no known vulnerabilities**. This required overriding Pillow to 13.0.0, because fastembed 0.7.4 caps Pillow below 12 and Pillow 11.3.0 had HIGH advisories. Pillow is imported by fastembed but unused by ProcureLens's text-only path, and the full test suite plus the retrieval evaluation pass with the override (`overrides.txt`).
- Trivy image scan: **0 fixable HIGH/CRITICAL**. 46 HIGH/CRITICAL remain in Debian 13 base packages (e.g. util-linux libraries, ncurses, sqlite) **with no fixed version available upstream** at scan time. CI fails on fixable HIGH/CRITICAL (`ignore-unfixed`). Rebuild regularly to pick up base-image fixes.
- Secret scan (Trivy locally; gitleaks in CI): no findings.

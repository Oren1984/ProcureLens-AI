## ERPNext connection

ProcureLens reads — and only reads — Suppliers, Purchase Orders, Purchase Receipts and Purchase Invoices through the Frappe REST API.

**Administrator setup in ERPNext**

1. Create a dedicated integration user, for example `procurelens-integration@yourcompany`.
2. Give it a role with **read** permission only on Supplier, Purchase Order, Purchase Receipt and Purchase Invoice (no write, submit, cancel or delete).
3. On that user, open *Settings → API Access → Generate Keys* and copy the API key and secret once.
4. Put them in ProcureLens's `.env` file (never in Git): `ERPNEXT_BASE_URL`, `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET`. Restart with `docker compose up -d`.

**Using it**

- Choose **Live ERPNext** in the sidebar, open *Integrations & knowledge → Synchronisation*, select *Check connection*, then *Sync from ERPNext*.
- Submitted and cancelled documents are fetched page by page (100 per page, max 2000 per type by default), with timeouts and up to 3 retries for timeouts, HTTP 429 and 5xx. Authentication errors stop immediately.
- All records are fetched first and applied in **one transaction** together with the re-evaluation, so results never mix old and new data. A failed fetch leaves the previous data untouched and is shown as a failed run.
- Malformed records are skipped and listed as warnings.

**Smoke test:** see `docs/OPERATIONS.md`, section *ERPNext smoke test*.

**Status:** the adapter is verified with HTTP contract tests against Frappe-shaped fixtures. A live ERPNext instance has **not** been tested in this release.

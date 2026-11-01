## Product overview and limitations

ProcureLens AI compares **purchase orders**, **goods receipts** and **supplier invoices** from ERPNext, flags discrepancies, shows the supporting records and agreement excerpts, and lets a human reviewer investigate and resolve each exception.

**What it does**

- Detects three discrepancy types per purchase-order line: invoiced quantity above accepted quantity, invoice price outside tolerance, and overdue undelivered quantity.
- Flags records it cannot calculate safely (foreign currency, unit conversion, returns, missing or inconsistent references) for **manual review** instead of guessing.
- Retrieves relevant clauses from supplier agreements and policies, and prepares a clarification draft.

**What it does not do**

- It does not pay invoices, post accounting entries, write to ERPNext or contact suppliers.
- AI never decides: amounts, matching, tolerances and statuses come from deterministic rules; the reviewer decides.

**Status:** portfolio-ready engineering POC/MVP. It is not a production-certified financial system. V1 supports one currency and consistent units of measure.

**Roles**

| Role | Can |
|---|---|
| Administrator | Synchronise, import knowledge, reindex, change settings, investigate and resolve |
| Demo viewer | Browse synthetic demo data and prepared results — read-only |

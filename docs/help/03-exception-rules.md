## Exception rules and tolerances

Matching uses **explicit ERPNext line references only** — `purchase_order` + `purchase_order_item` on receipts and `purchase_order` + `po_detail` on invoices. Text similarity is never used to match financial records.

**Included in totals:** submitted documents (docstatus 1). **Excluded:** drafts and cancelled documents; excluded documents are listed under the calculation.

| Rule | Condition | Exposure |
|---|---|---|
| Quantity | Σ invoiced qty > Σ accepted qty for the PO line (multiple receipts and invoices are summed; rejected qty is not accepted) | unmatched qty × average invoiced rate |
| Price | per invoice line: deviation of invoice rate from PO rate > tolerance (default **2 %**; exactly at the tolerance is accepted) | rate difference × invoiced qty |
| Overdue | ordered − accepted > 0 and scheduled date + grace (default **3 days**) has passed; PO not *Closed* or *Completed* | outstanding qty × PO rate |

**Manual review (not calculated):** currency other than the base currency, unit-of-measure mismatch, returns/debit notes, receipt or invoice lines without a PO reference, references to unknown or cancelled orders, item-code mismatches, non-positive PO values.

**Numbers:** amounts and quantities use exact decimals. Money is rounded half-up to 2 decimals and quantities to 3 decimals, only when a derived value is shown or stored.

**Triage score (0–100)** — a sorting aid, not a decision: 1 point per 100 of exposure (max 50) + 1 point per overdue day (max 30) + 15 for a critical item + 5 for manual review. The breakdown is shown on every exception.

Administrators can change the tolerance, grace days, critical items and retrieval threshold under **Integrations & knowledge → Settings**. Every change needs a reason, is audited and re-evaluates all exceptions.

## Demo walkthrough

The demo dataset is fully synthetic (fictional suppliers and documents) and is evaluated as of **2026-10-15**, so every run gives the same results.

1. **Overview** — open exceptions, exposure, overdue cases and data freshness.
2. **Exceptions** — the prioritised list. Select the row *BRG-6204: invoiced 100, accepted 80 on PO-2026-0001*.
3. **Calculation & source records** — 100 ordered at 12.50, 80 accepted, 100 invoiced → 20 units unmatched, exposure 250.00 EUR. Expand each linked record to see its lines and the raw ERPNext snapshot.
4. **Evidence & clarification draft** — the Nordwind agreement's invoicing clause ("may invoice only quantities that have been delivered and accepted") is retrieved with its file, version and section, alongside the partial-delivery rules. A clarification draft is prepared for you to copy or export.
5. **Reviewer actions** (administrator) — *Start review*, then *Resolve* with a reason such as "Credit note CN-1042 received for 20 units". The decision and its evidence snapshot appear under **History** and in the audit log.

Other demo cases: a price 4.39 % above the PO on a critical motor (PO-2026-0003), rejected pallets invoiced in full (PO-2026-0006), a long-overdue partial delivery with a cancelled invoice excluded (PO-2026-0005), and manual-review cases for USD documents, a return, a unit-of-measure mismatch and a freight line without a PO reference. Two orders match cleanly despite multiple partial receipts and a 1.67 % price deviation within tolerance.

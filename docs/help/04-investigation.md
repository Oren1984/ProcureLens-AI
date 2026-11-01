## Investigation and resolution

**Lifecycle:** Open → In review → Resolved. A resolved exception can be reopened.

- **Resolve** and **Reopen** require a reason (at least 5 characters).
- Each decision stores the reviewer, time, reason and an **evidence snapshot**: the facts and calculation at that moment plus the retrieved evidence references. The audit event is written in the same transaction.
- If two people act on the same exception, the second action is rejected and asks for a reload.

**When source data changes**

- *Material change* (quantities, rates, linked documents): the facts are updated, the exception is marked **source changed**, earlier facts stay in History, and a resolved exception is **reopened automatically** so the new discrepancy is visible.
- *Discrepancy no longer detected* (for example, the remaining delivery arrived): the exception is marked **no longer detected** but is **not** resolved automatically — the reviewer confirms and records the reason.

**Good resolution reasons** name the evidence: "Credit note CN-1042 received", "Remaining 20 units received on PR-2026-0010", "Price increase approved in amended PO rev. 2".

The read-only demo account sees the same screens with actions disabled; the server also rejects any attempted change from that account.

## Knowledge retrieval and optional AI

**Knowledge base.** UTF-8 Markdown (`.md`) or text (`.txt`) files up to 256 KB. Optional front matter:

```
---
title: Supply Agreement — Example Ltd
version: 2026.1
doc_type: agreement        # agreement | policy | procedure | other
supplier: SUP-0001         # omit for documents that apply to all suppliers
---
```

Documents are split by heading into chunks and embedded locally with **BAAI/bge-small-en-v1.5** (384 dimensions, via fastembed — no paid API). Re-importing the same file and version changes nothing; changed content needs a new `version`, and the previous version is deactivated.

**Retrieval.** For an exception, ProcureLens searches only documents that apply to all suppliers or to *that* supplier, then ranks by cosine similarity. Each excerpt shows file, version, section and chunk. Below the similarity threshold (default 0.60) the result is **insufficient evidence**. Similarity means topical relevance — it is not proof of a contractual entitlement.

**Optional AI (Ollama).** When enabled, a local model can draft the explanation and the supplier message. The *verified facts* section always comes from the deterministic calculation. Model output is validated (structure, length, only retrieved references cited). On a timeout, invalid output or an unavailable model the deterministic template is shown instead. The model cannot change records or statuses, approve payments or send messages, and document text is passed to it as untrusted data.

**Reindex** re-embeds every chunk with the configured model. Chunks embedded with a different model are excluded from search until reindexed.

"""Retrieval evaluation on the synthetic knowledge collection with the real local model.

Measures: recall@3 for questions with a known relevant source, and that no-evidence questions
stay below the configured similarity threshold (so the UI reports "insufficient evidence").
Requires PostgreSQL (TEST_DATABASE_URL) and the embedding model (downloaded once to
EMBEDDING_CACHE_DIR)."""

import json
import os
from pathlib import Path

import pytest

from procurelens.adapters.embeddings import FastEmbedProvider
from procurelens.services.app_config import DEFAULTS

pytestmark = [pytest.mark.db, pytest.mark.model]
ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "tests" / "eval" / "retrieval_cases.json").read_text())
THRESHOLD = float(DEFAULTS["retrieval_min_similarity"])


@pytest.fixture
def real_container(container):
    if os.environ.get("SKIP_MODEL_TESTS") == "1":
        pytest.skip("model tests disabled")
    cache = os.environ.get("EMBEDDING_CACHE_DIR")
    container.knowledge._factory = lambda: FastEmbedProvider(cache_dir=cache)
    container.knowledge._embedder = None
    container.knowledge.seed_directory(ROOT / "knowledge", "demo")
    return container


def test_retrieval_evaluation(real_container, admin):
    hits, rows = 0, []
    for case in CASES["relevant"]:
        res = real_container.knowledge.search(
            admin, "demo", case["q"], supplier=case["supplier"], k=3
        )
        files = [e.filename for e in res.evidence]
        hit = any(f in case["expect"] for f in files)
        hits += hit
        rows.append((case["q"][:50], res.best_similarity, hit))
        assert res.sufficient, f"relevant question judged insufficient: {case['q']}"
        if case["supplier"]:  # scope filter: no other supplier's agreement may appear
            for e in res.evidence:
                assert e.supplier in (None, case["supplier"])
    recall = hits / len(CASES["relevant"])
    for case in CASES["no_evidence"]:
        res = real_container.knowledge.search(admin, "demo", case["q"], supplier=case["supplier"])
        rows.append((case["q"][:50], res.best_similarity, not res.sufficient))
        assert not res.sufficient, f"no-evidence question returned evidence: {case['q']}"
    print("\nretrieval eval: recall@3 =", recall, "threshold =", THRESHOLD)
    for r in rows:
        print(f"  {r[1]:.3f}  {'ok ' if r[2] else 'MISS'}  {r[0]}")
    assert recall >= 0.9

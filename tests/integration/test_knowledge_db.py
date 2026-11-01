import pytest
from sqlalchemy import update

from procurelens.adapters.persistence.models import KnowledgeChunk
from procurelens.services.knowledge import KnowledgeError

pytestmark = pytest.mark.db


def doc(supplier=None, version="1", body="Supplier may invoice only accepted quantities."):
    fm = f"---\ntitle: T\nversion: {version}\ndoc_type: agreement\n"
    if supplier:
        fm += f"supplier: {supplier}\n"
    return (fm + f"---\n# Agreement\n\n## 6. Invoicing\n\n{body}\n").encode()


def test_supplier_scope_filter_applied_before_ranking(container, admin):
    container.knowledge.import_document(admin, "demo", "a.md", doc("SUP-A"))
    container.knowledge.import_document(admin, "demo", "b.md", doc("SUP-B"))
    container.knowledge.import_document(
        admin, "demo", "policy.md", doc(body="General invoice policy text.")
    )
    res = container.knowledge.search(
        admin, "demo", "invoice accepted quantities", supplier="SUP-A", min_similarity=0.0, k=10
    )
    files = {e.filename for e in res.evidence}
    assert files == {"a.md", "policy.md"}  # never another supplier's agreement
    no_sup = container.knowledge.search(admin, "demo", "invoice", min_similarity=0.0, k=10)
    assert {e.filename for e in no_sup.evidence} == {"policy.md"}


def test_source_references_are_preserved(container, admin):
    container.knowledge.import_document(admin, "demo", "a.md", doc("SUP-A", version="2026.1"))
    e = container.knowledge.search(
        admin, "demo", "invoice accepted", supplier="SUP-A", min_similarity=0.0
    ).evidence[0]
    assert e.ref == "a.md@v2026.1#chunk-0"
    assert e.section == "Agreement › 6. Invoicing"
    assert e.text == "Supplier may invoice only accepted quantities."  # exact excerpt


def test_insufficient_evidence_when_below_threshold(container, admin):
    container.knowledge.import_document(admin, "demo", "a.md", doc())
    res = container.knowledge.search(admin, "demo", "parking lot rules", min_similarity=0.9)
    assert not res.sufficient and res.evidence == [] and "Insufficient" in res.note


def test_versioning_and_idempotent_import(container, admin):
    assert container.knowledge.import_document(admin, "demo", "a.md", doc())[0] == "imported"
    assert container.knowledge.import_document(admin, "demo", "a.md", doc())[0] == "unchanged"
    with pytest.raises(KnowledgeError, match="Increase 'version'"):
        container.knowledge.import_document(admin, "demo", "a.md", doc(body="changed"))
    container.knowledge.import_document(admin, "demo", "a.md", doc(version="2", body="v2 text"))
    docs = container.knowledge.documents(admin, "demo")
    assert [(d.version, d.is_active) for d in docs] == [("2", True), ("1", False)]
    res = container.knowledge.search(admin, "demo", "text", min_similarity=0.0, k=10)
    assert {e.version for e in res.evidence} == {"2"}  # inactive versions never retrieved


def test_mixed_models_excluded_until_reindex(container, admin):
    container.knowledge.import_document(admin, "demo", "a.md", doc())
    with container.db.transaction() as s:
        s.execute(update(KnowledgeChunk).values(embedding_model="other-model"))
    assert container.knowledge.search(admin, "demo", "invoice", min_similarity=0.0).evidence == []
    st = container.knowledge.index_status(admin, container.knowledge.embedder.model_id)
    assert st["mismatched"] == 1
    assert container.knowledge.reindex(admin, "model changed") == 1
    assert container.knowledge.search(admin, "demo", "invoice", min_similarity=0.0).evidence


def test_dimension_mismatch_is_refused(container):
    class Wrong:
        model_id, dim = "wrong", 768

    container.knowledge._factory = Wrong
    container.knowledge._embedder = None
    with pytest.raises(KnowledgeError, match="dimension"):
        _ = container.knowledge.embedder


def test_bundled_collection_seeds_idempotently(container):
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    first = container.knowledge.seed_directory(root / "knowledge")
    second = container.knowledge.seed_directory(root / "knowledge")
    assert first["imported"] == 6 and second == {"imported": 0, "unchanged": 6}


def test_supplier_agreement_not_crowded_out_by_global_documents(container, admin):
    for i in range(5):  # five global docs that match the query better than the agreement
        container.knowledge.import_document(
            admin, "demo", f"g{i}.md", doc(body=f"invoice quantity accepted policy {i}")
        )
    container.knowledge.import_document(
        admin, "demo", "sup.md", doc("SUP-A", body="Supplier invoices only accepted goods.")
    )
    res = container.knowledge.search(
        admin, "demo", "invoice quantity accepted policy", supplier="SUP-A", k=4, min_similarity=0.0
    )
    files = [e.filename for e in res.evidence]
    assert "sup.md" in files and len(files) == 4
    assert [e.similarity for e in res.evidence] == sorted(
        (e.similarity for e in res.evidence), reverse=True
    )

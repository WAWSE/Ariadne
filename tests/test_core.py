from __future__ import annotations

from pathlib import Path

import pytest

from research_harness.embeddings import EmbeddingError, FastEmbedder, ProviderIdentity
from research_harness.models import normalize_bundle
from research_harness.store import ResearchStore


def _paper(record_id: str, summary: str) -> dict:
    return {"id": record_id, "type": "paper", "title": record_id,
            "summary": summary, "tags": ["synthetic"], "data": {"read_scope": "abstract"}}


def _bundle(records: list[dict], edges: list[dict] | None = None) -> dict:
    return {"schema_version": 1, "records": records, "edges": edges or []}


def test_atomic_validation_idempotence_and_stale_vector(tmp_path: Path) -> None:
    with ResearchStore(tmp_path, create=True) as store:
        paper = _paper("paper:one", "cue gating stable context")
        first = store.ingest(_bundle([paper]))
        assert first["inserted_records"] == 1
        assert store.ingest(_bundle([paper]))["unchanged_records"] == 1
        store.build_index(provider="hash")
        assert store.search("cue gating", mode="vector")["results"]

        changed = _paper("paper:one", "newly revised source wording")
        assert store.ingest(_bundle([changed]))["updated_records"] == 1
        # An old vector is never allowed to represent revised evidence.
        with pytest.raises(EmbeddingError, match="empty"):
            store.search("cue gating", mode="vector")
        assert store.stats()["vectors"] == 0
        assert store.stats()["unindexed_records"] == 1
        assert any("explicit vector indexing" in warning for warning in store.search("revised", mode="hybrid")["warnings"])

        invalid_edge = {"id": "edge:bad", "source": "paper:one", "target": "missing",
                        "relation": "cites", "status": "structural", "evidence_ids": [], "conditions": {}}
        with pytest.raises(ValueError):
            store.ingest(_bundle([_paper("paper:must-not-commit", "x")], [invalid_edge]))
        assert store.get_record("paper:must-not-commit") is None


def test_persisted_hash_vec_and_type_filter(tmp_path: Path) -> None:
    with ResearchStore(tmp_path, create=True) as store:
        claim = {"id": "claim:target", "type": "claim", "title": "gating claim",
                 "summary": "cue gating is a proposed bridge", "tags": [],
                 "data": {"origin": "assistant", "status": "proposed", "evidence_ids": [],
                          "conditions": {}, "alternative_explanations": []}}
        store.ingest(_bundle([_paper("paper:source", "cue gating source evidence"), claim]))
        store.build_index(provider="hash")
    # A new connection must use persisted sqlite-vec rows, not an in-memory cache.
    with ResearchStore(tmp_path) as reopened:
        results = reopened.search("cue gating", mode="vector", record_type="claim")["results"]
        assert [r["record"]["id"] for r in results] == ["claim:target"]


def test_provenance_reference_is_available_to_bounded_graph(tmp_path: Path) -> None:
    with ResearchStore(tmp_path, create=True) as store:
        evidence = {"id": "evidence:e", "type": "evidence", "title": "observed",
                    "summary": "source-bound excerpt", "tags": [],
                    "data": {"paper_id": "paper:p", "locator": "abstract", "excerpt": "observed text"}}
        hypothesis = {"id": "hypothesis:h", "type": "hypothesis", "title": "testable bridge",
                      "summary": "proposal", "tags": [],
                      "data": {"prediction": "p", "alternatives": [], "test": "t", "status": "proposed",
                               "evidence_ids": ["evidence:e"]}}
        store.ingest(_bundle([_paper("paper:p", "source"), evidence, hypothesis]))
        graph = store.graph("hypothesis:h", hops=1)
        assert {r["id"] for r in graph["records"]} >= {"hypothesis:h", "evidence:e"}


def test_schema_rejects_nonfinite_data_and_unsupported_structural_assertion() -> None:
    nonfinite = _paper("paper:nan", "x")
    nonfinite["data"] = {"measurement": float("nan")}
    with pytest.raises(ValueError, match="non-JSON"):
        normalize_bundle(_bundle([nonfinite]))
    bad_structural = {"id": "edge:bad", "source": "paper:a", "target": "paper:b",
                      "relation": "supports", "status": "structural", "evidence_ids": [], "conditions": {}}
    with pytest.raises(ValueError, match="structural"):
        normalize_bundle(_bundle([_paper("paper:a", "a"), _paper("paper:b", "b")], [bad_structural]))


def test_vector_identity_mismatch_and_failed_rebuild_leave_old_index_usable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with ResearchStore(tmp_path, create=True) as store:
        store.ingest(_bundle([_paper("paper:a", "stable cue gating")]))
        store.build_index(provider="hash")
        assert store.search("cue", mode="vector")["results"]

        # An invalid requested identity fails before replacing the existing index.
        with pytest.raises(EmbeddingError):
            store.build_index(provider="hash", model="not-a-hash-model")
        assert store.search("cue", mode="vector")["results"]

        store.conn.execute("UPDATE metadata SET value=? WHERE key='provider'", ('"fastembed"',))
        store.conn.commit()
        with pytest.raises(EmbeddingError, match="identity"):
            store.search("cue", mode="vector")


def test_mid_rebuild_failure_rolls_back_dimension_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """sqlite-vec DDL and metadata must roll back together on a failed rebuild."""
    import research_harness.store as store_module

    class TwoDimEmbedder:
        identity = ProviderIdentity("fastembed", "synthetic-two-dimensional", 2)

        def embed(self, texts):
            return [[0.5, 0.5] for _ in texts]

    with ResearchStore(tmp_path, create=True) as store:
        store.ingest(_bundle([_paper("paper:a", "stable cue gating")]))
        store.build_index(provider="hash")
        original_factory = store_module.make_embedder
        original_serialize = store._serialize
        monkeypatch.setattr(store_module, "make_embedder", lambda *args, **kwargs: TwoDimEmbedder())
        monkeypatch.setattr(store, "_serialize", lambda vector: (_ for _ in ()).throw(EmbeddingError("forced insertion failure")))
        with pytest.raises(EmbeddingError, match="forced"):
            store.build_index(provider="fastembed")
        monkeypatch.setattr(store_module, "make_embedder", original_factory)
        monkeypatch.setattr(store, "_serialize", original_serialize)
        assert store.stats()["provider"] == "hash"
        assert store.search("cue", mode="vector")["results"]


def test_fastembed_query_is_explicitly_local_and_graph_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fastembed = pytest.importorskip("fastembed", reason="optional semantic provider is not installed")

    calls: list[dict] = []

    class FakeTextEmbedding:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def passage_embed(self, texts):
            return [[0.0, 1.0] for _ in texts]

        def query_embed(self, text):
            return [[0.0, 1.0]]

    monkeypatch.setattr(fastembed, "TextEmbedding", FakeTextEmbedding)
    FastEmbedder("BAAI/bge-small-en-v1.5", allow_download=False, cache_dir=tmp_path / "cache").embed(["q"])
    assert calls == [{"model_name": "BAAI/bge-small-en-v1.5", "cache_dir": str(tmp_path / "cache"), "local_files_only": True}]

    with ResearchStore(tmp_path / "project", create=True) as store:
        store.ingest(_bundle([_paper(f"paper:{n}", "bounded") for n in range(3)]))
        graph = store.graph(limit=2)
        assert len(graph["records"]) == 2
        assert graph["truncated"] is True

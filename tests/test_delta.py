from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from research_harness.models import dependency_content_hash
from research_harness.store import ResearchStore


def paper(record_id: str, version: str = "v1", doi: str | None = None) -> dict:
    identifiers = {"doi": doi} if doi else {}
    return {"id": record_id, "type": "paper", "title": record_id, "summary": version,
            "tags": [], "data": {"read_scope": "fulltext", "version": version, "identifiers": identifiers}}


def evidence(record_id: str, source_id: str) -> dict:
    return {"id": record_id, "type": "evidence", "title": record_id, "summary": "observed",
            "tags": [], "data": {"paper_id": source_id, "locator": "p. 2", "excerpt": "text"}}


def claim(record_id: str, evidence_id: str, status: str = "reported") -> dict:
    return {"id": record_id, "type": "claim", "title": record_id, "summary": "claim", "tags": [],
            "data": {"origin": "author", "status": status, "evidence_ids": [evidence_id], "conditions": {}, "alternative_explanations": []}}


def delta(records: list[dict], edges: list[dict] | None = None, **extra: object) -> dict:
    return {"schema_version": 1, "records": records, "edges": edges or [], **extra}


def findings(store: ResearchStore, focus: str | None = None) -> list[dict]:
    return store.quality(focus=focus)["findings"]


def has_code(items: list[dict], code: str, subject_id: str | None = None) -> bool:
    return any(item["code"] == code and (subject_id is None or item.get("subject_id") == subject_id) for item in items)


def test_delta_dependency_review_receipt_is_explicit_and_propagates(tmp_path: Path) -> None:
    source, observed, conclusion = paper("paper:p"), evidence("evidence:e", "paper:p"), claim("claim:c", "evidence:e")
    edge = {"id": "edge:support", "source": "evidence:e", "target": "claim:c", "relation": "supports",
            "status": "reported", "evidence_ids": ["evidence:e"], "conditions": {"context": "stable"}}
    with ResearchStore(tmp_path, create=True) as store:
        store.apply_delta(delta([source, observed, conclusion], [edge]))
        stored = store.get_record("claim:c")
        assert stored and stored["data"]["basis"]["evidence:e"]

        changed_source = paper("paper:p", "v2")
        store.ingest({"schema_version": 1, "records": [changed_source], "edges": []})
        initial = findings(store, "paper:p")
        assert has_code(initial, "dependency_basis_drift", "evidence:e")
        assert has_code(initial, "dependency_needs_review", "claim:c")
        assert has_code(initial, "dependency_needs_review", "edge:support")

        # A repeat source import must not erase captured baselines or convert
        # stale work into untracked/fresh work.
        store.ingest({"schema_version": 1, "records": [changed_source], "edges": []})
        assert has_code(findings(store, "paper:p"), "dependency_basis_drift", "evidence:e")

        store.apply_delta(delta([], [], reviewed_ids=["evidence:e"], review_note="reread revised paper"))
        after_evidence_review = findings(store, "claim:c")
        assert store.get_record("evidence:e")["data"]["basis"]["paper:p"] == dependency_content_hash(store.get_record("paper:p"))
        # The receipt changes evidence content, so downstream claims remain
        # visibly stale until their own explicit reread.
        assert has_code(after_evidence_review, "dependency_basis_drift", "claim:c")

        store.apply_delta(delta([], [], reviewed_ids=["claim:c"], review_note="reread linked evidence"))
        after_claim_review = findings(store, "claim:c")
        assert not has_code(after_claim_review, "dependency_basis_drift", "claim:c")
        assert store.get_record("claim:c")["data"]["basis_review"]["note"] == "reread linked evidence"


def test_delta_review_of_related_records_captures_final_receipts(tmp_path: Path) -> None:
    with ResearchStore(tmp_path, create=True) as store:
        store.apply_delta(delta([paper("paper:p"), evidence("evidence:e", "paper:p"), claim("claim:c", "evidence:e")]))
        store.ingest({"schema_version": 1, "records": [paper("paper:p", "v2")], "edges": []})
        store.apply_delta(delta([], [], reviewed_ids=["evidence:e", "claim:c"], review_note="reviewed source and conclusion"))
        assessed = findings(store, "claim:c")
        assert not has_code(assessed, "dependency_basis_drift", "evidence:e")
        assert not has_code(assessed, "dependency_basis_drift", "claim:c")


def test_delta_is_atomic_for_invalid_endpoints_state_and_state_hash(tmp_path: Path) -> None:
    with ResearchStore(tmp_path, create=True) as store:
        state_hash = store.state_hash()
        invalid_edge = {"id": "edge:bad", "source": "paper:new", "target": "paper:new", "relation": "has_evidence",
                        "status": "structural", "evidence_ids": [], "conditions": {}}
        with pytest.raises(ValueError, match="incompatible"):
            store.apply_delta(delta([paper("paper:new")], [invalid_edge]))
        assert store.get_record("paper:new") is None

        hypothesis = {"id": "hypothesis:h", "type": "hypothesis", "title": "h", "summary": "h", "tags": [],
                      "data": {"prediction": "p", "alternatives": [], "test": "t", "status": "proposed", "evidence_ids": []}}
        with pytest.raises(ValueError, match="unknown"):
            store.apply_delta(delta([hypothesis], state_patch={"recent_decisions": ["decision:missing"]}, expected_state_hash=state_hash))
        assert store.get_record("hypothesis:h") is None
        with pytest.raises(ValueError, match="expected_state_hash"):
            store.apply_delta(delta([], [], expected_state_hash="0" * 64))
        assert store.state_hash() == state_hash


def test_export_restore_preserves_basis_and_ambiguous_review_needs_typed_address(tmp_path: Path) -> None:
    with ResearchStore(tmp_path / "one", create=True) as store:
        store.apply_delta(delta([paper("paper:p"), evidence("evidence:e", "paper:p"), claim("claim:c", "evidence:e")]))
        exported = store.export_bundle()
        assert exported["records"][1]["data"].get("basis") is not None
    with ResearchStore(tmp_path / "two", create=True) as restored:
        restored.ingest(exported)
        assert restored.export_bundle()["records"] == exported["records"]

    # Record and edge namespaces are independent, so a collision needs the
    # documented typed selector rather than silently reviewing both.
    with ResearchStore(tmp_path / "three", create=True) as store:
        store.ingest({"schema_version": 1, "records": [paper("same")], "edges": []})
        store.conn.execute("INSERT INTO edges(id,source,target,relation,status,evidence_json,conditions_json,payload_json,content_hash,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           ("same", "same", "same", "cites", "structural", "[]", "{}", '{"id":"same","source":"same","target":"same","relation":"cites","status":"structural","evidence_ids":[],"conditions":{}}', "x", 1, "now", "now"))
        store.conn.commit()
        with pytest.raises(ValueError, match="ambiguous"):
            store.apply_delta(delta([], [], reviewed_ids=["same"], review_note="x"))
        store.apply_delta(delta([], [], reviewed_ids=["record:same"], review_note="x"))


def test_hypothesis_status_change_does_not_stale_its_experiment_navigation_link(tmp_path: Path) -> None:
    experiment = {
        "id": "experiment:x", "type": "experiment", "title": "x", "summary": "x", "tags": [],
        "data": {"hypothesis_id": "hypothesis:h", "status": "completed", "command": "run", "budget": "1", "config": {"a": 1}, "seed": 1,
                 "code_ref": "run.py", "data_ref": "data", "metrics": {}, "artifacts": [], "outcome": "support"},
    }
    observation = {"id": "evidence:x", "type": "evidence", "title": "e", "summary": "e", "tags": [],
                   "data": {"experiment_id": "experiment:x", "locator": "metric: score", "excerpt": "0.9"}}
    hypothesis = {"id": "hypothesis:h", "type": "hypothesis", "title": "h", "summary": "h", "tags": [],
                  "data": {"prediction": "p", "alternatives": [], "test": "t", "status": "proposed", "evidence_ids": ["evidence:x"]}}
    result = {"id": "result:x", "type": "result", "title": "r", "summary": "r", "tags": [],
              "data": {"experiment_id": "experiment:x", "metrics": {}, "interpretation": "i", "limitations": []}}
    with ResearchStore(tmp_path, create=True) as store:
        store.apply_delta(delta([experiment, observation, hypothesis, result]))
        navigation = store.graph("experiment:x", hops=1)
        assert "hypothesis:h" in {record["id"] for record in navigation["records"]}
        assert any(item["field"] == "hypothesis_id" for item in navigation["provenance"])
        upgraded = deepcopy(hypothesis)
        upgraded["data"]["status"] = "supported"
        store.apply_delta(delta([upgraded]))
        found = findings(store, "experiment:x")
        assert not has_code(found, "dependency_basis_drift", "experiment:x")
        assert not has_code(found, "dependency_basis_drift", "evidence:x")
        assert not has_code(found, "dependency_basis_drift", "result:x")


def test_existing_ingest_cannot_silently_replace_review_basis(tmp_path: Path) -> None:
    with ResearchStore(tmp_path, create=True) as store:
        store.apply_delta(delta([paper("paper:p"), evidence("evidence:e", "paper:p")]))
        store.ingest({"schema_version": 1, "records": [paper("paper:p", "v2")], "edges": []})
        forged = deepcopy(store.get_record("evidence:e"))
        forged["data"]["basis"]["paper:p"] = dependency_content_hash(store.get_record("paper:p"))
        with pytest.raises(ValueError, match="apply_delta with reviewed_ids"):
            store.ingest({"schema_version": 1, "records": [forged], "edges": []})
        assert has_code(findings(store, "paper:p"), "dependency_basis_drift", "evidence:e")


def test_delta_id_replays_same_review_without_receipt_or_cas_change(tmp_path: Path) -> None:
    with ResearchStore(tmp_path, create=True) as store:
        store.apply_delta(delta([paper("paper:p"), evidence("evidence:e", "paper:p")]))
        expected = store.state_hash()
        request = delta([], [], reviewed_ids=["evidence:e"], review_note="reread source", expected_state_hash=expected, delta_id="codex-turn-42")
        first = store.apply_delta(request)
        receipt = deepcopy(store.get_record("evidence:e")["data"]["basis_review"])
        event_count = len(store.events(100))
        # A later state changes the ordinary compare-and-swap value.  Exact
        # replay nevertheless returns the first result before checking CAS.
        store.update_state({"question": "new concurrent state"})
        replay = store.apply_delta(deepcopy(request))
        assert replay == {**first, "replayed": True}
        assert store.get_record("evidence:e")["data"]["basis_review"] == receipt
        assert len(store.events(100)) == event_count + 1  # only update_state was new
        assert sum(event["event_type"] == "delta.applied" for event in store.events(100)) == 1

        conflicting = deepcopy(request)
        conflicting["review_note"] = "a different claimed reread"
        with pytest.raises(ValueError, match="already used with different content"):
            store.apply_delta(conflicting)
        assert store.get_record("evidence:e")["data"]["basis_review"] == receipt

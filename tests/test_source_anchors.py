from __future__ import annotations

import pytest

from research_harness.context import build_context
from research_harness.models import normalize_bundle
from research_harness.store import ResearchStore


def _bundle(records: list[dict], edges: list[dict] | None = None) -> dict:
    return {"schema_version": 1, "records": records, "edges": edges or []}


def _source(record_id: str = "source:brief", *, summary: str = "A supplied task brief.") -> dict:
    return {
        "id": record_id,
        "type": "source",
        "title": "Supplied task brief",
        "summary": summary,
        "tags": ["task-brief"],
        "data": {"kind": "user_task_brief"},
    }


def _source_evidence(record_id: str = "evidence:brief", source_id: str = "source:brief") -> dict:
    return {
        "id": record_id,
        "type": "evidence",
        "title": "Target constraint from supplied brief",
        "summary": "A supplied constraint, not a literature result.",
        "tags": ["context"],
        "data": {
            "source_id": source_id,
            "locator": "User task brief, constraints paragraph",
            "excerpt": "Target samples are unpaired and labels are unavailable.",
            "evidence_kind": "source_excerpt",
            "assertion_role": "target_assertion",
            "applicability": {"domain": "target task", "conditions": {}},
        },
    }


def _hypothesis(evidence_id: str = "evidence:brief") -> dict:
    return {
        "id": "hypothesis:target",
        "type": "hypothesis",
        "title": "Conditional target proposal",
        "summary": "A proposal constrained by the supplied task brief.",
        "tags": ["proposed"],
        "data": {
            "prediction": "A matched intervention changes the target metric.",
            "alternatives": ["The apparent change is a sampling artifact."],
            "test": "Compare the intervention with a matched control.",
            "status": "proposed",
            "evidence_ids": [evidence_id],
            "evidence_roles": {evidence_id: "context"},
            "applicability": {"domain": "target task", "conditions": {}},
        },
    }


def test_source_anchor_is_typed_graph_context_and_quality_provenance(tmp_path) -> None:
    source, evidence, hypothesis = _source(), _source_evidence(), _hypothesis()
    edge = {
        "id": "edge:brief-evidence",
        "source": source["id"],
        "target": evidence["id"],
        "relation": "has_evidence",
        "status": "structural",
        "evidence_ids": [],
        "conditions": {},
    }
    with ResearchStore(tmp_path, create=True) as store:
        store.apply_delta(_bundle([source, evidence, hypothesis], [edge]))
        saved = store.get_record("evidence:brief")
        assert saved is not None
        assert saved["data"]["basis"]["source:brief"]

        graph = store.graph("source:brief", hops=2)
        assert {record["id"] for record in graph["records"]} >= {
            "source:brief", "evidence:brief", "hypothesis:target",
        }
        assert ("evidence:brief", "source:brief", "source_id") in {
            (item["source"], item["target"], item["field"])
            for item in graph["provenance"]
        }
        assert "missing_dependency" not in {
            finding["code"] for finding in store.quality("hypothesis:target")["findings"]
        }

        context = build_context(
            store, intent="read", focus=["evidence:brief"], max_chars=6000,
            include_quality=False,
        )
        assert "source_id: source:brief" in context
        assert "type=source" in context
        assert "kind: user_task_brief" in context

        changed = _source(summary="A revised supplied task brief.")
        store.apply_delta(_bundle([changed]))
        assert "dependency_basis_drift" in {
            finding["code"] for finding in store.quality("evidence:brief")["findings"]
        }


def test_source_anchor_requires_explicit_kind_and_exactly_one_anchor(tmp_path) -> None:
    blank_kind = _source()
    blank_kind["data"] = {"kind": " "}
    with pytest.raises(ValueError, match="kind.*must not be empty"):
        normalize_bundle(_bundle([blank_kind]))

    missing = _source_evidence()
    missing["data"].pop("source_id")
    with pytest.raises(ValueError, match="exactly one source_id, paper_id, or experiment_id"):
        normalize_bundle(_bundle([missing]))

    multiple = _source_evidence()
    multiple["data"]["paper_id"] = "paper:wrong"
    with pytest.raises(ValueError, match="exactly one source_id, paper_id, or experiment_id"):
        normalize_bundle(_bundle([_source(), multiple]))

    with ResearchStore(tmp_path, create=True) as store:
        dangling = _source_evidence(source_id="source:missing")
        with pytest.raises(ValueError, match="must resolve to source"):
            store.apply_delta(_bundle([dangling]))


def test_legacy_paper_and_experiment_evidence_anchors_remain_valid(tmp_path) -> None:
    paper = {
        "id": "paper:legacy",
        "type": "paper",
        "title": "Legacy paper",
        "summary": "A paper source.",
        "tags": [],
        "data": {"read_scope": "abstract"},
    }
    paper_evidence = {
        "id": "evidence:paper",
        "type": "evidence",
        "title": "Paper evidence",
        "summary": "Legacy paper-bound evidence.",
        "tags": [],
        "data": {"paper_id": "paper:legacy", "locator": "Abstract", "excerpt": "Reported result."},
    }
    hypothesis = _hypothesis(evidence_id="evidence:paper")
    experiment = {
        "id": "experiment:legacy",
        "type": "experiment",
        "title": "Legacy planned run",
        "summary": "A planned run.",
        "tags": [],
        "data": {
            "hypothesis_id": "hypothesis:target",
            "status": "planned",
            "command": None,
            "budget": None,
            "config": {},
            "seed": None,
            "code_ref": None,
            "data_ref": None,
            "metrics": [],
            "artifacts": [],
        },
    }
    experiment_evidence = {
        "id": "evidence:experiment",
        "type": "evidence",
        "title": "Experiment evidence",
        "summary": "Legacy experiment-bound evidence.",
        "tags": [],
        "data": {"experiment_id": "experiment:legacy", "locator": "metric: score", "excerpt": "0.71"},
    }
    with ResearchStore(tmp_path, create=True) as store:
        store.apply_delta(_bundle([paper, paper_evidence, hypothesis, experiment, experiment_evidence]))
        assert store.get_record("evidence:paper")["data"]["paper_id"] == "paper:legacy"
        assert store.get_record("evidence:experiment")["data"]["experiment_id"] == "experiment:legacy"

"""Decision revision is lineage, not a globally negative evidence label."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re

import pytest

from research_harness.context import build_context
from research_harness.models import record_dependency_refs
from research_harness.project import (
    _EVIDENCE_TARGET_CHECKPOINT_SKILL,
    _legacy_template_payloads,
    init_project,
    install_templates,
    ProjectError,
)
from research_harness.semantics import usage_role
from research_harness.store import ResearchStore


def record(item_id: str, record_type: str, **data) -> dict:
    return {"id": item_id, "type": record_type, "title": item_id, "summary": "", "tags": [], "data": data}


def decision(item_id: str, role: str = "support") -> dict:
    return record(item_id, "decision", reason="Await protocol certification before deployment.",
                  next_action="Request certification.", evidence_ids=["evidence:correction"],
                  evidence_roles={"evidence:correction": role})


def revision(**overrides) -> dict:
    return {
        "id": "edge:revision", "source": "decision:new", "target": "decision:old",
        "relation": "revises", "status": "structural", "evidence_ids": [],
        "conditions": {
            "changed": "Actual certification covers protocol A only; do not deploy protocol B.",
            "retained": "The conditional protocol-B prediction and protocol-A measurements remain unchanged.",
        }, **overrides,
    }


def seed_records() -> list[dict]:
    return [
        record("source:certificate", "source", kind="protocol_certificate"),
        record("evidence:correction", "evidence", source_id="source:certificate",
               locator="certificate, paragraph 2", excerpt="Certification covers protocol A, not B.",
               evidence_kind="source_excerpt", applicability={"domain": "synthetic assay"}),
        record("claim:conditional", "claim", origin="assistant", status="proposed",
               conditions={"protocol": "B", "certified": True}),
        record("claim:scope", "claim", origin="user", status="proposed",
               conditions={"certification": "protocols A and B"}),
        record("decision:old", "decision", reason="The protocol-B route is conditional on certification.",
               next_action="Verify the certificate.", evidence_ids=[]),
    ]


def test_revision_survives_cold_graph_recovery_without_rewriting_old_knowledge(tmp_path: Path) -> None:
    with ResearchStore(tmp_path, create=True) as store:
        store.apply_delta({"schema_version": 1, "records": seed_records(), "edges": []})
        before = {item["id"]: item for item in store.list_records()}
        limiting_edge = {
            "id": "edge:scope-limit", "source": "evidence:correction", "target": "claim:scope",
            "relation": "limits", "status": "reported", "evidence_ids": ["evidence:correction"],
            "evidence_roles": {"evidence:correction": "counterevidence"},
            "conditions": {"changed": "Certification covers A only, not B."},
        }
        delta = {"schema_version": 1, "records": [decision("decision:new")], "edges": [revision(), limiting_edge],
                 "state_patch": {"recent_decisions": ["decision:new"], "next_action": "Request certification."}}
        checked = store.check_delta(delta)
        assert checked["valid"] is True
        assert checked["would_apply"]["inserted_edges"] == 2
        store.apply_delta(delta)
        for item_id, original in before.items():
            assert store.get_record(item_id) == original

    # New store handle: recovery must expose the authored direction and scope,
    # not synthesize a contradiction or change the conditional's status.
    with ResearchStore(tmp_path) as store:
        graph = store.graph("decision:new", hops=1)
        assert {"decision:new", "decision:old", "evidence:correction"} <= {r["id"] for r in graph["records"]}
        edge = next(e for e in graph["edges"] if e["id"] == "edge:revision")
        assert (edge["source"], edge["relation"], edge["target"]) == ("decision:new", "revises", "decision:old")
        assert edge["conditions"] == revision()["conditions"]
        assert usage_role(store.get_record("decision:new"), "evidence:correction") == "support"
        scoped = store.graph("claim:scope")
        opposition = next(e for e in scoped["edges"] if e["id"] == "edge:scope-limit")
        assert usage_role(opposition, "evidence:correction") == "counterevidence"
        assert usage_role(store.get_record("evidence:correction"), "evidence:correction") == "unknown"
        assert "decision:old" not in record_dependency_refs(store.get_record("decision:new"))
        brief = build_context(store, intent="map", focus=["decision:new"], max_chars=8000)
        assert "--revises/structural--> decision:old" in brief
        assert "protocol-A measurements remain unchanged" in brief
        assert store.quality()["counts"]["error"] == 0
        assert store.get_record("claim:conditional")["data"]["status"] == "proposed"
        exported = store.export_bundle()

    restored = tmp_path / "restored"
    with ResearchStore(restored, create=True) as store:
        store.ingest(exported)
        assert next(e for e in store.graph("decision:old")["edges"] if e["relation"] == "revises")["source"] == "decision:new"


@pytest.mark.parametrize("edge", [revision(source="claim:conditional"), revision(target="claim:conditional"),
                                  revision(target="decision:missing"), revision(target="decision:new")])
def test_bad_revision_references_are_rejected_atomically(tmp_path: Path, edge: dict) -> None:
    with ResearchStore(tmp_path, create=True) as store:
        store.apply_delta({"schema_version": 1, "records": seed_records(), "edges": []})
        before, events = deepcopy(store.export_bundle()), store.events(100)
        delta = {"schema_version": 1, "records": [decision("decision:new")], "edges": [edge]}
        assert store.check_delta(delta)["valid"] is False
        with pytest.raises(ValueError):
            store.apply_delta(delta)
        assert store.export_bundle() == before
        assert store.events(100) == events


def test_real_dissent_against_current_decision_is_not_banned_or_auto_relabelled(tmp_path: Path) -> None:
    with ResearchStore(tmp_path, create=True) as store:
        items = seed_records() + [decision("decision:new", "counterevidence")]
        store.apply_delta({"schema_version": 1, "records": items, "edges": []})
        assert usage_role(store.get_record("decision:new"), "evidence:correction") == "counterevidence"
        # A mechanical pass is deliberately not a claim that this prose or
        # authored scientific polarity has been understood/verified.
        assert "not scientific correctness" in store.check_delta({"schema_version": 1, "records": [], "edges": []})["guidance"]["evidence_role_target"]


def test_edge_errors_expose_legal_values_without_guessing_or_auto_repair(tmp_path: Path) -> None:
    with ResearchStore(tmp_path, create=True) as store:
        store.apply_delta({"schema_version": 1, "records": seed_records(), "edges": []})
        edge = revision(source="evidence:correction", target="evidence:correction", relation="limits")
        delta = {"schema_version": 1, "records": [], "edges": [edge]}
        checked = store.check_delta(delta)
        assert checked["valid"] is False
        assert "reported, observed, or hypothesized" in checked["error"]["message"]
        assert set(checked["guidance"]["enums"]["edge_status"]) == {"reported", "observed", "hypothesized", "structural"}
        assert "revises" in checked["guidance"]["enums"]["edge_relation"]
        edge["status"] = "supported"
        assert "must be hypothesized, observed, reported, structural" in store.check_delta(delta)["error"]["message"]
        edge["status"] = "reported"
        edge["evidence_ids"] = ["evidence:correction"]
        checked = store.check_delta(delta)
        assert checked["valid"] is False
        assert "allowed target types for evidence:" in checked["error"]["message"]
        assert "claim" in checked["error"]["message"]
        assert store.export_bundle()["edges"] == []


def test_packaged_checkpoint_example_is_executable_and_targets_new_decision(tmp_path: Path) -> None:
    init_project(tmp_path)
    reference = tmp_path / ".agents/skills/srh-checkpoint/references/ingest-schema.md"
    example = json.loads(re.search(r"```json\s*(.*?)\s*```", reference.read_text(encoding="utf-8"), re.S).group(1))
    with ResearchStore(tmp_path) as store:
        hypothesis = record("hypothesis:target", "hypothesis", prediction="Conditional effect.",
                            alternatives=["Preparation"], test="Read the limitation.", status="proposed")
        seeds = seed_records()
        seeds[1]["id"] = "evidence:counterexample"
        seeds[-1]["id"] = "decision:previous"
        store.apply_delta({"schema_version": 1, "records": seeds + [hypothesis], "edges": []})
        example["expected_state_hash"] = store.state_hash()
        assert store.check_delta(example)["valid"] is True
        store.apply_delta(example)
        assert usage_role(store.get_record("decision:next"), "evidence:counterexample") == "support"
        graph = store.graph("decision:next")
        assert next(e for e in graph["edges"] if e["relation"] == "revises")["target"] == "decision:previous"
        assert store.get_record("hypothesis:target")["data"]["status"] == "proposed"


@pytest.mark.parametrize("asset", ["SKILL.md", "references/ingest-schema.md"])
def test_owned_pre_role_target_templates_upgrade_but_user_edits_do_not(tmp_path: Path, asset: str) -> None:
    init_project(tmp_path)
    target = tmp_path / ".agents/skills/srh-checkpoint" / asset
    current = target.read_bytes()
    normalized = current.decode("utf-8").replace("\r\n", "\n")
    if asset == "SKILL.md":
        assert _EVIDENCE_TARGET_CHECKPOINT_SKILL in normalized
        predecessor = normalized.replace(_EVIDENCE_TARGET_CHECKPOINT_SKILL, "")
    else:
        predecessors = _legacy_template_payloads("srh-checkpoint", Path(asset), current)
        assert len(predecessors) == 1
        predecessor = predecessors.pop()
        assert '"edges": []' in predecessor
        assert '"evidence:counterexample":"counterevidence"' in predecessor
        assert "decision:previous" not in predecessor
    target.write_bytes(predecessor.replace("\n", "\r\n").encode("utf-8"))
    install_templates(tmp_path)
    assert target.read_bytes() == current
    custom = (predecessor + "\nUser-authored protocol policy.\n").encode("utf-8")
    target.write_bytes(custom)
    with pytest.raises(ProjectError, match="conflicting existing skill"):
        install_templates(tmp_path)
    assert target.read_bytes() == custom

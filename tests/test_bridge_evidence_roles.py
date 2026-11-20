"""Run the shipped bridge example, not just an independently correct fixture."""
from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from research_harness.context import build_context
from research_harness.project import (
    ProjectError, _legacy_template_payloads, init_project, install_templates,
)
from research_harness.semantics import usage_role
from research_harness.store import ResearchStore


ASSET = Path("references/ingest-schema.md")


def _reference(project: Path) -> Path:
    return project / ".agents/skills/srh-hypothesis-bridge" / ASSET


def _seed() -> list[dict]:
    def record(item_id: str, kind: str, **data) -> dict:
        return {"id": item_id, "type": kind, "title": item_id,
                "summary": "Synthetic bridge prerequisite.", "tags": [], "data": data}

    return [
        record("paper:source", "paper", read_scope="fulltext"),
        record("evidence:source", "evidence", paper_id="paper:source",
               locator="Results, stable context", excerpt="The source effect appeared in stable context.",
               evidence_kind="author_report", applicability={"domain": "synthetic source task",
                                                              "task_context": "stable source context"}),
        record("evidence:opposing", "evidence", paper_id="paper:source",
               locator="Results, volatile context", excerpt="The source effect disappeared in volatile context.",
               evidence_kind="author_report", applicability={"domain": "synthetic source task",
                                                              "task_context": "volatile source context"}),
        record("claim:source", "claim", origin="author", status="proposed",
               summary="The source effect generalizes across context stability.", evidence_ids=[]),
    ]


def test_shipped_bridge_assigns_roles_to_their_actual_owners_and_survives_cold_recovery(tmp_path: Path) -> None:
    init_project(tmp_path)
    delta = json.loads(re.search(r"```json\s*(.*?)\s*```", _reference(tmp_path).read_text(encoding="utf-8"), re.S).group(1))
    with ResearchStore(tmp_path) as store:
        store.apply_delta({"schema_version": 1, "records": _seed(), "edges": []})
        seeds = {item["id"]: item for item in store.list_records()}
        assert store.check_delta(delta)["valid"] is True
        store.apply_delta(delta)
        store.update_state({"recent_decisions": ["decision:bridge-boundary"]})
        snapshot = store.export_bundle()

    with ResearchStore(tmp_path) as store:
        proposal = store.get_record("hypothesis:target")
        decision = store.get_record("decision:bridge-boundary")
        opposition = next(edge for edge in store.graph("claim:source")["edges"]
                          if edge["id"] == "edge:source-opposition-limits")
        # One source observation, three distinct authored relationships:
        # against the source generalization; analogy for a conditional target
        # prediction; reason FOR the decision to test the failure boundary.
        assert usage_role(opposition, "evidence:opposing") == "counterevidence"
        assert opposition["target"] == "claim:source"
        assert usage_role(proposal, "evidence:opposing") == "analogy"
        assert usage_role(decision, "evidence:opposing") == "support"
        assert proposal["data"]["status"] == "proposed"
        assert usage_role(proposal, "evidence:target-context") == "context"
        assert not any(edge["relation"] in {"contradicts", "limits"}
                       and edge["target"] == proposal["id"] for edge in store.export_bundle()["edges"])
        recovered = build_context(store, intent="map", focus=[decision["id"]], max_chars=6000)
        assert "evidence_roles=evidence:opposing=support" in recovered
        resumed = build_context(store, max_chars=6000)
        assert "direct target counterevidence" not in resumed
        assert "direct target conflict exists" not in resumed
        for item_id, original in seeds.items():
            assert store.get_record(item_id) == original
        assert store.export_bundle() == snapshot  # reading cannot repair authored history


def test_only_exact_pre_role_bridge_reference_upgrades(tmp_path: Path) -> None:
    init_project(tmp_path)
    reference = _reference(tmp_path)
    current = reference.read_bytes()
    predecessors = _legacy_template_payloads("srh-hypothesis-bridge", ASSET, current)
    assert len(predecessors) == 1
    previous = predecessors.pop()
    previous_delta = json.loads(re.search(r"```json\s*(.*?)\s*```", previous, re.S).group(1))
    records = {item["id"]: item for item in previous_delta["records"]}
    assert usage_role(records["decision:bridge-boundary"], "evidence:opposing") == "counterevidence"
    assert usage_role(records["hypothesis:target"], "evidence:opposing") == "counterevidence"
    reference.write_bytes(previous.replace("\n", "\r\n").encode("utf-8"))
    install_templates(tmp_path)
    assert reference.read_bytes() == current
    customized = (previous + "\nUser-authored transfer policy.\n").encode("utf-8")
    reference.write_bytes(customized)
    with pytest.raises(ProjectError, match="conflicting existing skill"):
        install_templates(tmp_path)
    assert reference.read_bytes() == customized

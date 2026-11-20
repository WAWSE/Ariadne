from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from research_harness.cli import _expand_evidence_remember, main
from research_harness.project import ProjectError, init_project
from research_harness.store import ResearchStore


def _unit() -> dict:
    return {
        "id": "evidence:report", "title": "Conditional result",
        "source": {"id": "source:report", "title": "Inspected report", "kind": "paper"},
        "locator": "Results, paragraph 2", "excerpt": "Effect +0.02 in condition A; mechanism not isolated.",
        "evidence_kind": "author_report",
        "applicability": {"domain": "target", "conditions": {"stratum": "A"}},
    }


def test_evidence_check_commit_retry_and_scope_are_canonical(tmp_path: Path, capsys) -> None:
    init_project(tmp_path)
    path = tmp_path / "unit.json"
    path.write_text(json.dumps(_unit()), encoding="utf-8")
    args = ["--project", str(tmp_path), "remember", "--evidence", str(path)]
    assert main(args + ["--check"]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["valid"] and checked["guidance"]["submit"] == "srh remember --evidence FILE"
    with ResearchStore(tmp_path) as store:
        assert store.list_records() == []
        state_before = store.get_state()
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["inserted_records"] == 2
    with ResearchStore(tmp_path) as store:
        evidence = store.get_record("evidence:report")
        assert evidence["data"]["applicability"] == _unit()["applicability"]
        assert evidence["data"]["excerpt"] == _unit()["excerpt"]
        assert evidence["data"]["source_id"] == "source:report"
        assert "status" not in evidence["data"]
        assert store.get_state() == state_before
    assert main(args) == 0
    retry = json.loads(capsys.readouterr().out)
    assert retry["unchanged_records"] == 2 and retry["inserted_records"] == 0


@pytest.mark.parametrize("scope", [None, {"domain": "unknown", "conditions": {}}])
def test_source_scope_is_never_inferred_from_excerpt_or_promoted_when_unknown(tmp_path, scope):
    init_project(tmp_path)
    unit = _unit()
    if scope is None:
        unit.pop("applicability")
    else:
        unit["applicability"] = scope
    with ResearchStore(tmp_path) as store:
        store.apply_delta(_expand_evidence_remember(unit, store))
        saved = store.get_record(unit["id"])
        assert saved["data"].get("applicability") == scope
        assert "status" not in saved["data"]
        expected_code = "missing_applicability" if scope is None else "unknown_applicability"
        assert expected_code in {finding["code"] for finding in store.quality()["findings"]}


@pytest.mark.parametrize("mutation", [
    {"status": "established"}, {"state_patch": {"question": "not allowed"}},
    {"excerpt": ""}, {"source": {"id": "source:x"}},
])
def test_evidence_input_rejects_extra_or_incomplete_content_without_writes(tmp_path, mutation) -> None:
    init_project(tmp_path)
    with ResearchStore(tmp_path) as store:
        with pytest.raises(ProjectError):
            _expand_evidence_remember({**_unit(), **mutation}, store)
        assert store.list_records() == []


@pytest.mark.parametrize("kind", ["made_up_correction", "__SRH_TEMPLATE_EVIDENCE_KIND__"])
def test_evidence_short_path_uses_ordinary_semantic_validation(tmp_path, kind) -> None:
    init_project(tmp_path)
    with ResearchStore(tmp_path) as store:
        delta = _expand_evidence_remember({**_unit(), "evidence_kind": kind}, store)
        assert store.check_delta(delta)["valid"] is False
        assert store.list_records() == []


def test_evidence_reuses_source_metadata_and_refuses_silent_revisions(tmp_path) -> None:
    init_project(tmp_path)
    with ResearchStore(tmp_path) as store:
        first = _expand_evidence_remember(_unit(), store)
        first["records"][0]["data"]["authors"] = ["Original author"]
        store.apply_delta(first)
        second = {**_unit(), "id": "evidence:correction", "title": "Scope correction",
                  "excerpt": "Earlier condition A label was incorrect; actual condition B.",
                  "evidence_kind": "source_excerpt"}
        store.apply_delta(_expand_evidence_remember(second, store))
        assert store.get_record("source:report")["data"]["authors"] == ["Original author"]
        assert store.get_record("evidence:report")["data"]["excerpt"] == _unit()["excerpt"]
        with pytest.raises(ProjectError, match="cannot replace"):
            _expand_evidence_remember({**_unit(), "excerpt": "silently rewritten"}, store)


def test_evidence_guard_blocks_concurrent_collision_atomically(tmp_path) -> None:
    init_project(tmp_path)
    with ResearchStore(tmp_path) as store:
        stale = _expand_evidence_remember(_unit(), store)
        other = deepcopy(stale)
        other["records"][1]["data"]["excerpt"] = "Another writer's different report."
        store.apply_delta(other)
        before = store.export_bundle()
        assert store.check_delta(stale)["valid"] is False
        with pytest.raises(ValueError, match="expected_record_hashes"):
            store.apply_delta(stale)
        assert store.export_bundle() == before


def test_evidence_check_failure_retains_short_path_command(tmp_path, capsys) -> None:
    init_project(tmp_path)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({**_unit(), "evidence_kind": "invalid"}), encoding="utf-8")
    assert main(["--project", str(tmp_path), "remember", "--evidence", str(path), "--check"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert not result["valid"]
    assert "srh remember --evidence FILE" in result["next_step"]


def test_evidence_mode_cannot_be_combined_with_other_input_modes(tmp_path, capsys) -> None:
    init_project(tmp_path)
    for other in (["--compact", "x"], ["--check", "x"], ["--result-update", "x"],
                  ["--compact-template", "source-evidence"], ["--result-update-template", "hypothesis:x"]):
        assert main(["--project", str(tmp_path), "remember", "--evidence", "x", *other]) == 2
        capsys.readouterr()

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from research_harness.cli import COMPACT_REMEMBER_FORMAT, _expand_compact_remember, main
from research_harness.project import init_project
from research_harness.store import ResearchStore


def _replace_template_markers(template: dict) -> dict:
    replacements = {
        "__SRH_TEMPLATE_SOURCE_ID__": "acquisition-brief",
        "__SRH_TEMPLATE_SOURCE_TITLE__": "Acquisition brief",
        "__SRH_TEMPLATE_SOURCE_KIND__": "user_task_brief",
        "__SRH_TEMPLATE_EVIDENCE_ID__": "brightness-drift",
        "__SRH_TEMPLATE_EVIDENCE_TITLE__": "Observed brightness drift",
        "__SRH_TEMPLATE_LOCATOR__": "paragraph 1",
        "__SRH_TEMPLATE_EXCERPT__": "Frame brightness drifted during acquisition.",
        "__SRH_TEMPLATE_EVIDENCE_KIND__": "source_excerpt",
        "__SRH_TEMPLATE_EVIDENCE_DOMAIN__": "acquisition",
        "__SRH_TEMPLATE_EVIDENCE_CONDITIONS__": "session-A, fixed exposure",
    }
    rendered = json.dumps(template)
    for marker, replacement in replacements.items():
        rendered = rendered.replace(marker, replacement)
    return json.loads(rendered)


def test_checkpoint_first_graph_guidance_upgrades_owned_predecessor_only(tmp_path: Path) -> None:
    from research_harness.project import (
        _FIRST_GRAPH_CHECKPOINT_SKILL, _LEGACY_FIRST_GRAPH_CHECKPOINT_SKILL,
        _GRAPH_FIELD_CHECKPOINT_SKILL, ProjectError, install_templates,
        _UNSCOPED_FIRST_GRAPH_CHECKPOINT_SKILL,
    )

    init_project(tmp_path)
    skill = tmp_path / ".agents/skills/srh-checkpoint/SKILL.md"
    current = skill.read_text(encoding="utf-8")
    assert _FIRST_GRAPH_CHECKPOINT_SKILL in current
    unscoped_predecessor = current.replace(_FIRST_GRAPH_CHECKPOINT_SKILL, _UNSCOPED_FIRST_GRAPH_CHECKPOINT_SKILL)
    skill.write_text(unscoped_predecessor, encoding="utf-8")
    install_templates(tmp_path)
    assert skill.read_text(encoding="utf-8") == current
    skill.write_text(unscoped_predecessor + "\nUser-authored scope policy.\n", encoding="utf-8")
    with pytest.raises(ProjectError, match="conflicting existing skill"):
        install_templates(tmp_path)
    assert skill.read_text(encoding="utf-8").endswith("User-authored scope policy.\n")
    predecessor = current.replace(_FIRST_GRAPH_CHECKPOINT_SKILL, "")
    skill.write_text(predecessor, encoding="utf-8")
    install_templates(tmp_path)
    assert skill.read_text(encoding="utf-8") == current
    base = current.replace(_FIRST_GRAPH_CHECKPOINT_SKILL, "").replace(_GRAPH_FIELD_CHECKPOINT_SKILL, "")
    embedded_predecessor = base.replace(
        "\nIf the task explicitly", _LEGACY_FIRST_GRAPH_CHECKPOINT_SKILL + "\nIf the task explicitly"
    )
    skill.write_text(embedded_predecessor, encoding="utf-8")
    install_templates(tmp_path)
    assert skill.read_text(encoding="utf-8") == current
    skill.write_text(current.replace(_FIRST_GRAPH_CHECKPOINT_SKILL, _LEGACY_FIRST_GRAPH_CHECKPOINT_SKILL), encoding="utf-8")
    install_templates(tmp_path)
    assert skill.read_text(encoding="utf-8") == current
    skill.write_text(current.replace(_GRAPH_FIELD_CHECKPOINT_SKILL, ""), encoding="utf-8")
    install_templates(tmp_path)
    assert skill.read_text(encoding="utf-8") == current
    skill.write_text(predecessor + "\nUser custom research policy.\n", encoding="utf-8")
    with pytest.raises(ProjectError, match="conflicting existing skill"):
        install_templates(tmp_path)
    assert skill.read_text(encoding="utf-8").endswith("User custom research policy.\n")


_MARKER = re.compile(r"__SRH_TEMPLATE_([A-Z0-9_]+)__")


def _author_assertion_template(template: dict, kind: str) -> dict:
    """Fill every marker with deliberately authored, schema-valid test data."""

    exact = {
        "CLAIM_ORIGIN": "author",
        "CLAIM_STATUS": "supported",
        "CLAIM_EVIDENCE_ROLE": "support",
    }

    def replace(match: re.Match[str]) -> str:
        marker = match.group(1)
        if marker in exact:
            return exact[marker]
        if marker.endswith("EVIDENCE_KIND"):
            # The scoped claim exercises an attributed external empirical
            # report; transfer remains a proposed target hypothesis whose
            # source and target items are provenance only.
            return "author_report" if kind == "scoped-claim" else "source_excerpt"
        if marker in {"EVIDENCE_DOMAIN", "CLAIM_DOMAIN", "TARGET_DOMAIN"}:
            return "target-domain"
        if marker == "SOURCE_DOMAIN":
            return "source-domain"
        if "TASK_CONTEXT" in marker:
            return "matched-task" if "SOURCE" not in marker else "source-task"
        if "CONDITIONS" in marker:
            return "matched-protocol" if "SOURCE" not in marker else "source-protocol"
        return "authored-" + marker.casefold().replace("_", "-")

    def walk(value: object) -> object:
        if isinstance(value, str):
            return _MARKER.sub(replace, value)
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, dict):
            return {walk(key): walk(item) for key, item in value.items()}
        return value

    rendered = walk(template)
    assert isinstance(rendered, dict)
    assert "__SRH_TEMPLATE_" not in json.dumps(rendered)
    return rendered


def test_source_evidence_template_is_project_independent_and_has_no_science_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    outside_project = tmp_path / "no-project-here"
    outside_project.mkdir()
    monkeypatch.chdir(outside_project)

    assert main(["remember", "--compact-template", "source-evidence"]) == 0
    template = json.loads(capsys.readouterr().out)

    assert template["format"] == COMPACT_REMEMBER_FORMAT
    assert set(template) == {"format", "records"}
    assert [record["type"] for record in template["records"]] == ["source", "evidence"]
    assert "edges" not in template
    assert "state_patch" not in template
    assert all(record["type"] not in {"paper", "claim", "mechanism", "hypothesis", "decision", "experiment", "result"}
               for record in template["records"])
    serialized = json.dumps(template)
    assert "__SRH_TEMPLATE_SOURCE_KIND__" in serialized
    assert "__SRH_TEMPLATE_EXCERPT__" in serialized
    assert "__SRH_TEMPLATE_EVIDENCE_DOMAIN__" in serialized
    assert not (outside_project / ".research").exists()

    # It is a real compact envelope, not an undocumented quasi-schema.
    expanded = _expand_compact_remember(template)
    assert expanded["schema_version"] == 1
    assert expanded["edges"] == []
    assert all(record["summary"] == "" and record["tags"] == [] for record in expanded["records"])


def test_source_evidence_scaffold_preserves_authored_scope_without_scientific_inference(tmp_path, capsys):
    assert main(["remember", "--compact-template", "source-evidence"]) == 0
    template = json.loads(capsys.readouterr().out)
    authored = _replace_template_markers(template)
    project = tmp_path / "scoped-memory"
    init_project(project)
    path = project / "authored.json"
    path.write_text(json.dumps(authored), encoding="utf-8")
    assert main(["--project", str(project), "remember", "--compact", str(path)]) == 0
    capsys.readouterr()
    with ResearchStore(project) as store:
        evidence = store.get_record("evidence:brightness-drift")
        assert evidence["data"]["applicability"] == {
            "domain": "acquisition", "conditions": {"study_context": "session-A, fixed exposure"}}
        assert "status" not in evidence["data"]
        assert not {"missing_applicability", "unknown_applicability"} & {
            finding["code"] for finding in store.quality()["findings"]}
        assert {record["type"] for record in store.list_records()} == {"source", "evidence"}


def test_source_evidence_template_must_be_authored_before_preflight_succeeds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["remember", "--compact-template", "source-evidence"]) == 0
    template = json.loads(capsys.readouterr().out)
    project = tmp_path / "project"
    init_project(project)
    path = project / "source-evidence.json"
    path.write_text(json.dumps(template), encoding="utf-8")

    assert main(["--project", str(project), "remember", "--compact", str(path), "--check"]) == 0
    unfilled = json.loads(capsys.readouterr().out)
    assert unfilled["valid"] is False
    assert unfilled["error"]["category"] == "template"
    assert "unreplaced SRH template placeholder" in unfilled["error"]["message"]
    assert "Replace every __SRH_TEMPLATE_*__" in unfilled["next_step"]
    with ResearchStore(project) as store:
        assert store.list_records() == []

    path.write_text(json.dumps(_replace_template_markers(template)), encoding="utf-8")
    assert main(["--project", str(project), "remember", "--compact", str(path), "--check"]) == 0
    filled = json.loads(capsys.readouterr().out)
    assert filled["valid"] is True
    assert filled["would_apply"]["inserted_records"] == 2
    with ResearchStore(project) as store:
        assert store.list_records() == []


def test_incremental_intake_preserves_first_commit_when_next_unit_is_invalid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "incremental"
    init_project(project)
    assert main(["remember", "--compact-template", "source-evidence"]) == 0
    template = json.loads(capsys.readouterr().out)
    first = _replace_template_markers(template)
    path = project / "unit.json"
    path.write_text(json.dumps(first), encoding="utf-8")
    assert main(["--project", str(project), "remember", "--compact", str(path)]) == 0
    capsys.readouterr()
    with ResearchStore(project) as store:
        saved_first = store.export_bundle()

    second = _replace_template_markers(template)
    source, evidence = second["records"]
    source["id"] = "source:correction"
    evidence["id"] = "evidence:correction"
    evidence["data"].update({
        "source_id": source["id"], "excerpt": "The earlier report applies to condition B, not A.",
        "evidence_kind": "invented_correction_kind",
    })
    path.write_text(json.dumps(second), encoding="utf-8")
    assert main(["--project", str(project), "remember", "--compact", str(path), "--check"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is False
    with ResearchStore(project) as store:
        assert store.export_bundle() == saved_first

    evidence["data"]["evidence_kind"] = "source_excerpt"
    path.write_text(json.dumps(second), encoding="utf-8")
    assert main(["--project", str(project), "remember", "--compact", str(path)]) == 0
    capsys.readouterr()
    with ResearchStore(project) as store:
        records = {record["id"]: record for record in store.export_bundle()["records"]}
        assert len(records) == 4
        for record in saved_first["records"]:
            assert records[record["id"]] == record
        assert records["evidence:correction"]["data"]["excerpt"] == evidence["data"]["excerpt"]


@pytest.mark.parametrize(
    ("kind", "record_types"),
    [
        ("scoped-claim", ["source", "evidence", "claim"]),
        ("transfer-hypothesis", ["source", "source", "evidence", "evidence", "hypothesis"]),
    ],
)
def test_assertion_templates_reject_placeholders_then_preflight_authored_units_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], kind: str, record_types: list[str],
) -> None:
    # Template printing remains project-independent, so it cannot create a
    # hidden store merely to obtain a writing scaffold.
    outside_project = tmp_path / "outside"
    outside_project.mkdir()
    monkeypatch.chdir(outside_project)
    assert main(["remember", "--compact-template", kind]) == 0
    template = json.loads(capsys.readouterr().out)
    assert [record["type"] for record in template["records"]] == record_types
    assert template["format"] == COMPACT_REMEMBER_FORMAT
    assert "edges" not in template and "state_patch" not in template

    if kind == "scoped-claim":
        claim = template["records"][-1]["data"]
        assert {"status", "evidence_ids", "evidence_roles", "applicability", "unknowns"} <= set(claim)
    else:
        hypothesis = template["records"][-1]["data"]
        assert hypothesis["status"] == "proposed"
        assert hypothesis["assertion_role"] == "transfer_proposal"
        assert set(hypothesis["evidence_roles"].values()) == {"analogy", "context"}
        assert {"transfer_mapping", "unknowns", "minimum_falsifier", "test"} <= set(hypothesis)

    project = tmp_path / "project"
    init_project(project)
    path = project / f"{kind}.json"
    path.write_text(json.dumps(template), encoding="utf-8")
    with ResearchStore(project) as store:
        before_bundle, before_events = store.export_bundle(), store.events(100)

    assert main(["--project", str(project), "remember", "--compact", str(path), "--check"]) == 0
    unfilled = json.loads(capsys.readouterr().out)
    assert unfilled["valid"] is False
    assert unfilled["error"]["category"] == "template"

    path.write_text(json.dumps(_author_assertion_template(template, kind)), encoding="utf-8")
    assert main(["--project", str(project), "remember", "--compact", str(path), "--check"]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["valid"] is True
    assert checked["would_apply"]["inserted_records"] == len(record_types)
    assert checked["would_apply"]["inserted_edges"] == 0
    assert checked["would_apply"]["state_updated"] == 0
    with ResearchStore(project) as store:
        assert store.export_bundle() == before_bundle
        assert store.events(100) == before_events


def test_template_markers_in_object_keys_are_rejected_before_a_write(tmp_path: Path) -> None:
    project = tmp_path / "project"
    init_project(project)
    delta = {
        "schema_version": 1,
        "records": [{
            "id": "source:note", "type": "source", "title": "note", "summary": "", "tags": [],
            "data": {"kind": "notebook", "__SRH_TEMPLATE_ACCIDENTAL_KEY__": "must not persist"},
        }],
        "edges": [],
    }
    with ResearchStore(project) as store:
        checked = store.check_delta(delta)
        assert checked["valid"] is False
        assert checked["error"]["category"] == "template"
        assert store.list_records() == []


@pytest.mark.parametrize(
    "arguments",
    [
        ["remember", "some-delta.json", "--compact-template", "source-evidence"],
        ["remember", "--check", "some-delta.json", "--compact-template", "source-evidence"],
        ["remember", "--compact", "some-delta.json", "--compact-template", "source-evidence"],
    ],
)
def test_compact_template_cannot_be_mixed_with_a_remember_operation(
    capsys: pytest.CaptureFixture[str], arguments: list[str],
) -> None:
    assert main(arguments) == 2
    assert "cannot be combined" in capsys.readouterr().err

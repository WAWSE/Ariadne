from __future__ import annotations

import json
from pathlib import Path

from research_harness.cli import RESULT_UPDATE_REMEMBER_FORMAT, main
from research_harness.project import init_project
from research_harness.store import ResearchStore


def _record(record_id: str, record_type: str, data: dict) -> dict:
    return {"id": record_id, "type": record_type, "title": record_id, "summary": "", "tags": [], "data": data}


def _seed_transfer(project: Path) -> dict:
    """Save a small proposed transfer with target context but no target result."""

    source = _record("source:target-brief", "source", {"kind": "target_context"})
    context = _record("evidence:target-context", "evidence", {
        "source_id": source["id"], "locator": "brief", "excerpt": "Target acquisition is asynchronous.",
        "evidence_kind": "source_excerpt",
        "applicability": {
            "domain": "fluorescence", "task_context": "factor disentanglement",
            "conditions": {"acquisition": "asynchronous"},
        },
    })
    hypothesis = _record("hypothesis:transfer", "hypothesis", {
        "prediction": "Gated cross-attention improves target disentanglement.",
        "alternatives": ["Acquisition timing prevents the transfer."],
        "test": "Compare matched target runs with and without gated cross-attention.",
        "status": "proposed", "assertion_role": "transfer_proposal",
        "evidence_ids": [context["id"]], "evidence_roles": {context["id"]: "context"},
        "applicability": {
            "target_domain": "fluorescence", "task_context": "factor disentanglement",
            "conditions": {"acquisition": "asynchronous"},
        },
        "transfer_mapping": {"non_equivalences": ["Source streams were synchronized."]},
        "unknowns": ["Synchronization was not isolated."], "minimum_falsifier": "Effect below 0.05.",
    })
    with ResearchStore(project) as store:
        store.apply_delta({"schema_version": 1, "records": [source, context, hypothesis], "edges": []})
        return store.get_state()


def _template(project: Path, capsys) -> dict:
    assert main(["--project", str(project), "remember", "--result-update-template", "hypothesis:transfer"]) == 0
    template = json.loads(capsys.readouterr().out)
    assert template["format"] == RESULT_UPDATE_REMEMBER_FORMAT
    assert template["hypothesis_update"]["hypothesis_id"] == "hypothesis:transfer"
    return template


def _author_negative(template: dict) -> dict:
    source, evidence = template["records"]
    source.update({"id": "source:target-run-01", "title": "Target asynchronous run 01", "data": {"kind": "experiment_run_note"}})
    evidence.update({
        "id": "evidence:target-run-01", "title": "Target negative result",
        "data": {
            "source_id": source["id"], "locator": "metrics table, run 01",
            "excerpt": "Metric changed from 0.31 to 0.30; below preregistered 0.05 threshold.",
            "evidence_kind": "local_observation",
            "applicability": {
                "domain": "fluorescence", "task_context": "factor disentanglement",
                "conditions": {"acquisition": "asynchronous"},
            },
        },
    })
    template["hypothesis_update"].update({
        "status": "contradicted", "evidence_id": evidence["id"], "evidence_role": "counterevidence",
    })
    return template


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_result_update_preflight_then_commit_preserves_hypothesis_and_never_patches_state(
    tmp_path: Path, capsys,
) -> None:
    project = tmp_path / "project"
    init_project(project)
    state_before = _seed_transfer(project)
    template = _author_negative(_template(project, capsys))
    path = _write(project / "target-negative.json", template)

    with ResearchStore(project) as store:
        before = store.export_bundle()
    assert main(["--project", str(project), "remember", "--result-update", str(path), "--check"]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["valid"] is True
    assert checked["would_apply"]["inserted_records"] == 2
    assert checked["would_apply"]["updated_records"] == 1
    assert checked["would_apply"]["state_updated"] == 0
    assert checked["guidance"]["submit"] == "srh remember --result-update FILE"
    with ResearchStore(project) as store:
        assert store.export_bundle() == before
        assert store.get_state() == state_before

    assert main(["--project", str(project), "remember", "--result-update", str(path)]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["inserted_records"] == 2
    assert applied["updated_records"] == 1
    assert applied["state_updated"] == 0
    with ResearchStore(project) as store:
        hypothesis = store.get_record("hypothesis:transfer")
        assert hypothesis is not None
        data = hypothesis["data"]
        assert data["status"] == "contradicted"
        assert data["prediction"] == "Gated cross-attention improves target disentanglement."
        assert data["transfer_mapping"] == {"non_equivalences": ["Source streams were synchronized."]}
        assert data["unknowns"] == ["Synchronization was not isolated."]
        assert data["evidence_roles"] == {
            "evidence:target-context": "context", "evidence:target-run-01": "counterevidence",
        }
        assert "evidence:target-run-01" in data["basis"]
        assert data["basis_review"]["note"].startswith("Result-update template generated")
        assert store.get_state() == state_before


def test_result_update_rejects_stale_hash_wrong_role_scope_and_state_patch(
    tmp_path: Path, capsys,
) -> None:
    project = tmp_path / "project"
    init_project(project)
    _seed_transfer(project)
    template = _author_negative(_template(project, capsys))
    path = project / "update.json"

    stale = json.loads(json.dumps(template))
    stale["expected_hypothesis_hash"] = "0" * 64
    assert main(["--project", str(project), "remember", "--result-update", str(_write(path, stale)), "--check"]) == 2
    assert "does not match the saved hypothesis" in capsys.readouterr().err

    wrong_role = json.loads(json.dumps(template))
    wrong_role["hypothesis_update"]["evidence_role"] = "support"
    assert main(["--project", str(project), "remember", "--result-update", str(_write(path, wrong_role)), "--check"]) == 2
    assert "requires evidence_role 'counterevidence'" in capsys.readouterr().err

    wrong_scope = json.loads(json.dumps(template))
    wrong_scope["records"][1]["data"]["applicability"]["conditions"] = {"acquisition": "synchronized"}
    assert main(["--project", str(project), "remember", "--result-update", str(_write(path, wrong_scope)), "--check"]) == 2
    assert "exact explicit applicability match" in capsys.readouterr().err

    with_patch = json.loads(json.dumps(template))
    with_patch["state_patch"] = {"active_hypotheses": []}
    assert main(["--project", str(project), "remember", "--result-update", str(_write(path, with_patch)), "--check"]) == 2
    assert "unexpected state_patch" in capsys.readouterr().err
    with ResearchStore(project) as store:
        assert store.get_record("evidence:target-run-01") is None
        assert store.get_record("hypothesis:transfer")["data"]["status"] == "proposed"

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from research_harness.cli import COMPACT_REMEMBER_FORMAT, _expand_compact_remember, main
from research_harness.context import build_context
from research_harness.project import init_project
from research_harness.store import ResearchStore


def _compact_records() -> list[dict]:
    """A closed unit exercising every supported evidence anchor type."""

    return [
        {
            "id": "source:brief", "type": "source", "title": "Supplied brief",
            "data": {"kind": "user_task_brief"},
        },
        {
            "id": "paper:one", "type": "paper", "title": "Read paper",
            "data": {"read_scope": "abstract"},
        },
        {
            "id": "hypothesis:one", "type": "hypothesis", "title": "Proposed test",
            "data": {
                "prediction": "The intervention changes the metric.",
                "alternatives": ["A confound explains the change."],
                "test": "Compare a matched intervention and control.",
                "status": "proposed",
            },
        },
        {
            "id": "experiment:one", "type": "experiment", "title": "Planned comparison",
            "data": {
                "hypothesis_id": "hypothesis:one", "status": "planned",
                "command": None, "budget": None, "config": {}, "seed": None,
                "code_ref": None, "data_ref": None, "metrics": [], "artifacts": [],
            },
        },
        {
            "id": "evidence:from-source", "type": "evidence", "title": "Brief passage",
            "data": {
                "source_id": "source:brief", "locator": "brief paragraph 1",
                "excerpt": "Labels are unavailable.", "evidence_kind": "source_excerpt",
            },
        },
        {
            "id": "evidence:from-paper", "type": "evidence", "title": "Paper report",
            "data": {
                "paper_id": "paper:one", "locator": "Abstract",
                "excerpt": "The authors report an improvement.", "evidence_kind": "author_report",
            },
        },
        {
            "id": "evidence:from-experiment", "type": "evidence", "title": "Run note",
            "data": {
                "experiment_id": "experiment:one", "locator": "planned metric",
                "excerpt": "The comparison is planned.", "evidence_kind": "planned",
            },
        },
    ]


def _compact(**extra: object) -> dict:
    return {"format": COMPACT_REMEMBER_FORMAT, "records": _compact_records(), **extra}


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_compact_expands_to_the_same_stored_bundle_as_equivalent_raw_delta(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    compact = _compact()
    raw = _expand_compact_remember(deepcopy(compact))
    # Expansion is only structural: the marker is not part of the durable raw
    # request and the authored scientific payload stays byte-for-byte shaped.
    assert raw["schema_version"] == 1
    assert raw["edges"] == []
    assert raw["records"][0]["summary"] == ""
    assert raw["records"][0]["tags"] == []
    assert "format" not in raw

    compact_project, raw_project = tmp_path / "compact", tmp_path / "raw"
    init_project(compact_project)
    init_project(raw_project)
    compact_path = _write(compact_project / "compact.json", compact)
    raw_path = _write(raw_project / "raw.json", raw)

    assert main(["--project", str(compact_project), "remember", "--compact", str(compact_path)]) == 0
    compact_result = json.loads(capsys.readouterr().out)
    assert main(["--project", str(raw_project), "remember", str(raw_path)]) == 0
    raw_result = json.loads(capsys.readouterr().out)
    assert compact_result == raw_result
    with ResearchStore(compact_project) as compact_store, ResearchStore(raw_project) as raw_store:
        assert compact_store.export_bundle() == raw_store.export_bundle()


def test_complete_raw_delta_is_accepted_unchanged_with_compact_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = tmp_path / "project"
    init_project(project)
    raw = _expand_compact_remember(_compact())
    path = _write(project / "raw.json", raw)
    assert _expand_compact_remember(deepcopy(raw)) == raw
    assert main(["--project", str(project), "remember", "--compact", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["inserted_records"] == len(raw["records"])
    with ResearchStore(project) as store:
        assert store.get_record("evidence:from-source") is not None


def test_state_only_raw_handoff_is_durable_without_minting_graph_evidence(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = tmp_path / "handoff"
    init_project(project)
    delta = {
        "schema_version": 1,
        "records": [],
        "edges": [],
        "state_patch": {
            "question": "Does the frozen held result confirm the development gain?",
            "next_action": "Run the held comparison once without tuning.",
            "notes": [
                "E01 | source/dev | valid observation | low-SNR gain, no receiver claim",
                "E03 | source/dev | invalid old run | exclude from effect table",
                "E04 | held | planned only | result not yet observed",
            ],
        },
    }
    path = _write(project / "handoff.json", delta)
    assert main(["--project", str(project), "remember", "--check", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
    assert main(["--project", str(project), "remember", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state_updated"] == 1
    with ResearchStore(project) as store:
        assert store.list_records() == []
        brief = build_context(store, intent="resume", max_chars=2000)
    assert "E01 | source/dev | valid observation" in brief
    assert "E03 | source/dev | invalid old run" in brief
    assert "saved handoff note (unverified; not graph evidence)" in brief


def test_state_file_is_shortest_handoff_path_and_resumes_in_context(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = tmp_path / "state-file-handoff"
    init_project(project)
    patch = {
        "question": "Which result can guide the next study?",
        "next_action": "Run the frozen held comparison once.",
        "notes": [
            "E01 | source/dev | valid | PAIR improved 5 dB R by +0.019448",
            "E03 | source/dev | invalid v1 | exclude label-misaligned arms",
        ],
    }
    path = _write(project / "state-patch.json", patch)
    assert main(["--project", str(project), "state", "--file", str(path)]) == 0
    saved = json.loads(capsys.readouterr().out)
    assert saved["notes"] == patch["notes"]
    with ResearchStore(project) as store:
        assert store.list_records() == []
        brief = build_context(store, intent="resume", max_chars=2000)
    assert "E01 | source/dev | valid" in brief
    assert "E03 | source/dev | invalid v1" in brief


def test_state_file_rejects_a_remember_delta_envelope(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = tmp_path / "bad-state-file"
    init_project(project)
    mistaken = {
        "schema_version": 1,
        "records": [],
        "edges": [],
        "state_patch": {"question": "This must not be nested in Research State."},
    }
    path = _write(project / "mistaken-delta.json", mistaken)
    with ResearchStore(project) as store:
        before = store.get_state()
    assert main(["--project", str(project), "state", "--file", str(path)]) == 2
    assert "expects the state patch itself" in capsys.readouterr().err
    with ResearchStore(project) as store:
        assert store.get_state() == before


def test_compact_preflight_is_non_mutating_then_the_same_compact_commit_works(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    init_project(project)
    path = _write(project / "compact.json", _compact())
    with ResearchStore(project) as store:
        before_bundle, before_events = store.export_bundle(), store.events(100)

    assert main(["--project", str(project), "remember", "--compact", str(path), "--check"]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["valid"] is True
    assert checked["guidance"]["submit"] == "srh remember --compact FILE"
    assert "does not infer or repair" in checked["guidance"]["compact"]
    with ResearchStore(project) as store:
        assert store.export_bundle() == before_bundle
        assert store.events(100) == before_events

    assert main(["--project", str(project), "remember", "--compact", str(path)]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["inserted_records"] == len(_compact_records())
    with ResearchStore(project) as store:
        assert store.get_record("evidence:from-experiment") is not None


def test_compact_keeps_required_scientific_fields_and_typed_anchors_strict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    init_project(project)

    missing_prediction = _compact()
    hypothesis = next(item for item in missing_prediction["records"] if item["id"] == "hypothesis:one")
    hypothesis["data"].pop("prediction")
    invalid_path = _write(project / "missing-science.json", missing_prediction)
    assert main(["--project", str(project), "remember", "--compact", str(invalid_path), "--check"]) == 0
    missing = json.loads(capsys.readouterr().out)
    assert missing["valid"] is False
    assert "prediction" in missing["error"]["message"]

    wrong_anchor = _compact()
    evidence = next(item for item in wrong_anchor["records"] if item["id"] == "evidence:from-source")
    evidence["data"]["source_id"] = "paper:one"
    wrong_path = _write(project / "wrong-anchor.json", wrong_anchor)
    assert main(["--project", str(project), "remember", "--compact", str(wrong_path), "--check"]) == 0
    wrong = json.loads(capsys.readouterr().out)
    assert wrong["valid"] is False
    assert wrong["error"]["category"] == "reference"
    assert "must resolve to source" in wrong["error"]["message"]
    with ResearchStore(project) as store:
        assert store.list_records() == []


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"records": []}, "requires format"),
        ({"format": "srh.remember.compact.v0", "records": []}, "format must be"),
        ({"format": COMPACT_REMEMBER_FORMAT, "records": [{"id": "source:missing"}]}, "type is required"),
    ],
)
def test_compact_rejects_missing_or_malformed_marker_and_required_structure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], payload: dict, message: str,
) -> None:
    project = tmp_path / "project"
    init_project(project)
    path = _write(project / "bad.json", payload)
    assert main(["--project", str(project), "remember", "--compact", str(path)]) == 2
    assert message in capsys.readouterr().err


def test_compact_and_raw_remember_forms_cannot_be_ambiguously_combined(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    init_project(project)
    compact_path = _write(project / "compact.json", _compact())
    raw_path = _write(project / "raw.json", _expand_compact_remember(_compact()))

    assert main(["--project", str(project), "remember", str(raw_path), "--compact", str(compact_path)]) == 2
    assert "either FILE or --compact FILE" in capsys.readouterr().err
    assert main(["--project", str(project), "remember", "--check", str(raw_path), "--compact", str(compact_path)]) == 2
    assert "cannot be combined" in capsys.readouterr().err
    assert main(["--project", str(project), "remember", "--check"]) == 2
    assert "only valid with --compact FILE" in capsys.readouterr().err


def test_compact_invalid_semantics_keeps_compact_retry_instruction(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    init_project(project)
    payload = _compact()
    evidence = next(record for record in payload["records"] if record["type"] == "evidence")
    evidence["data"]["evidence_kind"] = "invented_kind"
    path = _write(project / "bad-kind.json", payload)
    assert main(["--project", str(project), "remember", "--compact", str(path), "--check"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["valid"] is False
    assert "srh remember --compact FILE" in result["next_step"]
    assert result["guidance"]["submit"] == "srh remember --compact FILE"
    with ResearchStore(project) as store:
        assert store.list_records() == []

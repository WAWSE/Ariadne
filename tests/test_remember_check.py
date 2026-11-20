from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from research_harness.cli import main
from research_harness.project import init_project
from research_harness.store import ResearchStore


def _source(record_id: str = "source:brief") -> dict:
    return {
        "id": record_id,
        "type": "source",
        "title": "task brief",
        "summary": "user-supplied task context",
        "tags": [],
        "data": {"kind": "user_task_brief"},
    }


def _evidence(record_id: str, source_id: str, **data: object) -> dict:
    return {
        "id": record_id,
        "type": "evidence",
        "title": record_id,
        "summary": "saved source statement",
        "tags": [],
        "data": {
            "source_id": source_id,
            "locator": "task brief, paragraph 1",
            "excerpt": "An authored observation.",
            "evidence_kind": "source_excerpt",
            **data,
        },
    }


def _delta(records: list[dict], **extra: object) -> dict:
    return {"schema_version": 1, "records": records, "edges": [], **extra}


def test_check_uses_the_apply_transaction_but_leaves_no_durable_write(tmp_path: Path) -> None:
    request = _delta(
        [_source("source:new-brief"), _evidence("evidence:new-context", "source:new-brief")],
        state_patch={"question": "What should be tested?"},
        delta_id="check-does-not-reserve-retry-key",
    )
    with ResearchStore(tmp_path, create=True) as store:
        before_bundle = deepcopy(store.export_bundle())
        before_events = store.events(100)

        checked = store.check_delta(request)

        assert checked["valid"] is True
        assert checked["would_apply"]["inserted_records"] == 2
        assert checked["guidance"]["submit"] == "srh remember FILE"
        assert checked["guidance"]["enums"]["assertion_role"]
        assert checked["guidance"]["enums"]["evidence_kind"]
        assert checked["guidance"]["enums"]["evidence_roles"]
        assert store.export_bundle() == before_bundle
        assert store.events(100) == before_events
        assert store.get_state() == before_bundle["state"]

        # A dry run must not reserve a retry key or otherwise change ordinary
        # remember semantics: this is the first real application, not a replay.
        applied = store.apply_delta(request)
        assert applied["inserted_records"] == 2
        assert "replayed" not in applied

        # A pre-existing durable retry receipt is checked on the same replay
        # path, but checking it adds no second receipt or event.
        after_apply_bundle = deepcopy(store.export_bundle())
        after_apply_events = store.events(100)
        replay_check = store.check_delta(request)
        assert replay_check["valid"] is True
        assert replay_check["would_apply"]["replayed"] is True
        assert store.export_bundle() == after_apply_bundle
        assert store.events(100) == after_apply_events


def test_check_reports_ordinary_role_reference_and_state_failures_without_writing(tmp_path: Path) -> None:
    with ResearchStore(tmp_path, create=True) as store:
        store.apply_delta(_delta([_source()]))
        before_bundle = deepcopy(store.export_bundle())
        before_events = store.events(100)

        invalid_role = _delta([_evidence("evidence:bad-role", "source:brief", assertion_role="made_up_role")])
        checked_role = store.check_delta(invalid_role)
        assert checked_role["valid"] is False
        assert checked_role["error"]["category"] == "semantic"
        assert "assertion_role" in checked_role["error"]["message"]

        dangling = _delta([_evidence("evidence:dangling", "source:missing")])
        checked_ref = store.check_delta(dangling)
        assert checked_ref["valid"] is False
        assert checked_ref["error"]["category"] == "reference"
        assert any(item["id"] == "source:brief" for item in checked_ref["available_anchors"])

        stale_state = _delta([], expected_state_hash="0" * 64)
        checked_state = store.check_delta(stale_state)
        assert checked_state["valid"] is False
        assert checked_state["error"]["category"] == "state"

        # Check failures preserve the same ordinary errors a real write would
        # raise, while neither path commits any partial entity or event.
        with pytest.raises(ValueError, match="assertion_role"):
            store.apply_delta(invalid_role)
        with pytest.raises(ValueError, match="must resolve"):
            store.apply_delta(dangling)
        with pytest.raises(ValueError, match="expected_state_hash"):
            store.apply_delta(stale_state)
        assert store.export_bundle() == before_bundle
        assert store.events(100) == before_events


def test_remember_check_cli_is_non_mutating_and_regular_remember_is_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    init_project(project)
    request = _delta([_source("source:cli")])
    path = project / "delta.json"
    path.write_text(json.dumps(request), encoding="utf-8")

    assert main(["--project", str(project), "remember", "--check", str(path)]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["valid"] is True
    with ResearchStore(project) as store:
        assert store.get_record("source:cli") is None

    assert main(["--project", str(project), "remember", str(path)]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["inserted_records"] == 1
    with ResearchStore(project) as store:
        assert store.get_record("source:cli") is not None

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex

import pytest

from research_harness.cli import main
from research_harness.hooks import session_start
from research_harness.models import content_hash
from research_harness.project import ProjectError, init_project
from research_harness.store import ResearchStore


def test_init_writes_canonical_nested_hooks_and_reinit_preserves_user(tmp_path: Path) -> None:
    project = tmp_path / "project with spaces"
    project.mkdir()
    codex = project / ".codex"
    codex.mkdir()
    (codex / "hooks.json").write_text(json.dumps({"version": 7, "hooks": {"SessionStart": [{"matcher": "*", "hooks": [{"type": "command", "command": "user-command"}]}]}}), encoding="utf-8")
    init_project(project, hooks=True)
    data = json.loads((codex / "hooks.json").read_text(encoding="utf-8"))
    assert data["version"] == 7
    assert data["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "user-command"
    managed = [handler for group in data["hooks"]["SessionStart"] if group.get("matcher") == "startup|resume|compact" for handler in group["hooks"]]
    assert managed[0]["additionalContextLimit"] == 2000
    assert managed[0]["timeout"] == 5
    assert data["hooks"]["Stop"][0]["hooks"][0]["type"] == "command"
    assert data["hooks"]["Stop"][0]["hooks"][0]["timeout"] == 5


def test_malformed_hooks_fail_before_project_setup(tmp_path: Path) -> None:
    project = tmp_path / "bad"
    project.mkdir()
    (project / ".codex").mkdir()
    (project / ".codex" / "hooks.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ProjectError):
        init_project(project, hooks=True)
    assert not (project / ".research").exists()


def test_export_refuses_overwrite_and_stop_is_system_message(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = tmp_path / "p"
    init_project(project)
    output = project / "export.json"
    output.write_text("keep", encoding="utf-8")
    assert main(["--project", str(project), "export", str(output)]) != 0
    assert output.read_text(encoding="utf-8") == "keep"
    assert main(["--project", str(project), "hook", "stop"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["systemMessage"] == "Research snapshot refreshed."


def test_hook_relocation_removes_only_validated_legacy_python_command(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    hooks = project / ".codex"
    hooks.mkdir()
    if os.name == "nt":
        legacy_python, wrapper_binary = r"C:\old location\python.exe", r"C:\tools\wrapper.exe"
        legacy = f'"{legacy_python}" -m research_harness hook session-start --project "{project}"'
        wrapper = f'"{wrapper_binary}" -m research_harness hook session-start --project "{project}"'
    else:
        legacy = f'/opt/relocated/python3 -m research_harness hook session-start --project {shlex.quote(str(project))}'
        wrapper = f'/opt/tools/wrapper -m research_harness hook session-start --project {shlex.quote(str(project))}'
    user_echo = f'echo -m research_harness hook session-start --project {shlex.quote(str(project))}'
    data = {"hooks": {"SessionStart": [{"matcher": "*", "hooks": [
        {"type": "command", "command": legacy},
        {"type": "command", "command": user_echo},
        {"type": "command", "command": wrapper},
    ]}]}}
    (hooks / "hooks.json").write_text(json.dumps(data), encoding="utf-8")
    init_project(project, hooks=True)
    merged = json.loads((hooks / "hooks.json").read_text(encoding="utf-8"))
    commands = [handler.get("command") for group in merged["hooks"]["SessionStart"] for handler in group["hooks"]]
    assert user_echo in commands
    assert wrapper in commands
    assert legacy not in commands


def test_state_hash_is_calculated_from_the_returned_snapshot(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = tmp_path / "p"
    init_project(project)
    assert main(["--project", str(project), "state", "--hash"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state_hash"] == content_hash(result["state"])

    patch = project / "state patch.json"
    patch.write_text(json.dumps({"question": "new question"}), encoding="utf-8")
    assert main(["--project", str(project), "state", "--file", str(patch), "--hash"]) == 0
    updated = json.loads(capsys.readouterr().out)
    assert updated["state_hash"] == content_hash(updated["state"])


def test_remember_and_quality_commands_delegate_to_store(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = tmp_path / "p"
    init_project(project)
    delta = project / "reviewed delta.json"
    delta.write_text(json.dumps({
        "schema_version": 1,
        "records": [{"id": "paper:remembered", "type": "paper", "title": "saved", "summary": "local note", "tags": [], "data": {"read_scope": "metadata"}}],
        "edges": [], "state_patch": {"question": "remembered question"}, "reviewed_ids": [],
    }), encoding="utf-8")
    assert main(["--project", str(project), "remember", str(delta)]) == 0
    remembered = json.loads(capsys.readouterr().out)
    assert remembered["inserted_records"] == 1
    assert main(["--project", str(project), "quality", "--focus", "paper:remembered", "--limit", "5"]) == 0
    quality = json.loads(capsys.readouterr().out)
    assert quality["scope"].startswith("mechanical/advisory")
    assert quality["counts"]


def test_session_start_recovers_current_work_at_its_context_cap(tmp_path: Path) -> None:
    project = tmp_path / "session"
    init_project(project)
    root = Path(__file__).resolve().parents[1]
    delta = json.loads((root / "examples" / "workflow-remember-delta.json").read_text(encoding="utf-8"))
    state = json.loads((root / "examples" / "workflow-context-state.json").read_text(encoding="utf-8"))
    with ResearchStore(project) as store:
        store.apply_delta(delta)
        store.update_state(state)
    context = session_start(project)["hookSpecificOutput"]["additionalContext"]
    assert len(context) <= 2000
    assert "active hypothesis: hypothesis:workflow-transfer | status=proposed" in context
    assert "prediction: Under stable target context" in context
    assert "test: Compare intermittent and matched continuous cues" in context
    assert "decision reason: The source association and counterexample differ" in context
    assert "related evidence: evidence:workflow-counterexample | locator: Abstract, paragraph 2" in context
    if "recorded next_action (non-driving" in context:
        assert context.index("recorded next_action (non-driving") > context.index("related evidence: evidence:workflow-counterexample")

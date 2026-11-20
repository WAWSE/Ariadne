from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from research_harness.cli import main
from research_harness.hooks import session_start
from research_harness.models import content_hash
from research_harness.project import (
    RUNTIME_LAUNCHER, ProjectError, _previous_compact_launcher_managed_agents_block,
    _previous_managed_agents_block, _previous_punctuation_managed_agents_block,
    init_project, managed_agents_block,
)
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
    manifest = json.loads((project / ".research" / "harness-assets.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert len(manifest["asset_set_sha256"]) == 64
    assert any(item["path"].endswith("srh-paper-read/SKILL.md") for item in manifest["assets"])
    launcher = project / RUNTIME_LAUNCHER
    assert manifest["runtime_launchers"] == [{
        "path": RUNTIME_LAUNCHER.as_posix(),
        "sha256": hashlib.sha256(launcher.read_bytes()).hexdigest(),
    }]


def _run_local_launcher(launcher: Path, *args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
    """Invoke the native project command in the same way Codex's shell does."""

    if os.name == "nt":
        command = subprocess.list2cmdline([str(launcher), *args])
        return subprocess.run(command, shell=True, **kwargs)  # type: ignore[arg-type]
    return subprocess.run([str(launcher), *args], **kwargs)  # type: ignore[arg-type]


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher encoding")
def test_windows_launcher_emits_utf8_handoff_and_upgrades_exact_managed_bytes(tmp_path: Path) -> None:
    project = tmp_path / "utf8-handoff"
    init_project(project, hooks=True)
    launcher = project / RUNTIME_LAUNCHER
    current = launcher.read_bytes()
    assert b" -X utf8 -m research_harness" in current
    (project / ".research" / "handoff.md").write_text("E01 | 来源/开发 | 有效观察\n", encoding="utf-8")
    result = _run_local_launcher(launcher, "context", cwd=project, capture_output=True)
    assert result.returncode == 0
    assert "E01 | 来源/开发 | 有效观察" in result.stdout.decode("utf-8")
    hooks = json.loads((project / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    assert "-X utf8" in hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"]

    predecessor = current.replace(b" -X utf8 -m research_harness", b" -m research_harness")
    launcher.write_bytes(predecessor)
    manifest_path = project / ".research" / "harness-assets.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["runtime_launchers"][0]["sha256"] = hashlib.sha256(predecessor).hexdigest()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    init_project(project, hooks=True)
    assert launcher.read_bytes() == current


def test_init_local_runtime_does_not_need_global_srh_or_generic_python(tmp_path: Path) -> None:
    project = tmp_path / "project with spaces"
    init_project(project)
    launcher = project / RUNTIME_LAUNCHER
    assert launcher.is_file()
    if os.name != "nt":
        assert os.access(launcher, os.X_OK)

    agents = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert ".research" in agents
    assert "python -m research_harness" not in agents
    assert ".\\.research\\srh.cmd context" in agents
    assert "context --intent ..." not in agents
    assert "pass an enum value, not a free-form task description" in agents
    assert str(Path(sys.executable).resolve()) in launcher.read_text(encoding="utf-8")

    # A deliberately PATH-isolated shell cannot resolve a generic Python or a
    # global ``srh``.  The generated command still works because it embeds the
    # initializing interpreter and project root, and it is launched outside
    # the repository to exercise that explicit project binding.
    no_tools = tmp_path / "no global tools"
    no_tools.mkdir()
    env = os.environ.copy()
    env["PATH"] = str(no_tools)
    if os.name == "nt":
        (no_tools / "python.cmd").write_text("@exit /b 37\r\n", encoding="utf-8")
        generic = subprocess.run(
            [env.get("COMSPEC", r"C:\\Windows\\System32\\cmd.exe"), "/d", "/s", "/c", "python -m research_harness --help"],
            env=env, text=True, encoding="utf-8", capture_output=True,
        )
    else:
        generic = subprocess.run(
            ["/bin/sh", "-c", "python -m research_harness --help"],
            env=env, text=True, encoding="utf-8", capture_output=True,
        )
    assert generic.returncode != 0

    completed = _run_local_launcher(
        launcher, "doctor", cwd=str(tmp_path), env=env, text=True,
        encoding="utf-8", errors="strict", capture_output=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["project"] == str(project.resolve())


def test_local_runtime_reinit_protects_user_edits_and_upgrades_known_agents_block(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    user_prefix = "# Local user instructions\n\n"
    (project / "AGENTS.md").write_text(user_prefix + _previous_managed_agents_block() + "\n", encoding="utf-8")
    init_project(project)
    agents = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.startswith(user_prefix)
    assert ".research" in agents
    assert "python -m research_harness" not in agents

    launcher = project / RUNTIME_LAUNCHER
    launcher.write_text("user-owned launcher\n", encoding="utf-8")
    with pytest.raises(ProjectError, match="conflicting existing runtime launcher"):
        init_project(project)
    assert launcher.read_text(encoding="utf-8") == "user-owned launcher\n"


def test_reinit_upgrades_only_the_exact_pretemplate_agents_block(tmp_path: Path) -> None:
    prefix, suffix = "# Local user instructions\n\n", "\n# User trailer\n"
    project = tmp_path / "known-pretemplate-block"
    project.mkdir()
    previous = _previous_compact_launcher_managed_agents_block()
    (project / "AGENTS.md").write_text(prefix + previous + suffix, encoding="utf-8")

    init_project(project)
    upgraded = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert upgraded.startswith(prefix)
    assert upgraded.endswith(suffix)
    assert upgraded.count(".\\.research\\srh.cmd context") == 1

    edited_project = tmp_path / "edited-pretemplate-block"
    edited_project.mkdir()
    edited = previous.replace("Treat local notes as notes", "Treat local notes as user-managed material")
    original = prefix + edited + suffix
    (edited_project / "AGENTS.md").write_text(original, encoding="utf-8")

    init_project(edited_project)
    assert (edited_project / "AGENTS.md").read_text(encoding="utf-8") == original


def test_reinit_upgrades_only_the_exact_previous_punctuation_agents_block(tmp_path: Path) -> None:
    prefix, suffix = "# Local user instructions\n\n", "\n# User trailer\n"
    previous = _previous_punctuation_managed_agents_block()
    assert "through a reviewed delta; When" in previous
    assert "then compact check/commit. there is" in previous

    project = tmp_path / "known-punctuation-predecessor"
    project.mkdir()
    (project / "AGENTS.md").write_text(prefix + previous + suffix, encoding="utf-8")
    init_project(project)
    assert (project / "AGENTS.md").read_text(encoding="utf-8") == (
        prefix + managed_agents_block() + suffix
    )

    edited_project = tmp_path / "edited-punctuation-predecessor"
    edited_project.mkdir()
    edited = previous.replace("through a reviewed delta; When", "through a reviewed delta; Then")
    original = prefix + edited + suffix
    (edited_project / "AGENTS.md").write_text(original, encoding="utf-8")
    init_project(edited_project)
    assert (edited_project / "AGENTS.md").read_text(encoding="utf-8") == original


def test_init_refuses_runtime_launcher_symlink_before_store_creation(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    research = project / ".research"
    research.mkdir()
    outside = tmp_path / "outside-launcher"
    outside.write_text("outside", encoding="utf-8")
    launcher = project / RUNTIME_LAUNCHER
    try:
        launcher.symlink_to(outside)
    except OSError:
        pytest.skip("creating symlinks is unavailable in this test environment")
    with pytest.raises(ProjectError, match="outside project root|symlink"):
        init_project(project)
    assert not (research / "knowledge.sqlite3").exists()


def test_malformed_hooks_fail_before_project_setup(tmp_path: Path) -> None:
    project = tmp_path / "bad"
    project.mkdir()
    (project / ".codex").mkdir()
    (project / ".codex" / "hooks.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ProjectError):
        init_project(project, hooks=True)
    assert not (project / ".research").exists()


def test_init_source_repository_guard_does_not_depend_on_documentation(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    (source_root / "src" / "research_harness").mkdir(parents=True)
    (source_root / "pyproject.toml").write_text("[project]\nname='local'\n", encoding="utf-8")
    with pytest.raises(ProjectError, match="source repository"):
        init_project(source_root)
    assert not (source_root / ".research").exists()


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


def test_checkpoint_exports_without_mutating_research_memory_or_recovery_context(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "checkpoint"
    init_project(project)
    root = Path(__file__).resolve().parents[1]
    delta = json.loads((root / "examples" / "workflow-remember-delta.json").read_text(encoding="utf-8"))
    state = json.loads((root / "examples" / "workflow-context-state.json").read_text(encoding="utf-8"))
    with ResearchStore(project) as store:
        store.apply_delta(delta)
        store.update_state(state)
        before_bundle = store.export_bundle()
    before_context = session_start(project)["hookSpecificOutput"]["additionalContext"]

    assert main(["--project", str(project), "checkpoint", "--note", "snapshot only"]) == 0
    checkpoint = json.loads(capsys.readouterr().out)
    assert "decision_id" not in checkpoint
    assert checkpoint["note"] == "snapshot only"
    assert checkpoint["records"] == len(before_bundle["records"])
    assert checkpoint["edges"] == len(before_bundle["edges"])
    exported = json.loads(Path(checkpoint["export"]).read_text(encoding="utf-8"))
    assert exported == before_bundle

    with ResearchStore(project) as store:
        assert store.export_bundle() == before_bundle
    assert session_start(project)["hookSpecificOutput"]["additionalContext"] == before_context


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
    assert "current route (historical, non-directive; may be stale): Test the volatility boundary" in context
    assert "parked alternatives (historical, not rejected): Preparation explanation remains parked" in context
    assert "reopen triggers (historical conditions, non-directive): Reopen preparation" in context
    # The complete related-evidence card is lower priority than saved current
    # work at the 2k SessionStart cap; it must be omitted whole, never leave a
    # label/locator without its scope and excerpt.
    assert "related evidence: evidence:workflow-counterexample" not in context
    assert context.endswith("status: truncated")
    if "recorded next_action (non-driving" in context:
        assert "may be stale" in context

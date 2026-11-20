"""Black-box integration checks for the installed research harness.

These tests intentionally drive the CLI in separate Python processes.  The
hash provider is used for deterministic/offline checks; the optional FastEmbed
smoke is separate and is never treated as a substitute for a failed download.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples" / "evidence-bundle.json"
STATE_FIXTURE = ROOT / "examples" / "state.json"


def _env() -> dict[str, str]:
    env = os.environ.copy()
    src = ROOT / "src"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(src), env.get("PYTHONPATH", "")] if env.get("PYTHONPATH") else [str(src)]
    )
    # Windows subprocesses otherwise inherit a legacy console encoding; the
    # product contract requires Unicode record IDs, text, and paths.
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_srh(project: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run the module entry point, preserving paths containing spaces."""

    command = [sys.executable, "-m", "research_harness", "--project", str(project), *args]
    completed = subprocess.run(
        command,
        cwd=str(ROOT),
        env=_env(),
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        timeout=120,
    )
    if check and completed.returncode:
        raise AssertionError(
            f"command failed ({completed.returncode}): {command}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def json_stdout(completed: subprocess.CompletedProcess[str]) -> dict:
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - failure diagnostic
        raise AssertionError(f"expected JSON stdout, got:\n{completed.stdout}") from exc
    assert isinstance(value, dict)
    return value


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    target = tmp_path / "研究 project with spaces"
    target.mkdir()
    # Existing bytes must survive init's owned-block merge.
    (target / "AGENTS.md").write_text("# Existing instructions\n\nKeep this sentence.\n", encoding="utf-8")
    run_srh(target, "init", str(target), "--question", "新的研究问题")
    return target


def test_init_merge_reinit_and_unicode(project: Path) -> None:
    agents = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert "Keep this sentence." in agents
    before = agents
    run_srh(project, "init", str(project), "--question", "新的研究问题")
    after = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert "Keep this sentence." in after
    # Re-init must not duplicate the owned block indefinitely.
    assert after.count("Scientific Research Harness") <= max(1, before.count("Scientific Research Harness")) + 1


def test_ingest_graph_contradiction_state_context_and_checkpoint(project: Path, tmp_path: Path) -> None:
    ingest = json_stdout(run_srh(project, "ingest", str(FIXTURE)))
    assert ingest.get("inserted_records", ingest.get("inserted", ingest.get("records", 0)))

    graph = json_stdout(run_srh(project, "graph", "hypothesis:cross-domain-bridge", "--hops", "2"))
    records = graph.get("records", [])
    ids = {item.get("id", item.get("record", {}).get("id")) for item in records}
    assert "evidence:source-mechanism" in ids
    assert "evidence:opposing-finding" in ids
    assert graph.get("edges")

    # `state --file` accepts a state patch, not a schema-bearing demo
    # snapshot.  Use the hypothesis already installed by this fixture.
    state_patch = tmp_path / "state-patch.json"
    state_patch.write_text(json.dumps({
        "question": "When is the synthetic cue-gating mechanism transferable across contexts?",
        "active_hypotheses": ["hypothesis:cross-domain-bridge"],
        "unresolved_hypotheses": ["hypothesis:cross-domain-bridge"],
        "recent_decisions": ["decision:retain-opposition"],
        "notes": ["integration"],
    }), encoding="utf-8")
    state = json_stdout(run_srh(project, "state", "--file", str(state_patch)))
    assert "question" in state or state.get("state", {}).get("question")
    context = run_srh(project, "context", "--query", "target toy task", "--max-chars", "5000").stdout
    assert len(context) <= 5000
    assert "Under stable context" in context  # active hypothesis prediction
    assert "source observation and opposing" in context  # recorded decision reason
    assert "abstract, paragraph" in context  # source-bearing evidence locator

    checkpoint = json_stdout(run_srh(project, "checkpoint", "--note", "integration checkpoint"))
    assert checkpoint
    exports = project / ".research" / "exports"
    assert exports.exists()
    assert any(exports.iterdir())


def test_hash_vectors_persist_across_processes_and_are_not_semantic(project: Path) -> None:
    run_srh(project, "ingest", str(FIXTURE))
    index = json_stdout(run_srh(project, "index", "--provider", "hash"))
    provider = index.get("provider") or index.get("index", {}).get("provider")
    assert provider == "hash"

    # This search is a new process and therefore checks persisted sqlite-vec data.
    result = json_stdout(run_srh(project, "search", "cue gating", "--mode", "vector", "--limit", "5"))
    assert result.get("mode") in ("vector", "hybrid")
    assert result.get("results")
    warnings = " ".join(result.get("warnings", []))
    assert "semantic" not in warnings.lower() or "hash" in warnings.lower()


def test_hooks_are_bounded_json_and_offline(project: Path) -> None:
    start = json_stdout(run_srh(project, "hook", "session-start"))
    assert "additionalContext" in start or "context" in start or "additionalContext" in start.get("hookSpecificOutput", {})
    stop = json_stdout(run_srh(project, "hook", "stop"))
    assert isinstance(stop, dict)
    assert "decision:block" not in json.dumps(stop, ensure_ascii=False)


def test_bad_reference_rolls_back_entire_bundle(project: Path, tmp_path: Path) -> None:
    bad = json.loads(FIXTURE.read_text(encoding="utf-8"))
    bad["records"].append(
        {
            "id": "claim:invalid-mixed-bundle",
            "type": "claim",
            "title": "Should not commit",
            "summary": "Invalid evidence reference",
            "tags": [],
            "data": {"origin": "assistant", "status": "supported", "evidence_ids": ["evidence:nope"]},
        }
    )
    bad_path = tmp_path / "bad bundle.json"
    bad_path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    failed = run_srh(project, "ingest", str(bad_path), check=False)
    assert failed.returncode != 0
    shown_proc = run_srh(project, "show", "claim:invalid-mixed-bundle", check=False)
    assert shown_proc.returncode != 0
    # A failed show may be a JSON null/object or a concise text error; either
    # way, the invalid record must not appear in the output.
    assert "record not found" in (shown_proc.stdout + shown_proc.stderr)


def test_fastembed_semantic_smoke_is_explicit_and_separate(project: Path) -> None:
    """Opt-in network/model check; never falls back to hash on failure."""

    if os.environ.get("SRH_RUN_FASTEMBED_SMOKE") != "1":
        pytest.skip("set SRH_RUN_FASTEMBED_SMOKE=1 to authorize the model download")
    run_srh(project, "ingest", str(FIXTURE))
    outcome = run_srh(project, "index", "--provider", "fastembed", "--model", "BAAI/bge-small-en-v1.5", check=False)
    if outcome.returncode:
        pytest.fail("FastEmbed model smoke failed visibly; no hash fallback is acceptable")
    result = json_stdout(run_srh(project, "search", "cue gating", "--mode", "vector"))
    assert result.get("results")
    identity = json.dumps(result, ensure_ascii=False).lower()
    assert "fastembed" in identity or "bge-small" in identity


def test_wheel_contains_packaged_skill_assets(tmp_path: Path) -> None:
    pyproject = ROOT / "pyproject.toml"
    if not pyproject.exists():
        pytest.skip("application agent has not added pyproject.toml yet")
    wheel_dir = tmp_path / "wheel output"
    wheel_dir.mkdir()
    built = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps", "--wheel-dir", str(wheel_dir)],
        cwd=str(ROOT),
        env=_env(),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=180,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    wheels = list(wheel_dir.glob("*.whl"))
    assert wheels
    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()
    assert any("srh-" in name and (name.endswith("SKILL.md") or "skill" in name.lower()) for name in names)

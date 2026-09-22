"""Offline command-level acceptance checks for the Codex working-memory loop.

The fixtures contain synthetic observations only.  These tests exercise saved
state, provenance, bounded recovery, and mechanical maintenance; they make no
claim about network search, model quality, or research efficacy.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from research_harness.store import ResearchStore


ROOT = Path(__file__).resolve().parents[1]
DELTA = ROOT / "examples" / "workflow-remember-delta.json"
STATE = ROOT / "examples" / "workflow-context-state.json"
REFERENCE_ROOT = ROOT / "src" / "research_harness" / "templates" / "skills"


def _env() -> dict[str, str]:
    env = os.environ.copy()
    src = str(ROOT / "src")
    env["PYTHONPATH"] = os.pathsep.join([src, env["PYTHONPATH"]]) if env.get("PYTHONPATH") else src
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _srh(project: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [sys.executable, "-m", "research_harness", "--project", str(project), *args],
        cwd=ROOT,
        env=_env(),
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        timeout=60,
    )
    if check and completed.returncode:
        raise AssertionError(f"srh failed: {args}\nstdout={completed.stdout}\nstderr={completed.stderr}")
    return completed


def _json(process: subprocess.CompletedProcess[str]) -> dict:
    result = json.loads(process.stdout)
    assert isinstance(result, dict)
    return result


def _record(record_id: str, record_type: str, data: dict, *, summary: str = "synthetic seed") -> dict:
    return {
        "id": record_id,
        "type": record_type,
        "title": record_id,
        "summary": summary,
        "tags": ["synthetic"],
        "data": data,
    }


def _reference_delta(skill: str) -> dict:
    text = (REFERENCE_ROOT / skill / "references" / "ingest-schema.md").read_text(encoding="utf-8")
    matches = re.findall(r"```json\s*\n(.*?)\n```", text, flags=re.DOTALL)
    assert len(matches) == 1, skill
    return json.loads(matches[0])


def _preload_for_reference(store: ResearchStore, skill: str) -> None:
    """Supply only the documented prerequisites of dependent reference deltas."""
    if skill == "srh-hypothesis-bridge":
        records = [
            _record("paper:source", "paper", {"read_scope": "abstract"}),
            _record("paper:opposing", "paper", {"read_scope": "abstract"}),
            _record("paper:target", "paper", {"read_scope": "abstract"}),
            _record("evidence:source", "evidence", {"paper_id": "paper:source", "locator": "Abstract, paragraph 1", "excerpt": "Synthetic source observation."}),
            _record("evidence:opposing", "evidence", {"paper_id": "paper:opposing", "locator": "Abstract, paragraph 2", "excerpt": "Synthetic opposing observation."}),
            _record("claim:source", "claim", {"origin": "author", "status": "reported", "evidence_ids": ["evidence:source"], "conditions": [], "alternative_explanations": []}),
        ]
    elif skill == "srh-experiment":
        records = [
            _record("hypothesis:target", "hypothesis", {"prediction": "Synthetic intervention changes outcome.", "alternatives": ["Synthetic alternative."], "test": "Synthetic comparison.", "status": "proposed", "evidence_ids": []}),
        ]
    elif skill == "srh-checkpoint":
        records = [
            _record("paper:counterexample", "paper", {"read_scope": "abstract"}),
            _record("evidence:counterexample", "evidence", {"paper_id": "paper:counterexample", "locator": "Abstract, paragraph 1", "excerpt": "Synthetic counterexample."}),
            _record("hypothesis:target", "hypothesis", {"prediction": "Synthetic intervention changes outcome.", "alternatives": ["Synthetic alternative."], "test": "Synthetic comparison.", "status": "proposed", "evidence_ids": []}),
        ]
    else:
        return
    store.ingest({"schema_version": 1, "records": records, "edges": []})


def test_reference_json_blocks_are_schema_valid_with_their_stated_prerequisites(tmp_path: Path) -> None:
    """Every skill's JSON block remains parseable and admissible by its store contract."""
    for skill in (
        "srh-research-map",
        "srh-paper-read",
        "srh-hypothesis-bridge",
        "srh-experiment",
        "srh-checkpoint",
    ):
        delta = _reference_delta(skill)
        # The checkpoint block deliberately illustrates an optimistic-concurrency
        # placeholder. It is not a literal value users can submit.
        delta.pop("expected_state_hash", None)
        with ResearchStore(tmp_path / skill, create=True) as store:
            _preload_for_reference(store, skill)
            result = store.apply_delta(delta)
            assert result["inserted_records"] or result["updated_records"], skill
        records = {record["id"]: record for record in delta["records"]}
        if skill == "srh-research-map":
            comparison = records["decision:route-comparison"]["data"]["route_comparison"]
            assert all({"bottleneck", "operation", "mechanism", "measurement", "boundary", "decision_changing_gap"} <= set(item) for item in comparison)
        elif skill == "srh-paper-read":
            kinds = {records[record_id]["data"]["evidence_kind"] for record_id in ("evidence:source-methods", "evidence:author-report", "evidence:author-interpretation")}
            assert kinds == {"observation", "author_report", "interpretation"}
        elif skill == "srh-hypothesis-bridge":
            hypothesis = records["hypothesis:target"]["data"]
            assert hypothesis["evidence_roles"]["evidence:target-context"] == "context"
            assert not any(edge["source"] == "evidence:opposing" and edge["target"] == "hypothesis:target" and edge["status"] == "observed" for edge in delta["edges"])
        elif skill == "srh-experiment":
            assert set(records["experiment:target-test"]["data"]["route_changes"]) == {"support", "contradict", "inconclusive", "invalid"}
        elif skill == "srh-checkpoint":
            assert {"current_route", "parked_alternatives", "reopen_triggers"} <= set(delta["state_patch"])


def test_fresh_process_offline_workflow_retains_negative_evidence_and_detects_drift(tmp_path: Path) -> None:
    """A new project can use remember → context → quality without an index/model."""
    project = tmp_path / "workflow project with spaces"
    _srh(project, "init", str(project))
    remembered = _json(_srh(project, "remember", str(DELTA)))
    assert remembered["inserted_records"] >= 8
    assert _json(_srh(project, "doctor"))["stats"]["vectors"] == 0

    # Store a deliberately old next action. Explicit bridge focus must not revive
    # it in a new process's current-task brief.
    _srh(project, "state", "--file", str(STATE))
    brief = _srh(
        project,
        "context", "--intent", "bridge", "--focus", "hypothesis:workflow-transfer",
        "--query", "volatile context", "--max-chars", "6000",
    ).stdout
    assert "intent: bridge" in brief
    assert "hypothesis:workflow-transfer" in brief
    assert "evidence:workflow-counterexample" in brief
    assert "Old saved action:" not in brief
    assert "fastembed" not in brief.casefold()
    # Each command above is a new Python process. No index was requested, and
    # context recovery must leave the persisted vector index empty.
    assert _json(_srh(project, "doctor"))["stats"]["vectors"] == 0

    # A tight cap must signal the omission instead of making a small output look
    # complete. The command output adds only its terminal newline.
    short = _srh(
        project, "context", "--intent", "bridge", "--focus", "hypothesis:workflow-transfer", "--max-chars", "120"
    ).stdout.rstrip("\r\n")
    assert len(short) <= 120
    assert "clipped" in short or "truncated" in short

    # Modify a source in a separate remembered delta. The source change must not
    # erase the counterexample or upgrade the hypothesis; quality should surface
    # downstream review need in another fresh process.
    source = next(record for record in json.loads(DELTA.read_text(encoding="utf-8"))["records"] if record["id"] == "paper:workflow-source")
    source["summary"] = "Revised synthetic source wording; downstream review is now required."
    changed = tmp_path / "changed-source.json"
    changed.write_text(json.dumps({"schema_version": 1, "records": [source], "edges": []}), encoding="utf-8")
    _srh(project, "remember", str(changed))
    quality = _json(_srh(project, "quality", "--focus", "hypothesis:workflow-transfer", "--limit", "50"))
    assert quality["scope"] == "mechanical/advisory; not scientific verification"
    codes = {finding["code"] for finding in quality["findings"]}
    assert {"dependency_basis_drift", "dependency_needs_review"} & codes
    assert all("subject_id" in finding for finding in quality["findings"])
    drift = next(finding for finding in quality["findings"] if finding["code"] == "dependency_basis_drift")
    assert drift["subject_kind"] in {"record", "edge"}
    assert _json(_srh(project, "show", "evidence:workflow-counterexample"))["id"] == "evidence:workflow-counterexample"
    hypothesis = _json(_srh(project, "show", "hypothesis:workflow-transfer"))
    assert hypothesis["data"]["status"] == "proposed"
    assert hypothesis["data"]["assertion_role"] == "transfer_proposal"
    assert hypothesis["data"]["evidence_roles"]["evidence:workflow-target-context"] == "context"
    graph = _json(_srh(project, "graph", "hypothesis:workflow-transfer", "--hops", "2"))
    assert any(edge["id"] == "edge:workflow-counterexample-limits-source" and edge["target"] == "claim:workflow-reported" for edge in graph["edges"])
    assert not any(edge["source"] == "evidence:workflow-counterexample" and edge["target"] == "hypothesis:workflow-transfer" and edge["status"] == "observed" for edge in graph["edges"])


def test_optional_delta_id_replays_only_the_exact_saved_delta(tmp_path: Path) -> None:
    """A retry key prevents duplicate writes without making normal deltas mandatory."""
    delta = {
        "schema_version": 1,
        "delta_id": "acceptance-retry-01",
        "records": [
            _record(
                "paper:retry-source",
                "paper",
                {"read_scope": "abstract"},
                summary="Synthetic record used to verify a durable retry key.",
            )
        ],
        "edges": [],
    }
    with ResearchStore(tmp_path / "retry project", create=True) as store:
        first = store.apply_delta(delta)
        saved_record = store.get_record("paper:retry-source")
        saved_events = store.events(limit=100)
        saved_bundle = store.export_bundle()

        replayed = store.apply_delta(json.loads(json.dumps(delta)))
        assert replayed == {**first, "replayed": True}
        assert store.get_record("paper:retry-source") == saved_record
        assert store.events(limit=100) == saved_events

        conflicting = json.loads(json.dumps(delta))
        conflicting["records"][0]["summary"] = "Different payload must not overwrite the prior retry."
        with pytest.raises(ValueError, match="already used with different content"):
            store.apply_delta(conflicting)
        assert store.export_bundle() == saved_bundle
        assert store.events(limit=100) == saved_events

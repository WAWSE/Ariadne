"""Installation regressions for the generic evidence-transfer/update gate.

These tests verify that the gate is present in Codex-facing instructions and
that a re-init upgrades an exact packaged predecessor without taking ownership
of an edited project file.  They do not claim that an LLM follows the guidance.
"""
from pathlib import Path

import pytest

from research_harness.project import (
    ProjectError,
    _TRANSFER_UPDATE_GATE_AGENTS_INSTRUCTION,
    _TRANSFER_UPDATE_GATE_BRIDGE_SKILL,
    _TRANSFER_UPDATE_GATE_CHECKPOINT_SKILL,
    _TRANSFER_UPDATE_GATE_EXPERIMENT_SKILL,
    _TRANSFER_POINT_UPDATE_GUARD_AGENTS_INSTRUCTION,
    _TRANSFER_POINT_UPDATE_GUARD_BRIDGE_SKILL,
    _TRANSFER_POINT_UPDATE_GUARD_CHECKPOINT_SKILL,
    _TRANSFER_POINT_UPDATE_GUARD_EXPERIMENT_SKILL,
    _FORECAST_UPDATE_RECEIPT_AGENTS_INSTRUCTION,
    _FORECAST_UPDATE_RECEIPT_CHECKPOINT_SKILL,
    _FORECAST_UPDATE_RECEIPT_EXPERIMENT_SKILL,
    init_project,
    install_templates,
)


def test_new_install_contains_transfer_gate_in_codex_entrypoints(tmp_path: Path):
    init_project(tmp_path, hooks=False)
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    experiment = (tmp_path / ".agents/skills/srh-experiment/SKILL.md").read_text(encoding="utf-8")
    bridge = (tmp_path / ".agents/skills/srh-hypothesis-bridge/SKILL.md").read_text(encoding="utf-8")
    checkpoint = (tmp_path / ".agents/skills/srh-checkpoint/SKILL.md").read_text(encoding="utf-8")
    assert _TRANSFER_UPDATE_GATE_AGENTS_INSTRUCTION in agents
    assert _TRANSFER_UPDATE_GATE_EXPERIMENT_SKILL in experiment
    assert _TRANSFER_UPDATE_GATE_BRIDGE_SKILL in bridge
    assert _TRANSFER_UPDATE_GATE_CHECKPOINT_SKILL in checkpoint
    assert _TRANSFER_POINT_UPDATE_GUARD_AGENTS_INSTRUCTION in agents
    assert _TRANSFER_POINT_UPDATE_GUARD_EXPERIMENT_SKILL in experiment
    assert _TRANSFER_POINT_UPDATE_GUARD_BRIDGE_SKILL in bridge
    assert _TRANSFER_POINT_UPDATE_GUARD_CHECKPOINT_SKILL in checkpoint
    assert _FORECAST_UPDATE_RECEIPT_AGENTS_INSTRUCTION in agents
    assert _FORECAST_UPDATE_RECEIPT_EXPERIMENT_SKILL in experiment
    assert _FORECAST_UPDATE_RECEIPT_CHECKPOINT_SKILL in checkpoint


def test_exact_managed_agents_predecessor_is_upgraded(tmp_path: Path):
    init_project(tmp_path, hooks=False)
    target = tmp_path / "AGENTS.md"
    current = target.read_text(encoding="utf-8")
    predecessor = current.replace(_TRANSFER_UPDATE_GATE_AGENTS_INSTRUCTION, "")
    target.write_text(predecessor, encoding="utf-8")
    init_project(tmp_path, hooks=False)
    assert target.read_text(encoding="utf-8") == current

    edited = predecessor.replace("Stored research is", "User wording.\nStored research is", 1)
    target.write_text(edited, encoding="utf-8")
    init_project(tmp_path, hooks=False)
    assert target.read_text(encoding="utf-8") == edited


@pytest.mark.parametrize(
    "relative, marker",
    [
        (".agents/skills/srh-experiment/SKILL.md", _TRANSFER_POINT_UPDATE_GUARD_EXPERIMENT_SKILL),
        (".agents/skills/srh-hypothesis-bridge/SKILL.md", _TRANSFER_POINT_UPDATE_GUARD_BRIDGE_SKILL),
        (".agents/skills/srh-checkpoint/SKILL.md", _TRANSFER_POINT_UPDATE_GUARD_CHECKPOINT_SKILL),
        (".agents/skills/srh-experiment/SKILL.md", _FORECAST_UPDATE_RECEIPT_EXPERIMENT_SKILL),
        (".agents/skills/srh-checkpoint/SKILL.md", _FORECAST_UPDATE_RECEIPT_CHECKPOINT_SKILL),
    ],
)
def test_exact_packaged_skill_predecessor_is_upgraded(tmp_path: Path, relative: str, marker: str):
    init_project(tmp_path, hooks=False)
    target = tmp_path / relative
    current = target.read_text(encoding="utf-8")
    predecessor = current.replace(marker, "")
    target.write_text(predecessor, encoding="utf-8")
    install_templates(tmp_path)
    assert target.read_text(encoding="utf-8") == current

    edited = predecessor + "\nUser-owned transfer policy.\n"
    target.write_text(edited, encoding="utf-8")
    with pytest.raises(ProjectError, match="conflicting existing skill"):
        install_templates(tmp_path)
    assert target.read_text(encoding="utf-8") == edited

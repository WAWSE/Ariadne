from __future__ import annotations

import pytest

from research_harness.project import (
    _RESULT_REASSESSMENT_AGENTS_INSTRUCTION,
    _RESULT_REASSESSMENT_CHECKPOINT_SKILL,
    _RESULT_REASSESSMENT_EXPERIMENT_SKILL,
    ProjectError,
    init_project,
    managed_agents_block,
)


def test_init_writes_result_reassessment_instructions_and_upgrades_only_owned_predecessors(tmp_path) -> None:
    project = tmp_path / "project"
    init_project(project, hooks=False)
    agents_path = project / "AGENTS.md"
    agents = agents_path.read_text(encoding="utf-8")
    experiment = project / ".agents" / "skills" / "srh-experiment" / "SKILL.md"
    checkpoint = project / ".agents" / "skills" / "srh-checkpoint" / "SKILL.md"
    assert _RESULT_REASSESSMENT_AGENTS_INSTRUCTION.strip() in agents
    assert _RESULT_REASSESSMENT_EXPERIMENT_SKILL.strip() in experiment.read_text(encoding="utf-8")
    assert _RESULT_REASSESSMENT_CHECKPOINT_SKILL.strip() in checkpoint.read_text(encoding="utf-8")
    assert "including stop/defer when allowed" in agents
    assert "do not select another action or observation" in experiment.read_text(encoding="utf-8")

    # Exact package-owned prior text may migrate; an altered skill remains user
    # content and must still fail instead of being silently overwritten.
    agents_path.write_text(managed_agents_block().replace(_RESULT_REASSESSMENT_AGENTS_INSTRUCTION, ""), encoding="utf-8")
    experiment.write_text(experiment.read_text(encoding="utf-8").replace(_RESULT_REASSESSMENT_EXPERIMENT_SKILL, ""), encoding="utf-8")
    checkpoint.write_text(checkpoint.read_text(encoding="utf-8").replace(_RESULT_REASSESSMENT_CHECKPOINT_SKILL, ""), encoding="utf-8")
    init_project(project, hooks=False)
    assert _RESULT_REASSESSMENT_AGENTS_INSTRUCTION.strip() in agents_path.read_text(encoding="utf-8")
    assert _RESULT_REASSESSMENT_EXPERIMENT_SKILL.strip() in experiment.read_text(encoding="utf-8")
    assert _RESULT_REASSESSMENT_CHECKPOINT_SKILL.strip() in checkpoint.read_text(encoding="utf-8")

    experiment.write_text(experiment.read_text(encoding="utf-8").replace("condition-bound evidence", "user-edited evidence", 1), encoding="utf-8")
    with pytest.raises(ProjectError, match="conflicting existing skill file"):
        init_project(project, hooks=False)

"""Narrow forecast guidance upgrades owned templates, never custom policies.

These are installation/migration tests, not behavioral evidence that an LLM's
scientific forecasts improve. The motivating exact witnesses live outside
the product in the archived routing01 evaluator.
"""
from pathlib import Path

import pytest

from research_harness.project import (
    _COMPARATOR_PREDICTION_EXPERIMENT_SKILL,
    _QUANTITATIVE_DECISION_EXPERIMENT_SKILL,
    _RESULT_REASSESSMENT_EXPERIMENT_SKILL,
    ProjectError,
    init_project,
    install_templates,
)


@pytest.mark.parametrize('remove_comparator,remove_reassessment', [(True, False), (True, True), (False, True)])
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_exact_owned_predecessors_upgrade_and_user_edits_survive(
    tmp_path: Path, remove_comparator: bool, remove_reassessment: bool, newline: str,
):
    init_project(tmp_path, hooks=False)
    target = tmp_path / '.agents/skills/srh-experiment/SKILL.md'
    current = target.read_bytes()
    predecessor = current.decode('utf-8').replace('\r\n', '\n')
    if remove_comparator:
        predecessor = predecessor.replace(_COMPARATOR_PREDICTION_EXPERIMENT_SKILL, '')
    if remove_reassessment:
        predecessor = predecessor.replace(_RESULT_REASSESSMENT_EXPERIMENT_SKILL, '')
    target.write_bytes(predecessor.replace('\n', newline).encode('utf-8'))
    install_templates(tmp_path)
    assert target.read_bytes() == current
    # No changes to a separately authored reference, even though a packaged
    # SKILL predecessor is eligible for a safe upgrade.
    reference = target.parent / 'references/user-protocol.txt'
    reference.write_text('User owns the scientific protocol.\n', encoding='utf-8')
    custom = (predecessor + '\nUser-specific comparator definition.\n').replace('\n', newline).encode('utf-8')
    target.write_bytes(custom)
    with pytest.raises(ProjectError, match='conflicting existing skill'):
        install_templates(tmp_path)
    assert target.read_bytes() == custom
    assert reference.read_text(encoding='utf-8') == 'User owns the scientific protocol.\n'


def test_new_install_contains_complete_single_forecast_block(tmp_path: Path):
    init_project(tmp_path, hooks=False)
    target = tmp_path / '.agents/skills/srh-experiment/SKILL.md'
    text = target.read_text(encoding='utf-8')
    assert text.count(_COMPARATOR_PREDICTION_EXPERIMENT_SKILL) == 1
    before = target.read_bytes()
    init_project(tmp_path, hooks=False)
    assert target.read_bytes() == before


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_quantitative_guidance_upgrades_only_the_exact_owned_predecessor(tmp_path: Path, newline: str):
    """Installation safety, not an assertion that a model checks its arithmetic."""
    init_project(tmp_path, hooks=False)
    target = tmp_path / '.agents/skills/srh-experiment/SKILL.md'
    current = target.read_bytes()
    text = current.decode('utf-8').replace('\r\n', '\n')
    assert text.count(_QUANTITATIVE_DECISION_EXPERIMENT_SKILL) == 1
    old = text.replace(_QUANTITATIVE_DECISION_EXPERIMENT_SKILL, '')
    database = tmp_path / '.research/knowledge.sqlite3'
    unchanged_database = database.read_bytes()
    target.write_bytes(old.replace('\n', newline).encode('utf-8'))
    install_templates(tmp_path)
    assert target.read_bytes() == current
    assert database.read_bytes() == unchanged_database
    custom = (old + '\nUser-owned decision precision policy.\n').replace('\n', newline).encode('utf-8')
    target.write_bytes(custom)
    with pytest.raises(ProjectError, match='conflicting existing skill'):
        install_templates(tmp_path)
    assert target.read_bytes() == custom
    assert database.read_bytes() == unchanged_database

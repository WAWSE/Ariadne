"""External measurements remain attributed even when the file/check is local."""
from copy import deepcopy
from pathlib import Path

import pytest

from research_harness.cli import _expand_evidence_remember
from research_harness.project import (
    _EVIDENCE_ORIGIN_CHECKPOINT_SKILL, _LEGACY_EVIDENCE_ORIGIN_CHECKPOINT_SKILL,
    ProjectError, init_project, install_templates,
)
from research_harness.store import ResearchStore


def test_exact_pre_origin_guidance_upgrades_without_touching_saved_science(tmp_path):
    init_project(tmp_path, hooks=False)
    target = tmp_path / '.agents/skills/srh-checkpoint/SKILL.md'
    current = target.read_bytes()
    predecessor = current.decode('utf-8').replace('\r\n', '\n').replace(
        _EVIDENCE_ORIGIN_CHECKPOINT_SKILL, _LEGACY_EVIDENCE_ORIGIN_CHECKPOINT_SKILL,
    )
    with ResearchStore(tmp_path) as store:
        before = deepcopy(store.export_bundle())
    target.write_bytes(predecessor.replace('\n', '\r\n').encode('utf-8'))
    install_templates(tmp_path)
    assert target.read_bytes() == current
    with ResearchStore(tmp_path) as store:
        assert store.export_bundle() == before
    edited = (predecessor + '\nUser-owned measurement protocol.\n').encode('utf-8')
    target.write_bytes(edited)
    with pytest.raises(ProjectError, match='conflicting existing skill'):
        install_templates(tmp_path)
    assert target.read_bytes() == edited


def test_local_file_location_does_not_merge_report_reanalysis_or_unknown_origin(tmp_path):
    init_project(tmp_path, hooks=False)
    specs = (
        ('report', 'author_report', 'Imported archive, raw rows: 10,14; external measurements.'),
        ('calculation', 'local_observation', 'analysis.py: mean([10,14])=12; no new measurements.'),
        ('unattributed', 'unknown', 'Supplied rows without acquisition provenance.'),
    )
    with ResearchStore(tmp_path) as store:
        for name, kind, text in specs:
            unit = {
                'id': 'evidence:' + name, 'title': name,
                'source': {'id': 'source:' + name, 'title': name + '.json', 'kind': 'local_file'},
                'locator': name + '.json::rows', 'excerpt': text, 'evidence_kind': kind,
                'applicability': {'domain': 'source assay', 'conditions': {'setting': 'original'}},
            }
            store.apply_delta(_expand_evidence_remember(unit, store))
        before = deepcopy(store.export_bundle())
    # Cold reopen keeps the authored distinction; filename/kind cannot supply
    # provenance, manufacture an independent experiment, or relabel old data.
    with ResearchStore(tmp_path) as store:
        for name, kind, text in specs:
            evidence = store.get_record('evidence:' + name)
            assert evidence['data']['evidence_kind'] == kind
            assert evidence['data']['excerpt'] == text
            assert evidence['data']['source_id'] == 'source:' + name
        assert store.list_records('experiment') == []
        assert store.list_records('claim') == []
        assert store.export_bundle() == before

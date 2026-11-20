"""Recorded scope equality is not a model's scientific compatibility judgment."""
from copy import deepcopy

import pytest

from research_harness.context import build_context
from research_harness.semantics import (
    applicability_compatibility,
    applicability_domain,
    is_qualified_assertion_evidence,
    is_qualified_observed_limiting_relation,
)
from research_harness.store import ResearchStore


def pair(evidence_scope, target_scope):
    evidence = {'id': 'evidence:measurement', 'type': 'evidence', 'title': 'Measurement', 'summary': '', 'tags': [],
                'data': {'source_id': 'source:readings', 'locator': 'readings.csv row 2',
                         'excerpt': 'A conditional measured gain; mechanism unresolved.',
                         'evidence_kind': 'local_observation', 'applicability': evidence_scope}}
    target = {'id': 'hypothesis:target', 'type': 'hypothesis', 'title': 'Target prediction', 'summary': '', 'tags': [],
              'data': {'status': 'supported', 'prediction': 'Gain under the named conditions.', 'test': 'A discriminating contrast.',
                       'alternatives': ['Measurement artifact'], 'evidence_ids': [evidence['id']],
                       'evidence_roles': {evidence['id']: 'support'}, 'applicability': target_scope}}
    return evidence, target


@pytest.mark.parametrize('left,right', [
    ({'domain': 'replication project means', 'conditions': {}},
     {'domain': '跨项目复现均值', 'conditions': {}}),
    ({'domain': 'assay', 'task_context': 'held-out paired assay', 'conditions': {}},
     {'domain': 'assay', 'task_context': 'paired assay on held-out cases', 'conditions': {}}),
    ({'domain': 'assay', 'conditions': {'notes': 'paired held-out measurements'}},
     {'domain': 'assay', 'conditions': {'notes': 'held-out paired measurements'}}),
    ({'domain': 'assay', 'conditions': {'dose': 2}},
     {'domain': 'assay', 'conditions': {'dose': 3}}),
])
def test_different_fields_are_not_claimed_semantically_compatible_or_conflicting(left, right):
    evidence, target = pair(left, right)
    before = deepcopy((evidence, target))
    assert applicability_compatibility(evidence, target) == 'different'
    assert not is_qualified_assertion_evidence(target, evidence)
    assert (evidence, target) == before


@pytest.mark.parametrize('scope', [
    {'domain': 'unknown', 'conditions': {}},
    {'domain': ' UNKNOWN ', 'conditions': {}},
    {'domain': 'target', 'task_context': 'unknown', 'conditions': {}},
    {'domain': 'target', 'conditions': {'protocol': 'unknown'}},
    {'domain': 'target', 'conditions': {'protocol': None}},
    {'domain': 'target', 'conditions': {'protocol': ' '}},
    {'domain': 'target', 'conditions': {'protocol': {'name': 'unknown'}}},
    {'domain': 'target', 'conditions': {'protocols': ['known', 'unknown']}},
])
def test_equal_unknown_fields_do_not_mint_target_support(scope):
    evidence, target = pair(scope, deepcopy(scope))
    assert applicability_compatibility(evidence, target) == 'unknown'
    assert not is_qualified_assertion_evidence(target, evidence)


@pytest.mark.parametrize('conditions', [{}, {'disabled': False}, {'level': 0}, {'protocols': ['A', 'B']}])
def test_known_exact_scope_keeps_existing_evidence_qualification(conditions):
    scope = {'domain': 'target', 'conditions': conditions}
    evidence, target = pair(scope, deepcopy(scope))
    assert applicability_compatibility(evidence, target) == 'match'
    assert is_qualified_assertion_evidence(target, evidence)


@pytest.mark.parametrize('scope,comparison', [
    ({'source_domain': 'source assay', 'target_domain': 'target assay', 'conditions': {}}, 'different'),
    ({'domain': 'source assay', 'target_domain': 'target assay', 'conditions': {}}, 'different'),
    ({'target_domain': 'target assay', 'conditions': {}}, 'unknown'),
    ({'domain': 'unknown', 'source_domain': 'target assay', 'conditions': {}}, 'unknown'),
])
def test_intended_transfer_target_never_relabels_observed_source_domain(scope, comparison):
    evidence, target = pair(scope, {'target_domain': 'target assay', 'conditions': {}})
    assert applicability_compatibility(evidence, target) == comparison
    assert not is_qualified_assertion_evidence(target, evidence)
    assert applicability_domain(evidence) != 'target assay'


def test_explicit_source_scope_can_be_compared_without_inventing_target_observation():
    evidence, target = pair({'source_domain': 'source assay', 'target_domain': 'future assay', 'conditions': {}},
                            {'target_domain': 'source assay', 'conditions': {}})
    assert applicability_domain(evidence) == 'source assay'
    assert applicability_compatibility(evidence, target) == 'match'


def test_present_difference_survives_missing_sibling_without_claiming_scientific_conflict():
    evidence, target = pair({'domain': 'target', 'conditions': {'split': {'seed': 8}}},
                            {'conditions': {'split': {'seed': 7, 'fold': 0}}})
    assert applicability_compatibility(evidence, target) == 'different'


def test_quality_and_context_report_recorded_difference_without_rewriting_science(tmp_path):
    evidence, target = pair({'domain': 'synthetic measurement study', 'conditions': {}},
                            {'domain': 'synthetic assay study', 'conditions': {}})
    source = {'id': 'source:readings', 'type': 'source', 'title': 'Readings', 'summary': '', 'tags': [],
              'data': {'kind': 'local_file'}}
    edge = {'id': 'edge:opposition', 'source': evidence['id'], 'target': target['id'],
            'relation': 'contradicts', 'status': 'observed', 'evidence_ids': [evidence['id']],
            'evidence_roles': {evidence['id']: 'counterevidence'}, 'conditions': {}}
    with ResearchStore(tmp_path, create=True) as store:
        store.apply_delta({'schema_version': 1, 'records': [source, evidence, target], 'edges': [edge],
                           'state_patch': {'active_hypotheses': [target['id']]}})
        before, events = store.export_bundle(), store.events(100)
        report = store.quality(target['id'])
        warning = next(f for f in report['findings'] if f['code'] == 'edge_evidence_applicability_different')
        assert 'recorded' in warning['message'] and 'not scientific incompatibility' in warning['message']
        assert 'conflicts with' not in warning['message']
        assert not is_qualified_observed_limiting_relation(edge, target, evidence)
        context = build_context(store, max_chars=6000)
        assert 'recorded_scope=different' in context
        assert 'saved values differ; scientific applicability not assessed' in context
        assert 'scope=mismatch' not in context and 'target=mismatch' not in context
        focused = build_context(store, focus=[target['id']], intent='bridge', max_chars=6000)
        assert '--contradicts/observed-->' in focused
        assert 'observed boundary path:' not in context
        assert store.export_bundle() == before and store.events(100) == events


@pytest.mark.parametrize('evidence_scope,target_scope', [
    ({'domain': 'aqueous catalysis on carbon-supported metal', 'conditions': {}},
     {'domain': 'aqueous reaction on metal/C', 'conditions': {}}),
    ({'domain': 'assay', 'conditions': {'duration': '60 s'}},
     {'domain': 'assay', 'conditions': {'duration': '1 min'}}),
    ({'domain': 'assay', 'conditions': {'dose': 2}},
     {'domain': 'assay', 'conditions': {'dose': 3}}),
])
def test_scope_display_does_not_infer_synonyms_units_or_scientific_incompatibility(evidence_scope, target_scope):
    from research_harness.context import (
        _evidence_semantic_suffix, _focused_evidence_semantics,
        _resume_evidence_semantics,
    )
    evidence, target = pair(evidence_scope, target_scope)
    before = deepcopy((evidence, target))
    rendered = (
        _evidence_semantic_suffix(evidence, target),
        _focused_evidence_semantics(evidence, target),
        _resume_evidence_semantics(evidence, target, evidence_role='support', evidence_id=evidence['id']),
    )
    for text in rendered:
        assert 'recorded_scope=different' in text
        assert 'saved values differ; scientific applicability not assessed' in text
    # A display clarification is not an automatic mapping or evidence upgrade.
    assert applicability_compatibility(evidence, target) == 'different'
    assert not is_qualified_assertion_evidence(target, evidence)
    assert (evidence, target) == before

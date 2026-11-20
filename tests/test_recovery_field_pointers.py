"""Recovery omissions point to the actual owner, not unrelated global state."""
from research_harness.context import (
    _resume_evidence_card, _resume_recovery_lines, _state_field, build_context,
)
from test_context_workflow import StubStore


def test_long_decision_reason_has_recoverable_record_pointer_not_state():
    reason = 'Scoped rationale with retained contrary evidence. ' * 30
    decision = {'id': 'decision:conditional', 'type': 'decision',
                'title': 'Current conditional route', 'data': {'reason': reason}}
    state = {'recent_decisions': [decision['id']]}
    store = StubStore([decision], [], state)
    lines = '\n'.join(_resume_recovery_lines(store, state))
    assert 'srh show decision:conditional' in lines
    assert '[long decision reason omitted; inspect `srh state`]' not in lines
    # The suggested record actually holds the complete omitted rationale.
    assert store.get_record('decision:conditional')['data']['reason'] == reason
    for size in (1800, 4500, 8000):
        brief = build_context(store, max_chars=size)
        assert '[long decision reason omitted; inspect `srh state`]' not in brief
        if 'long decision reason omitted' in brief:
            assert 'srh show decision:conditional' in brief


def test_long_hypothesis_fields_point_to_the_hypothesis():
    data = {'status': 'proposed', 'prediction': 'Prediction retained as authored. ' * 30,
            'test': 'Discriminating test. ' * 30, 'conditions': 'Scoped setting. ' * 30,
            'failure_risks': 'Unresolved condition. ' * 30}
    hypothesis = {'id': 'hypothesis:route', 'type': 'hypothesis', 'title': 'Route', 'data': data}
    state = {'active_hypotheses': [hypothesis['id']]}
    store = StubStore([hypothesis], [], state)
    lines = '\n'.join(_resume_recovery_lines(store, state))
    for field in ('prediction', 'test', 'conditions', 'failure_risks'):
        assert f'[long {field} omitted; inspect with `srh show hypothesis:route`]' in lines
        assert store.get_record(hypothesis['id'])['data'][field] == data[field]


def test_long_evidence_locator_points_to_evidence_and_state_still_points_to_state():
    evidence = {'id': 'evidence:local', 'type': 'evidence', 'data': {
        'locator': 'Long source paragraph locator. ' * 30,
        'excerpt': 'A condition-bound reported observation.', 'evidence_kind': 'author_report'}}
    hypothesis = {'id': 'hypothesis:route', 'type': 'hypothesis', 'data': {}}
    card = _resume_evidence_card(evidence, hypothesis, label='evidence', evidence_role='context')
    assert 'srh show evidence:local' in card
    assert '[long locator omitted; inspect `srh state`]' not in card
    assert _state_field('saved global state ' * 30, field='notes', limit=40) == '[long notes omitted; inspect `srh state`]'

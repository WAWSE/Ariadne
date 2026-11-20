"""A bounded graph neighborhood is not an exhausted research-route search."""
from pathlib import Path

import pytest

from research_harness.context import build_context
from research_harness.store import ResearchStore


def record(key, record_type, **data):
    return {'id': key, 'type': record_type, 'title': key, 'summary': '', 'tags': [], 'data': data}


def edge(key, source, target, relation='uses', **extra):
    return {'id': key, 'source': source, 'target': target, 'relation': relation,
            'status': 'hypothesized', 'evidence_ids': [], 'conditions': {}, **extra}


def seed(store):
    store.apply_delta({'schema_version': 1, 'records': [
        record('problem:target', 'problem'),
        record('mechanism:separation', 'mechanism'),
        record('technique:coupling', 'technique'),
        record('method:alternative', 'method'),
        record('source:report', 'source', kind='synthetic report'),
        record('evidence:boundary', 'evidence', source_id='source:report',
               locator='Results, condition B', evidence_kind='author_report',
               applicability={'domain': 'source context B'},
               excerpt='The reported effect failed in condition B; target transfer remains unknown.'),
    ], 'edges': [
        edge('edge:problem', 'mechanism:separation', 'problem:target', 'addresses'),
        edge('edge:technique', 'mechanism:separation', 'technique:coupling'),
        edge('edge:alternative', 'method:alternative', 'technique:coupling', 'analogous_to',
             conditions={'target_transfer': 'unknown'}),
        edge('edge:boundary', 'evidence:boundary', 'technique:coupling', 'limits',
             status='reported', evidence_ids=['evidence:boundary'],
             evidence_roles={'evidence:boundary': 'counterevidence'},
             applicability={'domain': 'source context B'},
             conditions={'condition': 'source context B only; not target validation'}),
    ]})


def test_two_hop_route_reports_a_real_frontier_without_inventing_a_conflict(tmp_path: Path):
    with ResearchStore(tmp_path, create=True) as store:
        seed(store)
        before = store.export_bundle()
        graph = store.graph('problem:target', hops=2)
        ids = {r['id'] for r in graph['records']}
        assert 'technique:coupling' in ids
        assert 'method:alternative' not in ids and 'evidence:boundary' not in ids
        assert graph['truncated'] is False  # no entity cap was hit
        assert graph['boundary']['continuation_ids'] == ['technique:coupling']
        assert graph['boundary']['more_connections'] == 'known'
        assert graph['boundary']['hops'] == 2
        brief = build_context(store, intent='map', focus=['problem:target'], max_chars=6500)
        assert 'graph boundary: saved connections continue beyond this 2-hop view' in brief
        assert 'srh context --intent map --focus technique:coupling' in brief
        assert 'observed limiting/contradicting evidence is present' not in brief
        expanded = build_context(store, intent='map', focus=['technique:coupling'], max_chars=10000)
        assert 'method:alternative' in expanded and 'evidence:boundary' in expanded
        assert 'counterevidence' in expanded
        assert 'source context B' in expanded
        assert store.export_bundle() == before  # navigation is not a graph repair
        resumed = build_context(store, intent='resume', focus=['problem:target'], max_chars=6500)
        assert 'graph boundary:' not in resumed
        assert 'method:alternative' not in resumed
    with ResearchStore(tmp_path) as store:
        restored = store.graph('technique:coupling', hops=1)
        boundary = next(e for e in restored['edges'] if e['id'] == 'edge:boundary')
        assert boundary['evidence_roles'] == {'evidence:boundary': 'counterevidence'}
        assert boundary['target'] == 'technique:coupling'
        assert boundary['status'] == 'reported'


def test_saturated_saved_component_is_not_called_scientifically_complete(tmp_path: Path):
    with ResearchStore(tmp_path, create=True) as store:
        seed(store)
        graph = store.graph('problem:target', hops=5)
        assert len(graph['records']) == 6
        assert graph['boundary']['more_connections'] == 'none_in_loaded_graph'
        assert graph['boundary']['continuation_ids'] == []
        assert 'scientifically_complete' not in graph['boundary']
        assert store.graph('missing', hops=2)['boundary']['more_connections'] == 'unknown'


def test_limit_boundary_is_bounded_and_distinct_from_hop_limit(tmp_path: Path):
    with ResearchStore(tmp_path, create=True) as store:
        seed(store)
        graph = store.graph('problem:target', hops=5, limit=2)
        assert graph['truncated'] is True
        assert len(graph['records']) == 2
        assert graph['boundary']['more_connections'] == 'known'
        assert len(graph['boundary']['continuation_ids']) <= 8


def test_graph_follows_evidence_usage_on_an_assertion_edge(tmp_path: Path):
    with ResearchStore(tmp_path, create=True) as store:
        seed(store)
        usage = edge('edge:attributed-usage', 'claim:interpretation', 'mechanism:separation',
                     'proposes_mechanism', status='reported',
                     evidence_ids=['evidence:boundary'],
                     evidence_roles={'evidence:boundary': 'context'})
        store.apply_delta({'schema_version': 1,
                           'records': [record('claim:interpretation', 'claim', origin='author', status='proposed')],
                           'edges': [usage]})
        graph = store.graph('evidence:boundary', hops=1)
        assert {'claim:interpretation', 'mechanism:separation'} <= {r['id'] for r in graph['records']}
        saved = next(e for e in graph['edges'] if e['id'] == usage['id'])
        assert saved['evidence_roles']['evidence:boundary'] == 'context'
        assert saved['target'] == 'mechanism:separation'


@pytest.mark.parametrize('hops', [0, 1, 2])
def test_boundary_never_changes_the_requested_radius(tmp_path: Path, hops):
    with ResearchStore(tmp_path, create=True) as store:
        seed(store)
        graph = store.graph('problem:target', hops=hops)
        assert len(graph['records']) == hops + 1
        assert graph['boundary']['hops'] == hops


def test_partial_backing_graph_cannot_claim_exhaustion(tmp_path: Path, monkeypatch):
    with ResearchStore(tmp_path, create=True) as store:
        root = record('problem:target', 'problem')
        monkeypatch.setattr(store, '_quality_focus_payloads', lambda *a, **kw: ([root], [], True))
        graph = store.graph(root['id'], hops=2)
        assert graph['truncated'] is True
        assert graph['boundary']['more_connections'] == 'unknown'
        assert graph['boundary']['continuation_ids'] == []


def test_continuation_metadata_is_bounded_without_raising_entity_limit(tmp_path: Path):
    with ResearchStore(tmp_path, create=True) as store:
        records = [record('problem:root', 'problem')]
        edges = []
        for i in range(12):
            mid, tip, outside = f'method:mid{i:02}', f'technique:tip{i:02}', f'method:outside{i:02}'
            records += [record(mid, 'method'), record(tip, 'technique'), record(outside, 'method')]
            edges += [edge(f'edge:a{i:02}', mid, 'problem:root', 'addresses'),
                      edge(f'edge:b{i:02}', mid, tip), edge(f'edge:c{i:02}', outside, tip)]
        store.apply_delta({'schema_version': 1, 'records': records, 'edges': edges})
        graph = store.graph('problem:root', hops=2, limit=80)
        assert len(graph['records']) == 25
        assert graph['truncated'] is False
        assert graph['boundary']['continuation_ids_truncated'] is True
        assert graph['boundary']['continuation_ids'] == [f'technique:tip{i:02}' for i in range(8)]

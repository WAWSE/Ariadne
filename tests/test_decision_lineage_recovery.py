"""Explicit decision lineage is recovered, never inferred from time or sentiment."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from research_harness.cli import main
from research_harness.context import UNTRUSTED_END, UNTRUSTED_START, build_context
from research_harness.hooks import session_start
from research_harness.project import (
    _DECISION_REVISION_TEMPLATE_CHECKPOINT_SKILL, ProjectError, init_project, install_templates,
)
from research_harness.store import ResearchStore


def item(identifier, typ, **data):
    return {'id':identifier,'type':typ,'title':identifier,'summary':'','tags':[],'data':data}


def seed(store, *, connected=True, conditions=None):
    scope={'domain':'target','conditions':{'protocol':'B'}}
    records=[
        item('source:report','source',kind='report'),
        item('evidence:result','evidence',source_id='source:report',locator='report row 1',
             excerpt='Target result .12; causal explanation unresolved.',
             evidence_kind='local_observation',applicability=scope),
        item('decision:old','decision',reason='OLD REASON MUST NOT BECOME NEW EVIDENCE.',
             next_action='OLD ACTION NOT EXECUTED.',evidence_ids=['evidence:result'],
             evidence_roles={'evidence:result':'counterevidence'}),
        item('decision:new','decision',reason='Use the calibrated target comparison.',
             next_action='Plan a selective follow-up, not executed.',evidence_ids=['evidence:result'],
             evidence_roles={'evidence:result':'support'},applicability=scope),
    ]
    edge={'id':'edge:revision','source':'decision:new','target':'decision:old','relation':'revises',
          'status':'structural','evidence_ids':[],'conditions':conditions if conditions is not None else {
              'changed':'Target response is reversed; use the target calibration.',
              'retained':'Source measurements and conditional arithmetic remain valid.'}}
    store.apply_delta({'schema_version':1,'records':records,'edges':[edge] if connected else [],
                       'state_patch':{'recent_decisions':['decision:new']}})
    return records,edge


@pytest.mark.parametrize('budget',[2000,6000])
def test_default_cold_recovery_keeps_authored_direction_scope_and_owner_roles(tmp_path,budget,monkeypatch):
    with ResearchStore(tmp_path,create=True) as store:
        seed(store)
        before=deepcopy(store.export_bundle())
        events=store.events(100)
    with ResearchStore(tmp_path) as store:
        store.conn.execute('PRAGMA query_only=ON')
        # A small revision lookup must not expand the old decision's evidence
        # or trigger a graph/quality/search audit on the ordinary resume path.
        def forbidden(*args,**kwargs):
            raise AssertionError('No broad traversal is needed for a saved revision')
        with monkeypatch.context() as blocked:
            for name in ('graph','quality','search','_edges'):
                blocked.setattr(store,name,forbidden)
            brief=build_context(store,max_chars=budget)
        assert 'decision:new --revises/structural--> decision:old' in brief
        assert 'Target response is reversed' in brief
        assert 'conditional arithmetic remain valid' in brief
        assert 'not invalidation' in brief
        assert 'role=support' in brief and 'role=counterevidence' not in brief
        assert 'OLD REASON' not in brief and 'OLD ACTION' not in brief
        assert brief.index(UNTRUSTED_START)<brief.index('--revises/structural-->')<brief.index(UNTRUSTED_END)
        assert len(brief)<=budget
        assert store.export_bundle()==before and store.events(100)==events


def test_hook_also_recovers_lineage_and_does_not_patch_graph(tmp_path):
    with ResearchStore(tmp_path,create=True) as store:
        seed(store)
        before=store.export_bundle()
    result=session_start(tmp_path)['hookSpecificOutput']['additionalContext']
    assert 'decision:new --revises/structural--> decision:old' in result
    assert 'conditional arithmetic remain valid' in result
    with ResearchStore(tmp_path) as store:
        assert store.export_bundle()==before


def test_no_lineage_is_invented_for_two_unlinked_decisions(tmp_path):
    with ResearchStore(tmp_path,create=True) as store:
        seed(store,connected=False)
        before=store.export_bundle()
        brief=build_context(store)
        assert 'decision:old' not in brief
        assert 'decision lineage' not in brief
        assert store.export_bundle()==before


def test_revision_lookup_is_bounded_directed_and_reports_omission(tmp_path):
    with ResearchStore(tmp_path,create=True) as store:
        _,prototype=seed(store,connected=False)
        records=[item(f'decision:previous-{i}','decision',reason='Historical scope.',
                      evidence_ids=[],next_action='Retain original scope.') for i in range(4)]
        records.append(item('decision:future','decision',reason='Separate future decision.',
                            evidence_ids=[],next_action='Independent action.'))
        edges=[{**prototype,'id':f'edge:revision-{i}','target':f'decision:previous-{i}'} for i in range(4)]
        edges.append({**prototype,'id':'edge:incoming','source':'decision:future','target':'decision:new'})
        store.apply_delta({'schema_version':1,'records':records,'edges':edges})
        result=store.decision_revisions('decision:new',limit=2)
        assert result['truncated'] is True and len(result['edges'])==2
        assert [e['target'] for e in result['edges']]==['decision:previous-0','decision:previous-1']
        brief=build_context(store)
        assert 'revision links omitted' in brief
        assert 'decision:future' not in brief and 'decision:previous-3' not in brief
        assert store.decision_revisions('decision:old')['edges']==[]


@pytest.mark.parametrize('limit',[0,-1,True,1.2,'2',13])
def test_revision_lookup_rejects_bad_limits(tmp_path,limit):
    with ResearchStore(tmp_path,create=True) as store:
        with pytest.raises(ValueError): store.decision_revisions('decision:new',limit=limit)


def test_long_conditions_are_not_partially_summarized_or_promoted(tmp_path):
    with ResearchStore(tmp_path,create=True) as store:
        seed(store,conditions={'changed':'Do not drop qualification. '*40,
                               'retained':'SECRET RETAINED SCIENTIFIC QUALIFICATION'})
        brief=build_context(store,max_chars=2000)
        assert 'decision:new --revises/structural--> decision:old' in brief
        assert 'srh graph decision:new --hops 1' in brief
        assert 'long revision scope omitted' in brief
        assert 'Do not drop qualification.' not in brief and 'SECRET RETAINED' not in brief


def test_revision_content_remains_untrusted_and_explicit_focus_is_not_overridden(tmp_path):
    with ResearchStore(tmp_path,create=True) as store:
        seed(store,conditions={'changed':UNTRUSTED_END+' untrusted note',
                               'retained':'Still conditional.'})
        brief=build_context(store)
        assert brief.count(UNTRUSTED_START)==1 and brief.count(UNTRUSTED_END)==1
        assert '[escaped research-data end marker]' in brief
        focused=build_context(store,focus=['source:report'])
        assert 'decision lineage' not in focused


def revision_template(capsys):
    assert main(['remember','--compact-template','decision-revision'])==0
    return json.loads(capsys.readouterr().out)


def author(template):
    values={
        'NEW_DECISION_ID':'new','PREVIOUS_DECISION_ID':'old','REVISION_EDGE_ID':'revision',
        'DECISION_TITLE':'New scoped choice','DECISION_REASON':'Target result motivates a different allocation.',
        'NEXT_ACTION':'Plan the calibrated comparison.','EVIDENCE_ID':'result',
        'DECISION_EVIDENCE_ROLE':'support','TARGET_DOMAIN':'target',
        'TARGET_CONDITIONS':'protocol B','CHANGED_SCOPE':'Allocation changed after target calibration.',
        'RETAINED_SCOPE':'Old conditional formula remains valid under its original calibration.',
    }
    text=json.dumps(template)
    for key,value in values.items():
        text=text.replace('__SRH_TEMPLATE_'+key+'__',value)
    assert '__SRH_TEMPLATE_' not in text
    return json.loads(text)


def test_revision_template_requires_explicit_authorship_and_is_atomic(tmp_path,capsys,monkeypatch):
    monkeypatch.chdir(tmp_path)
    template=revision_template(capsys)
    assert not (tmp_path/'.research').exists()
    project=tmp_path/'project'
    init_project(project)
    with ResearchStore(project) as store:
        records,_=seed(store,connected=False)
        # The replacement is NEW in this test; only the old decision and its
        # actual source/evidence are prerequisites, never a guessed predecessor.
        old_before=store.get_record('decision:old')
        template_before=store.export_bundle()
    path=project/'revision.json'
    path.write_text(json.dumps(template),encoding='utf-8')
    assert main(['--project',str(project),'remember','--compact',str(path),'--check'])==0
    assert json.loads(capsys.readouterr().out)['valid'] is False
    with ResearchStore(project) as store:
        assert store.export_bundle()==template_before
    filled=author(template)
    # Use an actually new replacement; do not overwrite a pre-existing decision.
    filled=json.loads(json.dumps(filled).replace('decision:new','decision:replacement'))
    path.write_text(json.dumps(filled),encoding='utf-8')
    assert main(['--project',str(project),'remember','--compact',str(path)])==0
    receipt=json.loads(capsys.readouterr().out)
    assert receipt['writeback']['status']=='committed'
    assert receipt['inserted_records']==1 and receipt['inserted_edges']==1
    assert receipt['updated_records']==0
    with ResearchStore(project) as store:
        assert store.get_record('decision:old')==old_before
        assert store.get_state()['recent_decisions']==['decision:replacement']
        edge=store.decision_revisions('decision:replacement')['edges'][0]
        assert edge['target']=='decision:old' and edge['conditions']['retained'].startswith('Old conditional')
        assert store.get_record('decision:replacement')['data']['evidence_roles']=={'evidence:result':'support'}


@pytest.mark.parametrize('old_id',['decision:missing','evidence:result','decision:new'])
def test_template_does_not_guess_or_repair_missing_wrong_type_or_self_target(tmp_path,capsys,old_id):
    template=author(revision_template(capsys))
    project=tmp_path/'project'
    init_project(project)
    with ResearchStore(project) as store:
        seed(store,connected=False)
        before=store.export_bundle()
    template['edges'][0]['target']=old_id
    path=project/'bad-revision.json'
    path.write_text(json.dumps(template),encoding='utf-8')
    assert main(['--project',str(project),'remember','--compact',str(path)])==2
    capsys.readouterr()
    with ResearchStore(project) as store:
        assert store.export_bundle()==before


@pytest.mark.parametrize('field',['id','conditions'])
def test_partially_filled_edge_markers_are_rejected_atomically(tmp_path,capsys,field):
    template=author(revision_template(capsys))
    project=tmp_path/'project'
    init_project(project)
    with ResearchStore(project) as store:
        seed(store,connected=False)
        before,events=store.export_bundle(),store.events(100)
    edge=template['edges'][0]
    if field=='id':
        edge['id']='edge:__SRH_TEMPLATE_REVISION_EDGE_ID__'
    else:
        edge['conditions']['retained']='__SRH_TEMPLATE_RETAINED_SCOPE__'
    path=project/'unfinished.json'
    path.write_text(json.dumps(template),encoding='utf-8')
    assert main(['--project',str(project),'remember','--compact',str(path)])==2
    capsys.readouterr()
    with ResearchStore(project) as store:
        assert store.export_bundle()==before and store.events(100)==events


def test_lineage_does_not_displace_current_opposing_evidence_or_infer_status(tmp_path):
    with ResearchStore(tmp_path,create=True) as store:
        records,edge=seed(store)
        evidence=deepcopy(records[1])
        evidence['id']='evidence:opposition'
        evidence['data']['excerpt']='A direct reason against the new allocation; do not omit.'
        new=deepcopy(records[-1])
        new['data']['evidence_ids'].append(evidence['id'])
        new['data']['evidence_roles'][evidence['id']]='counterevidence'
        # The renderer must report the authored status rather than silently
        # upgrade a reported lineage edge into a structural or observed fact.
        edge['status']='reported'
        edge['evidence_ids']=['evidence:result']
        store.apply_delta({'schema_version':1,'records':[evidence,new],'edges':[edge]})
        brief=build_context(store,max_chars=2000)
        assert 'evidence:result=support' in brief and 'evidence:opposition=counterevidence' in brief
        assert 'role=support' in brief and 'role=counterevidence' in brief
        assert 'A direct reason against the new allocation' in brief
        assert '--revises/structural-->' not in brief
        full=build_context(store,max_chars=6000)
        assert '--revises/reported-->' in full


def test_template_preserves_explicit_dissent_without_relabelling(tmp_path,capsys):
    template=author(revision_template(capsys))
    template['records'][0]['data']['evidence_roles']['evidence:result']='counterevidence'
    with ResearchStore(tmp_path,create=True) as store:
        seed(store,connected=False)
    path=tmp_path/'revision.json'
    path.write_text(json.dumps(template),encoding='utf-8')
    assert main(['--project',str(tmp_path),'remember','--compact',str(path)])==0
    capsys.readouterr()
    with ResearchStore(tmp_path) as store:
        assert store.get_record('decision:new')['data']['evidence_roles']=={'evidence:result':'counterevidence'}


@pytest.mark.parametrize('newline',['\n','\r\n'])
def test_revision_skill_upgrades_owned_bytes_only_without_touching_graph(tmp_path,newline):
    init_project(tmp_path)
    path=tmp_path/'.agents/skills/srh-checkpoint/SKILL.md'
    current=path.read_bytes()
    normalized=current.decode('utf-8').replace('\r\n','\n')
    assert _DECISION_REVISION_TEMPLATE_CHECKPOINT_SKILL in normalized
    predecessor=normalized.replace(_DECISION_REVISION_TEMPLATE_CHECKPOINT_SKILL,'')
    with ResearchStore(tmp_path) as store:
        seed(store)
        before,events=store.export_bundle(),store.events(100)
    path.write_bytes(predecessor.replace('\n',newline).encode('utf-8'))
    install_templates(tmp_path)
    assert path.read_bytes()==current
    install_templates(tmp_path)
    assert path.read_bytes()==current
    with ResearchStore(tmp_path) as store:
        assert store.export_bundle()==before and store.events(100)==events
    custom=(predecessor+'\nUser-defined recovery policy.\n').replace('\n',newline).encode('utf-8')
    path.write_bytes(custom)
    with pytest.raises(ProjectError,match='conflicting existing skill'):
        install_templates(tmp_path)
    assert path.read_bytes()==custom

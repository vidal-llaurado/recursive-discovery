"""Tests for telemetry recording, redaction and failure handling."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import json
from pathlib import Path
import re
import sqlite3
import sys

import pytest

from recursive_discovery import telemetry
from recursive_discovery.core import Task, frontier
from recursive_discovery.context import compile_context, ContextOverflow
from recursive_discovery.agenda import available_work, choose_work
from recursive_discovery.investigation import create_mission, open_branch, include, set_attention, Scope, request
from recursive_discovery.runtime import Runtime
from recursive_discovery.session import research_session
from recursive_discovery.schema import define_schema
from recursive_discovery.search import multi_search, acquire_source


def recorded_rows(directory, table):
    database = (Path(directory) / 'events.sqlite3').resolve().as_uri() + '?mode=ro'
    with sqlite3.connect(database, uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(f'SELECT * FROM {table}')]


def events(directory, name=None):
    out = []
    for row in sorted(recorded_rows(directory, 'events'), key=lambda row: row['seq']):
        if name is not None and row['name'] != name:
            continue
        row['attributes'] = json.loads(row['attributes'])
        if row['payload'] is None:
            row.pop('payload')
        else:
            row['payload'] = json.loads(row['payload'])
        out.append(row)
    return out


def ids(ledger):
    return {a.id for a in ledger.all()}


@pytest.mark.parametrize('capture', ['metadata', 'content'])
def test_records_do_not_enter_ledger_or_rewrite_evidence(world, capture):
    ledger, kernel, _, root = world
    claim = ledger.put('claim', {'statement': 'A claim'})
    ev = kernel.run(ledger, claim, 'math', [sys.executable, '-c', "print('hello')"])
    before, raw = ids(ledger), json.dumps(ev.data, sort_keys=True)
    pending = {(t.verb,t.target) for t in frontier(ledger,kernel)}
    with telemetry.observe(root/'ops', ledger=ledger, capture=capture):
        compile_context(ledger, kernel, Task('verify', claim.id, 'check'))
    assert ids(ledger) == before
    assert json.dumps(ledger.get(ev.id).data, sort_keys=True) == raw and kernel.verify(ev)
    assert {(t.verb,t.target) for t in frontier(ledger,kernel)} == pending
    assert events(root/'ops', 'artifact.snapshot')
    assert not any(a.kind.startswith('telemetry') for a in ledger.all())


def test_disabled_is_noop_does_not_evaluate_payload(tmp_path):
    called=[]
    telemetry.emit('ignore', payload=lambda: called.append(1))
    with telemetry.span('nothing'):
        assert not telemetry.enabled()
    assert not called and not list(tmp_path.iterdir())


@pytest.mark.parametrize('capture', ['metadata', 'content'])
def test_metadata_and_content_redaction(tmp_path, capture):
    secret='Bearer VERYSECRETABC'
    request=json.dumps({'api_key':'ALSOSECRET','context':json.dumps({'password':'SECRETPASS','text':secret}), 'statement':'private scientific thought'})
    with telemetry.observe(tmp_path/'ops', capture=capture):
        assert telemetry.call_model(lambda p: p, request) == request
        telemetry.emit('test', {'authorization':secret}, payload=lambda:{'credentials':'X','env':{'A':'B'}, '_sig':'SIGNATURE'})
    text=json.dumps(events(tmp_path/'ops'))
    for value in ['VERYSECRETABC','ALSOSECRET','SECRETPASS','SIGNATURE']:
        assert value not in text
    if capture == 'metadata':
        assert 'private scientific thought' not in text
    else:
        assert 'private scientific thought' in text and 'content_redacted' in text


def test_payload_capture_limit_is_explicit_and_not_prefix(tmp_path):
    with telemetry.observe(tmp_path/'ops',capture='content',max_payload_bytes=50):
        telemetry.emit('large',payload=lambda:{'text':'x'*1000})
    e=events(tmp_path/'ops','large')[0]
    assert e['capture']=='omitted_payload_limit' and 'payload' not in e


def test_errors_are_recorded_and_original_exception_survives(tmp_path):
    with telemetry.observe(tmp_path/'ops'):
        with pytest.raises(ValueError,match='sensitive message'):
            with telemetry.span('job'):
                raise ValueError('sensitive message')
    es=events(tmp_path/'ops')
    assert 'sensitive message' not in json.dumps(es)
    assert any(e['phase']=='end' and e['attributes']['status']=='ERROR' for e in es)
    assert any(e['name']=='exception' and e['attributes']['error_type']=='ValueError' for e in es)


def test_broken_sink_fails_open_even_with_warnings_as_errors(tmp_path):
    path=tmp_path/'not_a_directory';path.write_text('x')
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        with telemetry.observe(path) as observer:
            assert telemetry.call_model(lambda x:x, 'untouched')=='untouched'
    assert observer.failures > 0 and observer.dropped > 0


def test_sink_failure_after_start_is_visible(tmp_path):
    with telemetry.observe(tmp_path/'ops') as observer:
        observer.db.close()
        assert telemetry.call_model(lambda _: 'result', 'request')=='result'
    assert observer.failures > 0


def test_quotas_report_drops(tmp_path):
    with telemetry.observe(tmp_path/'ops', max_events=2) as observer:
        for _ in range(10): telemetry.emit('event')
    assert observer.dropped > 0
    assert recorded_rows(tmp_path/'ops', 'health')[0]['dropped'] == observer.dropped


def test_invalid_configuration(tmp_path):
    with pytest.raises(ValueError): telemetry.Recorder(tmp_path, capture='unsafe')
    with pytest.raises(ValueError): telemetry.Recorder(tmp_path, max_events=0)


def test_trace_identity_parentage_and_scoping(tmp_path):
    with telemetry.observe(tmp_path/'ops'):
        with telemetry.span('mission', mission_id='m') as parent:
            with telemetry.span('tool') as child:
                assert child['trace_id']==parent['trace_id']
                assert child['parent_span_id']==parent['span_id']
                telemetry.emit('result')
    es=events(tmp_path/'ops')
    assert all(re.fullmatch('[0-9a-f]{32}',e['trace_id']) for e in es)
    assert all(re.fullmatch('[0-9a-f]{16}',e['span_id']) for e in es)
    assert events(tmp_path/'ops','result')[0]['mission_id']=='m'
    assert not telemetry.enabled()


def test_nested_observers_restore_parent(tmp_path):
    with telemetry.observe(tmp_path/'outer'):
        telemetry.emit('one')
        with telemetry.observe(tmp_path/'inner'): telemetry.emit('two')
        telemetry.emit('three')
    assert not events(tmp_path/'outer','two')
    assert len(events(tmp_path/'outer','three'))==1


def test_async_contexts_do_not_cross_parents(tmp_path):
    async def task(n):
        with telemetry.span('async', mission_id=str(n)):
            await asyncio.sleep(0)
            telemetry.emit('async.result')
    async def run(): await asyncio.gather(task(1),task(2))
    with telemetry.observe(tmp_path/'ops'): asyncio.run(run())
    es=events(tmp_path/'ops','async.result')
    assert {e['mission_id'] for e in es}=={'1','2'}
    assert len({e['span_id'] for e in es})==2


def test_threads_can_propagate_context_explicitly(tmp_path):
    with telemetry.observe(tmp_path/'ops'):
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures=[pool.submit(copy_context().run,telemetry.emit,'thread.event',{'n':i}) for i in range(12)]
            for f in futures: f.result()
    assert len(events(tmp_path/'ops','thread.event'))==12


def test_fake_evidence_never_gets_observer_verification(world):
    ledger, _, _, root=world
    with telemetry.observe(root/'ops'):
        ledger.put('evidence',{'_sig':'fake','verdict':'pass','returncode':0})
    e=events(root/'ops','artifact.append')[0]
    assert e['attributes']['verification']=='not_checked_by_observer'
    assert '_sig' not in e['attributes']


def test_negative_execution_is_not_an_exception_or_truth_verdict(world):
    ledger,kernel,_,root=world
    target=ledger.put('claim',{'statement':'x'})
    with telemetry.observe(root/'ops',ledger=ledger):
        evidence=kernel.run(ledger,target,'math',[sys.executable,'-c',"raise SystemExit(2)"])
    assert kernel.verify(evidence)
    es=events(root/'ops')
    assert any(e['name']=='execution' and e['phase']=='end' and e['attributes']['status']=='OK' for e in es)
    assert next(e for e in es if e['artifact_id']==evidence.id)['attributes']['execution_verdict']=='fail'


def test_action_observation_budget_and_real_request_match(world):
    ledger,kernel,_,root=world
    source=ledger.put('source_text',{'text':'A necessary observation. '*1500})
    target=ledger.put('study',{'question':'Find a condition'}, {'sources':[source.id]})
    prompts=[]
    def model(p):
        prompts.append(p)
        return json.dumps({'action':'read','id':source.id,'query':'necessary','length':700}) if len(prompts)==1 else json.dumps({'action':'finalize','candidates':[],'activate':[]})
    with telemetry.observe(root/'ops',ledger=ledger,capture='content'):
        research_session(ledger,kernel,Task('explore',target.id,'Inspect'),model,root=root,max_turns=3,context_chars=8000)
    delivered=events(root/'ops','context.delivered')
    requests=events(root/'ops','model.request')
    assert len(delivered)==len(prompts)==2
    for e,p in zip(delivered,prompts):
        a=e['attributes']
        assert a['request_chars']==len(p)<=8000
        assert sum(a['component_chars'].values())+a['envelope_chars']==len(p)
        assert 'trace_id' not in p and 'telemetry' not in json.loads(p)['actions']
    assert any(x.get('id')==source.id and 'start' in x for x in delivered[1]['attributes']['exposures'])
    assert requests[0]['capture']=='content_redacted'
    assert all(e['attributes'].get('session_id') for e in delivered)
    assert events(root/'ops','action.decision') and events(root/'ops','decision.outcome')


def test_context_omission_diagnostics_do_not_enter_packet(world):
    ledger,kernel,_,root=world
    target=ledger.put('study',{'question':'Relevant result'})
    for i in range(12): ledger.put('note',{'text':'Relevant result '+str(i)+' long '*80},{'target':[target.id]})
    with telemetry.observe(root/'ops'):
        packet=compile_context(ledger,kernel,Task('explore',target.id,'Relevant result'),max_chars=2500)
    audit=events(root/'ops','context.selection')[0]['attributes']
    assert any(x['disposition']=='budget_omitted' for x in audit['candidates'])
    assert 'candidates' not in packet
    assert set(audit['selected_ids'])=={x['id'] for x in packet['artifacts']}


def test_required_overflow_is_observed_not_changed(world):
    ledger,kernel,_,root=world
    large=ledger.put('note',{'text':'X'*10000})
    mission=create_mission(ledger,'Mission',required=[large.id])
    with telemetry.observe(root/'ops'):
        with pytest.raises(ContextOverflow):
            compile_context(ledger,kernel,Task('explore',mission.id,'Work'),scope=Scope(ledger,mission.id),max_chars=2000)
    assert any(e['attributes'].get('error_type')=='ContextOverflow' for e in events(root/'ops'))


def test_routing_generated_suppressed_shown_selected(world):
    ledger,kernel,_,root=world
    mission=create_mission(ledger,'Live question')
    branch=open_branch(ledger,mission.id,'Paused branch')
    a=ledger.children(mission.id,'request','mission')[0]
    request(ledger,mission.id,'Dormant question',branch=branch.id)
    set_attention(ledger,branch.id,mission.id,'dormant',expected=[],reason='pause')
    scope=Scope(ledger,mission.id)
    with telemetry.observe(root/'ops',ledger=ledger):
        agenda=available_work(ledger,kernel,scope)
        chosen=choose_work(ledger,kernel,scope,lambda p:json.dumps({'key':json.loads(p)['items'][0]['key'],'reason':'inspect'}),work=agenda['eligible'])
    assert chosen.request==a.id
    entry=events(root/'ops','agenda.candidates')[0]['attributes']
    assert entry['eligible_count']==1 and entry['suppressed_count']==1
    assert entry['suppressed'][0]['reason']=='branch_not_active'
    assert events(root/'ops','agenda.choice')[0]['attributes']['key']==chosen.key
    assert events(root/'ops','agenda.page')


def test_provider_failures_cache_hits_and_query_privacy(world,monkeypatch):
    ledger,_,_,root=world
    import recursive_discovery.search as search
    monkeypatch.setattr(search,'arxiv',lambda *a,**k: (_ for _ in ()).throw(OSError('secret query')))
    monkeypatch.setattr(search,'openalex',lambda *a,**k: [])
    monkeypatch.setattr(search,'crossref',lambda *a,**k: [])
    source=ledger.put('source',{'url':'https://example.test/private','title':'Research'})
    with telemetry.observe(root/'ops'):
        assert multi_search('private scientific query')==[]
        a=acquire_source(ledger,source,root=root,fetch=lambda _:b'plain text')
        b=acquire_source(ledger,source,root=root,fetch=lambda _: (_ for _ in ()).throw(AssertionError('cache should be used')))
    assert a.id==b.id
    es=events(root/'ops');raw=json.dumps(es)
    assert 'private scientific query' not in raw and 'secret query' not in raw and 'example.test' not in raw
    assert [e['attributes']['hit'] for e in es if e['name']=='source.cache']==[False,True]
    assert {e['attributes']['status'] for e in es if e['name']=='search.provider_result'}=={'error','empty'}


def test_usage_is_unknown_until_explicitly_reported(tmp_path):
    with telemetry.observe(tmp_path/'ops'):
        telemetry.call_model(lambda p:'ok','prompt')
        assert not events(tmp_path/'ops', 'model.usage')
        with telemetry.span('model.call'):
            telemetry.usage(input_tokens=100,output_tokens=20,cost=.2,currency='USD')
    usage=events(tmp_path/'ops', 'model.usage')[0]['attributes']
    assert usage['input_tokens']==100 and usage['output_tokens']==20
    assert usage['cost']==.2 and usage['currency']=='USD'
    with pytest.raises(ValueError): telemetry.usage(input_tokens=-1)
    with pytest.raises(ValueError): telemetry.usage(cost=float('nan'),currency='USD')


def test_cli_telemetry_options(tmp_path):
    from recursive_discovery import cli
    args=cli.parser().parse_args(['--telemetry-dir',str(tmp_path),'run','world'])
    assert args.telemetry_dir==str(tmp_path)
    assert args.command=='run'


@pytest.mark.skipif(not hasattr(__import__('os'),'O_NOFOLLOW'),reason='OS lacks no-follow flag')
def test_telemetry_will_not_follow_database_symlink(tmp_path):
    science=tmp_path/'science.sqlite3';science.write_text('do not touch')
    ops=tmp_path/'ops';ops.mkdir();(ops/'events.sqlite3').symlink_to(science)
    with telemetry.observe(ops) as recorder: telemetry.emit('x')
    assert recorder.failures and science.read_text()=='do not touch'


def test_live_health_records_progress_before_close(tmp_path):
    with telemetry.observe(tmp_path/'ops') as recorder:
        telemetry.emit('sample')
        health=recorded_rows(tmp_path/'ops', 'health')[0]
        assert health['closed']==0 and health['written']==recorder.written and health['written']>0


def test_locked_sink_is_bounded_and_does_not_fail_model_call(tmp_path):
    with telemetry.observe(tmp_path/'ops') as recorder:
        lock=sqlite3.connect(tmp_path/'ops/events.sqlite3')
        lock.execute('BEGIN IMMEDIATE')
        try:
            assert telemetry.call_model(lambda p:'unchanged','request')=='unchanged'
        finally:
            lock.rollback();lock.close()
        assert recorder.dropped>0


def test_redactor_does_not_capture_scientific_state_in_metadata(world):
    ledger,_,_,root=world
    with telemetry.observe(root/'ops'):
        ledger.put('note',{'state':'sensitive scientific state','text':'sensitive text'})
    assert 'sensitive' not in json.dumps(events(root/'ops'))


def test_observation_does_not_change_prepared_model_request(world):
    from recursive_discovery.actions import ActionContext
    from recursive_discovery.instruments import Workbench
    from recursive_discovery.session import _prepare_request
    ledger,kernel,_,root=world
    target=ledger.put('study',{'question':'Inspect this result'})
    ctx=ActionContext(ledger,kernel,Task('explore',target.id,'Inspect'),root,Workbench(ledger,kernel,root=root),None,None,None,18000,3)
    before=ids(ledger)
    p0=_prepare_request(ctx,0,[])[1]
    with telemetry.observe(root/'ops',ledger=ledger):
        p1=_prepare_request(ctx,0,[])[1]
    assert p1==p0 and ids(ledger)==before


def test_empty_usage_call_does_not_invent_a_report(tmp_path):
    with telemetry.observe(tmp_path/'ops'):
        telemetry.usage()
    assert not events(tmp_path/'ops', 'model.usage')


def test_live_health_updates_when_payload_metadata_exceeds_limits(tmp_path):
    with telemetry.observe(tmp_path/'ops') as recorder:
        telemetry.emit('oversized_metadata', {'not_a_payload': 'x'*260_000})
        assert recorded_rows(tmp_path/'ops', 'health')[0]['dropped']==recorder.dropped==1

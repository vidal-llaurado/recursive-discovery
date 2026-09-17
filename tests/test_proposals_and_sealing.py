from pathlib import Path
import json
import sys

import pytest

from recursive_discovery import (
    Kernel, Ledger, Task, Vault, activate, authority, before, candidate_set,
    commit, compile_context, evaluate, register_test,
)


def test_candidate_set_searches_then_commits_distinct_candidates(tmp_path: Path):
    l = Ledger(tmp_path/'x.jsonl'); k = Kernel(tmp_path/'key')
    s = l.put('study', {'question':'q'})
    calls = []
    def model(prompt):
        req = json.loads(prompt); calls.append(req['mode'])
        if req['mode'] == 'search_queries':
            return json.dumps({'queries':['q1']})
        ctx = json.loads(req['context'])
        src = [a['id'] for a in ctx['artifacts'] if a['kind']=='source']
        return json.dumps({'candidates':[
            {'object_kind':'claim','title':'A','commitment':'H1','predictions':['p1'],
             'falsifier':'f1','assumptions':[],'source_ids':src,'gain':.8,'cost':1},
            {'object_kind':'claim','title':'A duplicate','commitment':'H1','predictions':['p1'],
             'falsifier':'f1','assumptions':[],'source_ids':src,'gain':.7,'cost':1},
            {'object_kind':'claim','title':'B','commitment':'H2','predictions':['p2'],
             'falsifier':'f2','assumptions':[],'source_ids':src,'gain':.6,'cost':1},
        ]})
    def retrieve(q):
        return [{'provider':'arxiv','id':'x','title':'Paper','abstract':'a','authors':['A'],
                 'published':'2025-01-01','updated':'2025-01-01','categories':[],
                 'url':'u','pdf':''}]
    cs = candidate_set(l,k,Task('explain',s.id,'why'),model,retriever=retrieve,
                       source_before='2026-01-01')
    assert calls == ['search_queries','candidate_set']
    assert [c.data['commitment'] for c in cs] == ['H1','H2']
    assert cs[0].refs['sources']
    live = activate(l, cs[0])
    assert live.kind == 'claim' and live.data['statement'] == 'H1'


def test_historical_cutoff_filters_future_sources_from_retrieval_and_context(tmp_path: Path):
    assert [r['title'] for r in before([
        {'title':'old','published':'2025-01-01'},
        {'title':'new','published':'2030-01-01'},
    ], '2026-01-01')] == ['old']

    l=Ledger(tmp_path/'x.jsonl'); k=Kernel(tmp_path/'key')
    s=l.put('study',{'question':'q'})
    old=l.put('source',{'title':'old','published':'2025-01-01'},{'target':[s.id]})
    new=l.put('source',{'title':'new','published':'2030-01-01'},{'target':[s.id]})
    p=compile_context(l,k,Task('explain',s.id,'x'),source_before='2026-01-01')
    ids={a['id'] for a in p['artifacts']}
    assert old.id in ids and new.id not in ids


def test_sealed_test_is_one_shot_and_grounded(tmp_path: Path):
    l=Ledger(tmp_path/'x.jsonl'); k=Kernel(tmp_path/'key'); v=Vault(tmp_path/'vault')
    proposal=l.put('claim',{'statement':'x'})
    meta=v.seal_json([{'x':1,'y':2}])
    test=register_test(l,meta)
    c=commit(l,proposal,test,metric='score',decision='score == 1')
    script=tmp_path/'eval.py'
    script.write_text("import json,sys\nrows=json.load(open(sys.argv[1]))\nprint(json.dumps({'score':1,'passed':True}))\n")
    tool=l.put('tool',{'lane':'empirical','argv':[sys.executable,str(script),'{sealed}'],
                       'inputs':[str(script),'{sealed}']})
    e,r=evaluate(l,k,tool,c,v,cwd=tmp_path)
    assert k.verify(e)
    assert r is not None and authority(r,l,k)=='derived_from_consequence'
    assert v.metadata(meta['handle'])['retired'] is True
    with pytest.raises(RuntimeError):
        evaluate(l,k,tool,c,v,cwd=tmp_path)
    # Secret rows are not stored in the test artifact.
    assert 'x' not in json.dumps(test.data)

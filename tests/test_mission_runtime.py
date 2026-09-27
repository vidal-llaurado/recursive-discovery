import json
import sys
import pytest

from recursive_discovery.core import Task, frontier
from recursive_discovery.runtime import Runtime
from recursive_discovery.session import research_session
from recursive_discovery.investigation import (
    create_mission, Scope, open_branch, include, request, attention, set_attention,
    revise_mission, Conflict,
)
from recursive_discovery.capsules import load_capsule
from recursive_discovery.context import compile_context, render_context


def selecting(req):
    return {'key': req['items'][0]['key'], 'reason': 'Pursue this question'}


def test_mission_runtime_does_not_stop_at_empty_frontier(world):
    l, k, _, root = world
    m = create_mission(l, 'Investigate an open question')
    calls = []
    def model(raw):
        assert len(raw) <= 18000
        req = json.loads(raw); calls.append(req['mode'])
        if req['mode'] == 'choose_research_work':
            return json.dumps(selecting(req))
        return json.dumps({'action': 'finalize', 'candidates': [], 'activate': []})
    result = Runtime(l, k, root=root).run(mission=m.id, model=model)
    assert result['status'] == 'idle'
    assert calls == ['choose_research_work', 'research_session']
    assert attention(l, l.all('request')[0].id, m.id)['state'] == 'done'
    assert not frontier(l, k)


def test_session_can_originate_work_without_frontier_motif(world):
    l, k, _, root = world
    m = create_mission(l, 'Begin an investigation')
    first_request = l.all('request')[0].id
    seen = set()
    def model(raw):
        req = json.loads(raw)
        if req['mode'] == 'choose_research_work':
            return json.dumps(selecting(req))
        target = req['task']['target']
        if target == first_request and target not in seen:
            seen.add(target)
            return json.dumps({'action': 'request', 'question': 'An unexpected new question', 'targets': []})
        seen.add(target)
        return json.dumps({'action': 'finalize', 'candidates': [], 'activate': []})
    result = Runtime(l, k, root=root).run(mission=m.id, model=model, max_steps=5)
    assert result['status'] == 'idle'
    requests = l.all('request')
    assert len(requests) == 2
    assert requests[1].data['origin'] == 'explore'
    assert len(seen) == 2


def test_explicit_empty_activation_preserves_proposals(world):
    l, k, _, root = world
    m = create_mission(l, 'Consider alternatives')
    def model(raw):
        req = json.loads(raw)
        if req['mode'] == 'choose_research_work':
            return json.dumps(selecting(req))
        return json.dumps({'action': 'finalize', 'candidates': [
            {'object_kind': 'claim', 'commitment': 'A conjecture'}], 'activate': []})
    result = Runtime(l, k, root=root).run(mission=m.id, model=model)
    assert result['status'] == 'idle'
    assert len(l.all('candidate')) == 1 and not l.all('claim')


def test_session_capsule_handoff_uses_the_actual_blob_store(world):
    l, k, blobs, root = world
    m = create_mission(l, 'A question for external investigation')
    def model(raw):
        req = json.loads(raw)
        if req['mode'] == 'choose_research_work':
            return json.dumps(selecting(req))
        return json.dumps({'action': 'capsule', 'max_chars': 15000})
    result = Runtime(l, k, root=root).run(mission=m.id, model=model)
    assert result['status'] == 'handoff'
    manifest, text = load_capsule(l, blobs, result['capsule'])
    assert manifest['mission'] == m.id and m.data['objective'] in text
    assert attention(l, l.all('request')[0].id, m.id)['state'] == 'deferred'


def test_model_cannot_relax_tool_allowlist(world):
    l, k, _, root = world
    m = create_mission(l, 'No execution', policy={'tool_ids': []})
    target = l.all('request')[0]
    prompts = []
    def model(raw):
        req = json.loads(raw); prompts.append(req)
        if len(prompts) == 1:
            assert req['instruments'] == []
            return json.dumps({'action': 'define_instrument', 'name': 'bypass', 'inputs': ['x'],
                               'expression': 'x+1', 'tests': []})
        assert 'approval' in json.dumps(req['recent_observations'])
        return json.dumps({'action': 'finalize', 'candidates': [], 'activate': []})
    result = research_session(l, k, Task('investigate', target.id, 'Question'), model,
                              root=root, scope=Scope(l, m.id))
    assert result['status'] == 'no_candidates'
    assert not l.all('evidence') and not l.all('instrument_definition')


def test_scope_change_between_selection_and_execution_blocks_dispatch(world):
    l, k, _, root = world
    m = create_mission(l, 'Original scope')
    def model(raw):
        req = json.loads(raw)
        revise_mission(l, m.id, expected=[m.id], objective='Changed scope')
        return json.dumps(selecting(req))
    with pytest.raises(Conflict):
        Runtime(l, k, root=root).run(mission=m.id, model=model)
    assert not l.all('evidence')


def test_signed_experiment_runs_in_native_runtime(world):
    l, k, _, root = world
    tool = l.put('tool', {'name': 'a declared protocol', 'lane': 'empirical',
                          'argv': [sys.executable, '-c', 'print("observed")'], 'inputs': []})
    exp = l.put('experiment', {'protocol': 'Measure'}, {'tool': [tool.id]})
    m = create_mission(l, 'Run the protocol', targets=[exp.id], policy={'tool_ids': [tool.id]})
    def model(raw):
        req = json.loads(raw)
        if req['mode'] == 'choose_research_work':
            item = next((x for x in req['items'] if x['verb'] == 'run'), req['items'][0])
            return json.dumps({'key': item['key']})
        return json.dumps({'action': 'finalize', 'candidates': [], 'activate': []})
    result = Runtime(l, k, root=root).run(mission=m.id, model=model)
    assert result['status'] == 'idle'
    evidence = l.children(exp.id, 'evidence', 'target')
    assert len(evidence) == 1 and k.verify(evidence[0])
    assert evidence[0].data['stdout'].strip() == 'observed'


def test_denied_source_does_not_leak_through_inspection(world):
    l, k, _, root = world
    secret = l.put('source', {'title': 'DO_NOT_EXPOSE', 'published': '2030-01-01'})
    m = create_mission(l, 'A historical question', policy={'source_before': '2020-01-01'})
    target = l.all('request')[0]
    calls = 0
    def model(raw):
        nonlocal calls
        calls += 1
        assert 'DO_NOT_EXPOSE' not in raw
        if calls == 1:
            return json.dumps({'action': 'inspect', 'ids': [secret.id]})
        return json.dumps({'action': 'finalize', 'candidates': [], 'activate': []})
    research_session(l, k, Task('investigate', target.id, 'Historical'), model,
                     root=root, scope=Scope(l, m.id))
    assert calls == 2


def test_inspected_source_reference_survives_outside_compiled_packet(world):
    l, k, _, root = world
    source = l.put('source', {'title': 'Zyxuv Unique Source', 'published': '2020-01-01'})
    m = create_mission(l, 'A general question')
    target = l.all('request')[0]
    calls = 0
    def model(raw):
        nonlocal calls
        calls += 1
        if calls == 1:
            return json.dumps({'action': 'inspect', 'ids': [source.id]})
        return json.dumps({'action': 'finalize', 'candidates': [
            {'object_kind': 'note', 'commitment': 'Use the inspected source', 'source_ids': [source.id]}], 'activate': [0]})
    result = research_session(l, k, Task('investigate', target.id, 'Question'), model,
                              root=root, scope=Scope(l, m.id))
    assert source.id in result['live'][0].refs['sources']
    assert result['live'][0].refs['mission'] == (m.id,)


def test_context_report_accounts_for_final_serialized_size(world):
    l, k, _, _ = world
    target = l.put('study', {'question': 'Compact'})
    for i in range(10):
        l.put('note', {'text': 'some contents ' * 100, 'i': i}, {'target': [target.id]})
    packet = compile_context(l, k, Task('inspect', target.id, 'Read'), max_chars=3000)
    assert len(render_context(packet)) == packet['used_chars'] <= 3000
    assert packet['omitted'] > 0


def test_invalid_instrument_and_activation_can_be_revised(world):
    l, k, _, root = world
    m = create_mission(l, 'Recover gracefully')
    r = l.all('request')[0]
    actions = iter([
        {'action': 'instrument', 'name': 'not-an-instrument', 'spec': {}},
        {'action': 'finalize', 'candidates': [{'object_kind': 'note', 'commitment': 'Draft'}], 'activate': [99]},
        {'action': 'finalize', 'candidates': [], 'activate': []},
    ])
    def model(raw):
        assert len(raw) <= 18000
        return json.dumps(next(actions))
    result = research_session(l, k, Task('investigate', r.id, 'Question'), model,
                              root=root, scope=Scope(l, m.id))
    assert result['status'] == 'no_candidates'
    failures = [a for a in l.all('decision_outcome') if a.data['status'] == 'failed']
    assert len(failures) == 2 and not l.all('evidence')


def test_agent_can_page_and_reactivate_a_mission_branch(world):
    l, k, _, root = world
    m = create_mission(l, 'Return to a branch')
    b = open_branch(l, m.id, 'An older approach')
    e = set_attention(l, b.id, m.id, 'dormant', expected=[], reason='No data at the time')
    r = l.all('request')[0]
    actions = iter([
        {'action': 'branches', 'offset': 0, 'limit': 2},
        {'action': 'branch_state', 'branch': b.id, 'state': 'active', 'expected': [e.id], 'reason': 'New data may unblock it'},
        {'action': 'finalize', 'candidates': [], 'activate': []},
    ])
    research_session(l, k, Task('investigate', r.id, 'Revisit'), lambda raw: json.dumps(next(actions)),
                     root=root, scope=Scope(l, m.id))
    assert Scope(l, m.id).branch_active(b.id)


def test_missing_experiment_path_is_execution_blockage_not_evidence(world):
    l, k, _, root = world
    exp = l.put('experiment', {'path': str(root / 'does-not-exist.py')})
    m = create_mission(l, 'Execute the proposed path', targets=[exp.id])
    def model(raw):
        req = json.loads(raw)
        item = next(x for x in req['items'] if x['verb'] == 'run')
        return json.dumps({'key': item['key']})
    result = Runtime(l, k, root=root).run(mission=m.id, model=model)
    assert result['status'] == 'blocked'
    assert not l.all('evidence')


def test_narrower_runtime_cutoff_covers_derived_text(world):
    l, k, _, root = world
    source = l.put('source', {'title': 'A later source', 'published': '2020-01-01'})
    text = l.put('source_text', {'text': 'FUTURE_INFORMATION'}, {'source': [source.id]})
    m = create_mission(l, 'Historical', policy={'source_before': '2025-01-01'})
    r = l.all('request')[0]
    calls = 0
    def model(raw):
        nonlocal calls
        calls += 1
        assert 'FUTURE_INFORMATION' not in raw
        if calls == 1:
            return json.dumps({'action': 'inspect', 'ids': [text.id]})
        return json.dumps({'action': 'finalize', 'candidates': [], 'activate': []})
    research_session(l, k, Task('investigate', r.id, 'Historical'), model, root=root,
                     scope=Scope(l, m.id), source_before='2019-01-01')
    assert calls == 2


def test_large_observation_is_recoverable_under_complete_prompt_bound(world):
    l, k, _, root = world
    huge = l.put('note', {'text': 'Deep text ' * 20000})
    m = create_mission(l, 'Inspect boundedly')
    r = l.all('request')[0]
    calls = 0
    def model(raw):
        nonlocal calls
        calls += 1
        assert len(raw) <= 16000
        req = json.loads(raw)
        if calls == 1:
            return json.dumps({'action': 'inspect', 'ids': [huge.id]})
        if calls == 2:
            handles = [x for x in req['recent_observations'] if x.get('observation')]
            assert handles
            return json.dumps({'action': 'inspect', 'ids': [handles[0]['observation']],
                               'span': {'field': 'text', 'start': 0, 'end': 200}})
        return json.dumps({'action': 'finalize', 'candidates': [], 'activate': []})
    result = research_session(l, k, Task('investigate', r.id, 'Read'), model, root=root,
                              scope=Scope(l, m.id), context_chars=16000)
    assert result['status'] == 'no_candidates'
    saved = l.all('observation')
    assert saved and len(saved[0].data['text']) > 100000

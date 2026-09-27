"""Integration tests for missions, reading, checks and capsule workflows."""
from pathlib import Path
from types import SimpleNamespace
import json
import sys

import pytest

from recursive_discovery.core import Task, frontier, valid_evidence
from recursive_discovery.context import authority, compile_context, measure_packet
from recursive_discovery.investigation import (
    Scope, create_mission, open_branch, include, set_attention, revise_mission,
    declare_check, interpret_checks, challenge,
)
from recursive_discovery.agenda import available_work
from recursive_discovery.capsules import (
    compile_capsule, load_capsule, receive_response, changes_since, accept_response,
)
from recursive_discovery.reading import search_local
from recursive_discovery.replay import attempt_history
from recursive_discovery.runtime import Runtime
from recursive_discovery.session import research_session
from recursive_discovery.search import remember_text


def checking_world(world, *, script='import json\nprint(json.dumps({"supports":False}))\n'):
    ledger, kernel, blobs, root = world
    path = root / 'protocol.py'
    path.write_text(script)
    claim = ledger.put('claim', {'statement': 'A falsifiable empirical conjecture', 'check_policy': 'explicit'})
    tool = ledger.put('tool', {'name': 'declared-test', 'lane': 'empirical',
        'argv': [sys.executable, '{path}'], 'inputs': ['{path}'],
        'semantics': 'measure_statistic_not_adjudicate_claim'})
    mission = create_mission(ledger, 'Investigate the conjecture', targets=[claim.id],
                             policy={'tool_ids': [tool.id]})
    return claim, tool, mission, path


def run_one(world, mission):
    ledger, kernel, _, root = world
    def model(raw):
        req = json.loads(raw)
        assert req['mode'] == 'choose_research_work'
        choice = next(x for x in req['items'] if x['verb'] == 'run')
        return json.dumps({'key': choice['key']})
    return Runtime(ledger, kernel, root=root).run(mission=mission.id, model=model, max_steps=1)


def test_explicit_check_needs_protocol_then_interpretation_not_math_verification(world):
    ledger, kernel, _, _ = world
    claim, tool, mission, path = checking_world(world)
    assert [(x.verb, x.target) for x in frontier(ledger, kernel)] == [('design_check', claim.id)]
    exp = declare_check(ledger, mission.id, claim.id, 'Measure whether the effect occurs',
                         tool=tool.id, values={'path': str(path)})
    assert {x.verb for x in frontier(ledger, kernel)} == {'run'}
    run_one(world, mission)
    evidence = ledger.children(exp.id, 'evidence', 'target')[0]
    assert kernel.verify(evidence) and evidence.data['verdict'] == 'pass'
    assert json.loads(evidence.data['stdout'])['supports'] is False
    assert not valid_evidence(ledger, kernel, claim, 'math')
    assert {x.verb for x in frontier(ledger, kernel)} == {'interpret'}
    interpretation = interpret_checks(ledger, kernel, claim.id, [evidence.id],
                                     'Under this protocol the measurement does not support the conjecture.')
    assert authority(interpretation, ledger, kernel) == 'proposal_or_state'
    assert frontier(ledger, kernel) == []


def test_new_check_result_reopens_interpretation_without_rewriting_history(world):
    ledger, kernel, _, root = world
    claim, tool, mission, path = checking_world(world)
    exp = declare_check(ledger, mission.id, claim.id, 'Measure', tool=tool.id, values={'path': str(path)})
    old = kernel.use(ledger, tool, exp, {'path': str(path)}, cwd=root, seed=0)
    resolution = interpret_checks(ledger, kernel, claim.id, [old.id], 'Initial interpretation')
    saved = json.dumps(resolution.data, sort_keys=True)
    newer = kernel.use(ledger, tool, exp, {'path': str(path)}, cwd=root, seed=1)
    assert newer.id != old.id
    assert any(x.verb == 'interpret' and x.target == claim.id for x in frontier(ledger, kernel))
    assert json.dumps(ledger.get(resolution.id).data, sort_keys=True) == saved


def test_bad_signature_or_wrong_target_cannot_be_interpreted_as_check_result(world):
    ledger, kernel, _, root = world
    claim, tool, mission, path = checking_world(world)
    exp = declare_check(ledger, mission.id, claim.id, 'Measure', tool=tool.id, values={'path': str(path)})
    fake = ledger.put('evidence', {'_sig': 'fake', 'lane': 'empirical', 'verdict': 'pass'}, {'target': [exp.id]})
    with pytest.raises(ValueError, match='authenticated'):
        interpret_checks(ledger, kernel, claim.id, [fake.id], 'Do not accept')
    assert any(t.verb == 'run' for t in frontier(ledger, kernel))
    other = ledger.put('claim', {'statement': 'Different conjecture', 'check_policy': 'explicit'})
    real = kernel.use(ledger, tool, exp, {'path': str(path)}, cwd=root)
    with pytest.raises(ValueError, match='this target'):
        interpret_checks(ledger, kernel, other.id, [real.id], 'Not its result')


@pytest.mark.parametrize('tag', [None, 123, [], {}, 'λ'*64, 'x'*64])
def test_malformed_authentication_tag_is_untrusted_not_a_crash(world, tag):
    ledger, kernel, _, _ = world
    fake = ledger.put('evidence', {'_sig': tag, 'verdict': 'pass'})
    assert kernel.verify(fake) is False


def test_source_metadata_cannot_originate_an_explicit_check_policy(world):
    ledger, kernel, _, _ = world
    ledger.put('source', {'published': '2020-01-01', 'check_policy': 'explicit'})
    assert not frontier(ledger, kernel)


def test_legacy_claim_retains_default_math_frontier(world):
    ledger, kernel, _, _ = world
    claim = ledger.put('claim', {'statement': 'Legacy claim'})
    task = frontier(ledger, kernel)[0]
    assert (task.verb, task.target, task.lane) == ('verify', claim.id, 'math')


def test_checks_are_not_a_closed_kind_taxonomy(world):
    ledger, kernel, _, root = world
    _, tool, mission, path = checking_world(world)
    novel = ledger.put('liquidity-boundary-shape', {'geometry': 'A new representation'})
    exp = declare_check(ledger, mission.id, novel.id, 'Probe it', tool=tool.id, values={'path': str(path)})
    evidence = kernel.use(ledger, tool, exp, {'path': str(path)}, cwd=root)
    interpretation = interpret_checks(ledger, kernel, novel.id, [evidence.id], 'A result about this representation')
    assert interpretation.refs['target'] == (novel.id,)
    assert ledger.get(novel.id).kind == 'liquidity-boundary-shape'


def test_check_respects_tool_input_and_branch_access(world):
    ledger, _, _, _ = world
    claim, tool, mission, path = checking_world(world)
    branch = open_branch(ledger, mission.id, 'An approach')
    set_attention(ledger, branch.id, mission.id, 'dormant', expected=[], reason='Paused')
    with pytest.raises(ValueError, match='active'):
        declare_check(ledger, mission.id, claim.id, 'Check', tool=tool.id, branch=branch.id)
    denied = create_mission(ledger, 'No tools', targets=[claim.id], policy={'tool_ids': []})
    with pytest.raises(PermissionError):
        declare_check(ledger, denied.id, claim.id, 'Check', tool=tool.id)
    assert not ledger.all('experiment')


def test_dormancy_defers_checks_without_erasing_their_results(world):
    ledger, kernel, _, root = world
    claim, tool, mission, path = checking_world(world)
    branch = open_branch(ledger, mission.id, 'Candidate mechanism')
    include(ledger, mission.id, [claim.id], branch=branch.id)
    exp = declare_check(ledger, mission.id, claim.id, 'Check', tool=tool.id,
                        values={'path': str(path)}, branch=branch.id)
    evidence = kernel.use(ledger, tool, exp, {'path': str(path)}, cwd=root)
    include(ledger, mission.id, [evidence.id], branch=branch.id)
    set_attention(ledger, branch.id, mission.id, 'dormant', expected=[], reason='Needs new measurement',
                  revisit='Revisit when data arrive', support=[evidence.id])
    scope = Scope(ledger, mission.id)
    assert any(x.verb == 'interpret' for x in frontier(ledger, kernel))
    assert all(x.task.target != claim.id for x in available_work(ledger, kernel, scope)['eligible'])
    packet = compile_context(ledger, kernel, Task('inspect', claim.id, 'Inspect the negative result'), scope=scope)
    assert evidence.id in {x['id'] for x in packet['artifacts']}
    assert 'Revisit when data arrive' in json.dumps(scope.context_note())


def test_repeated_attempt_is_recorded_and_allowed_and_file_change_changes_identity(world):
    ledger, kernel, _, _ = world
    claim, tool, mission, path = checking_world(world)
    first = declare_check(ledger, mission.id, claim.id, 'Repeated protocol', tool=tool.id, values={'path': str(path)}, by='first')
    run_one(world, mission)
    second = declare_check(ledger, mission.id, claim.id, 'Repeated protocol', tool=tool.id, values={'path': str(path)}, by='replication')
    assert second.id != first.id
    run_one(world, mission)
    history = attempt_history(ledger, kernel, targets=[claim.id])
    assert history['total'] == 2
    assert history['items'][0]['signature'] == history['items'][1]['signature']
    decision = ledger.get(history['items'][0]['decision'])
    assert decision.data['payload']['prior_attempts'] == [history['items'][1]['decision']]
    path.write_text('print("new computation")\n')
    declare_check(ledger, mission.id, claim.id, 'Repeated protocol', tool=tool.id, values={'path': str(path)}, by='changed-code')
    run_one(world, mission)
    newer = attempt_history(ledger, kernel, targets=[claim.id])
    assert newer['total'] == 3 and newer['items'][0]['signature'] != newer['items'][1]['signature']
    assert all(row['execution_records'][0]['authenticated'] for row in newer['items'])


def test_execution_blockage_has_no_fabricated_evidence_and_is_searchable_history(world):
    ledger, kernel, _, _ = world
    claim, tool, mission, path = checking_world(world)
    path.unlink()
    declare_check(ledger, mission.id, claim.id, 'A currently blocked protocol', tool=tool.id, values={'path': str(path)})
    result = run_one(world, mission)
    assert result['status'] == 'blocked' and not ledger.all('evidence')
    history = attempt_history(ledger, kernel, targets=[claim.id])
    assert history['total'] == 1
    row = history['items'][0]
    assert row['execution_records'] == [] and row['outcomes'][0]['status'] == 'blocked'
    assert row['authority'] == 'execution_history_not_scientific_verdict'


def test_process_failure_remains_process_failure_in_attempt_history(world):
    ledger, kernel, _, _ = world
    claim, tool, mission, path = checking_world(world, script='raise RuntimeError("numerical error")\n')
    declare_check(ledger, mission.id, claim.id, 'Probe', tool=tool.id, values={'path': str(path)})
    run_one(world, mission)
    row = attempt_history(ledger, kernel, targets=[claim.id])['items'][0]
    assert row['execution_records'][0]['execution_verdict'] == 'fail'
    assert 'refuted' not in json.dumps(row)
    assert any(x.verb == 'repair' for x in frontier(ledger, kernel))


def test_attempt_history_obeys_mission_denials(world):
    ledger, kernel, _, _ = world
    claim, tool, mission, path = checking_world(world)
    declare_check(ledger, mission.id, claim.id, 'Probe', tool=tool.id, values={'path': str(path)})
    run_one(world, mission)
    another = create_mission(ledger, 'A separate scope', policy={'deny_ids': [claim.id]})
    assert attempt_history(ledger, kernel, targets=[claim.id], accept=Scope(ledger, another.id).allowed)['total'] == 0


def test_scoped_session_can_read_then_cite_after_both_context_and_observation_eviction(world, monkeypatch):
    import recursive_discovery.session as session
    ledger, kernel, _, root = world
    source = ledger.put('source', {'title': 'An appendix', 'published': '2020-01-01'})
    doc = remember_text(ledger, source, 'unrelated background\n'*3000 + '\n# Caveat\nOnly under independence.\n')
    mission = create_mission(ledger, 'Study the caveat', policy={'source_before': '2021-01-01'})
    branch = open_branch(ledger, mission.id, 'Read the conditions')
    task = Task('investigate', ledger.all('request')[0].id, 'Read the conditions')
    original = session.compile_context
    def trimmed(*args, **kwargs):
        packet = original(*args, **kwargs)
        packet['artifacts'] = [x for x in packet['artifacts'] if x['id'] not in {source.id, doc.id}]
        measure_packet(packet)
        return packet
    monkeypatch.setattr(session, 'compile_context', trimmed)
    seen = {}
    def policy(raw):
        assert len(raw) <= 18000
        req = json.loads(raw)
        turn = req['turn']
        if turn == 0:
            return json.dumps({'action': 'read', 'id': doc.id, 'query': 'Only under independence', 'length': 200})
        if turn == 1:
            seen.update(req['recent_observations'][-1]['passage'])
        if turn < 10:
            return json.dumps({'action': 'memo', 'text': f'Work {turn}'})
        assert not any('passage' in x for x in req['recent_observations'])
        return json.dumps({'action': 'finalize', 'candidates': [{'object_kind': 'note',
            'commitment': 'The condition matters', 'source_ids': [source.id],
            'citations': [{'id': doc.id, 'start': seen['start'], 'end': seen['end']}]}], 'activate': [0]})
    result = research_session(ledger, kernel, task, policy, root=root, scope=Scope(ledger, mission.id),
                              branch=branch.id, max_turns=12)
    assert result['status'] == 'activated'
    contribution = result['live'][0]
    assert contribution.refs['mission'] == (mission.id,) and contribution.refs['branch'] == (branch.id,)
    assert contribution.refs['passages'] == (doc.id,) and contribution.refs['sources'] == (source.id,)


def test_scope_change_inside_model_call_prevents_tool_execution(world, monkeypatch):
    import recursive_discovery.session as session
    ledger, kernel, _, root = world
    mission = create_mission(ledger, 'Original permissions')
    request_id = ledger.all('request')[0].id
    invoked = []
    monkeypatch.setattr(session.Workbench, 'call', lambda *args, **kwargs: invoked.append(True))
    def model(raw):
        revise_mission(ledger, mission.id, expected=[mission.id], objective='No execution', policy={'tool_ids': []})
        return json.dumps({'action': 'instrument', 'name': 'matrix', 'spec': {'matrix': [[1]]}})
    result = research_session(ledger, kernel, Task('investigate', request_id, 'Question'), model,
                              root=root, scope=Scope(ledger, mission.id))
    assert result['status'] == 'scope_changed' and invoked == []


def test_scoped_source_orphan_and_denied_ranked_hits_fail_closed(world):
    ledger, kernel, _, _ = world
    denied = [ledger.put('note', {'text': f'magnetic magnetic {i}'}) for i in range(5)]
    kept = ledger.put('note', {'text': 'magnetic allowed'})
    orphan = ledger.put('source_text', {'text': 'No provenance'})
    mission = create_mission(ledger, 'Historical', policy={'source_before': '2021-01-01',
                                                          'deny_ids': [x.id for x in denied]})
    scope = Scope(ledger, mission.id)
    assert not scope.allowed(orphan.id)
    result = search_local(ledger, 'magnetic', limit=1, accept=scope.allowed, cutoff=scope.source_before)
    assert [x.id for x in result] == [kept.id]


def test_capsule_can_freeze_a_passage_read_in_the_same_native_session(world):
    ledger, kernel, blobs, root = world
    source = ledger.put('source', {'title': 'Conditions', 'published': '2020-01-01'})
    text = 'preface '*3000 + '\nOnly valid for bounded inputs.\n'
    doc = remember_text(ledger, source, text)
    mission = create_mission(ledger, 'Inspect then hand off')
    r = ledger.all('request')[0]
    read = {}
    def model(raw):
        req = json.loads(raw)
        if req['turn'] == 0:
            return json.dumps({'action': 'read', 'id': doc.id, 'query': 'Only valid', 'length': 80})
        read.update(req['recent_observations'][-1]['passage'])
        return json.dumps({'action': 'capsule', 'selections': [{'id': doc.id,
            'span': {'field': 'text', 'start': read['start'], 'end': read['end']}}]})
    result = research_session(ledger, kernel, Task('investigate', r.id, 'Read'), model,
                              root=root, scope=Scope(ledger, mission.id))
    manifest, body = load_capsule(ledger, blobs, result['capsule'].id)
    item = next(x for x in manifest['items'] if x['id'] == doc.id)
    assert item['span']['start'] == read['start'] and item['span']['end'] == read['end']
    assert text[read['start']:read['end']] in json.loads(json.dumps(manifest))['packet']['artifacts'][-1]['data']['text']
    response, _ = receive_response(ledger, blobs, result['capsule'].id, json.dumps({
        'capsule': ledger.get(result['capsule'].id).data['delivery_id'],
        'contributions': [{'object_kind': 'note', 'commitment': 'Check boundedness',
                          'citations': [{'handle': item['handle'], 'field': 'text',
                                         'start': read['start'], 'end': read['end']}]}]}))
    change = changes_since(ledger, blobs, result['capsule'].id)
    accepted = accept_response(ledger, blobs, response.id, indices=[0],
        reviewed_revision=change['revision'], reviewed_changes=change['review_digest'], reason='Reviewed')
    assert accepted[0].data['citations']
    assert authority(accepted[0], ledger, kernel) == 'proposal_or_state'


def test_cli_dispatch_round_trip_and_scoped_read(world, monkeypatch, capsys):
    from recursive_discovery import cli
    ledger, kernel, blobs, root = world
    closed = []
    project = SimpleNamespace(ledger=ledger, kernel=kernel, blobs=blobs, root=root, close=lambda: closed.append(True))
    monkeypatch.setattr(cli, '_project', lambda path: project)
    def invoke(operation, spec):
        path = root / 'command.json'; path.write_text(json.dumps(spec))
        args = cli.parser().parse_args(['mission', str(root), operation, '--spec', str(path)])
        args.fn(args)
        return json.loads(capsys.readouterr().out)
    mission = invoke('create', {'objective': 'CLI round trip'})
    branch = invoke('branch', {'mission': mission['id'], 'question': 'A branch'})
    invoke('attention', {'subject': branch['id'], 'scope': mission['id'], 'state': 'dormant',
                         'expected': [], 'reason': 'Wait for evidence', 'revisit': 'New data'})
    assert invoke('agenda', {'mission': mission['id']})['items']
    cap = invoke('capsule', {'mission': mission['id'], 'directory': str(root/'export')})
    assert Path(cap['body']).exists()
    response = invoke('receive', {'capsule': cap['capsule'], 'text': json.dumps({
        'capsule': cap['delivery_id'], 'contributions': [{'object_kind': 'note', 'commitment': 'An idea'}]})})
    change = invoke('changes', {'capsule': cap['capsule']})
    accepted = invoke('accept', {'response': response[0]['id'], 'indices': [0],
        'reviewed_revision': change['revision'], 'reviewed_changes': change['review_digest'], 'reason': 'Read it'})
    assert accepted[0]['kind'] == 'note'
    source = ledger.put('source', {'title': 'Local', 'published': '2020-01-01'})
    doc = remember_text(ledger, source, '# Heading\nContent\n')
    args = cli.parser().parse_args(['read', str(root), doc.id, '--mission', mission['id'], '--outline'])
    args.fn(args)
    assert json.loads(capsys.readouterr().out)['entries'][0]['title'] == 'Heading'
    assert len(closed) == 9


def test_generic_passage_coordinates_cannot_masquerade_as_text_field(world):
    from recursive_discovery.reading import passage
    from recursive_discovery.session import _exposures, _store_candidates
    ledger, kernel, _, _ = world
    target = ledger.put('study', {'question': 'Understand the note'})
    note = ledger.put('note', {'text': 'Short text', 'argument': 'A different field'})
    row = passage(note, length=120)
    assert row['field'] == '$artifact_json'
    exposures = _exposures({'artifacts': []}, [{'action': 'read', 'passage': row}])
    spans = {(x['id'], x['field']): [(x['start'], x['end'])] for x in exposures if 'start' in x}
    raw = {'object_kind': 'note', 'commitment': 'Cite what was shown',
           'citations': [{'id': note.id, 'start': 0, 'end': 5}]}
    with pytest.raises(ValueError, match='actually shown'):
        _store_candidates(ledger, Task('explore', target.id, 'inspect'), [raw],
                          visible_ids={note.id}, source_ids=set(), spans=spans)
    raw['citations'][0]['field'] = '$artifact_json'
    saved = _store_candidates(ledger, Task('explore', target.id, 'inspect'), [raw],
                              visible_ids={note.id}, source_ids=set(), spans=spans)[0]
    assert saved.data['citations'][0]['field'] == '$artifact_json'


def test_named_string_field_exposure_survives_native_activation(world):
    from recursive_discovery.session import _exposures, _store_candidates
    from recursive_discovery.context import artifact_view
    from recursive_discovery.propose import activate
    ledger, kernel, _, _ = world
    target = ledger.put('study', {'question': 'Keep original coordinates'})
    note = ledger.put('note', {'argument': 'A long argument that deserves inspection'})
    row = artifact_view(ledger, kernel, note, span={'field': 'argument', 'start': 7, 'end': 20})
    exposed = _exposures({'artifacts': [row]}, [])
    ranges = {(x['id'], x['field']): [(x['start'], x['end'])] for x in exposed if 'start' in x}
    candidate = _store_candidates(ledger, Task('explore', target.id, 'inspect'), [{
        'object_kind': 'note', 'commitment': 'A partial argument',
        'citations': [{'id': note.id, 'field': 'argument', 'start': 7, 'end': 15}]}],
        visible_ids={note.id}, source_ids=set(), spans=ranges)[0]
    live = activate(ledger, candidate)
    assert live.data['citations'] == [{'id': note.id, 'field': 'argument', 'start': 7, 'end': 15}]


def test_multiple_execution_plans_keep_their_evidence_separate(world):
    from recursive_discovery.replay import execution_attempt, record_decision, record_outcome, attempt_history
    ledger, kernel, _, root = world
    target = ledger.put('claim', {'statement': 'Test via two implementations'})
    a = ledger.put('tool', {'lane': 'math', 'argv': [sys.executable, '-c', 'print(1)']})
    b = ledger.put('tool', {'lane': 'math', 'argv': [sys.executable, '-c', 'print(2)']})
    plans = [execution_attempt(ledger, target, t, {}, cwd=root) for t in (a, b)]
    decision = record_decision(ledger, Task('verify', target.id, 'two tools'), action='execute_frontier_task',
                               model=None, state={}, payload={'attempt': None, 'plans': plans})
    results = [kernel.use(ledger, t, target, {}, cwd=root) for t in (a, b)]
    record_outcome(ledger, decision, status='executed', produced=results)
    for plan, evidence in zip(plans, results):
        rows = attempt_history(ledger, kernel, signature=plan['signature'])['items']
        assert len(rows) == 1
        assert [x['id'] for x in rows[0]['execution_records']] == [evidence.id]


def test_cli_search_retains_full_source_and_reports_provider_failures(world, monkeypatch, capsys):
    from recursive_discovery import cli
    from recursive_discovery.search import acquire_source
    ledger, kernel, blobs, root = world
    closed = []
    monkeypatch.setattr(cli, '_project', lambda _: SimpleNamespace(
        ledger=ledger, kernel=kernel, blobs=blobs, root=root, close=lambda: closed.append(True)))
    def search(query, *, diagnostics, **kwargs):
        diagnostics.append({'provider': 'unavailable', 'status': 'error', 'error': 'offline fixture'})
        return [{'provider': 'fixture', 'title': 'Long source', 'url': 'https://source.invalid/paper',
                 'published': '2020-01-01'}]
    body = b'# Conditions\n' + b'background '*1000 + b'only under independence'
    monkeypatch.setattr(cli, 'multi_search', search)
    monkeypatch.setattr(cli, 'acquire_source', lambda *args, **kwargs: acquire_source(*args, **kwargs, fetch=lambda _: body))
    mission = create_mission(ledger, 'Acquire source', policy={'source_before': '2021-01-01'})
    args = cli.parser().parse_args(['search', str(root), 'conditions', '--read', '1',
                                    '--read-chars', '32', '--mission', mission.id])
    args.fn(args)
    printed = capsys.readouterr()
    assert json.loads(printed.err)['providers'][0]['status'] == 'error'
    row = json.loads(printed.out)
    assert row['read_status'] == 'acquired' and len(row['preview']) == 32
    extract = ledger.get(row['text_id'])
    assert extract.data['text'].endswith('only under independence')
    assert blobs.path(extract.data['raw_sha256']).read_bytes() == body
    assert closed == [True]

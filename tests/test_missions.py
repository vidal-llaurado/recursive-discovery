import json
import sys
import pytest

from recursive_discovery.core import Task, frontier
from recursive_discovery.investigation import (
    Scope, Conflict, create_mission, create_program, mission_revision, mission_heads,
    revise_mission, open_branch, include, set_attention, attention, supersede,
    request, challenge, attempt_signature,
)
from recursive_discovery.agenda import available_work, choose_work, Work, contradiction_opportunities
from recursive_discovery.context import compile_context, authority


def test_mission_creates_available_goal_without_a_frontier(world):
    l, k, _, _ = world
    m = create_mission(l, 'Investigate without assuming a result')
    assert not frontier(l, k)
    work = available_work(l, k, Scope(l, m.id))['eligible']
    assert len(work) == 1 and work[0].origin == 'directed'
    assert work[0].task.lane == 'meta'


def test_explicit_membership_not_reachability(world):
    l, k, _, _ = world
    common = l.put('tool', {'name': 'reusable'})
    m = create_mission(l, 'Question')
    b = open_branch(l, m.id, 'An approach', targets=[common.id])
    c = l.put('claim', {'statement': 'A'}, {'branch': [b.id], 'tool': [common.id]})
    set_attention(l, b.id, m.id, 'dormant', expected=[], reason='No longer useful here')
    scope = Scope(l, m.id)
    assert not scope.attending(c.id)
    assert scope.attending(common.id) and scope.allowed(c.id)
    assert any(t.target == c.id for t in frontier(l, k))  # Obligation was not discharged.
    agenda = available_work(l, k, scope)
    assert c.id not in {w.task.target for w in agenda['eligible']}
    assert any(x.get('target') == c.id and x['reason'] == 'branch_not_active' for x in agenda['suppressed'])


def test_dormancy_is_mission_scoped_and_program_suppression_explicit(world):
    l, _, _, _ = world
    p = create_program(l, 'Long program')
    a, b = create_mission(l, 'A', program=p.id), create_mission(l, 'B', program=p.id)
    branch = open_branch(l, a.id, 'Reusable investigation')
    include(l, b.id, [], branch=branch.id)
    set_attention(l, branch.id, a.id, 'dormant', expected=[], reason='A is not pursuing this')
    assert not Scope(l, a.id).branch_active(branch.id)
    assert Scope(l, b.id).branch_active(branch.id)
    set_attention(l, branch.id, p.id, 'dormant', expected=[], reason='Explicit program-wide pause')
    assert not Scope(l, b.id).branch_active(branch.id)


def test_overlapping_membership_uses_active_branch(world):
    l, _, _, _ = world
    m = create_mission(l, 'Shared work')
    a, b = open_branch(l, m.id, 'A'), open_branch(l, m.id, 'B')
    c = l.put('note', {'x': 1})
    include(l, m.id, [c.id], branch=a.id)
    include(l, m.id, [c.id], branch=b.id)
    set_attention(l, a.id, m.id, 'closed', expected=[], reason='A is complete')
    assert Scope(l, m.id).attending(c.id)


def test_causal_heads_reject_stale_updates_and_reconcile_forks(world):
    l, _, _, _ = world
    m = create_mission(l, 'Mission')
    b = open_branch(l, m.id, 'Branch')
    a = set_attention(l, b.id, m.id, 'dormant', expected=[], reason='Pause')
    with pytest.raises(Conflict):
        set_attention(l, b.id, m.id, 'active', expected=[], reason='Stale caller')
    # Emulate concurrent writers that appended siblings against the same prior head.
    x = l.put('attention', {'state': 'active', 'reason': 'Writer one'},
              {'subject': [b.id], 'scope': [m.id], 'previous': [a.id]}, by='one')
    y = l.put('attention', {'state': 'closed', 'reason': 'Writer two'},
              {'subject': [b.id], 'scope': [m.id], 'previous': [a.id]}, by='two')
    assert attention(l, b.id, m.id)['state'] == 'conflict'
    assert not Scope(l, m.id).branch_active(b.id)
    set_attention(l, b.id, m.id, 'active', expected=[x.id, y.id], reason='Reconciled both decisions')
    assert Scope(l, m.id).branch_active(b.id)


def test_revision_is_complete_and_causal(world):
    l, _, _, _ = world
    m = create_mission(l, 'Old objective')
    new = revise_mission(l, m.id, expected=[m.id], objective='New objective', guidance='New focus')
    assert mission_revision(l, m.id).id == new.id
    assert l.get(m.id).data['objective'] == 'Old objective'
    with pytest.raises(Conflict):
        revise_mission(l, m.id, expected=[m.id], objective='Stale objective')
    assert [h.id for h in mission_heads(l, m.id)] == [new.id]


def test_supersession_does_not_rewrite_or_close_old_work(world):
    l, k, _, _ = world
    a = l.put('representation', {'name': 'A'})
    b = l.put('representation', {'name': 'B'})
    e = l.put('experiment', {'protocol': 'Old experiment'}, {'representation': [a.id]})
    m = create_mission(l, 'Use B for one purpose', targets=[e.id])
    rel = supersede(l, a.id, b.id, m.id, purpose='small-noise limit', reason='More accurate', support=[e.id])
    assert l.get(e.id).refs['representation'] == (a.id,)
    assert authority(rel, l, k) == 'proposal_or_state'
    assert Scope(l, m.id).attending(e.id)


def test_dormancy_does_not_hide_counterevidence(world):
    l, k, _, root = world
    claim = l.put('claim', {'statement': 'Proposed claim'})
    m = create_mission(l, 'Check the claim', targets=[claim.id])
    b = open_branch(l, m.id, 'Earlier approach')
    e = k.run(l, claim, 'math', [sys.executable, '-c', 'print("COUNTEREXAMPLE"); raise SystemExit(1)'], cwd=root)
    include(l, m.id, [e.id], branch=b.id)
    set_attention(l, b.id, m.id, 'dormant', expected=[], reason='Approach abandoned')
    packet = compile_context(l, k, Task('verify', claim.id, 'Check'), scope=Scope(l, m.id), max_chars=12000)
    ids = {x['id'] for x in packet['artifacts']}
    assert e.id in ids
    assert k.verify(e) and e.data['verdict'] == 'fail'
    assert any(t.verb == 'revise' for t in frontier(l, k))


def test_program_policy_cannot_be_relaxed_by_mission(world):
    l, _, _, _ = world
    tool1, tool2 = l.put('tool', {'name': 'one'}), l.put('tool', {'name': 'two'})
    p = create_program(l, 'Restricted', policy={'source_before': '2020-01-01', 'tool_ids': [tool1.id]})
    m = create_mission(l, 'Later request', program=p.id, policy={'source_before': '2030-01-01', 'tool_ids': [tool1.id, tool2.id]})
    scope = Scope(l, m.id)
    assert scope.source_before == '2020-01-01'
    assert scope.tool_allowed(tool1.id) and not scope.tool_allowed(tool2.id)
    with pytest.raises(ValueError, match='guidance'):
        create_mission(l, 'Not an enforced policy', policy={'be_careful': True})


@pytest.mark.parametrize('published', ['2030-01-01', '', '2020', 'invalid'])
def test_declared_temporal_dependencies_fail_closed(world, published):
    l, _, _, _ = world
    source = l.put('source', {'title': 'Restricted', 'published': published})
    text = l.put('source_text', {'text': 'Should not be exposed'}, {'source': [source.id]})
    note = l.put('note', {'text': 'Dependent note'}, {'based_on': [text.id]})
    m = create_mission(l, 'Historical question', policy={'source_before': '2025-01-01'})
    assert not Scope(l, m.id).allowed(note.id)


def test_prerequisites_and_challenge_are_work_not_truth(world):
    l, k, _, _ = world
    m = create_mission(l, 'Mission')
    a = request(l, m.id, 'First')
    b = request(l, m.id, 'Second', after=[a.id])
    c = challenge(l, m.id, a.id, 'The assumption is not justified', methods='Seek an independent derivation')
    agenda = available_work(l, k, Scope(l, m.id))
    assert b.id not in {w.task.target for w in agenda['eligible']}
    assert c.id in {w.task.target for w in agenda['eligible']}
    set_attention(l, a.id, m.id, 'done', expected=[], reason='Investigation concluded')
    assert b.id in {w.task.target for w in available_work(l, k, Scope(l, m.id))['eligible']}
    assert authority(l.get(a.id), l, k) == 'proposal_or_state'


def test_arbitrary_opportunity_is_eligible_without_new_taxonomy(world):
    l, k, _, _ = world
    m = create_mission(l, 'Look beyond the current plan')
    unknown = l.put('unexpected-topological-analogy', {'question': 'Can this be useful?'})
    def scanner(ledger, scope):
        yield Work(Task('investigate', unknown.id, 'Unexpected connection'), 'custom-method')
    assert unknown.id in {w.task.target for w in available_work(l, k, Scope(l, m.id), scanners=[scanner])['eligible']}


def test_agenda_pages_have_hard_request_bound_and_displayed_keys(world):
    l, k, _, _ = world
    m = create_mission(l, 'Many questions')
    for i in range(24):
        request(l, m.id, f'Question {i}: ' + 'detail ' * 80)
    scope = Scope(l, m.id)
    work = available_work(l, k, scope)['eligible']
    calls = []
    def model(raw):
        assert len(raw) <= 2600
        data = json.loads(raw); calls.append(data)
        if len(calls) == 1:
            assert len(data['items']) < data['total']
            return json.dumps({'offset': len(work) - 1})
        return json.dumps({'key': data['items'][0]['key'], 'reason': 'Chosen after inspecting page'})
    selected = choose_work(l, k, scope, model, work=work, max_chars=2600)
    assert selected.key == work[-1].key and len(calls) == 2
    with pytest.raises(ValueError, match='displayed'):
        choose_work(l, k, scope, lambda raw: json.dumps({'key': work[-1].key}), work=work, max_chars=2600)


def test_attempt_signatures_preserve_meaningful_inputs(world):
    l, _, _, _ = world
    a, b = l.put('note', {'a': 1}), l.put('note', {'b': 1})
    x = attempt_signature(l, targets=[a.id, b.id], protocol={'seed': 1})
    assert x == attempt_signature(l, targets=[b.id, a.id], protocol={'seed': 1})
    assert x != attempt_signature(l, targets=[a.id, b.id], protocol={'seed': 2})


def test_branch_index_and_negative_memory_are_bounded_not_erased(world):
    l, _, _, _ = world
    m = create_mission(l, 'A long-running program')
    for i in range(25):
        b = open_branch(l, m.id, f'Abandoned attempt {i}')
        set_attention(l, b.id, m.id, 'dormant', expected=[], reason='Obstruction', revisit='A different representation')
    scope = Scope(l, m.id)
    note = scope.context_note()
    assert len(note['dormant_branches']) == 8 and note['dormant_omitted'] == 17
    page = scope.branch_page(offset=12, limit=10)
    assert len(page['items']) == 10 and page['total'] == 25 and page['next'] == 22


def test_supersession_is_rendered_as_purpose_scoped_context(world):
    l, k, _, _ = world
    a, b = l.put('representation', {'name': 'A'}), l.put('representation', {'name': 'B'})
    m = create_mission(l, 'Study A and its alternatives', targets=[a.id])
    supersede(l, a.id, b.id, m.id, purpose='high-signal regime', reason='Prefer B only here')
    packet = compile_context(l, k, Task('inspect', a.id, 'Review'), scope=Scope(l, m.id))
    row = next(x for x in packet['artifacts'] if x['id'] == a.id)
    assert row['superseded_for'][0]['purpose'] == 'high-signal regime'
    assert row['superseded_for'][0]['new'] == [b.id]


def test_attention_reason_cannot_smuggle_disallowed_support(world):
    l, _, _, _ = world
    secret = l.put('source', {'published': '2030-01-01'})
    m = create_mission(l, 'Historical', policy={'source_before': '2020-01-01'})
    b = open_branch(l, m.id, 'An old branch')
    set_attention(l, b.id, m.id, 'dormant', expected=[], reason='HIDDEN_NEW_FACT', support=[secret.id])
    assert 'HIDDEN_NEW_FACT' not in json.dumps(Scope(l, m.id).context_note())


def test_shared_assumption_stress_retains_origin_without_ownership(world):
    l, k, _, root = world
    m = create_mission(l, 'Stress validated theory')
    b = open_branch(l, m.id, 'One theory')
    assumption = l.put('assumption', {'var': 'x', 'op': '>', 'value': 0})
    claim = l.put('claim', {'statement': 'A conditional result'},
                  {'assumptions': [assumption.id], 'branch': [b.id]})
    k.run(l, claim, 'math', [sys.executable, '-c', 'print("checked")'], cwd=root)
    scope = Scope(l, m.id)
    assert assumption.id not in scope.members
    rows = available_work(l, k, scope)['eligible']
    stress = next(w for w in rows if w.task.verb == 'stress')
    assert stress.task.target == assumption.id and stress.branches == (b.id,)
    set_attention(l, b.id, m.id, 'dormant', expected=[], reason='Defer theory work')
    scope = Scope(l, m.id)
    assert scope.attending(assumption.id)  # A shared dependency has not been put to sleep.
    assert not any(w.task.verb == 'stress' for w in available_work(l, k, scope)['eligible'])
    # Another explicitly scoped, branchless claim still needs the same check.
    claim2 = l.put('claim', {'statement': 'Another conditional result'}, {'assumptions': [assumption.id]})
    include(l, m.id, [claim2.id])
    k.run(l, claim2, 'math', [sys.executable, '-c', 'print("also checked")'], cwd=root)
    assert any(w.task.verb == 'stress' for w in available_work(l, k, Scope(l, m.id))['eligible'])

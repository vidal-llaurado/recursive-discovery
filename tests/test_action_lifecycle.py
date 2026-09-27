"""Tests for shared session action handling."""
from __future__ import annotations

import ast
from dataclasses import replace
import inspect
import json

import pytest

from recursive_discovery import actions, session, investigation, protocols, replay, propose
from recursive_discovery.core import Task
from recursive_discovery.instruments import Workbench
from recursive_discovery.investigation import Scope, create_mission, open_branch, revise_mission


DONE = {"action": "finalize", "candidates": [], "activate": []}


def setup_context(world, scoped=False, branched=False):
    ledger, kernel, _, root = world
    target = ledger.put("study", {"question": "Find what is needed"})
    mission = create_mission(ledger, "Investigate", targets=[target.id]) if scoped else None
    branch = open_branch(ledger, mission.id, "Alternative") if branched else None
    task = Task("explore", target.id, "investigate")
    ctx = actions.ActionContext(ledger, kernel, task, root, Workbench(ledger, kernel, root=root),
        None, Scope(ledger, mission.id) if mission else None, branch.id if branch else None, 18000, 3)
    return ctx


def run(ctx, answers, **kwargs):
    answers = iter(answers)
    def model(prompt):
        answer = next(answers)
        return json.dumps(answer(json.loads(prompt)) if callable(answer) else answer)
    return session.research_session(ctx.ledger, ctx.kernel, ctx.task, model, root=ctx.root,
        scope=ctx.scope, branch=ctx.branch, **kwargs)


def test_registry_is_one_contract_and_descriptions_do_not_mutate_it(world):
    ctx = setup_context(world)
    offered = actions.offered(ctx)
    assert len(offered) == 10
    assert all(callable(a.handler) for a in offered.values())
    prompt_actions = actions.describe(ctx, offered)
    assert prompt_actions.keys() == offered.keys()
    prompt_actions['read']['id'] = 'mutated-by-caller'
    assert actions.ACTIONS['read'].arguments['id'] == 'artifact-id'
    assert prompt_actions['attempts']['targets'] == [ctx.task.target]
    ctx = setup_context(world, scoped=True, branched=True)
    offered = actions.offered(ctx)
    assert len(offered) == 19
    assert actions.describe(ctx, offered)['pause_branch']['expected'] == ctx.scope.state(ctx.branch)['heads']


@pytest.mark.parametrize('name', ['branches', 'branch_state', 'request', 'open_branch', 'check',
                                  'challenge', 'define_schema', 'capsule', 'pause_branch'])
def test_unadvertised_mission_action_never_reaches_handler(world, monkeypatch, name):
    ctx = setup_context(world)
    called = []
    monkeypatch.setitem(actions.ACTIONS, name, replace(actions.ACTIONS[name],
                        handler=lambda *args: called.append(True)))
    result = run(ctx, [{'action': name}, DONE])
    assert result['status'] == 'no_candidates'
    assert called == []
    decision = next(d for d in ctx.ledger.all('decision') if d.data['action'] == name)
    outcomes = ctx.ledger.children(decision.id, 'decision_outcome', 'decision')
    assert len(outcomes) == 1 and outcomes[0].data['status'] == 'invalid_action'


@pytest.mark.parametrize('inherit', [False, True])
def test_new_trusted_handler_uses_the_same_commit_membership_outcome_path(world, monkeypatch, inherit):
    ctx = setup_context(world, scoped=True, branched=True)
    observed = []
    def prepare(c, answer):
        assert c.ledger.all('decision') == []
        return {'prepared_value': answer['value']}
    def handler(c, answer):
        decision = c.ledger.get(c.decision.id)
        assert decision.data['payload']['prepared_value'] == 7
        assert not c.ledger.children(decision.id, 'decision_outcome', 'decision')
        note = c.ledger.put('note', {'text': 'a prepared consequence'}, by='test-handler')
        observed.append(note.id)
        return actions.Result('recorded', {'artifacts': [{'id': note.id}]}, (note,), summary={'value': 7})
    monkeypatch.setitem(actions.ACTIONS, 'probe', actions.Action({'value': 7}, handler,
                        inherit=inherit, prepare=prepare))
    result = run(ctx, [{'action': 'probe', 'value': 7}, DONE])
    assert result['status'] == 'no_candidates'
    current_scope = Scope(ctx.ledger, ctx.scope.root.id)
    assert (observed[0] in current_scope.members) is inherit
    if inherit:
        assert ctx.branch in current_scope.owners[observed[0]]
    for decision in ctx.ledger.all('decision'):
        outcomes = ctx.ledger.children(decision.id, 'decision_outcome', 'decision')
        assert len(outcomes) == 1
        if decision.data['action'] == 'probe':
            assert outcomes[0].refs['produced'] == tuple(observed)


@pytest.mark.parametrize('fail_stage', ['prepare', 'handler'])
def test_recoverable_failure_is_logged_once_and_session_continues(world, monkeypatch, fail_stage):
    ctx = setup_context(world)
    invoked = []
    def fail(*args):
        raise ValueError('expected failure')
    def success(*args):
        invoked.append(True)
        return actions.Result()
    monkeypatch.setitem(actions.ACTIONS, 'probe', actions.Action({},
        fail if fail_stage == 'handler' else success, prepare=fail if fail_stage == 'prepare' else None))
    result = run(ctx, [{'action': 'probe'}, DONE])
    assert result['status'] == 'no_candidates' and invoked == []
    decision = next(d for d in ctx.ledger.all('decision') if d.data['action'] == 'probe')
    outcomes = ctx.ledger.children(decision.id, 'decision_outcome', 'decision')
    assert len(outcomes) == 1
    assert outcomes[0].data['status'] == 'failed'
    assert 'expected failure' in result['observations'][0]['error']


def test_scope_change_blocks_even_a_new_handler_and_its_preparation(world, monkeypatch):
    ctx = setup_context(world, scoped=True)
    invoked = []
    monkeypatch.setitem(actions.ACTIONS, 'probe', actions.Action({},
        lambda *args: invoked.append('handler'), prepare=lambda *args: invoked.append('prepare')))
    def model(prompt):
        revise_mission(ctx.ledger, ctx.scope.root.id, expected=[ctx.scope.revision.id],
                       objective='Changed while the model was running')
        return {'action': 'probe'}
    result = run(ctx, [model])
    assert result['status'] == 'scope_changed' and invoked == []
    outcomes = ctx.ledger.children(result['last_decision'].id, 'decision_outcome', 'decision')
    assert len(outcomes) == 1 and outcomes[0].data['status'] == 'scope_changed'


def test_terminal_handler_still_records_an_outcome(world, monkeypatch):
    ctx = setup_context(world)
    monkeypatch.setitem(actions.ACTIONS, 'finish', actions.Action({},
        lambda *_: actions.Result('handoff', terminal={'delivery': 'test-delivery'})))
    result = run(ctx, [{'action': 'finish'}])
    assert result['delivery'] == 'test-delivery'
    assert len(ctx.ledger.children(result['last_decision'].id, 'decision_outcome', 'decision')) == 1


def test_unexpected_programming_error_is_not_disguised_as_research_failure(world, monkeypatch):
    ctx = setup_context(world)
    def broken(*_):
        raise AssertionError('handler invariant broken')
    monkeypatch.setitem(actions.ACTIONS, 'broken', actions.Action({}, broken))
    with pytest.raises(AssertionError, match='handler invariant'):
        run(ctx, [{'action': 'broken'}])
    assert len(ctx.ledger.all('decision')) == 1
    assert ctx.ledger.all('decision_outcome') == []  # documented interrupted-turn boundary


def test_old_imports_refer_to_the_same_implementations():
    assert investigation.declare_check is protocols.declare_check
    assert investigation.interpret_checks is protocols.interpret_checks
    assert investigation.attempt_signature is replay.attempt_signature
    assert session._store_candidates is propose.store_candidates


def test_loop_has_one_recording_path_and_handlers_cannot_duplicate_it():
    tree = ast.parse(inspect.getsource(session.research_session))
    calls = [n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert calls.count('record_decision') == calls.count('record_outcome') == 1
    handler_tree = ast.parse(inspect.getsource(actions))
    names = {n.func.id for n in ast.walk(handler_tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not names.intersection({'record_decision', 'record_outcome', 'include'})


@pytest.mark.parametrize('first', ['protocols', 'investigation', 'replay', 'session'])
def test_boundary_modules_can_be_imported_in_either_order(first):
    import subprocess
    import sys
    source = (f'import recursive_discovery.{first}\n'
              'from recursive_discovery import investigation, protocols, replay\n'
              'assert investigation.declare_check is protocols.declare_check\n'
              'assert investigation.attempt_signature is replay.attempt_signature\n')
    subprocess.run([sys.executable, '-c', source], check=True, capture_output=True, text=True)

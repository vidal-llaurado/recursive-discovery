"""Finite witnesses for FORMALISM.md's operational invariants, not proof certificates."""
from __future__ import annotations

import json
import sys

import pytest

from recursive_discovery.core import frontier
from recursive_discovery.context import authority, compile_context, ContextOverflow
from recursive_discovery.core import Task
from recursive_discovery.investigation import (
    Scope, create_mission, create_program, open_branch, include, set_attention,
    attention, supersede, Conflict, digest,
)
from recursive_discovery.capsules import compile_capsule, changes_since, load_capsule
from recursive_discovery.schema import define_schema, check


def keys(ledger, kernel):
    return {(t.verb, t.target, t.lane) for t in frontier(ledger, kernel)}


def test_management_only_extension_preserves_raw_frontier_and_authentication(world):
    ledger, kernel, _, _ = world
    claim = ledger.put('claim', {'statement': 'Check this'})
    evidence = kernel.run(ledger, claim, 'math', [sys.executable, '-c', "print('checked')"])
    mission = create_mission(ledger, 'Investigate', targets=[claim.id])
    branch = open_branch(ledger, mission.id, 'Try a representation')
    include(ledger, mission.id, [claim.id], branch=branch.id)
    before = keys(ledger, kernel)
    old_ids = {a.id for a in ledger.all()}
    old_bytes = json.dumps(evidence.data, sort_keys=True)
    set_attention(ledger, branch.id, mission.id, 'dormant', expected=[], reason='pause attention')
    supersede(ledger, claim.id, branch.id, mission.id, purpose='future work', reason='different approach')
    assert old_ids <= {a.id for a in ledger.all()}
    assert keys(ledger, kernel) == before
    assert json.dumps(ledger.get(evidence.id).data, sort_keys=True) == old_bytes
    assert kernel.verify(evidence) and authority(evidence, ledger, kernel) == 'consequence'
    assert not Scope(ledger, mission.id).attending(claim.id)
    assert Scope(ledger, mission.id).allowed(claim.id)


def test_attention_union_and_program_intersection_are_distinct(world):
    ledger, _, _, _ = world
    program = create_program(ledger, 'Research program')
    m1 = create_mission(ledger, 'Mission one', program=program.id)
    m2 = create_mission(ledger, 'Mission two', program=program.id)
    b1, b2 = open_branch(ledger, m1.id, 'Branch one'), open_branch(ledger, m1.id, 'Branch two')
    item = ledger.put('note', {'text': 'shared definition'})
    for mission, branch in [(m1, b1), (m1, b2), (m2, b1)]:
        include(ledger, mission.id, [item.id], branch=branch.id)
    set_attention(ledger, b1.id, m1.id, 'dormant', expected=[], reason='local pause')
    assert Scope(ledger, m1.id).attending(item.id)  # b2 still active
    assert Scope(ledger, m2.id).branch_active(b1.id)
    set_attention(ledger, b1.id, program.id, 'dormant', expected=[], reason='program pause')
    assert not Scope(ledger, m2.id).branch_active(b1.id)
    assert Scope(ledger, m1.id).attending(item.id)  # program pause does not own b2


def test_causal_heads_expose_forks_and_require_complete_reconciliation(world):
    ledger, _, _, _ = world
    mission = create_mission(ledger, 'Mission')
    branch = open_branch(ledger, mission.id, 'Branch')
    first = set_attention(ledger, branch.id, mission.id, 'dormant', expected=[], reason='pause')
    with pytest.raises(Conflict):
        set_attention(ledger, branch.id, mission.id, 'active', expected=[], reason='stale')
    # Simulate independently imported competing histories; this is not a concurrency test.
    second = ledger.put('attention', {'state': 'closed', 'reason': 'independent closure'},
                        {'subject': [branch.id], 'scope': [mission.id]}, by='other-controller')
    state = attention(ledger, branch.id, mission.id)
    assert state['state'] == 'conflict' and set(state['heads']) == {first.id, second.id}
    end = set_attention(ledger, branch.id, mission.id, 'active', expected=state['heads'], reason='reconciled')
    assert attention(ledger, branch.id, mission.id)['heads'] == [end.id]


def test_capsule_digest_covers_full_manifest_and_review_report(world):
    ledger, kernel, blobs, _ = world
    mission = create_mission(ledger, 'Mission')
    capsule = compile_capsule(ledger, kernel, blobs, mission.id)
    manifest, body = load_capsule(ledger, blobs, capsule.id)
    assert capsule.data['delivery_id'] == digest(manifest)
    assert capsule.data['delivery_id'] != capsule.id
    before = body
    addition = ledger.put('note', {'text': 'unrelated information'})
    report = changes_since(ledger, blobs, capsule.id)
    assert report['review_digest'] == digest({k: v for k, v in report.items() if k != 'review_digest'})
    assert addition.id in report['added'] and addition.id not in report['touching_delivery']
    assert report['changed'] is True
    assert load_capsule(ledger, blobs, capsule.id)[1] == before


def test_schema_conformance_is_not_authentication(world):
    ledger, kernel, _, _ = world
    schema = define_schema(ledger, 'A description', fields={'statement': {'type': 'string', 'required': True}})
    claim = ledger.put('claim', {'statement': 'A claim without checked evidence'}, {'schema': [schema.id]})
    assert check(ledger, schema.id, claim.data, claim.refs)['conforms']
    assert not kernel.verify(claim)
    assert authority(claim, ledger, kernel) == 'proposal_or_state'


def test_required_payload_overflow_is_not_silent_truncation(world):
    ledger, kernel, _, _ = world
    required = ledger.put('note', {'text': 'X' * 20_000})
    mission = create_mission(ledger, 'Mission', required=[required.id])
    scope = Scope(ledger, mission.id)
    with pytest.raises(ContextOverflow):
        compile_context(ledger, kernel, Task('investigate', scope.revision.id, 'work'), scope=scope, max_chars=3000)


def test_explicit_span_can_narrow_a_large_required_payload(world):
    ledger, kernel, _, _ = world
    required = ledger.put('note', {'text': 'prefix ' * 1000 + 'the required condition' + ' suffix' * 1000})
    start = required.data['text'].index('the required condition')
    mission = create_mission(ledger, 'Mission', required=[required.id])
    scope = Scope(ledger, mission.id)
    packet = compile_context(ledger, kernel, Task('investigate', scope.revision.id, 'work'),
        scope=scope, max_chars=4000, selections=[{'id': required.id,
            'span': {'field': 'text', 'start': start, 'end': start + 22}}])
    row = next(r for r in packet['artifacts'] if r['id'] == required.id)
    assert row['data']['text'] == required.data['text'][start:start + 22]
    assert row['span']['start'] == start


def test_explicit_selection_without_span_requires_the_whole_payload(world):
    ledger, kernel, _, _ = world
    target = ledger.put('study', {'question': 'Inspect'})
    large = ledger.put('note', {'text': 'X' * 20_000})
    with pytest.raises(ContextOverflow):
        compile_context(ledger, kernel, Task('inspect', target.id, 'Read'), max_chars=3000,
                        selections=[{'id': large.id}])


def test_required_payload_propagates_through_schema_context_dependencies(world):
    ledger, kernel, _, _ = world
    definition = ledger.put('definition', {'text': 'A required condition ' * 2000})
    schema = define_schema(ledger, 'A result', references={'definition': {'context': True, 'min': 1}})
    result = ledger.put('result', {'x': 1}, {'schema': [schema.id], 'definition': [definition.id]})
    mission = create_mission(ledger, 'Mission', required=[result.id])
    with pytest.raises(ContextOverflow):
        compile_context(ledger, kernel, Task('inspect', result.id, 'Read'), max_chars=4000,
                        scope=Scope(ledger, mission.id))


def test_unrequired_local_target_still_has_a_recoverable_preview(world):
    ledger, kernel, _, _ = world
    # Use ordinary words: this test isolates payload previewing, not pathological query metadata.
    large = ledger.put('note', {'text': 'Long source passage. ' * 2000})
    packet = compile_context(ledger, kernel, Task('inspect', large.id, 'Read'), max_chars=3000)
    row = next(r for r in packet['artifacts'] if r['id'] == large.id)
    assert row['complete'] is False
    assert packet['used_chars'] <= 3000

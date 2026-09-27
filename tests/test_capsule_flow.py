import json
import pytest

from recursive_discovery.core import Task
from recursive_discovery.investigation import create_mission, Scope, Conflict, revise_mission, open_branch, set_attention
from recursive_discovery.context import ContextOverflow, authority
from recursive_discovery.propose import activate
from recursive_discovery.capsules import (
    compile_capsule, load_capsule, export_capsule, changes_since, receive_response, accept_response,
)


def fixture(world, text='Definitions. The construction requires positive variance. Appendix: counterexample.'):
    l, k, blobs, _ = world
    source = l.put('source', {'title': 'A paper', 'published': '2020-01-01', 'url': 'https://example.org/paper'})
    body = l.put('source_text', {'text': text}, {'source': [source.id]})
    mission = create_mission(l, 'Investigate the construction', targets=[body.id], required=[source.id],
                             deliverables='An argument with explicit conditions')
    return mission, source, body


def test_capsule_is_portable_and_deterministic(world):
    l, k, blobs, root = world
    m, source, body = fixture(world)
    cap = compile_capsule(l, k, blobs, m.id)
    manifest, text = load_capsule(l, blobs, cap.id)
    assert body.data['text'] in text
    assert manifest['revision'] == m.id
    assert cap.data['chars'] == len(text) <= 120000
    again = compile_capsule(l, k, blobs, m.id)
    assert again.id == cap.id
    files = export_capsule(l, blobs, cap.id, root / 'exports')
    from pathlib import Path
    assert Path(files['body']).read_text() == text
    assert json.loads(Path(files['manifest']).read_text()) == manifest
    assert not changes_since(l, blobs, cap.id)['changed']


def test_new_incoming_evidence_changes_next_capsule(world):
    l, k, blobs, _ = world
    m, _, body = fixture(world)
    cap = compile_capsule(l, k, blobs, m.id)
    original = load_capsule(l, blobs, cap.id)[1]
    new = l.put('note', {'text': 'A proposed contradiction'}, {'contradicts': [body.id]})
    change = changes_since(l, blobs, cap.id)
    assert new.id in change['added'] and new.id in change['touching_delivery']
    assert change['changed']
    next_cap = compile_capsule(l, k, blobs, m.id)
    assert next_cap.id != cap.id
    assert load_capsule(l, blobs, cap.id)[1] == original  # History never rewritten.


def test_revision_is_not_silently_substituted_in_export(world):
    l, k, blobs, _ = world
    m, _, body = fixture(world)
    old = compile_capsule(l, k, blobs, m.id)
    revision = revise_mission(l, m.id, expected=[m.id], objective='A different question', targets=[body.id])
    assert load_capsule(l, blobs, old.id)[0]['revision'] == m.id
    new = compile_capsule(l, k, blobs, m.id)
    assert load_capsule(l, blobs, new.id)[0]['revision'] == revision.id


def test_required_context_overflows_instead_of_becoming_a_prefix(world):
    l, k, blobs, _ = world
    m, _, body = fixture(world, 'x' * 30000)
    with pytest.raises(ContextOverflow):
        compile_capsule(l, k, blobs, m.id, max_chars=5000)
    cap = compile_capsule(l, k, blobs, m.id, max_chars=7000,
                          selections=[{'id': body.id, 'span': {'field': 'text', 'start': 29000, 'end': 29200}}])
    manifest, text = load_capsule(l, blobs, cap.id)
    row = next(x for x in manifest['packet']['artifacts'] if x['id'] == body.id)
    assert row['span']['start'] == 29000 and row['data']['text'] == 'x' * 200
    assert len(text) <= 7000


def test_exact_unicode_exposure_controls_citations(world):
    l, k, blobs, _ = world
    original = 'prefix ' * 100 + 'α≤β；条件是 σ²>0。' + ' suffix' * 100
    m, _, body = fixture(world, original)
    start = original.index('α'); end = start + len('α≤β；条件是 σ²>0。')
    cap = compile_capsule(l, k, blobs, m.id, selections=[{'id': body.id, 'span': {'field': 'text', 'start': start, 'end': end}}])
    manifest, _ = load_capsule(l, blobs, cap.id)
    h = next(x['handle'] for x in manifest['items'] if x['id'] == body.id)
    payload = {'capsule': cap.data['delivery_id'], 'contributions': [
        {'object_kind': 'new-scientific-kind', 'commitment': 'Use the stated condition',
         'citations': [{'handle': h, 'field': 'text', 'start': start, 'end': end}]}]}
    response, outcome = receive_response(l, blobs, cap.id, json.dumps(payload, ensure_ascii=False))
    assert outcome.data['status'] == 'proposals'
    candidate = l.get(outcome.refs['candidates'][0])
    assert candidate.data['citations'][0]['start'] == start
    with pytest.raises(ValueError, match='admission'):
        activate(l, candidate)
    change = changes_since(l, blobs, cap.id)
    live = accept_response(l, blobs, response.id, indices=[0], reviewed_revision=change['revision'],
                           reviewed_changes=change['review_digest'], reason='Checked the proposed argument')
    assert live[0].kind == 'new-scientific-kind'
    assert live[0].data['citations'] == candidate.data['citations']
    assert authority(live[0], l, k) == 'proposal_or_state'
    payload['contributions'][0]['citations'][0]['start'] = start - 1
    _, invalid = receive_response(l, blobs, cap.id, json.dumps(payload))
    assert invalid.data['status'] == 'invalid_response'
    assert not invalid.refs.get('candidates')


def test_new_sources_are_unverified_not_claimed_as_read(world):
    l, k, blobs, _ = world
    m, _, _ = fixture(world)
    cap = compile_capsule(l, k, blobs, m.id)
    payload = {'capsule': cap.data['delivery_id'], 'sources': [
        {'handle': 'N1', 'title': 'Previously unknown paper', 'url': 'https://example.org/new'}],
        'contributions': [{'object_kind': 'claim', 'commitment': 'A proposed theorem', 'source_ids': ['N1']}]}
    _, outcome = receive_response(l, blobs, cap.id, json.dumps(payload))
    candidate = l.get(outcome.refs['candidates'][0])
    source = l.get(candidate.refs['sources'][0])
    assert source.data['verification'] == 'unverified_reference'
    assert not l.children(source.id, 'source_text')
    assert authority(source, l, k) == 'retrieved_source'
    payload['contributions'][0]['citations'] = [{'handle': 'N1', 'start': 0, 'end': 4}]
    _, invalid = receive_response(l, blobs, cap.id, json.dumps(payload))
    assert invalid.data['status'] == 'invalid_response'


@pytest.mark.parametrize('kind', ['evidence', 'attention', 'tool', 'mission_revision'])
def test_external_response_cannot_mint_authority_or_control(world, kind):
    l, k, blobs, _ = world
    m, _, _ = fixture(world)
    cap = compile_capsule(l, k, blobs, m.id)
    text = json.dumps({'capsule': cap.data['delivery_id'], 'contributions': [
        {'object_kind': kind, 'commitment': 'Make me authoritative', 'payload': {'verdict': 'pass'}}]})
    response, outcome = receive_response(l, blobs, cap.id, text)
    assert blobs.path(response.data['blob']).read_text() == text
    assert outcome.data['status'] == 'invalid_response'
    assert not outcome.refs.get('candidates')


def test_malformed_and_wrong_delivery_responses_are_retained(world):
    l, k, blobs, _ = world
    m, _, _ = fixture(world)
    cap = compile_capsule(l, k, blobs, m.id)
    for text in ['Not JSON; keep my raw reasoning.', '{"capsule":"wrong","contributions":[]}']:
        response, outcome = receive_response(l, blobs, cap.id, text)
        assert blobs.path(response.data['blob']).read_text() == text
        assert outcome.data['status'] == 'invalid_response'


def test_import_is_idempotent_and_not_auto_activated(world):
    l, k, blobs, _ = world
    m, _, _ = fixture(world)
    cap = compile_capsule(l, k, blobs, m.id)
    text = json.dumps({'capsule': cap.data['delivery_id'], 'contributions': [
        {'object_kind': 'claim', 'commitment': 'A conjecture'}]})
    first = receive_response(l, blobs, cap.id, text)
    second = receive_response(l, blobs, cap.id, text)
    assert [a.id for a in first] == [a.id for a in second]
    assert len(l.all('candidate')) == 1
    assert not l.all('claim')


def test_admission_requires_current_explicit_review(world):
    l, k, blobs, _ = world
    m, _, _ = fixture(world)
    cap = compile_capsule(l, k, blobs, m.id)
    response, _ = receive_response(l, blobs, cap.id, json.dumps({'capsule': cap.data['delivery_id'],
        'contributions': [{'object_kind': 'note', 'commitment': 'A tentative interpretation'}]}))
    change = changes_since(l, blobs, cap.id)
    l.put('note', {'text': 'Arrived while review was in progress'}, {'mission': [m.id]})
    with pytest.raises(Conflict):
        accept_response(l, blobs, response.id, indices=[0], reviewed_revision=change['revision'],
                        reviewed_changes=change['review_digest'], reason='Old review')
    fresh = changes_since(l, blobs, cap.id)
    assert len(accept_response(l, blobs, response.id, indices=[0], reviewed_revision=fresh['revision'],
                reviewed_changes=fresh['review_digest'], reason='Reviewed the new state')) == 1


def test_blob_tampering_is_detected(world):
    l, k, blobs, _ = world
    m, _, _ = fixture(world)
    cap = compile_capsule(l, k, blobs, m.id)
    blobs.path(cap.data['body_blob']).write_text('Changed bytes')
    with pytest.raises(ValueError, match='digest'):
        load_capsule(l, blobs, cap.id)


def test_export_refuses_to_overwrite_different_bytes(world):
    l, k, blobs, root = world
    m, _, _ = fixture(world)
    cap = compile_capsule(l, k, blobs, m.id)
    out = export_capsule(l, blobs, cap.id, root / 'delivery')
    from pathlib import Path
    Path(out['body']).write_text('A user edit')
    with pytest.raises(FileExistsError):
        export_capsule(l, blobs, cap.id, root / 'delivery')


def test_changed_kernel_trust_does_not_reuse_authority_labels(world):
    from recursive_discovery.core import Kernel
    import sys
    l, k, blobs, root = world
    m, _, body = fixture(world)
    e = k.run(l, body, 'empirical', [sys.executable, '-c', 'print("ran")'], cwd=root)
    a = compile_capsule(l, k, blobs, m.id, selections=[{'id': e.id}])
    other = Kernel(root / 'other.key')
    b = compile_capsule(l, other, blobs, m.id, selections=[{'id': e.id}])
    assert a.id != b.id
    packet = load_capsule(l, blobs, b.id)[0]['packet']
    assert next(x for x in packet['artifacts'] if x['id'] == e.id)['authority'] == 'untrusted'


def test_capsule_honors_narrower_call_cutoff(world):
    l, k, blobs, _ = world
    m, source, _ = fixture(world)
    with pytest.raises(PermissionError):
        compile_capsule(l, k, blobs, m.id, source_before='2019-01-01')


def test_local_span_renderer_cannot_reveal_the_rest_of_text(world):
    from recursive_discovery.schema import define_schema
    from recursive_discovery.context import artifact_view
    l, k, _, _ = world
    d = define_schema(l, 'text display', fields={'text': {'type': 'string'}}, display=['text'])
    a = l.put('note', {'text': 'VISIBLE HIDDEN_SUFFIX'}, {'schema': [d.id]})
    row = artifact_view(l, k, a, span={'field': 'text', 'start': 0, 'end': 7})
    assert 'HIDDEN_SUFFIX' not in json.dumps(row)
    assert row['conformance'][0]['display'][0]['value'] == 'VISIBLE'


def test_current_branch_state_is_checked_at_admission(world):
    l, k, blobs, _ = world
    m, _, _ = fixture(world)
    b = open_branch(l, m.id, 'A possible return branch')
    cap = compile_capsule(l, k, blobs, m.id)
    response, _ = receive_response(l, blobs, cap.id, json.dumps({'capsule': cap.data['delivery_id'],
        'contributions': [{'object_kind': 'note', 'commitment': 'Proposed observation to investigate'}]}))
    set_attention(l, b.id, m.id, 'dormant', expected=[], reason='Wait for other data')
    change = changes_since(l, blobs, cap.id)
    with pytest.raises(ValueError, match='active'):
        accept_response(l, blobs, response.id, indices=[0], branch=b.id, reviewed_revision=change['revision'],
                        reviewed_changes=change['review_digest'], reason='Cannot return into paused branch')


def test_portable_manifest_does_not_expose_unselected_world_identifiers(world):
    l, k, blobs, _ = world
    m, _, body = fixture(world)
    other = l.put('note', {'text': 'Unrelated private material'})
    cap = compile_capsule(l, k, blobs, m.id, selections=[{'id': body.id}])
    manifest, text = load_capsule(l, blobs, cap.id)
    assert other.id not in json.dumps(manifest) and other.id not in text
    assert 'snapshot_blob' in manifest and 'snapshot_ids' not in manifest
    assert not changes_since(l, blobs, cap.id)['changed']


def test_declared_candidate_links_enforce_information_boundary_before_admission(world):
    l, k, blobs, _ = world
    m, _, _ = fixture(world)
    # The required fixture sources are dated before this cutoff.
    revise_mission(l, m.id, expected=[m.id], objective='Only known sources', policy={'source_before': '2021-01-01'})
    cap = compile_capsule(l, k, blobs, m.id)
    raw = json.dumps({'capsule': cap.data['delivery_id'],
        'sources': [{'handle': 'N1', 'title': 'Future', 'url': 'https://example.org/future', 'published': '2030-01-01'}],
        'contributions': [{'object_kind': 'claim', 'commitment': 'Uses future source via a semantic link',
                           'links': {'derived_from': ['N1']}}]})
    response, outcome = receive_response(l, blobs, cap.id, raw)
    assert outcome.data['status'] == 'proposals'
    change = changes_since(l, blobs, cap.id)
    with pytest.raises(PermissionError):
        accept_response(l, blobs, response.id, indices=[0], reviewed_revision=change['revision'],
                        reviewed_changes=change['review_digest'], reason='Must not admit this dependency')
    assert not l.all('claim')


def test_second_admission_requires_reuse_not_duplicate_activation(world):
    l, k, blobs, _ = world
    m, _, _ = fixture(world)
    cap = compile_capsule(l, k, blobs, m.id)
    r, _ = receive_response(l, blobs, cap.id, json.dumps({'capsule': cap.data['delivery_id'],
        'contributions': [{'object_kind': 'note', 'commitment': 'A proposal'}]}))
    change = changes_since(l, blobs, cap.id)
    first = accept_response(l, blobs, r.id, indices=[0], reviewed_revision=change['revision'],
                            reviewed_changes=change['review_digest'], reason='Reviewed')
    change = changes_since(l, blobs, cap.id)
    with pytest.raises(ValueError, match='already activated'):
        accept_response(l, blobs, r.id, indices=[0], reviewed_revision=change['revision'],
                        reviewed_changes=change['review_digest'], reason='Accidental repeated submission')
    assert len(l.children(first[0].refs['candidate'][0], 'note', 'candidate')) == 1


def test_invalid_numeric_payload_does_not_partially_introduce_sources(world):
    l, k, blobs, _ = world
    m, _, _ = fixture(world)
    cap = compile_capsule(l, k, blobs, m.id)
    before = {x.id for x in l.all('source')}
    raw = json.dumps({'capsule': cap.data['delivery_id'],
        'sources': [{'handle': 'N1', 'url': 'https://example.org/paper'}],
        'contributions': [{'object_kind': 'note', 'commitment': 'Invalid serialization',
                           'payload': {'number': float('nan')}}]})
    response, outcome = receive_response(l, blobs, cap.id, raw)
    assert outcome.data['status'] == 'invalid_response'
    assert blobs.path(response.data['blob']).read_text() == raw
    assert {x.id for x in l.all('source')} == before
    assert not l.all('candidate')


def test_capsule_records_selection_and_rendering_code_identity(world):
    from recursive_discovery.capsules import _compiler_fingerprint
    l, k, blobs, _ = world
    m, _, _ = fixture(world)
    cap = compile_capsule(l, k, blobs, m.id)
    manifest, _ = load_capsule(l, blobs, cap.id)
    assert manifest['compiler_digest'] == _compiler_fingerprint()
    assert len(manifest['compiler_digest']) == 64

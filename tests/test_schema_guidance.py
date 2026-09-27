import math
import sys
import pytest

from recursive_discovery.core import Task
from recursive_discovery.context import compile_context, authority, ContextOverflow
from recursive_discovery.schema import define_schema, check, describe, require_inputs
from recursive_discovery.investigation import create_mission, Scope
from recursive_discovery.runtime import Runtime


def test_unknown_scientific_kinds_are_not_gated(world):
    l, k, _, _ = world
    a = l.put('an-unanticipated-concept', {'arbitrary': ['structure', 1]})
    assert describe(l, a) == []
    packet = compile_context(l, k, Task('investigate', a.id, 'Explore'))
    assert a.id in {x['id'] for x in packet['artifacts']}
    assert authority(a, l, k) == 'proposal_or_state'


def test_drafts_remain_storable_with_conformance_diagnostics(world):
    l, k, _, _ = world
    descriptor = define_schema(l, 'experiment outline', fields={'protocol': {'type': 'string', 'required': True}})
    draft = l.put('experimental-idea', {}, {'schema': [descriptor.id]})
    assert not describe(l, draft)[0]['conforms']
    row = next(x for x in compile_context(l, k, Task('inspect', draft.id, 'Review'))['artifacts'] if x['id'] == draft.id)
    assert row['conformance'][0]['errors'] == ['missing field: protocol']


def test_descriptor_drives_dependency_closure_and_rendering(world):
    l, k, _, _ = world
    definition = l.put('definition', {'text': 'sigma denotes a positive standard deviation'})
    descriptor = define_schema(l, 'diagnostic', fields={'formula': {'type': 'string', 'required': True}},
                               references={'definitions': {'kinds': ['definition'], 'min': 1, 'context': True}},
                               display=['formula'])
    a = l.put('diagnostic', {'formula': 'x / sigma'}, {'schema': [descriptor.id], 'definitions': [definition.id]})
    packet = compile_context(l, k, Task('inspect', a.id, 'Read'), radius=0)
    assert {a.id, descriptor.id, definition.id}.issubset({x['id'] for x in packet['artifacts']})
    row = next(x for x in packet['artifacts'] if x['id'] == a.id)
    assert row['conformance'][0]['display'] == [{'field': 'formula', 'value': 'x / sigma'}]


def test_schema_versions_do_not_reinterpret_old_objects(world):
    l, _, _, _ = world
    one = define_schema(l, 'estimate', fields={'mean': {'type': 'number', 'required': True}})
    a = l.put('estimate', {'mean': 2}, {'schema': [one.id]})
    two = define_schema(l, 'estimate', fields={'mean': {'type': 'number', 'required': True},
                        'se': {'type': 'number', 'required': True}}, supersedes=one.id)
    assert one.id != two.id
    assert describe(l, a)[0]['conforms']
    assert not check(l, two.id, a.data)['conforms']
    assert a.refs['schema'] == (one.id,)


@pytest.mark.parametrize('value', [True, '2', math.inf, math.nan])
def test_number_contract_does_not_accept_bool_text_or_nonfinite(world, value):
    l, _, _, _ = world
    d = define_schema(l, 'number', fields={'n': {'type': 'number', 'required': True}})
    assert not check(l, d.id, {'n': value})['conforms']


def test_strict_tool_contract_is_enforced_before_execution(world):
    l, k, _, root = world
    d = define_schema(l, 'count input', fields={'n': {'type': 'integer', 'required': True}})
    tool = l.put('tool', {'name': 'count', 'lane': 'empirical',
                 'argv': [sys.executable, '-c', 'print({n})'], 'inputs': []}, {'input_schema': [d.id]})
    exp = l.put('experiment', {'values': {'n': 'not a number'}}, {'tool': [tool.id]})
    runtime = Runtime(l, k, root=root)
    with pytest.raises(ValueError, match='input schema'):
        runtime.execute(Task('run', exp.id, 'Run'))
    assert not l.all('evidence')
    valid = l.put('experiment', {'values': {'n': 4}}, {'tool': [tool.id]})
    evidence = runtime.execute(Task('run', valid.id, 'Run'))[0]
    assert evidence.data['stdout'].strip() == '4' and k.verify(evidence)


def test_required_schema_context_cannot_be_silently_cut(world):
    l, k, _, _ = world
    long = l.put('definition', {'text': 'Required conditions ' * 4000})
    d = define_schema(l, 'needs definition', references={'definitions': {'context': True, 'min': 1}})
    a = l.put('diagnostic', {'x': 1}, {'schema': [d.id], 'definitions': [long.id]})
    with pytest.raises(ContextOverflow):
        compile_context(l, k, Task('inspect', a.id, 'Read'), full=True, max_chars=4000)


def test_descriptors_are_data_not_executable_validators(world):
    l, _, _, _ = world
    with pytest.raises(ValueError):
        define_schema(l, 'unsafe descriptor', fields={'x': {'type': 'integer', 'code': 'run arbitrary Python'}})
    with pytest.raises(ValueError):
        define_schema(l, 'unknown type', fields={'x': {'type': 'my-validator'}})


def test_explicit_capsule_selection_carries_its_descriptor_dependencies(world):
    from recursive_discovery.investigation import create_mission
    from recursive_discovery.capsules import compile_capsule, load_capsule
    l, k, blobs, _ = world
    definition = l.put('note', {'text': 'The exact definition required to interpret this object.'})
    schema = define_schema(l, 'condition', fields={'text': {'type': 'string'}},
        references={'definition': {'min': 1, 'context': True}})
    a = l.put('unregistered-scientific-kind', {'text': 'An interpretation'},
              {'schema': [schema.id], 'definition': [definition.id]})
    m = create_mission(l, 'Inspect a selected scientific object')
    cap = compile_capsule(l, k, blobs, m.id, selections=[{'id': a.id}])
    manifest, _ = load_capsule(l, blobs, cap.id)
    ids = {row['id'] for row in manifest['items']}
    assert {a.id, schema.id, definition.id} <= ids

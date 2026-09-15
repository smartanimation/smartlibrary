from dataclasses import replace
from types import SimpleNamespace
import pytest

from smartlib.apps.smart_reference_editor.service import Reference, Target, compare_cast, suggest_target, ReferenceEditorService
from smartlib.dcc.maya.reference_editor import Operation, apply, snapshot
from smartlib.dcc.maya import smart_menu


def item(path, namespace='hero', **kwargs):
    return SimpleNamespace(**dict(dict(cast_key=namespace, namespace=namespace, publish_path=str(path), status='resolved', message=''), **kwargs))


@pytest.fixture
def scene_files(tmp_path):
    old, new = tmp_path / 'old.ma', tmp_path / 'new.ma'
    old.write_text('// old')
    new.write_text('// new')
    return old, new


def test_instances_are_compared_by_namespace(scene_files):
    old, new = scene_files
    refs = [Reference('heroRN', 'hero', str(new)), Reference('extraRN', 'extra', str(old))]
    rows = compare_cast([item(new), item(new, 'hero2')], refs)
    assert [row.status for row in rows] == ['In sync', 'Added', 'Not in casting']


def test_changed_and_missing_publish(scene_files):
    old, new = scene_files
    refs = [Reference('heroRN', 'hero', str(old), False)]
    assert compare_cast([item(new)], refs)[0].status == 'Changed'
    new.unlink()
    assert compare_cast([item(new)], refs)[0].status == 'Unresolved'


def test_namespace_collision_and_duplicate_cast_blocked(scene_files):
    _, new = scene_files
    assert compare_cast([item(new)], [], ['hero'])[0].status == 'Conflict'
    rows = compare_cast([item(new, 'hero-a'), item(new, 'hero_a')], [])
    assert [row.status for row in rows] == ['Conflict', 'Conflict']


def test_nested_and_duplicate_references_blocked(scene_files):
    _, new = scene_files
    ref = Reference('heroRN', 'hero', str(new), nested=True)
    assert compare_cast([item(new)], [ref])[0].status == 'Conflict'
    assert compare_cast([item(new)], [replace(ref, nested=False), replace(ref, node='otherRN')])[0].status == 'Conflict'


def test_ambiguous_filename_never_suggested(tmp_path):
    targets = [Target('A', str(tmp_path / 'A' / 'hero.ma')), Target('B', str(tmp_path / 'B' / 'hero.ma'))]
    ref = Reference('heroRN', 'hero', 'Z:/client/hero.ma')
    assert suggest_target(ref, targets) is None
    assert suggest_target(ref, targets[:1]) == 0
    assert suggest_target(replace(ref, path=targets[1].path), targets) == 1


class FakeMaya:
    def __init__(self, refs=()):
        self.refs = {ref.node: ref for ref in refs}
        self.calls = []
        self.failed = []
        self.occupied = set()
        self.scene = 'working.ma'

    def ls(self, **kwargs):
        return list(self.refs)

    def namespace(self, exists):
        return exists in self.occupied or any(ref.namespace == exists for ref in self.refs.values())

    def namespaceInfo(self, *args, **kwargs):
        return list(self.occupied) + [ref.namespace for ref in self.refs.values()]

    def referenceQuery(self, node, **kwargs):
        ref = self.refs[node]
        if kwargs.get('namespace'):
            return ':' + ref.namespace
        if kwargs.get('filename'):
            return ref.path
        if kwargs.get('isLoaded'):
            return ref.loaded
        if kwargs.get('parent'):
            return 'parentRN' if ref.nested else None
        if kwargs.get('editStrings'):
            return self.failed
        raise AssertionError(kwargs)

    def file(self, path=None, **kwargs):
        if kwargs.get('query'):
            return self.scene
        self.calls.append((path, kwargs))
        if kwargs.get('reference'):
            ns = kwargs['namespace']
            self.refs[ns + 'RN'] = Reference(ns + 'RN', ns, path)
            return path
        node = kwargs['loadReference']
        self.refs[node] = replace(self.refs[node], path=path)


def test_replacement_preserves_node_namespace_and_deferred_state(scene_files):
    old, new = scene_files
    ref = Reference('heroRN', 'hero', str(old), False)
    maya = FakeMaya([ref])
    result = apply(maya, snapshot(maya), [Operation('replace', 'hero', str(new), ref)])
    assert not result.errors and len(result.completed) == 1
    assert maya.calls == [(str(new), {'loadReference': 'heroRN', 'loadReferenceDepth': 'none'})]
    assert not maya.refs['heroRN'].loaded


def test_stale_snapshot_and_scene_rejected(scene_files):
    old, new = scene_files
    ref = Reference('heroRN', 'hero', str(old))
    maya = FakeMaya([ref])
    snap = snapshot(maya)
    maya.scene = 'another.ma'
    with pytest.raises(RuntimeError, match='changed'):
        apply(maya, snap, [Operation('replace', 'hero', str(new), ref)])
    assert maya.calls == []


def test_preflight_validates_entire_batch_before_mutation(scene_files):
    old, new = scene_files
    ref = Reference('heroRN', 'hero', str(old))
    maya = FakeMaya([ref])
    with pytest.raises(RuntimeError, match='missing'):
        apply(maya, snapshot(maya), [Operation('replace', 'hero', str(new), ref), Operation('add', 'prop', str(new) + '.missing')])
    assert maya.calls == []


def test_failed_edits_stop_batch_with_partial_result(scene_files):
    old, new = scene_files
    ref = Reference('heroRN', 'hero', str(old))
    other = replace(ref, node='otherRN', namespace='other')
    maya = FakeMaya([ref, other])
    maya.failed = ['setAttr hero:missing.tx 1']
    result = apply(maya, snapshot(maya), [Operation('replace', 'hero', str(new), ref), Operation('replace', 'other', str(new), other)])
    assert len(result.errors) == 1 and 'edits failed' in result.errors[0]
    assert len(maya.calls) == 1
    assert maya.refs['otherRN'].path == str(old)


def test_add_and_repeated_apply_is_blocked(scene_files):
    _, new = scene_files
    maya = FakeMaya()
    op = Operation('add', 'hero', str(new))
    snap = snapshot(maya)
    assert len(apply(maya, snap, [op]).completed) == 1
    with pytest.raises(RuntimeError):
        apply(maya, snap, [op])
    assert len(maya.refs) == 1


def test_duplicate_operations_rejected(scene_files):
    _, new = scene_files
    maya = FakeMaya()
    op = Operation('add', 'hero', str(new))
    with pytest.raises(RuntimeError, match='conflict'):
        apply(maya, snapshot(maya), [op, op])


@pytest.mark.parametrize('entries', [[], {}])
def test_menu_migration_is_idempotent(entries):
    data = {'maya_menu': {'categories': {'File': entries}}}
    smart_menu._ensure_reference_editor_entry(data)
    smart_menu._ensure_reference_editor_entry(data)
    assert len(data['maya_menu']['categories']['File']) == 1


def test_catalog_uses_shared_resolvers(scene_files):
    _, new = scene_files
    calls = []
    service = ReferenceEditorService.__new__(ReferenceEditorService)
    asset = SimpleNamespace(category='CHA', group='hero', asset='A', variant='default')
    root = new.parent
    service.casting = SimpleNamespace(list_assets=lambda: [asset], paths=SimpleNamespace(asset_variant_root=lambda identity: calls.append(identity) or root))
    service.resolver = SimpleNamespace(resolve=lambda path, **kw: calls.append((path, kw)) or new)
    targets = service.targets('sequence', 'layout')
    assert targets[0].path == str(new)
    assert calls[0].name == 'A'
    assert calls[1] == (root, {'consumer': 'sequence', 'department': 'layout'})


def test_scene_sequence_identity_uses_resolver(tmp_path):
    from smartlib.apps.shot_manager import SequenceIdentity
    identity = SequenceIdentity('ep01', 's009')
    root = tmp_path / 'canonical-sequence'
    service = ReferenceEditorService.__new__(ReferenceEditorService)
    service.shots = SimpleNamespace(shot_identity_from_path=lambda path: None, shot_departments=['layout', 'anim'])
    service.casting = SimpleNamespace(sequences=lambda: [identity], paths=SimpleNamespace(
        sequence_root_from_scene_path=lambda scene, dept: root,
        sequence_workspace_root=lambda episode, sequence: root))
    assert service.scene_identity('custom/workspace/scene.ma') == identity
    assert service.scene_identity('') is None


@pytest.mark.parametrize('scope', ['sequence', 'shot'])
def test_preview_accepts_identity_from_reloaded_module(scope):
    from dataclasses import make_dataclass
    fields = [('episode', str), ('sequence', str)]
    values = ['ep01', 's009']
    if scope == 'shot':
        fields.append(('shot', str))
        values.append('c001')
    identity = make_dataclass('SequenceIdentity' if scope == 'sequence' else 'ShotIdentity', fields)(*values)
    calls = []
    service = ReferenceEditorService.__new__(ReferenceEditorService)
    service.shots = SimpleNamespace(
        build_sequence_preview=lambda row: calls.append(('sequence', row)) or [],
        build_preview=lambda row, **kwargs: calls.append(('shot', row)) or [])
    assert service.preview(identity) == []
    assert calls == [(scope, identity)]


def test_orphan_reference_is_reported_and_valid_replacement_continues(scene_files):
    old, new = scene_files
    valid = Reference('heroRN', 'hero', str(old))
    maya = FakeMaya([valid, Reference('sotaiMRN2', 'sotaiM', '')])
    query = maya.referenceQuery
    def orphan_query(node, **kwargs):
        if node == 'sotaiMRN2':
            raise RuntimeError("Reference node 'sotaiMRN2' is not associated with a reference file.")
        return query(node, **kwargs)
    maya.referenceQuery = orphan_query
    snap = snapshot(maya)
    orphan = next(ref for ref in snap[1] if ref.node == 'sotaiMRN2')
    assert orphan.error
    result = apply(maya, snap, [Operation('replace', 'hero', str(new), valid)])
    assert not result.errors and len(result.completed) == 1
    assert 'sotaiMRN2' in maya.refs
    with pytest.raises(RuntimeError, match='unique top-level'):
        apply(maya, snapshot(maya), [Operation('replace', orphan.namespace, str(new), orphan)])


def test_unreadable_reference_namespace_not_classified_as_added(scene_files):
    _, new = scene_files
    ref = Reference('heroRN', 'hero', '', error='No associated file')
    assert compare_cast([item(new)], [ref])[0].status == 'Unreadable'

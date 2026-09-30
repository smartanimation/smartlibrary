"""Publication contract and UI regressions; Maya behavior has a mayapy smoke."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from smartlib.dcc.maya.assembly_replacement import export_plan, replacement_contract


def test_readable_namespace_avoids_cast_and_other_placements():
    from smartlib.dcc.maya.assembly_replacement import _asset_namespace
    cmds = SimpleNamespace(namespace=lambda *, exists: exists in {':DeleinChair', ':DeleinChair_2'})
    assert _asset_namespace(cmds, 'DeleinChair') == 'DeleinChair_3'
    assert _asset_namespace(cmds, 'DeleinChair', current='DeleinChair_2') == 'DeleinChair_2'


def test_cast_namespace_fallback_excludes_assembly_owned_nested_references(monkeypatch):
    from smartlib.dcc.maya import assembly_replacement, shot_builder
    monkeypatch.setattr(assembly_replacement, 'assembly_reference_namespaces',
                        lambda cmds: {'Room:DeleinChair', 'DeleinChair_2'})
    cmds = SimpleNamespace(namespace=lambda **_: True,
                           namespaceInfo=lambda *a, **kw: ['DeleinChair', 'Room:DeleinChair',
                               'Room:DeleinChair:rig', 'DeleinChair_2'])
    assert shot_builder._matching_namespaces(cmds, 'DeleinChair') == ['DeleinChair']
    with pytest.raises(RuntimeError, match='Assembly prop'):
        shot_builder._reference_file(cmds, Path('unused.mb'), 'DeleinChair_2')


def test_export_plan_preserves_group_boundary_and_removes_rig_hierarchy():
    target = '|Root|geo_grp|prop_geo_grp|chair_geo_grp'
    meshes = [target + '|rig:Root|rig:joint|rig:seat|rig:seatShape',
              target + '|rig:Root|rig:joint|rig:legs|rig:legsShape']
    background = '|Root|geo_grp|wall|wallShape'
    plan, mapping = export_plan(None, dict(meshes=[background, *meshes],
                                           replacements=[dict(target=target, meshes=meshes)]))
    public = '/Root/geo_grp/prop_geo_grp/chair_geo_grp'
    assert set(plan) == {public + '/seat', public + '/legs', '/Root/geo_grp/wall'}
    assert mapping[target] == public
    assert not any('joint' in path or 'rig:' in path for path in plan)


def test_export_plan_rejects_ambiguous_names_before_scene_changes():
    target = '|Root|chair'
    meshes = [target + '|rig:a|rig:seat|shape', target + '|rig:b|rig:seat|shape']
    with pytest.raises(ValueError, match='unique'):
        export_plan(None, dict(meshes=meshes, replacements=[dict(target=target, meshes=meshes)]))


def test_export_plan_rejects_multiple_roots():
    with pytest.raises(ValueError, match='one public root'):
        export_plan(None, dict(meshes=['|A|wall|shape', '|B|floor|shape'], replacements=[]))


def test_background_contract_excludes_archived_and_nonoutput_rig_meshes(monkeypatch):
    from smartlib.dcc.maya import assembly_replacement as module
    target = '|Root|chair'
    output = target + '|ref:rig|ref:seat|shape'
    row = dict(id='chair-1', target=target, meshes=[output])
    monkeypatch.setattr(module, 'validate_replacements', lambda cmds, rows: rows)
    monkeypatch.setattr(module, 'collect_meshes', lambda *args: [
        '|Root|wall|shape', target + '|__assemblyOriginal|old|shape',
        target + '|ref:rig|blendTarget|shape', output])
    cmds = SimpleNamespace(ls=lambda **_: ['cache_geo_set', 'ref:cache_geo_set'],
                           referenceQuery=lambda name, **_: name.startswith('ref:'))
    result = replacement_contract(cmds, [row])
    assert result['meshes'] == sorted(['|Root|wall|shape', output])
    assert result['geometry_set'] == 'cache_geo_set'


def test_failed_background_contract_does_not_silently_publish_unregistered_group(monkeypatch):
    from smartlib.dcc.maya import assembly_replacement as module
    monkeypatch.setattr(module, 'validate_replacements', lambda cmds, rows: rows)
    monkeypatch.setattr(module, 'collect_meshes', lambda *args: ['|Root|wall|shape'])
    cmds = SimpleNamespace(ls=lambda **_: ['cache_geo_set'], referenceQuery=lambda *a, **kw: False)
    with pytest.raises(ValueError, match='Add the replacement group'):
        replacement_contract(cmds, [dict(id='chair', target='|Root|chair', meshes=[])])


def test_release_manifest_carries_fixed_component_dependencies():
    from smartlib.apps.asset_manager.environment_release import assembly_for_release
    from dataclasses import dataclass, field
    @dataclass
    class Assembly:
        quality_profile: str = 'PROXY'
        entries: list = field(default_factory=list)
        errors: list = field(default_factory=list)
        manifest: dict = field(default_factory=dict)
    components = [dict(id='chair', target_path='|Root|chair', version='v005', source_path='resolved.mb')]
    result = assembly_for_release(Assembly(), Path('v006'), dict(
        version='v006', absolute_files={'mb': 'background.mb', 'usd': 'background.usd'},
        component_replacements=components))
    assert result.manifest['component_replacements'] == components
    restored = assembly_for_release(result, Path('v007'), dict(
        version='v007', absolute_files={'mb': 'restored.mb', 'usd': 'restored.usd'}))
    assert 'component_replacements' not in restored.manifest


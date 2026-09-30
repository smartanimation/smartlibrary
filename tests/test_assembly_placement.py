import pytest

from pxr import Gf, Usd, UsdGeom
from smartlib.apps.shot_manager.assembly_placement import resolve
from smartlib.apps.shot_manager.placement_motion import author_placement


def flat(matrix):
    return [v for row in matrix for v in row]


def fixture():
    stage = Usd.Stage.CreateInMemory()
    root = UsdGeom.Xform.Define(stage, '/Shot/Assets/BG')
    root.AddTranslateOp().Set((10, 0, 0))
    group = UsdGeom.Xform.Define(stage, '/Shot/Assets/BG/Props/Chair')
    group.AddTranslateOp().Set((0, 5, 0))
    group.GetPrim().SetCustomDataByKey('smartpipeline:assembly:instanceId', 'chair-id')
    geometry = []
    delta = Gf.Matrix4d().SetTranslate((20, 0, 0))
    for name in ('Seat', 'Back'):
        mesh = UsdGeom.Xform.Define(stage, str(group.GetPath()) + '/' + name)
        mesh.AddTranslateOp().Set((0, 0, 2 if name == 'Seat' else 4))
        mesh.GetPrim().SetCustomDataByKey('smartpipeline:setdress:sourcePath', name)
        matrix = UsdGeom.XformCache().GetLocalToWorldTransform(mesh.GetPrim()) * delta
        geometry.append(dict(source_path=name, samples=[dict(frame=1., matrix=flat(matrix))]))
    return stage, dict(cast='BG', instance_id='chair-id', geometry=geometry), dict(
        mode='STATIC', samples=[dict(frame=1., matrix=flat(Gf.Matrix4d(1)))])


def test_nested_placement_preserves_hierarchy_and_world_transform():
    stage, binding, data = fixture()
    paths = [str(p.GetPath()) for p in stage.Traverse()]
    path, motion = resolve(stage, binding, data)
    author_placement(stage.GetPrimAtPath(path), motion)
    assert paths == [str(p.GetPath()) for p in stage.Traverse()]
    for item in binding['geometry']:
        prim = stage.GetPrimAtPath(path + '/' + item['source_path'])
        assert Gf.IsClose(UsdGeom.XformCache().GetLocalToWorldTransform(prim),
                          Gf.Matrix4d(*item['samples'][0]['matrix']), 1e-8)


def test_missing_id_rejects_old_background():
    stage, binding, data = fixture()
    binding['instance_id'] = 'missing'
    with pytest.raises(ValueError, match='Expected one Assembly'):
        resolve(stage, binding, data)


def test_nonrigid_motion_is_rejected():
    stage, binding, data = fixture()
    binding['geometry'][1]['samples'][0]['matrix'][12] += 1
    with pytest.raises(ValueError, match='rigid geometry'):
        resolve(stage, binding, data)


def test_same_id_in_other_background_is_not_selected():
    stage, binding, data = fixture()
    other = UsdGeom.Xform.Define(stage, '/Shot/Assets/Other/Chair').GetPrim()
    other.SetCustomDataByKey('smartpipeline:assembly:instanceId', 'chair-id')
    assert resolve(stage, binding, data)[0] == '/Shot/Assets/BG/Props/Chair'


def test_evaluated_vertices_capture_rig_motion():
    from smartlib.apps.shot_manager.assembly_placement import _rigid_delta
    points = [Gf.Vec3d(0, 0, 0), Gf.Vec3d(1, 0, 0), Gf.Vec3d(0, 1, 0), Gf.Vec3d(0, 0, 1)]
    expected = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0, 1, 0), 30))
    expected.SetTranslateOnly((12, 3, 4))
    moved = [v for p in points for v in expected.Transform(p)]
    assert Gf.IsClose(_rigid_delta(points, moved), expected, 1e-8)
    moved[-1] += 1
    with pytest.raises(ValueError, match='rigid geometry'):
        _rigid_delta(points, moved)


def test_maya_capture_uses_assembly_owner_and_restores_time(monkeypatch):
    import json
    from types import SimpleNamespace
    from smartlib.dcc.maya import assembly_placement as capture_module
    group = '|assets|BG:Root|BG:chair'
    parent = group + '|BG:Chair_GRP|BG:Chair:geo'
    current = [42]
    def time(value=None, **kwargs):
        if kwargs.get('query'):
            return current[0]
        current[0] = value
    cmds = SimpleNamespace(
        ls=lambda pattern, **kwargs: [group + '.' + capture_module.ATTR] if kwargs.get('recursive') else [group],
        referenceQuery=lambda node, **kwargs: ('BG:Chair' if node == 'childRN' else 'BG') if kwargs.get('namespace') else 'parentRN',
        getAttr=lambda plug: json.dumps(dict(id='chair-id', target_path='|Root|chair')),
        currentTime=time,
        xform=lambda node, **kwargs: [0, 0, 0, 1, 0, 0, 0, 1, 0] if kwargs.get('translation') else flat(Gf.Matrix4d(1)),
    )
    monkeypatch.setattr(capture_module, '_linked', lambda *args: ['childRN'])
    monkeypatch.setattr(capture_module, '_reference_geometry', lambda *args: ('set', [parent + '|shape']))
    result = capture_module.capture(cmds, SimpleNamespace(member='BG:Chair'), {'BG_main': {'namespace': 'BG'}}, [1, 2])
    assert result['cast'] == 'BG_main'
    assert result['instance_id'] == 'chair-id'
    assert result['geometry'][0]['source_path'] == '|Root|chair|Chair_GRP|geo'
    assert [s['frame'] for s in result['geometry'][0]['samples']] == [1, 2]
    assert current[0] == 42

import json
from pathlib import Path

import pytest

from smartlib.apps.shot_manager import ShotIdentity, ShotManagerService
from smartlib.apps.shot_manager.usd_handoff import UsdHandoffService, layout_changes
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json, write_json
from smartlib.dcc.maya.set_dress import SetDressPackage, SetDressLayer, Change


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.delenv('SMARTPIPELINE_STUDIO_CONFIG', raising=False)
    monkeypatch.delenv('SMARTPIPELINE_STUDIO_CONFIG_DIR', raising=False)
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'templates_base.yml').write_text(
        f"anchors:\n  project_name: TEST\n  project_root: '{tmp_path.as_posix()}/project'\n", encoding='utf8')
    shots = ShotManagerService(ProjectConfig(config))
    return UsdHandoffService(shots), ShotIdentity('ep01', 'sq01', 'sh001')


def asset(tmp_path, kind='asset'):
    from pxr import Usd, UsdGeom
    path = tmp_path / kind / 'v001' / (kind + '.usda')
    path.parent.mkdir(parents=True)
    stage = Usd.Stage.CreateNew(str(path))
    root = UsdGeom.Xform.Define(stage, '/Root')
    stage.SetDefaultPrim(root.GetPrim())
    if kind == 'camera':
        UsdGeom.Camera.Define(stage, '/Root/cam')
    else:
        UsdGeom.Cube.Define(stage, '/Root/geo')
    UsdGeom.SetStageUpAxis(stage, 'Y')
    UsdGeom.SetStageMetersPerUnit(stage, .01)
    stage.GetRootLayer().Save()
    return path


def plan(svc, identity, rows):
    return svc.plan(identity, rows, frame_range=[1, 2], fps=24)


def test_partial_background_camera_without_profile_or_submit(service, tmp_path):
    from pxr import Usd, UsdGeom
    svc, identity = service
    bg, cam = asset(tmp_path), asset(tmp_path, 'camera')
    result = svc.publish(identity, plan(svc, identity, [
        dict(kind='assets', target='BG_01', source=str(bg)),
        dict(kind='camera', target='primary', source=str(cam))]))
    data = svc.load_handoff(result)
    stage = Usd.Stage.Open(data['entrypoint']['path'])
    assert stage.GetPrimAtPath('/Shot/Assets/BG_01/geo')
    assert stage.GetPrimAtPath('/Shot/Camera/primary/cam').IsA(UsdGeom.Camera)
    assert data['approval'] == 'not_reviewed' and data['partial']
    assert not stage.GetPrimAtPath('/Shot/Animation')
    again = svc.compose_products(identity, [v['path'] for v in data['products']])
    assert read_json(again, {})['version'] == 'v002'
    assert read_json(again, {})['products'] == data['products']


def test_setdress_is_sparse_and_does_not_modify_asset(service, tmp_path):
    from pxr import Usd, UsdGeom
    svc, identity = service
    bg = asset(tmp_path)
    original = bg.read_bytes()
    package = SetDressPackage(layers=[SetDressLayer(changes=[Change('id', 'node', 'translateX', 0, 12)])])
    source = tmp_path / 'layout' / 'v001' / 'layout.setdress.json'
    write_json(source, package.to_dict())
    result = svc.publish(identity, plan(svc, identity, [dict(kind='assets', target='BG', source=str(bg)),
        dict(kind='layout', target='main', source=str(source), node_map={'id': '/Shot/Assets/BG'})]))
    data = svc.load_handoff(result)
    stage = Usd.Stage.Open(data['entrypoint']['path'])
    pos = UsdGeom.XformCommonAPI(stage.GetPrimAtPath('/Shot/Assets/BG')).GetXformVectors(Usd.TimeCode.Default())[0]
    assert pos[0] == 12
    assert bg.read_bytes() == original
    text = Path(data['layers']['layout']['path']).read_text()
    assert 'over "BG"' in text and 'points' not in text and 'references' not in text
    assert 'xformOp:rotate' not in text and 'xformOp:scale' not in text


def test_setdress_missing_mapping_and_unsupported_attribute():
    p = SetDressPackage(layers=[SetDressLayer(changes=[Change('id', 'node', 'translateX', 0, 1)])])
    with pytest.raises(ValueError, match='mapping'):
        layout_changes(p, {})
    p.layers[0].changes[0].attribute = 'customRigControl'
    with pytest.raises(ValueError, match='no USD mapping'):
        layout_changes(p, {'id': '/Shot/Assets/BG'})


def test_changed_input_fails_before_publish(service, tmp_path):
    svc, identity = service
    bg = asset(tmp_path)
    selection = plan(svc, identity, [dict(kind='assets', target='BG', source=str(bg))])
    bg.write_text(bg.read_text() + '\n# changed\n')
    with pytest.raises(ValueError, match='changed'):
        svc.publish(identity, selection)


def test_work_and_duplicate_primary_rejected(service, tmp_path):
    svc, identity = service
    work = tmp_path / 'WORK.usda'
    work.write_text('not published')
    with pytest.raises(ValueError, match='fixed'):
        svc.pin(work)
    cam = asset(tmp_path, 'camera')
    with pytest.raises(ValueError, match='Primary'):
        plan(svc, identity, [dict(kind='camera', target=t, source=str(cam)) for t in ['one', 'two']])


def test_sculpt_absent_is_not_missing_but_selected_missing_is_error(service, tmp_path):
    from smartlib.apps.shot_manager.animation_publish import file_hash
    svc, identity = service
    directory = tmp_path / 'animation' / 'v001'
    directory.mkdir(parents=True)
    rig = directory / 'rig.ma'
    rig.write_text('// test rig')
    atom = directory / 'animation.atom'
    atom.write_text('atomVersion 1.0;')
    source = directory / 'animation_manifest.json'
    write_json(source, dict(schema='smartpipeline.animation_atom.v3', payload=atom.name,
        payload_sha256=file_hash(atom), rig_dependencies=[svc.pin(rig)], frame_range=[1, 2]))
    row = dict(kind='animation', target='Hero', source=str(source), rig=str(rig))
    assert plan(svc, identity, [row])['rows'][0]['sculpt'] is None
    row['sculpt'] = str(directory / 'missing.json')
    with pytest.raises(ValueError, match='fixed'):
        plan(svc, identity, [row])


def test_explicit_context_rig_keeps_source_provenance(service, tmp_path, monkeypatch):
    from smartlib.apps.shot_manager.animation_publish import file_hash
    svc, identity = service
    directory = tmp_path / 'animation' / 'v001'
    directory.mkdir(parents=True)
    rig = directory / 'source.ma'
    rig.write_text('// source Rig')
    selected = directory / 'anim.ma'
    selected.write_text('// selected ANIM Rig')
    atom = directory / 'animation.atom'
    atom.write_text('atomVersion 1.0;')
    source = write_json(directory / 'animation_manifest.json', dict(
        schema='smartpipeline.animation_atom.v3', payload=atom.name,
        payload_sha256=file_hash(atom), rig_dependencies=[svc.pin(rig)], frame_range=[1, 2]))
    monkeypatch.setattr(svc, 'animation_rig_versions', lambda i, t:
        {'ANIM': [{'path': str(selected), 'version': 'v001'}]} if t == 'Hero' else {})
    row = dict(kind='animation', target='Hero', source=str(source), rig=str(selected), rig_context='ANIM')
    frozen = plan(svc, identity, [row])
    assert frozen['rows'][0]['rig'] == svc.pin(selected)
    assert read_json(source, {})['rig_dependencies'] == [svc.pin(rig)]
    svc.validate_plan(frozen, identity)
    with pytest.raises(ValueError, match='context'):
        plan(svc, identity, [dict(row, rig_context='REND')])
    with pytest.raises(ValueError, match='context'):
        plan(svc, identity, [dict(row, target='Other')])
    del row['rig_context']
    with pytest.raises(ValueError, match='not pinned'):
        plan(svc, identity, [row])


def test_context_versions_use_shared_asset_paths(service, monkeypatch):
    from smartlib.core.path_resolver import AssetIdentity
    from smartlib.apps.asset_manager.context import AssetContextService
    svc, identity = service
    asset_id = AssetIdentity('CH', 'main', 'Hero', 'default')
    root = svc.paths.asset_root(asset_id)
    write_json(svc.paths.artifact_file(root, 'asset.json'),
        {'category': 'CH', 'group': 'main', 'asset': 'Hero'})
    monkeypatch.setattr(svc.shots, 'load_cast', lambda _: {'cast': {'Hero_main':
        {'asset': 'Hero', 'variant': 'default'}}})
    monkeypatch.setattr(AssetContextService, 'quality_profiles_for_asset', lambda self, asset: ['ANIM', 'REND'])
    directory = svc.paths.asset_publish_dir(asset_id, 'asset', 'anim')
    rig = svc.paths.artifact_file(svc.paths.artifact_file(directory, 'v003'), 'Hero.ma')
    rig.parent.mkdir(parents=True)
    rig.write_text('// published')
    rows = svc.animation_rig_versions(identity, 'Hero_main')
    assert rows['ANIM'] == [{'version': 'v003', 'path': str(rig)}]
    assert rows['REND'] == []


def test_sculpt_applies_only_to_matching_animation_and_topology(tmp_path):
    from pxr import Usd, UsdGeom
    from smartlib.dcc.maya.shot_sculpt import apply_to_usd, topology_signature, SCHEMA
    path = tmp_path / 'mesh.usda'
    stage = Usd.Stage.CreateNew(str(path))
    mesh = UsdGeom.Mesh.Define(stage, '/Hero/body')
    mesh.GetFaceVertexCountsAttr().Set([3])
    mesh.GetFaceVertexIndicesAttr().Set([0, 1, 2])
    for frame in (1, 2):
        mesh.GetPointsAttr().Set([(0, 0, 0), (1, 0, 0), (0, 1, 0)], frame)
    stage.GetRootLayer().Save()
    data = dict(schema=SCHEMA, space='post_deform_object', curve_sha256='curves', frame_range=[1, 2],
        meshes=[dict(prim_path='/Hero/body', topology_signature=topology_signature(mesh),
            samples={str(f): [[0, 0, 1]] * 3 for f in (1, 2)})])
    with pytest.raises(ValueError, match='different'):
        apply_to_usd(path, data, curve_sha256='other', frame_range=[1, 2])
    apply_to_usd(path, data, curve_sha256='curves', frame_range=[1, 2])
    assert mesh.GetPointsAttr().Get(1)[0][2] == 1


def test_sculpt_capture_roundtrip(tmp_path):
    from pxr import Usd, UsdGeom
    from smartlib.dcc.maya.shot_sculpt import collect_from_usd, apply_to_usd
    paths = [tmp_path / (name + '.usda') for name in ('base', 'sculpted')]
    for offset, path in enumerate(paths):
        stage = Usd.Stage.CreateNew(str(path))
        mesh = UsdGeom.Mesh.Define(stage, '/Hero/body')
        mesh.GetFaceVertexCountsAttr().Set([3])
        mesh.GetFaceVertexIndicesAttr().Set([0, 1, 2])
        for frame in (1, 2):
            mesh.GetPointsAttr().Set([(0, 0, offset), (1, 0, offset), (0, 1, offset)], frame)
        stage.GetRootLayer().Save()
    data = collect_from_usd(*paths, curve_sha256='curve', frame_range=[1, 2])
    assert data['meshes'][0]['samples']['1'][0] == [0, 0, 1]
    apply_to_usd(paths[0], data, curve_sha256='curve', frame_range=[1, 2])
    stage = Usd.Stage.Open(str(paths[0]))
    assert UsdGeom.Mesh(stage.GetPrimAtPath('/Hero/body')).GetPointsAttr().Get(1)[0][2] == 1


def test_usd_dialog_constructs_without_maya(service, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    pytest.importorskip('PySide6')
    from PySide6 import QtWidgets
    from smartlib.apps.shot_manager.usd_handoff_ui import UsdPublishDialog
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    svc, identity = service
    dialog = UsdPublishDialog(svc.shots, identity)
    assert dialog.table.rowCount() == 1
    assert not hasattr(dialog, 'open_btn')
    dialog.add_row()
    assert dialog.table.rowCount() == 2
    dialog.close()


def test_constraint_detection_stays_on_transfer_controls():
    from smartlib.dcc.maya.animation_data_bake import constraint_plugs
    class Commands:
        def listConnections(self, plug, **kwargs):
            return {'ctrl.tx': ['blend.out'], 'blend': ['constraint.out'],
                    'ctrl.ty': ['curve.out']}.get(plug, [])
        def nodeType(self, node):
            return {'blend': 'pairBlend', 'constraint': 'parentConstraint', 'curve': 'animCurveTL'}[node]
    assert constraint_plugs(Commands(), ['ctrl'], lambda c, n: [n+'.tx', n+'.ty']) == ['ctrl.tx']

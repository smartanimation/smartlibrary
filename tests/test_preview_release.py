from pathlib import Path
import pytest
from test_usd_handoff import service
from test_preview_look import fixture_files
from smartlib.apps.asset_manager.preview_publish import PreviewPublishService
from smartlib.apps.asset_manager.preview_release import release_preview
from smartlib.core.metadata import read_json
from smartlib.core.path_resolver import AssetIdentity
from smartlib.core.preview_look import write_preview_look


def inputs(service, tmp_path):
    svc, _ = service
    publisher = PreviewPublishService(svc.config)
    identity = AssetIdentity('environment', 'park', 'park')
    geo, _ = fixture_files(tmp_path)
    source = tmp_path / 'source.mb'
    source.write_bytes(b'Maya source')
    geometry = publisher.publish_geometry(identity, geo.GetRootLayer().identifier, source)
    recipe = dict(materials={'body': {'diffuse_color': [.2, .3, .4]}}, bindings={'/Geometry/body': 'body'})
    staged = tmp_path / 'look.usda'
    write_preview_look(staged, read_json(geometry, {})['artifacts']['usd']['path'], recipe)
    look = publisher.publish_look(identity, staged, geometry, None, recipe, dcc='maya')
    return publisher, identity, geometry, look


def test_release_composes_pinned_inputs_without_maya(service, tmp_path):
    from pxr import Usd, UsdShade
    publisher, identity, geometry, look = inputs(service, tmp_path)
    original = {str(p): p.read_bytes() for p in [geometry, look]}
    manifest = release_preview(publisher, identity, geometry, look)
    record = read_json(manifest, {})
    stage = Usd.Stage.Open(record['absolute_files']['usd'])
    assert UsdShade.MaterialBindingAPI(stage.GetPrimAtPath('/Geometry/body')).ComputeBoundMaterial()[0]
    assert not stage.GetRootLayer().GetPrimAtPath('/Geometry/body')
    assert record['geometry'] == publisher.pin(geometry)
    assert record['look'] == publisher.pin(look)
    assert set(record['files']) == {'usd'}
    entry = Usd.Stage.Open(record['usd_entrypoint'])
    assert entry.GetPrimAtPath('/park/body')
    from smartlib.core.asset_publish_resolver import AssetPublishResolver
    assert AssetPublishResolver(publisher.project_config).resolve_usd_entry(identity)['pack_version'] == 'v001'
    second = release_preview(publisher, identity, geometry, look)
    assert read_json(second, {})['version'] == 'v002'
    assert all(Path(p).read_bytes() == data for p, data in original.items())
    assert Usd.Stage.Open(record['usd_entrypoint']).GetPrimAtPath('/park/body')


def test_release_rejects_modified_and_foreign_dependencies(service, tmp_path):
    publisher, identity, geometry, look = inputs(service, tmp_path)
    with pytest.raises(ValueError, match='another Asset'):
        release_preview(publisher, AssetIdentity('environment', 'park', 'other'), geometry, look)
    path = Path(read_json(geometry, {})['artifacts']['usd']['path'])
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='changed'):
        release_preview(publisher, identity, geometry, look)
    assert not publisher.paths.asset_publish_dir(identity, 'asset', 'proxy').exists()


def test_release_dialog_and_context_discovery(service, tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from smartlib.apps.asset_manager.preview_release_ui import PreviewReleaseDialog, QtWidgets
    from smartlib.apps.asset_manager.context import AssetContextService, AssetContextAssembly
    from smartlib.apps.asset_manager.environment_release import latest_release
    publisher, identity, geometry, look = inputs(service, tmp_path)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dialog = PreviewReleaseDialog(publisher.project_config, identity)
    assert dialog.release.isEnabled()  # Defaults to low input and proxy quality.
    dialog.subset.setCurrentText('low')
    assert dialog.release.isEnabled()
    assert dialog.geometry.currentData() == str(geometry)
    assert dialog.look.currentData() == str(look)
    dialog.execute()
    assert dialog.manifest, dialog.status.text()
    context = AssetContextService(publisher.project_config)
    assembly = AssetContextAssembly(identity, 'asset', 'v001', 'PROXY', [], ['missing model'], {})
    result = latest_release(context, assembly)
    assert not result.errors
    assert result.manifest['source_policy'] == 'preview_release'
    assert set(result.entries[0].files) == {'usd'}
    assert not context.has_pack_changes(result)
    dialog.close()


def test_quality_release_panel_is_independent_of_context(service, tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from pxr import Usd
    from smartlib.apps.asset_manager.preview_release_ui import PreviewReleasePanel, QtWidgets
    publisher, identity, geometry, look = inputs(service, tmp_path)
    release_preview(publisher, identity, geometry, look, subset='proxy')
    geo_record = read_json(geometry, {})
    high_geo = publisher.publish_geometry(identity, geo_record['artifacts']['usd']['path'],
        geo_record['source_scene']['path'], subset='high')
    recipe = read_json(look, {})['recipe']
    staged = tmp_path / 'high-look.usda'
    write_preview_look(staged, read_json(high_geo, {})['artifacts']['usd']['path'], recipe)
    publisher.publish_look(identity, staged, high_geo, None, recipe, dcc='maya', subset='high')
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = PreviewReleasePanel(publisher.project_config)
    assert not panel.release.isEnabled()
    panel.configure(identity)
    from smartlib.apps.asset_manager.texture_publish_ui import QtCore
    assert not panel.quality.item(1).flags() & QtCore.Qt.ItemIsEnabled
    high_look = publisher.paths.artifact_file(publisher.paths.asset_publish_version_dir(identity, 'look', 'high', 'v001'), 'publish.json')
    manifest = release_preview(publisher, identity, high_geo, high_look, subset='render')
    record = read_json(manifest, {})
    assert record['subset'] == 'render' and record['look_purpose'] == 'preview'
    assert panel.history.topLevelItemCount() == 1
    stage = Usd.Stage.Open(record['usd_entrypoint'])
    root = stage.GetDefaultPrim()
    assert set(root.GetVariantSet('quality').GetVariantNames()) == {'proxy', 'render'}
    assert panel.history.topLevelItemCount() == 1
    panel.configure(None)
    assert not panel.release.isEnabled() and panel.history.topLevelItemCount() == 0
    panel.close()


def test_release_check_is_temporary_and_history_paths_follow_selection(service, tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from smartlib.apps.asset_manager.preview_release_ui import PreviewReleasePanel, QtWidgets
    publisher, identity, geometry, look = inputs(service, tmp_path)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = PreviewReleasePanel(publisher.project_config, identity)
    previews = []
    panel.previewReady.connect(previews.append)
    panel.check_preview()
    assert len(previews) == 1, panel.status.text()
    assert Path(previews[0]).is_file()
    assert not publisher.paths.asset_publish_dir(identity, 'asset', 'proxy').exists()
    panel.execute()
    first = panel.manifest
    panel.execute()
    assert panel.history.topLevelItemCount() == 2
    assert panel.history.topLevelItem(0).text(3) == 'LATEST'
    assert panel.history.topLevelItem(0).childCount() == 2
    panel.history.setCurrentItem(panel.history.topLevelItem(1).child(0))
    assert panel.paths['release'][0].text() == read_json(first, {})['absolute_files']['usd']
    panel.look_subset.setCurrentText('proxy')
    assert not panel.release.isEnabled() and not panel.check_button.isEnabled()
    panel.configure(None)
    assert not panel.paths['entry'][0].text()
    panel.close()



def rig_input(publisher, identity, tmp_path, subset='anim'):
    from pxr import UsdGeom, UsdSkel, Gf
    from test_skel_layers import source
    from smartlib.core.skel_layers import split_skel_layers
    stage = source(tmp_path)
    UsdGeom.Xform.Define(stage, '/Asset/Geometry')
    stage.GetPrimAtPath('/Asset/Geometry/body').RemoveProperty('primvars:st')
    UsdSkel.BindingAPI(stage.GetPrimAtPath('/Asset/Geometry/body')).CreateGeomBindTransformAttr(Gf.Matrix4d(1))
    UsdGeom.SetStageMetersPerUnit(stage, .01)
    UsdGeom.SetStageUpAxis(stage, 'Y')
    stage.GetRootLayer().Save()
    version, directory = publisher.reserve(identity, 'rig', subset)
    files = dict(usd='usdSkel.usd', geometry_usd='geo.usd', rig_usd='rig.usd', validation='validation.json')
    paths = {k: publisher.paths.artifact_file(directory, v) for k, v in files.items()}
    split_skel_layers(stage.GetRootLayer().identifier, geometry_path=paths['geometry_usd'], rig_path=paths['rig_usd'], entry_path=paths['usd'])
    paths['validation'].write_text('{}')
    return publisher.commit(identity, 'rig', subset, version, directory, files,
        usd_skel=dict(schema='smartpipeline.usd_skel.v2', entry='usdSkel.usd', geometry='geo.usd', rig='rig.usd', validation='validation.json'))


def test_rig_release_preserves_skinning_look_and_shared_entry(service, tmp_path):
    from pxr import Usd, UsdGeom, UsdShade, UsdSkel, Gf
    publisher, identity, geometry, look = inputs(service, tmp_path)
    rig = rig_input(publisher, identity, tmp_path)
    manifest = release_preview(publisher, identity, geometry, look, rig_manifest=rig)
    record = read_json(manifest, {})
    assert record['rig'] == publisher.pin(rig)
    assert len(record['resolved_representations']) == 3
    stage = Usd.Stage.Open(record['usd_entrypoint'])
    assert stage.GetDefaultPrim().IsA(UsdSkel.Root)
    meshes = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
    assert len(meshes) == 1
    mesh = meshes[0]
    assert UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()[0]
    assert UsdSkel.BindingAPI(mesh).GetInheritedSkeleton()
    # Bake in memory: shared Asset entry must remain a functioning SkelRoot.
    stage = Usd.Stage.Open(stage.Flatten())
    anim = UsdSkel.Animation.Define(stage, '/park/Animation')
    anim.CreateJointsAttr(['root'])
    anim.CreateTranslationsAttr().Set([(2,0,0)], 1)
    anim.CreateRotationsAttr().Set([Gf.Quatf(1)], 1)
    anim.CreateScalesAttr().Set([(1,1,1)], 1)
    UsdSkel.BindingAPI.Apply(stage.GetPrimAtPath('/park/Skeleton')).CreateAnimationSourceRel().SetTargets([anim.GetPath()])
    assert UsdSkel.BakeSkinning(Usd.PrimRange(stage.GetDefaultPrim()))
    points = UsdGeom.Mesh(stage.GetPrimAtPath(mesh.GetPath())).GetPointsAttr()
    assert points.GetNumTimeSamples() > 0
    assert points.Get(1)[0][0] == pytest.approx(2)
    output = Usd.Stage.Open(record['absolute_files']['usd'])
    assert not output.GetRootLayer().GetAttributeAtPath('/Asset/Geometry/body.points')
    assert output.GetPrimAtPath('/Asset/Geometry/body').GetMetadata('references')


def test_rig_release_rejects_wrong_bind_shape_before_reserving(service, tmp_path):
    from pxr import Usd, UsdGeom
    publisher, identity, geometry, look = inputs(service, tmp_path)
    rig = rig_input(publisher, identity, tmp_path)
    src = Usd.Stage.Open(read_json(geometry, {})['artifacts']['usd']['path'])
    changed = tmp_path / 'changed.usda'
    src.Export(str(changed))
    edited = Usd.Stage.Open(str(changed))
    UsdGeom.Mesh(edited.GetPrimAtPath('/Geometry/body')).GetPointsAttr().Set([(10,0,0),(1,0,0),(0,1,0)])
    edited.GetRootLayer().Save()
    geometry = publisher.publish_geometry(identity, changed, tmp_path / 'source.mb')
    with pytest.raises(ValueError, match='bind shape'):
        release_preview(publisher, identity, geometry, look, rig_manifest=rig)
    assert not publisher.paths.asset_publish_dir(identity, 'asset', 'proxy').exists()


def test_release_panel_optional_rig_selection(service, tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from smartlib.apps.asset_manager.preview_release_ui import PreviewReleasePanel, QtWidgets
    publisher, identity, geometry, look = inputs(service, tmp_path)
    rig = rig_input(publisher, identity, tmp_path)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = PreviewReleasePanel(publisher.project_config, identity)
    assert panel.inputs.rowCount() == 3 and panel.rig.currentData() is None
    panel.rig.setCurrentIndex(panel.rig.findData(str(rig)))
    panel.refresh()
    assert panel.rig.currentData() == str(rig)
    panel.execute()
    assert panel.manifest, panel.status.text()
    assert panel.history.topLevelItem(0).childCount() == 3
    assert Path(read_json(panel.manifest, {})['rig']['path']) == rig
    low_source = tmp_path / 'low-source'
    low_source.mkdir()
    low = rig_input(publisher, identity, low_source, subset='low')
    panel.refresh()
    assert {panel.rig_subset.itemText(i) for i in range(panel.rig_subset.count())} == {'anim', 'low'}
    assert panel.rig.currentData() == str(rig)
    panel.rig_subset.setCurrentText('low')
    assert panel.rig.currentData() == str(low)
    assert panel.inputs.item(2, 3).text() == 'v001'
    panel.execute()
    assert Path(read_json(panel.manifest, {})['rig']['path']) == low
    panel.configure(None)
    assert panel.rig_subset.count() == 0
    panel.close()

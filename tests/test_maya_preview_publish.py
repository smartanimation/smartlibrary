import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from test_texture_publish import publisher
from test_preview_look import fixture_files
from smartlib.core.path_resolver import AssetIdentity
from smartlib.core.preview_look import write_preview_look


def test_work_scene_accepts_any_department_but_rejects_other_assets(publisher, tmp_path):
    import pytest
    from smartlib.dcc.maya.current_preview import validate_work_scene
    identity = AssetIdentity('character', 'main', 'JIN')
    for department in ('model', 'look', 'rig', 'custom_department'):
        source = publisher.paths.artifact_file(publisher.paths.asset_work_dir(identity, department, 'maya'), 'scene.mb')
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'Maya scene')
        assert validate_work_scene(publisher.paths, identity, source) == source.resolve()
        with pytest.raises(ValueError, match='Asset/Variant'):
            validate_work_scene(publisher.paths, AssetIdentity('character', 'main', 'DLI'), source)
    outside = tmp_path / 'outside.mb'
    outside.write_bytes(b'Maya scene')
    with pytest.raises(ValueError, match='Asset/Variant'):
        validate_work_scene(publisher.paths, identity, outside)
    missing = publisher.paths.artifact_file(publisher.paths.asset_work_dir(identity, 'rig', 'maya'), 'missing.mb')
    with pytest.raises(ValueError, match='existing'):
        validate_work_scene(publisher.paths, identity, missing)


def test_dependency_latest_uses_pointer_not_directory_order(publisher):
    from smartlib.apps.asset_manager.current_preview_ui import dependency_versions
    from smartlib.core.metadata import write_json
    identity = AssetIdentity('character', 'main', 'DLI')
    root = publisher.paths.asset_publish_dir(identity, 'texture', 'low')
    for version in ('v002', 'v010', 'v1000'):
        directory = publisher.paths.asset_publish_version_dir(identity, 'texture', 'low', version)
        directory.mkdir(parents=True)
        write_json(publisher.paths.artifact_file(directory, 'publish.json'), {'status': 'published'})
    write_json(publisher.paths.artifact_file(root, 'latest.json'), {'version': 'v010'})
    rows, latest = dependency_versions(publisher, identity, 'texture', 'low')
    assert [v for v, _ in rows] == ['v1000', 'v010', 'v002']
    assert latest == 'v010'


def test_preview_uv_and_color_parameters(tmp_path):
    from pxr import Usd
    geo, _ = fixture_files(tmp_path)
    texture = tmp_path / 'color.png'
    texture.write_bytes(b'image')
    result = tmp_path / 'look.usda'
    write_preview_look(result, geo.GetRootLayer().identifier,
        dict(materials={'body': dict(texture=str(texture), scale=[2, 3], color_space='raw',
             color_scale=[.8, .8, .8, 1], wrap_t='repeat')}, bindings={'/Geometry/body': 'body'}))
    stage = Usd.Stage.Open(str(result))
    uv = stage.GetPrimAtPath('/Geometry/Looks/body/uv_transform')
    assert tuple(uv.GetAttribute('inputs:scale').Get()) == (2, 3)
    image = stage.GetPrimAtPath('/Geometry/Looks/body/texture')
    assert image.GetAttribute('inputs:sourceColorSpace').Get() == 'raw'
    assert image.GetAttribute('inputs:wrapT').Get() == 'repeat'


def test_dialog_requires_published_dependencies(publisher):
    from smartlib.apps.asset_manager.maya_preview_ui import MayaPreviewPublishDialog, QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dialog = MayaPreviewPublishDialog(publisher.project_config, AssetIdentity('character','main','DLI'), 'look')
    dialog.source.setText(__file__)
    dialog.start()
    assert dialog.process is None
    assert 'Publish Geometry before Look' in dialog.log.toPlainText()
    dialog.close()


def test_solid_look_publishes_without_texture_manifest(publisher, tmp_path):
    import pytest
    from pxr import Usd, UsdShade
    from smartlib.core.metadata import read_json
    geo, _ = fixture_files(tmp_path)
    scene = tmp_path / 'solid.ma'
    scene.write_text('// test scene')
    identity = AssetIdentity('character', 'main', 'DLI')
    geometry = publisher.publish_geometry(identity, geo.GetRootLayer().identifier, scene)
    geometry_path = read_json(geometry, {})['artifacts']['usd']['path']
    recipe = dict(materials={'body': {'diffuse_color': [.25, .5, .75]}}, bindings={'/Geometry/body': 'body'})
    staged = tmp_path / 'solid.usda'
    write_preview_look(staged, geometry_path, recipe)
    manifest = publisher.publish_look(identity, staged, geometry, None, recipe, dcc='maya')
    record = read_json(manifest, {})
    assert record['textures'] is None
    stage = Usd.Stage.Open(record['artifacts']['preview']['path'])
    surface = UsdShade.Shader(stage.GetPrimAtPath('/Geometry/Looks/body/surface'))
    assert tuple(surface.GetInput('diffuseColor').Get()) == pytest.approx((.25, .5, .75))
    assert not surface.GetInput('diffuseColor').HasConnectedSource()
    assert not stage.GetPrimAtPath('/Geometry/Looks/body/texture')

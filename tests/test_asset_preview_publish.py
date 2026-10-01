from pathlib import Path
import pytest
from test_usd_handoff import service
from test_preview_look import fixture_files
from smartlib.apps.asset_manager.preview_publish import PreviewPublishService
from smartlib.core.path_resolver import AssetIdentity
from smartlib.core.metadata import read_json
from smartlib.core.preview_look import write_preview_look


def test_received_textures_geometry_and_two_dcc_looks(service,tmp_path):
    svc,_ = service
    publisher = PreviewPublishService(svc.config)
    identity = AssetIdentity('character','main','TEST')
    received = tmp_path / 'received'
    received.mkdir()
    (received/'color.png').write_bytes(b'original received texture')
    texture = publisher.publish_textures(identity,received)
    texture_data = read_json(texture,{})
    source_texture = texture_data['artifacts']['texture_000']['path']
    assert Path(source_texture).read_bytes() == (received/'color.png').read_bytes()
    geo,_ = fixture_files(tmp_path)
    source_scene = tmp_path/'source.ma'
    source_scene.write_text('// Maya scene')
    geometry = publisher.publish_geometry(identity,geo.GetRootLayer().identifier,source_scene)
    geometry_path = read_json(geometry,{})['artifacts']['usd']['path']
    recipe = {'materials':{'body':{'texture':source_texture}},'bindings':{'/Geometry/body':'body'}}
    staged = tmp_path/'staged.usda'
    write_preview_look(staged,geometry_path,recipe)
    first = publisher.publish_look(identity,staged,geometry,texture,recipe,dcc='maya')
    original = first.read_bytes()
    second = publisher.publish_look(identity,staged,geometry,texture,recipe,dcc='houdini')
    assert read_json(first,{})['version']=='v001'
    assert read_json(second,{})['version']=='v002'
    assert first.read_bytes()==original
    Path(source_texture).write_bytes(b'changed')
    with pytest.raises(ValueError,match='selected Texture'):
        publisher.publish_look(identity,staged,geometry,texture,recipe,dcc='maya')


def test_preview_recipe_requires_complete_bindings(tmp_path):
    geo,_ = fixture_files(tmp_path)
    with pytest.raises(ValueError,match='every geometry mesh'):
        write_preview_look(tmp_path/'bad.usda',geo.GetRootLayer().identifier,{'materials':{},'bindings':{}})

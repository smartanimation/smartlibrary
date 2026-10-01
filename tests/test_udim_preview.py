from pathlib import Path
import pytest
from test_usd_handoff import service
from test_preview_look import fixture_files
from smartlib.core.metadata import read_json
from smartlib.core.path_resolver import AssetIdentity
from smartlib.core.preview_look import write_preview_look, apply_preview_look
from smartlib.core.udim import published_udim, asset_files, usd_dependencies
from smartlib.apps.asset_manager.preview_publish import PreviewPublishService
from smartlib.apps.asset_manager.preview_release import release_preview
from smartlib.apps.asset_manager.texture_intake import TextureInput


def test_split_version_tiles_publish_release_and_shot(service, tmp_path):
    from pxr import Usd, UsdShade
    svc, shot = service
    publisher = PreviewPublishService(svc.config)
    identity = AssetIdentity('environment', 'park', 'park')
    source = tmp_path / 'received'
    source.mkdir()
    one, two = source / 'color.1001.png', source / 'color.1002.png'
    one.write_bytes(b'first tile')
    two.write_bytes(b'second tile')
    first = publisher.publish_textures(identity, entries=[TextureInput(one), TextureInput(two)])
    two.write_bytes(b'updated second tile')
    texture = publisher.publish_textures(identity, entries=[TextureInput(two)])
    record = read_json(texture, {})
    pattern, tiles = published_udim(one, record['artifacts'].values())
    assert len({Path(t['path']).parent for t in tiles}) == 2
    geometry_stage, _ = fixture_files(tmp_path)
    scene = tmp_path / 'work.mb'
    scene.write_bytes(b'scene')
    geometry = publisher.publish_geometry(identity, geometry_stage.GetRootLayer().identifier, scene)
    geo_path = read_json(geometry, {})['artifacts']['usd']['path']
    recipe = dict(materials={'body': dict(texture=pattern, texture_tiles=tiles)},
                  bindings={'/Geometry/body': 'body'})
    staging = tmp_path / 'staging'
    staging.mkdir()
    staged = staging / 'look.usda'
    write_preview_look(staged, geo_path, recipe)
    look = publisher.publish_look(identity, staged, geometry, texture, recipe, dcc='maya')
    look_record = read_json(look, {})
    look_path = look_record['artifacts']['usd']['path']
    look_stage = Usd.Stage.Open(look_path)
    value = look_stage.GetAttributeAtPath('/Geometry/Looks/body/texture.inputs:file').Get().path
    files = asset_files(value)
    assert len(files) == 2
    assert {Path(f).parent for f in files} == {look.parent}
    assert (look.parent / two.name).read_bytes() == two.read_bytes()
    composed = Usd.Stage.CreateInMemory()
    apply_preview_look(composed, '/Shot/park', Usd.Stage.Open(geo_path), look_path,
                       {'/Geometry/body': '/Geometry/body'})
    assert '<UDIM>' in composed.GetAttributeAtPath('/Shot/park/Looks/body/texture.inputs:file').Get().path
    released = read_json(release_preview(publisher, identity, geometry, look), {})
    _, deps, missing = usd_dependencies(released['absolute_files']['usd'])
    assert not missing
    assert set(files).issubset(set(deps))
    result = svc.publish(shot, svc.plan(shot, [dict(kind='assets', target='park',
        source=released['absolute_files']['usd'])], frame_range=[1, 2], fps=24))
    assert result.is_file()
    # Old snapshots are untouched and source mismatches are actionable.
    assert read_json(first, {})['version'] == 'v001'
    one.write_bytes(b'changed source')
    with pytest.raises(ValueError, match='content differs'):
        published_udim(one, record['artifacts'].values())
    (look.parent / two.name).unlink()
    from smartlib.dcc.maya.animation_build import validate_look_usd
    with pytest.raises(ValueError, match='tile set changed'):
        validate_look_usd(look_path, '/Geometry', {})
    assert usd_dependencies(released['absolute_files']['usd'])[2]


def test_missing_udim_and_incomplete_publish_rejected(tmp_path):
    from smartlib.apps.shot_manager.animation_publish import file_hash
    one = tmp_path / 'color.1001.png'
    two = tmp_path / 'color.1002.png'
    one.write_bytes(b'a'); two.write_bytes(b'b')
    with pytest.raises(ValueError, match='tile set differs'):
        published_udim(one, [dict(path=str(one), sha256=file_hash(one))])
    with pytest.raises(ValueError, match='No UDIM tiles'):
        asset_files(tmp_path / 'absent.<UDIM>.png')


def test_maya_2024_udim_compatibility(tmp_path, monkeypatch):
    from pxr import UsdShade, Sdf
    tile = tmp_path / 'color.1001.png'
    tile.write_bytes(b'tile')
    layer = Sdf.Layer.CreateNew(str(tmp_path / 'look.usda'))
    monkeypatch.delattr(UsdShade, 'UdimUtils')
    assert asset_files('color.<UDIM>.png', layer) == [str(tile.resolve())]

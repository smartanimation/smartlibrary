from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import shutil

import pytest
from pxr import Usd, UsdGeom

from test_environment_pack import service, release
from smartlib.apps.asset_manager.environment_pack import release_quality
from smartlib.apps.asset_manager.environment_release import release_current_scene, latest_release
from smartlib.core.asset_publish_resolver import AssetPublishResolver
from smartlib.core.metadata import read_json
from smartlib.core.path_resolver import AssetIdentity


def prop_release(service, profile):
    original = release(service, variant=profile)
    return replace(original, identity=AssetIdentity('prop', 'main', 'Box', 'default'),
                   quality_profile=profile, manifest={'context': {'asset_class': 'prop'}})


def test_prop_lo_rend_share_entry_and_keep_context_paths(service):
    lo = prop_release(service, 'LO')
    packed = service.pack(lo)
    assert packed.version_dir == service.paths.asset_publish_version_dir(lo.identity, 'asset', 'lo', 'v001')
    previous = packed.usd_path.read_bytes()
    rend = prop_release(service, 'REND')
    final = service.pack(rend)
    assert final.version_dir == service.paths.asset_publish_version_dir(lo.identity, 'asset', 'rend', 'v001')
    stage = Usd.Stage.Open(str(final.usd_path))
    assert stage.GetDefaultPrim().GetVariantSet('quality').GetVariantNames() == ['proxy', 'render']
    manifest = read_json(final.usd_path.parent / 'manifest.json', {})
    assert manifest['members']['default']['proxy']['context'] == 'lo'
    assert manifest['members']['default']['render']['context'] == 'rend'
    assert packed.usd_path.read_bytes() == previous
    resolver = AssetPublishResolver(service.project_config)
    assert resolver.list_usd_versions(lo.identity) == ['v002', 'v001']
    assert resolver.resolve_usd_entry(lo.identity, quality='render')['path'] == str(final.usd_path)


def test_prop_current_release_retry_and_anim_route(service):
    lo = prop_release(service, 'LO')
    source = service.paths.artifact_file(service.paths.asset_work_root(lo.identity), 'saved.mb')
    source.parent.mkdir(parents=True)
    source.write_bytes(b'saved prop')
    def exporter(target):
        shutil.copy2(lo.entries[0].files['usd'], target)
        return {'status': 'PASS'}
    released = release_current_scene(service, lo, source, exporter)
    recovered = latest_release(service, lo)
    assert recovered.entries == released.entries
    assert service.has_pack_changes(recovered)
    service.pack(recovered)
    assert not service.has_pack_changes(recovered)
    assert release_quality(replace(lo, quality_profile='ANIM')) is None
    assert release_quality(replace(lo, manifest={'context': {'asset_class': 'character'}})) is None


def test_environment_rend_alias(service):
    assert release_quality(replace(release(service), quality_profile='REND')) == 'render'


def test_render_only_prop_can_be_registered(service):
    assembly = prop_release(service, 'REND')
    packed = service.pack(assembly)
    resolver = AssetPublishResolver(service.project_config)
    assert resolver.list_usd_versions(assembly.identity) == ['v001']
    resolved = resolver.resolve_usd_entry(assembly.identity, quality=None, version='v001')
    assert resolved['path'] == str(packed.usd_path)
    assert resolved['variant_selections']['quality'] == 'render'


def matrix(x):
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, 0, 0, 1]


@pytest.mark.parametrize('mode', ['STATIC', 'CURVE'])
def test_smart_maker_capture_world_transform(mode, monkeypatch):
    from smartlib.dcc.maya.placement_usd import capture_placements
    from smartlib.dcc.maya import animation_data_bake
    monkeypatch.setattr(animation_data_bake, 'scene_timing', lambda cmds: {'fps': 24})
    current = [9]
    def time(frame=None, **kwargs):
        if kwargs.get('query'):
            return current[0]
        current[0] = frame
    cmds = SimpleNamespace(currentTime=time, objExists=lambda _: True,
        currentUnit=lambda **kw: 'cm', upAxis=lambda **kw: 'y',
        xform=lambda *args, **kw: matrix(current[0] * 2))
    locator = SimpleNamespace(member='Box', node='Box_place', attach_root='Box:Root', motion=mode)
    plan = dict(rows=[dict(target='Box', geometry_source='asset'),
                      dict(target='Cloth', geometry_source='animation')],
                frame_range=[1, 3], fps=24, usd={'meters_per_unit': .01, 'up_axis': 'Y'})
    values = capture_placements(plan, cmds, [locator])
    assert list(values) == ['Box']
    assert [s['frame'] for s in values['Box']['samples']] == ([1] if mode == 'STATIC' else [1, 2, 3])
    assert values['Box']['samples'][0]['matrix'] == matrix(2)
    assert current[0] == 9
    with pytest.raises(ValueError, match='exactly one'):
        capture_placements(plan, cmds, [])
    assert current[0] == 9


def test_placement_sample_contract():
    from smartlib.apps.shot_manager.placement_motion import validate_placement, author_placement
    data = dict(mode='CURVE', samples=[dict(frame=1, matrix=matrix(2)), dict(frame=2, matrix=matrix(4))])
    validate_placement(data, [1, 2])
    stage = Usd.Stage.CreateInMemory()
    prim = UsdGeom.Xform.Define(stage, '/Box').GetPrim()
    author_placement(prim, data)
    op = UsdGeom.Xformable(prim).GetOrderedXformOps()[0]
    assert op.GetAttr().GetTimeSamples() == [1, 2]
    assert op.Get(2).ExtractTranslation()[0] == 4
    with pytest.raises(ValueError, match='exact shot range'):
        validate_placement(data, [1, 3])

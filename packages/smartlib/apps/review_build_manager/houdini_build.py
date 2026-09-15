"""Pinned, DCC-independent USD inputs for the Houdini WORK STAGE worker."""
import copy
import math
import os
from pathlib import Path

from smartlib.core.asset_publish_resolver import AssetPublishResolver
from smartlib.core.metadata import read_json
from smartlib.core.usd_settings import usd_settings
from smartlib.apps.asset_manager.context import AssetContextService
from smartlib.apps.asset_manager.environment_pack import digest

SCHEMA = 'smartpipeline.usd_shot_build.v1'


def runtime(config):
    settings = config.load('project_settings.yml')
    name = (settings.get('build_worker') or {}).get('houdini_config')
    names = [name] if name else sorted(p.name for p in config.config_dir.glob('software_*.yml') if 'houdini' in p.name.lower())
    if len(names) != 1:
        raise ValueError('Select build_worker.houdini_config in project_settings.yml when multiple Houdini registrations exist.')
    data = config.load(names[0])
    executable = Path(os.path.expandvars(str(data.get('path') or '')))
    hython = executable.with_name('hython.exe' if os.name == 'nt' else 'hython')
    if not hython.is_file():
        raise FileNotFoundError(f'Houdini hython is not available: {hython}')
    return hython, data


def snapshot(service, identity, construct):
    resolver = AssetPublishResolver(service.project_config)
    paths = service.shots.paths
    result = dict(schema=SCHEMA, episode=identity.episode, sequence=identity.sequence,
                  shot=identity.shot, usd=usd_settings(service.project_config.load('project_settings.yml')),
                  frame_range=list(service.shots.shot_frame_range(identity)),
                  fps=float((service.shots.load_shot(identity).get('editorial') or {}).get('fps') or service.shots.project_fps),
                  construct=copy.deepcopy(construct), assets=[], dependencies=[])
    names = set()
    for component in construct.get('components', []):
        if not component.get('enabled', True):
            continue
        kind = component.get('component_type')
        source = component.get('source') or {}
        if kind == 'review_layers':
            continue  # Render-only metadata is not consumed by WORK STAGE.
        if kind == 'editorial_timing':
            path = Path(component.get('path') or '')
            timing = read_json(path, {})
            if not timing:
                raise ValueError('Published editorial timing is missing.')
            result['frame_range'] = list(timing.get('frame_range') or [timing['cut_in'], timing['cut_out']])
            result['fps'] = float(timing['fps'])
            result['dependencies'].append({'path': str(path), 'sha256': digest(path)})
            continue
        if kind not in {'rig', 'usd'} or source.get('category') != 'environment':
            raise ValueError(f'Houdini WORK STAGE does not yet support enabled input: {kind}/{component.get("name")}')
        asset = resolver.identity_from_publish_path(component.get('path') or '')
        if asset is None:
            raise ValueError(f'Cannot resolve asset identity: {component.get("name")}')
        quality, _ = resolver.context_version_from_publish_path(component['path'])
        if quality not in {'proxy', 'render'}:
            raise ValueError(f'Choose PROXY or RENDER for {component.get("name")}')
        entry = resolver.resolve_usd_entry(asset, quality=quality, release_path=component['path'])
        directory = paths.asset_usd_version_dir(asset, entry['version'])
        manifest_path = paths.artifact_file(directory, 'manifest.json')
        manifest = read_json(manifest_path, {})
        member = manifest['members'][asset.variant][quality]
        name = str(component.get('name') or '')
        if not name or not name.replace('_', 'a').isalnum() or name[0].isdigit() or name in names:
            raise ValueError(f'Unique USD-compatible placement name required: {name}')
        names.add(name)
        result['assets'].append(dict(name=name, prim_path='/World/' + name,
            entry_path=entry['path'], entry_prim=entry['prim_path'], pack_version=entry['version'],
            release_version=member.get('release_version') or member['pack_version'],
            variant_selections=entry['variant_selections'], transform=list(source.get('transform') or
            [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1])))
        for path in (entry['path'], manifest_path, member['usd']['path']):
            result['dependencies'].append({'path': str(path), 'sha256': digest(path)})
    if not result['assets']:
        raise ValueError('Select at least one published environment USD asset.')
    if not math.isfinite(result['fps']) or result['fps'] <= 0 or result['frame_range'][1] < result['frame_range'][0]:
        raise ValueError('Invalid shot timing.')
    return result


def verify(snapshot_data):
    if snapshot_data.get('schema') != SCHEMA:
        raise ValueError('Unsupported USD Build Snapshot.')
    for dependency in snapshot_data['dependencies']:
        if digest(dependency['path']) != dependency['sha256']:
            raise ValueError('Build input changed after queueing: ' + dependency['path'])


def write_stage(snapshot_data, target):
    from pxr import Usd, UsdGeom, Gf
    verify(snapshot_data)
    stage = Usd.Stage.CreateNew(str(target))
    root = UsdGeom.Xform.Define(stage, '/World').GetPrim()
    stage.SetDefaultPrim(root)
    UsdGeom.SetStageMetersPerUnit(stage, snapshot_data['usd']['meters_per_unit'])
    UsdGeom.SetStageUpAxis(stage, snapshot_data['usd']['up_axis'])
    stage.SetFramesPerSecond(snapshot_data['fps'])
    stage.SetTimeCodesPerSecond(snapshot_data['fps'])
    stage.SetStartTimeCode(snapshot_data['frame_range'][0]); stage.SetEndTimeCode(snapshot_data['frame_range'][1])
    for item in snapshot_data['assets']:
        placement = UsdGeom.Xform.Define(stage, item['prim_path']).GetPrim()
        prim = stage.DefinePrim(item['prim_path'] + '/Asset')
        prim.GetReferences().AddReference(item['entry_path'], item['entry_prim'])
        for key in ('variant', 'quality'):
            variants = prim.GetVariantSet(key)
            value = item['variant_selections'][key]
            if value not in variants.GetVariantNames():
                raise ValueError(f'Missing USD selection: {key}={value}')
            variants.SetVariantSelection(value)
        matrix = item['transform']
        if len(matrix) != 16 or not all(math.isfinite(float(v)) for v in matrix):
            raise ValueError('Placement requires a finite 4x4 matrix.')
        xform = UsdGeom.Xformable(placement)
        xform.MakeMatrixXform().Set(Gf.Matrix4d(*matrix))
    stage.GetRootLayer().Save()
    return stage

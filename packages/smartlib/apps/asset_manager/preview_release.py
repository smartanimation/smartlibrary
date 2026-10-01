"""Release immutable Geometry and Preview Look publishes without a DCC export."""
from pathlib import Path

from smartlib.core.metadata import read_json, write_json
from smartlib.core.preview_look import apply_preview_look
from smartlib.core.usd_settings import usd_settings
from .preview_publish import PreviewPublishService
from .environment_pack import SCHEMA, _write_entry, current_members, inspect_usd, next_version

RELEASE_SCHEMA = 'smartpipeline.preview_release.v1'


def release_preview(service, identity, geometry_manifest, look_manifest, *, subset='proxy', comment='', preview_output=None, rig_manifest=None):
    from pxr import Usd, UsdGeom
    publisher = PreviewPublishService(service.project_config)
    paths = service.paths
    dependencies = {}

    def pin(path, expected=None):
        ref = publisher.pin(path)
        if expected is not None and ref != expected:
            raise ValueError('Published dependency changed: ' + str(path))
        dependencies[ref['path']] = ref
        return ref

    def receipt(path, kind):
        record = read_json(path, {}) or {}
        if record.get('status') != 'published' or record.get('publish_type') != kind:
            raise ValueError('Select a published ' + kind + ' receipt')
        for key, value in [('asset', identity.name), ('category', identity.category),
                           ('group', identity.group), ('variant', identity.variant)]:
            if record.get(key) != value:
                raise ValueError('Publish belongs to another Asset: ' + str(path))
        expected = paths.artifact_file(paths.asset_publish_version_dir(
            identity, kind, record['subset'], record['version']), 'publish.json')
        if Path(path).resolve() != expected.resolve():
            raise ValueError('Publish receipt is outside its resolved location')
        pin(path)
        for ref in record.get('artifacts', {}).values():
            pin(ref['path'], ref)
        return record

    geo_record = receipt(geometry_manifest, 'model')
    look_record = receipt(look_manifest, 'look')
    if geo_record['subset'] != look_record['subset']:
        raise ValueError('Geometry and Look subsets must match')
    # Keep the Look authoring dependencies pinned even when selecting newer compatible geometry.
    for key, kind in [('geometry', 'model'), ('textures', 'texture')]:
        ref = look_record.get(key)
        if ref:
            pin(ref['path'], ref)
            receipt(ref['path'], kind)
    geo_path = geo_record['artifacts']['usd']['path']
    look_path = look_record['artifacts']['usd']['path']
    geometry = Usd.Stage.Open(geo_path)
    look = Usd.Stage.Open(look_path)
    if not geometry or not geometry.GetDefaultPrim() or not look:
        raise ValueError('Invalid Geometry or Look USD')
    mapping = {p: p for p in look.GetRootLayer().customLayerData.get('preview_meshes', {})}
    meshes = {str(p.GetPath()) for p in geometry.Traverse() if p.IsA(UsdGeom.Mesh)}
    if set(mapping) != meshes:
        raise ValueError('Look must cover every selected Geometry mesh')
    for texture in apply_preview_look(Usd.Stage.CreateInMemory(), '/Check', geometry, look_path, mapping):
        ref = pin(texture)
        allowed = read_json(look_record.get('textures', {}).get('path'), {}) if look_record.get('textures') else {}
        copied_tile = ref in look_record.get('artifacts', {}).values() and any(
            Path(t['path']).name == Path(texture).name and t['sha256'] == ref['sha256']
            for t in allowed.get('artifacts', {}).values())
        if not copied_tile and ref not in allowed.get('artifacts', {}).values():
            raise ValueError('Look texture is outside its Texture Publish')
    if look.GetDefaultPrim().GetPath() != geometry.GetDefaultPrim().GetPath():
        raise ValueError('Geometry and Look roots must match')
    settings = usd_settings(service.project_config.load('project_settings.yml'))
    inspect_usd(geo_path, settings)
    composed = None
    rig_record = None
    if rig_manifest:
        from smartlib.core.skel_release import compose_skel_release
        rig_record = receipt(rig_manifest, 'rig')
        contract = rig_record.get('usd_skel', {})
        if contract.get('schema') != 'smartpipeline.usd_skel.v2':
            raise ValueError('Select a separated static Rig USD publish')
        rig_files = {}
        for key in ('entry', 'geometry', 'rig', 'validation'):
            path = paths.artifact_file(Path(rig_manifest).parent, contract[key])
            if not path.resolve().is_relative_to(Path(rig_manifest).parent.resolve()):
                raise ValueError('Rig dependency is outside its publish')
            pin(path)
            rig_files[key] = str(path)
        inspect_usd(rig_files['entry'], settings)
        basis = Usd.Stage.Open(rig_files['geometry'])
        binding = Usd.Stage.Open(rig_files['rig'])
        for source in (basis, binding):
            if not source:
                raise ValueError('Invalid static Rig USD')
            if any(not layer.anonymous and Path(layer.realPath).resolve() not in
                   {Path(rig_files[k]).resolve() for k in ('geometry', 'rig')}
                   for layer in source.GetUsedLayers()):
                raise ValueError('Static Rig has external unpinned USD dependencies')
            if any(a.GetNumTimeSamples() for p in source.TraverseAll() for a in p.GetAttributes()):
                raise ValueError('Rig Release requires static USD')
        composed, rig_mapping = compose_skel_release(geometry, basis,
            rig_files['rig'], look_path)

    def write_stage(output):
        stage = Usd.Stage.CreateNew(str(output))
        if composed:
            stage.GetRootLayer().TransferContent(composed.GetRootLayer())
        else:
            stage.GetRootLayer().subLayerPaths = [look_path, geo_path]
            stage.SetDefaultPrim(stage.GetPrimAtPath(geometry.GetDefaultPrim().GetPath()))
        UsdGeom.SetStageMetersPerUnit(stage, settings['meters_per_unit'])
        UsdGeom.SetStageUpAxis(stage, settings['up_axis'])
        stage.GetRootLayer().Save()
        return stage

    if preview_output is not None:
        output = Path(preview_output).resolve()
        if output.is_relative_to(paths.production_root().resolve()):
            raise ValueError('USD check output must be outside Production')
        stage = write_stage(output)
        inspect_usd(output, settings)
        for ref in list(dependencies.values()):
            pin(ref['path'], ref)
        return output
    shared_root = paths.asset_usd_root(identity)
    shared_root.mkdir(parents=True, exist_ok=True)
    lock = paths.artifact_file(shared_root, '_pack.lock')
    with lock.open('x'):
        pass
    try:
        members = current_members(service, identity)
        for qualities in members.values():
            for member in qualities.values():
                for ref in [member['usd'], *member.get('dependencies', [])]:
                    if publisher.pin(ref['path']) != ref:
                        raise ValueError('Previous Release dependency changed: ' + ref['path'])
        version, directory = publisher.reserve(identity, 'asset', subset)
        marker = paths.artifact_file(directory, '_building')
        marker.touch()
        output = paths.artifact_file(directory, 'asset.usd')
        stage = write_stage(output)
        default_prim = inspect_usd(output, settings)
        quality = 'render' if subset.lower() in {'rend', 'render', 'high'} else 'proxy'
        members.setdefault(identity.variant, {})[quality] = dict(
            pack_version=version, release_version=version, context=subset,
            default_prim=default_prim, root_type=stage.GetDefaultPrim().GetTypeName(), usd=publisher.pin(output),
            dependencies=list(dependencies.values()),
            release_files={'usd': str(output)}, release_hashes={'usd': publisher.pin(output)['sha256']})
        shared_version = next_version(shared_root)
        shared_dir = paths.asset_usd_version_dir(identity, shared_version)
        shared_dir.mkdir()
        entrypoint = paths.artifact_file(shared_dir, identity.name + '.usda')
        _write_entry(entrypoint, identity.name, members, settings, identity.variant, quality)
        inspect_usd(entrypoint, settings)
        for ref in list(dependencies.values()):
            pin(ref['path'], ref)
        record = dict(schema=RELEASE_SCHEMA, status='complete', asset=identity.name,
            category=identity.category, group=identity.group, variant=identity.variant,
            publish_type='asset', subset=subset, version=version, comment=comment,
            quality=quality, look_purpose='preview',
            files={'usd': output.name}, absolute_files={'usd': str(output)},
            geometry=publisher.pin(geometry_manifest), look=publisher.pin(look_manifest),
            dependencies=list(dependencies.values()), usd_entrypoint=str(entrypoint),
            resolved_representations=[dict(publish_type=kind, requested_subset=data['subset'],
                resolved_subset=data['subset'], version=data['version'], status='RESOLVED',
                path=str(Path(receipt_path).parent), files={'usd': data['artifacts']['usd']['path']})
                for kind, data, receipt_path in [('model', geo_record, geometry_manifest),
                                                ('look', look_record, look_manifest)]],
            release_hashes={'usd': publisher.pin(output)['sha256']})
        if rig_record:
            record['rig'] = publisher.pin(rig_manifest)
            record['rig_mesh_mapping'] = rig_mapping
            record['resolved_representations'].append(dict(publish_type='rig',
                requested_subset=rig_record['subset'], resolved_subset=rig_record['subset'],
                version=rig_record['version'], status='RESOLVED', path=str(Path(rig_manifest).parent),
                files={'usd': rig_files['rig']}))
        manifest = paths.artifact_file(directory, 'publish.json')
        write_json(manifest, record)
        write_json(paths.artifact_file(directory, 'build_manifest.json'), record)
        write_json(paths.artifact_file(shared_dir, 'manifest.json'), dict(schema=SCHEMA,
            status='complete', asset=identity.name, category=identity.category, group=identity.group,
            version=shared_version, usd=settings, default_variant=identity.variant,
            default_quality=quality, members=members))
        marker.unlink()
        write_json(paths.artifact_file(paths.asset_publish_dir(identity, 'asset', subset), 'latest.json'),
                   dict(version=version, usd=f'{version}/{output.name}'))
        write_json(paths.artifact_file(shared_root, 'latest.json'),
                   dict(version=shared_version, path=f'{shared_version}/{entrypoint.name}'))
        return manifest
    finally:
        lock.unlink()

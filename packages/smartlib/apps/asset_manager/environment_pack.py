"""Versioned environment Packs and an asset-wide USD variant entrypoint."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil

from smartlib.core.metadata import read_json, write_json
from smartlib.core.usd_settings import usd_settings

SCHEMA = 'smartpipeline.asset_usd_entry.v1'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def next_version(root):
    return 'v%03d' % (max((int(p.name[1:]) for p in root.glob('v*')
                         if p.is_dir() and p.name[1:].isdigit()), default=0) + 1)


def inspect_usd(path, settings):
    from pxr import Usd, UsdGeom, UsdUtils
    stage = Usd.Stage.Open(str(path))
    if not stage or not stage.GetDefaultPrim():
        raise ValueError('USD requires a valid defaultPrim: ' + str(path))
    if not stage.HasAuthoredMetadata('metersPerUnit') or not stage.HasAuthoredMetadata('upAxis'):
        raise ValueError('USD must explicitly author metersPerUnit and upAxis: ' + str(path))
    if abs(UsdGeom.GetStageMetersPerUnit(stage) - settings['meters_per_unit']) > 1e-12 or str(UsdGeom.GetStageUpAxis(stage)) != settings['up_axis']:
        raise ValueError('USD conventions differ from the project: ' + str(path))
    _, _, missing = UsdUtils.ComputeAllDependencies(str(path))
    if missing:
        raise ValueError('Unresolved USD dependencies: ' + ', '.join(missing))
    return str(stage.GetDefaultPrim().GetPath())


def _write_entry(path, name, members, settings, default_variant, default_quality):
    from pxr import Usd, UsdGeom
    stage = Usd.Stage.CreateNew(str(path))
    root = UsdGeom.Xform.Define(stage, '/' + name).GetPrim()
    stage.SetDefaultPrim(root)
    UsdGeom.SetStageMetersPerUnit(stage, settings['meters_per_unit'])
    UsdGeom.SetStageUpAxis(stage, settings['up_axis'])
    variants = root.GetVariantSets().AddVariantSet('variant')
    for variant in sorted(members):
        variants.AddVariant(variant)
        variants.SetVariantSelection(variant)
        with variants.GetVariantEditContext():
            qualities = root.GetVariantSets().AddVariantSet('quality')
            for quality, member in sorted(members[variant].items()):
                qualities.AddVariant(quality)
                qualities.SetVariantSelection(quality)
                with qualities.GetVariantEditContext():
                    relative = os.path.relpath(member['usd']['path'], path.parent).replace('\\', '/')
                    root.GetPayloads().AddPayload(relative, member['default_prim'])
            qualities.SetVariantSelection('proxy' if 'proxy' in members[variant] else sorted(members[variant])[0])
    variants.SetVariantSelection(default_variant)
    with variants.GetVariantEditContext():
        root.GetVariantSets().GetVariantSet('quality').SetVariantSelection(default_quality)
    stage.GetRootLayer().Save()


def release_input_error(assembly):
    """Return a blocking reason shared by the UI and Pack execution."""
    quality = assembly.quality_profile.lower()
    if quality not in {'proxy', 'render'}:
        return ('Environment quality must be proxy or render.')
    if assembly.errors or (assembly.manifest.get('source_policy') == 'current_scene' or any(e.publish_type == 'current_scene' for e in assembly.entries)):
        return ('Environment Pack requires published Release inputs. Publish the source scene in the Publish tab, then click Context > Assemble.')
    entries = [e for e in assembly.entries if e.status in {'RESOLVED', 'FALLBACK'}]
    if len(entries) != 1:
        return ('Environment Pack currently requires one complete model or assembly Release.')
    entry = entries[0]
    release_maya = Path(entry.files.get('mb') or entry.files.get('ma') or '')
    release_usd = Path(entry.files.get('usd') or entry.files.get('usdc') or entry.files.get('usda') or '')
    if not release_maya.is_file() or not release_usd.is_file():
        return ('Release must contain both Maya and USD files.')
    if release_maya.suffix != '.mb':
        return ('This environment Pack requires a .mb Release; save and release the Maya scene as binary.')
    return ''


def current_members(service, identity):
    root = service.paths.asset_usd_root(identity)
    for folder in sorted(root.glob('v*'), key=lambda p: int(p.name[1:]) if p.name[1:].isdigit() else -1, reverse=True):
        candidate = read_json(service.paths.artifact_file(folder, 'manifest.json'), {}) or {}
        if candidate.get('schema') == SCHEMA and candidate.get('status') == 'complete':
            return candidate.get('members', {})
    return {}


def pack_environment(service, assembly):
    """Copy a fixed Release into a Pack, then publish a new common entry version."""
    from pxr import Sdf, UsdUtils
    from smartlib.apps.asset_manager.context import PackedAssetContext
    paths = service.paths
    identity = assembly.identity
    quality = assembly.quality_profile.lower()
    error = release_input_error(assembly)
    if error:
        raise ValueError(error)
    entry = next(e for e in assembly.entries if e.status in {'RESOLVED', 'FALLBACK'})
    release_maya = Path(entry.files.get('mb') or entry.files.get('ma'))
    release_usd = Path(entry.files.get('usd') or entry.files.get('usdc') or entry.files.get('usda'))
    settings = usd_settings(service.project_config.load('project_settings.yml'))
    default_prim = inspect_usd(release_usd, settings)
    release_hashes = {'maya': digest(release_maya), 'usd': digest(release_usd)}
    shared_root = paths.asset_usd_root(identity)
    shared_root.mkdir(parents=True, exist_ok=True)
    lock = paths.artifact_file(shared_root, '_pack.lock')
    try:
        handle = lock.open('x')
    except FileExistsError as exc:
        raise RuntimeError('Another asset Pack is in progress: ' + str(lock)) from exc
    try:
        with handle:
            handle.write(str(os.getpid()))
        previous = None
        for folder in sorted(shared_root.glob('v*'), key=lambda p: int(p.name[1:]) if p.name[1:].isdigit() else -1, reverse=True):
            candidate = read_json(paths.artifact_file(folder, 'manifest.json'), {}) or {}
            if candidate.get('schema') == SCHEMA and candidate.get('status') == 'complete':
                previous = candidate
                break
        members = (previous or {}).get('members', {})
        for by_quality in members.values():
            for member in by_quality.values():
                inspect_usd(member['usd']['path'], settings)
                for key in ('maya','usd'):
                    if digest(member[key]['path']) != member[key]['sha256']:
                        raise ValueError('Previously published Pack was modified: ' + member[key]['path'])
        old = members.get(identity.variant, {}).get(quality, {})
        direct_release = assembly.manifest.get('source_policy') == 'asset_release'
        if old.get('release_hashes') == release_hashes and (not direct_release or old.get('release_files') == {'maya': str(release_maya), 'usd': str(release_usd)}):
            raise ValueError('This Release is already packed.')
        if direct_release:
            destination = paths.asset_publish_version_dir(identity, 'asset', quality, entry.version)
            record = read_json(paths.artifact_file(destination, 'publish.json'), {}) or {}
            if record.get('status') != 'complete' or record.get('release_hashes') != release_hashes:
                raise ValueError('Release is incomplete or modified; create a new Release.')
            version = entry.version
            scene, payload = release_maya, release_usd
        else:
            pack_root = paths.asset_publish_dir(identity, 'asset', quality)
            pack_root.mkdir(parents=True, exist_ok=True)
            version = next_version(pack_root)
            destination = paths.asset_publish_version_dir(identity, 'asset', quality, version)
            # Version reservation is serialized by the asset-wide lock.
            destination.mkdir()
            scene = paths.artifact_file(destination, f'{identity.name}_{identity.variant}.mb')
            payload = paths.artifact_file(destination, f'{identity.name}_{identity.variant}_payload.usd')
            marker = paths.artifact_file(destination, '_building')
            marker.touch()
            shutil.copy2(release_maya, scene)
            layer = Sdf.Layer.FindOrOpen(str(release_usd))
            # Re-anchor relative references/textures when moving the payload to Pack.
            copied = Sdf.Layer.CreateAnonymous()
            copied.TransferContent(layer)
            def reanchor(value):
                if not value or os.path.isabs(value) or '://' in value:
                    return value
                return os.path.relpath(Sdf.ComputeAssetPathRelativeToLayer(layer, value), destination).replace('\\', '/')
            UsdUtils.ModifyAssetPaths(copied, reanchor)
            copied.Export(str(payload))
            inspect_usd(payload, settings)
            if digest(release_maya) != release_hashes['maya'] or digest(release_usd) != release_hashes['usd']:
                raise ValueError('Release changed while packing.')
        member = {'pack_version': version, 'release_version': entry.version,
                  'release_hashes': release_hashes, 'release_files': {'maya': str(release_maya), 'usd': str(release_usd)},
                  'default_prim': default_prim,
                  'maya': {'path':str(scene),'sha256':digest(scene)},
                  'usd': {'path':str(payload),'sha256':digest(payload)}}
        members.setdefault(identity.variant, {})[quality] = member
        shared_version = next_version(shared_root)
        shared_dir = paths.asset_usd_version_dir(identity, shared_version)
        shared_dir.mkdir()
        entrypoint = paths.artifact_file(shared_dir, identity.name + '.usda')
        _write_entry(entrypoint, identity.name, members, settings, identity.variant, quality)
        inspect_usd(entrypoint, settings)
        manifest = {'schema':SCHEMA,'status':'complete','asset':identity.name,
                    'category':identity.category,'group':identity.group,'version':shared_version,
                    'usd':settings,'default_variant':identity.variant,'default_quality':quality,'members':members}
        if direct_release:
            build_manifest = paths.artifact_file(destination, 'build_manifest.json')
            publish_json = paths.artifact_file(destination, 'publish.json')
        else:
            build_manifest = write_json(paths.artifact_file(destination, 'build_manifest.json'), assembly.manifest)
            record = {'asset':identity.name,'variant':identity.variant,'publish_type':'asset','subset':quality,
                      'version':version,'context':assembly.manifest['context'],
                      'files':{'mb':scene.name,'usd':payload.name,'build_manifest':build_manifest.name},
                      'composition':{'mode':'release_pack','usd_pack_revision':service.USD_PACK_REVISION},
                      'usd_entrypoint':str(entrypoint),'release_hashes':release_hashes}
            publish_json = write_json(paths.artifact_file(destination, 'publish.json'), record)
        write_json(paths.artifact_file(shared_dir, 'manifest.json'), manifest)
        if not direct_release:
            marker.unlink()
            write_json(paths.artifact_file(pack_root, 'latest.json'), {'version':version,'path':f'{version}/{scene.name}','usd':f'{version}/{payload.name}'})
            service._update_versions(paths.artifact_file(pack_root, 'versions.json'), version)
        write_json(paths.artifact_file(shared_root, 'latest.json'), {'version':shared_version,'path':f'{shared_version}/{entrypoint.name}'})
        return PackedAssetContext(destination, build_manifest, scene, entrypoint, publish_json)
    finally:
        lock.unlink()

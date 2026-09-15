"""Publish a complete environment scene before composing its common USD Pack."""
from dataclasses import asdict, replace
from pathlib import Path
import shutil

from smartlib.core.metadata import read_json, write_json
from smartlib.core.usd_settings import usd_settings
from .environment_pack import digest, inspect_usd, next_version


def assembly_for_release(assembly, directory, record):
    from .context import AssetContextEntry
    quality = assembly.quality_profile.lower()
    entry = AssetContextEntry('asset', quality, quality, record['version'], 'RESOLVED',
                              str(directory), record['absolute_files'],
                              latest_version=record['version'], comment=record.get('comment', ''))
    manifest = dict(assembly.manifest)
    manifest.update(source_policy='asset_release', source_scene=record.get('source_scene', ''),
                    resolved_representations=[asdict(entry)], validation={'status': 'OK', 'errors': []})
    return replace(assembly, entries=[entry], errors=[], manifest=manifest)


def latest_release(service, assembly):
    root = service.paths.asset_publish_dir(assembly.identity, 'asset', assembly.quality_profile.lower())
    versions = sorted((p for p in root.glob('v*') if p.is_dir() and p.name[1:].isdigit()),
                      key=lambda p: int(p.name[1:]), reverse=True)
    for directory in versions:
        if service.paths.artifact_file(directory, '_building').exists():
            continue
        record = read_json(service.paths.artifact_file(directory, 'publish.json'), {}) or {}
        if record.get('schema') == 'smartpipeline.environment_release.v1' and record.get('status') == 'complete':
            return assembly_for_release(assembly, directory, record)
    return assembly


def release_current_scene(service, assembly, source, export_usd, *, comment=''):
    """export_usd is a DCC callback; failed exports never advance release discovery."""
    if not service.is_environment_release_pack(assembly):
        raise ValueError('Release & Pack currently supports environment PROXY and RENDER.')
    source = Path(source).resolve()
    source.relative_to(service.paths.asset_work_root(assembly.identity).resolve())
    if source.suffix.lower() != '.mb' or not source.is_file():
        raise ValueError('Save the current scene as Maya binary (.mb) before Release & Pack.')
    identity = assembly.identity
    quality = assembly.quality_profile.lower()
    root = service.paths.asset_publish_dir(identity, 'asset', quality)
    root.mkdir(parents=True, exist_ok=True)
    lock = service.paths.artifact_file(root, '_release.lock')
    with lock.open('x'):
        pass
    try:
        version = next_version(root)
        directory = service.paths.asset_publish_version_dir(identity, 'asset', quality, version)
        directory.mkdir()
        marker = service.paths.artifact_file(directory, '_building')
        marker.touch()
        maya = service.paths.artifact_file(directory, f'{identity.name}_{identity.variant}.mb')
        usd = service.paths.artifact_file(directory, f'{identity.name}_{identity.variant}_payload.usd')
        source_hash = digest(source)
        shutil.copy2(source, maya)
        validation = export_usd(usd)
        inspect_usd(usd, usd_settings(service.project_config.load('project_settings.yml')))
        if digest(source) != source_hash or digest(maya) != source_hash:
            raise ValueError('Source scene changed during Release.')
        record = dict(schema='smartpipeline.environment_release.v1', status='complete',
                      asset=identity.name, variant=identity.variant, publish_type='asset', subset=quality,
                      version=version, context=assembly.manifest['context'], comment=comment,
                      source_scene=str(source), source_sha256=source_hash, usd_validation=validation,
                      files={'mb': maya.name, 'usd': usd.name},
                      absolute_files={'mb': str(maya), 'usd': str(usd)},
                      release_hashes={'maya': digest(maya), 'usd': digest(usd)})
        released = assembly_for_release(assembly, directory, record)
        write_json(service.paths.artifact_file(directory, 'build_manifest.json'), released.manifest)
        write_json(service.paths.artifact_file(directory, 'publish.json'), record)
        marker.unlink()
        write_json(service.paths.artifact_file(root, 'latest.json'),
                   {'version': version, 'path': f'{version}/{maya.name}', 'usd': f'{version}/{usd.name}'})
        service._update_versions(service.paths.artifact_file(root, 'versions.json'), version)
        return released
    finally:
        lock.unlink()

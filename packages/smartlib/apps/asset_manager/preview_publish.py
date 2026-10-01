"""Versioned geometry, received textures, and portable Preview Look publishes."""
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil

from .service import AssetManagerService
from smartlib.core.metadata import read_json, write_json
from smartlib.apps.shot_manager.animation_publish import file_hash


class PreviewPublishService(AssetManagerService):
    def reserve(self, identity, kind, subset):
        root = self.paths.asset_publish_dir(identity, kind, subset)
        root.mkdir(parents=True, exist_ok=True)
        number = max((int(p.name[1:]) for p in root.iterdir()
                      if p.is_dir() and re.fullmatch(r'v[0-9]+', p.name)), default=0) + 1
        while True:
            version = f'v{number:03d}'
            directory = self.paths.asset_publish_version_dir(identity, kind, subset, version)
            try:
                directory.mkdir()
                return version, directory
            except FileExistsError:
                number += 1

    @staticmethod
    def pin(path):
        path = Path(path).resolve()
        return dict(path=path.as_posix(), sha256=file_hash(path))

    def commit(self, identity, kind, subset, version, directory, files, *, artifacts=None, **metadata):
        record = dict(schema='smartpipeline.asset_preview_publish.v1', status='published',
            asset=identity.name, category=identity.category, group=identity.group,
            variant=identity.variant, publish_type=kind, subset=subset, version=version,
            created_at=datetime.now(timezone.utc).isoformat(), files=files,
            artifacts=artifacts if artifacts is not None else {key:self.pin(self.paths.artifact_file(directory,name)) for key,name in files.items()},
            **metadata)
        manifest = self.paths.artifact_file(directory, 'publish.json')
        write_json(manifest, record)
        root = self.paths.asset_publish_dir(identity,kind,subset)
        write_json(self.paths.artifact_file(root,'latest.json'), dict(version=version))
        versions_path = self.paths.artifact_file(root,'versions.json')
        versions = read_json(versions_path, []) or []
        versions.append(dict(version=version,status='published'))
        write_json(versions_path, versions)
        return manifest

    def texture_snapshot(self, identity, subset='low'):
        root = self.paths.asset_publish_dir(identity, 'texture', subset)
        latest = read_json(self.paths.artifact_file(root, 'latest.json'), {}) or {}
        if not latest:
            return {}, None
        directory = self.paths.asset_publish_version_dir(identity, 'texture', subset, latest['version'])
        manifest = self.paths.artifact_file(directory, 'publish.json')
        record = read_json(manifest, {})
        if not record or record.get('status') != 'published':
            raise ValueError('Invalid Texture Publish manifest')
        result = {}
        for key, ref in record['artifacts'].items():
            name = Path(ref['path']).name
            result[name.casefold()] = dict(name=name, artifact=ref,
                metadata=record.get('textures', {}).get(key, {}),
                version=record.get('origins', {}).get(key, record['version']))
        return result, manifest

    def publish_textures(self, identity, source=None, *, subset='low', entries=None, removed=()):
        from .texture_intake import collect_textures, validate_inputs
        if entries is None:
            entries, ignored = collect_textures([source])
            if ignored:
                raise ValueError('Folder contains non-texture files; inspect the selection before publishing')
        entries = validate_inputs(entries) if entries or not removed else []
        snapshot, previous = self.texture_snapshot(identity, subset)
        previous_ref = self.pin(previous) if previous else None
        for item in snapshot.values():
            if self.pin(item['artifact']['path']) != item['artifact']:
                raise ValueError('Published texture changed')
        for name in removed:
            if name.casefold() not in snapshot:
                raise ValueError(f'Texture not in published composition: {name}')
            del snapshot[name.casefold()]
        originals = [Path(entry.path).resolve() for entry in entries]
        # Validate all output names before allocating a version.
        root = self.paths.asset_publish_dir(identity, 'texture', subset)
        for original in originals:
            self.paths.artifact_file(root, original.name)
        refs = [self.pin(p) for p in originals]
        version, directory = self.reserve(identity,'texture',subset)
        files = {}
        changed = []
        for index, (original, ref, entry) in enumerate(zip(originals,refs,entries)):
            output = self.paths.artifact_file(directory,original.name)
            shutil.copy2(original,output)
            if file_hash(output) != ref['sha256'] or file_hash(original) != ref['sha256']:
                raise ValueError('Received texture changed during publication')
            key = f'texture_{index:03d}'
            files[key] = output.name
            snapshot[output.name.casefold()] = dict(name=output.name, artifact=self.pin(output),
                metadata=dict(usage=entry.usage, color_space=entry.color_space, udim=entry.udim), version=version)
            changed.append(output.name)
        _, current = self.texture_snapshot(identity, subset)
        if (self.pin(current) if current else None) != previous_ref:
            raise ValueError('Texture Publish changed; refresh and retry')
        items = sorted(snapshot.values(), key=lambda item: item['name'].casefold())
        for item in items:
            if self.pin(item['artifact']['path']) != item['artifact']:
                raise ValueError('Published texture changed during publication')
        artifacts = {f'texture_{index:03d}': item['artifact'] for index, item in enumerate(items)}
        files = {f'texture_{index:03d}': item['name'] for index, item in enumerate(items)
                 if item['version'] == version}
        textures = {f'texture_{index:03d}': item['metadata'] for index, item in enumerate(items)}
        origins = {f'texture_{index:03d}': item['version'] for index, item in enumerate(items)}
        return self.commit(identity,'texture',subset,version,directory,files,
                           artifacts=artifacts, source_files=refs, textures=textures, origins=origins,
                           changed=changed, removed=list(removed), previous=previous_ref)

    def publish_geometry(self, identity, staged_usd, source_scene, *, subset='low', scene_state=None):
        from smartlib.dcc.maya.animation_build import validate_deform_usd
        from .environment_pack import inspect_usd
        from smartlib.core.usd_settings import usd_settings
        validate_deform_usd(Path(staged_usd))
        inspect_usd(staged_usd,usd_settings(self.project_config.load('project_settings.yml')))
        version,directory = self.reserve(identity,'model',subset)
        output = self.paths.artifact_file(directory,'geo.usd')
        shutil.copy2(staged_usd,output)
        return self.commit(identity,'model',subset,version,directory,{'usd':output.name},
                           dcc='maya',source_scene=self.pin(source_scene), scene_state=scene_state)

    def publish_look(self, identity, staged_usd, geometry_manifest, texture_manifest, recipe, *, dcc, subset='low', source_scene=None, scene_state=None):
        from pxr import Usd, UsdGeom
        from smartlib.core.preview_look import apply_preview_look
        geo = read_json(geometry_manifest, {})['artifacts']['usd']
        if self.pin(geo['path']) != geo:
            raise ValueError('Geometry Publish changed')
        geometry = Usd.Stage.Open(geo['path'])
        look = Usd.Stage.Open(str(staged_usd))
        mapping = {path:path for path in look.GetRootLayer().customLayerData['preview_meshes']}
        scratch = Usd.Stage.CreateInMemory()
        textures = apply_preview_look(scratch,'/Preview',geometry,staged_usd,mapping)
        texture_record = read_json(texture_manifest,{}) if texture_manifest else {}
        allowed = {ref['path']:ref for ref in texture_record.get('artifacts', {}).values()}
        tile_refs = [ref for material in recipe.get('materials', {}).values()
                     for ref in material.get('texture_tiles', [])]
        for ref in tile_refs:
            if ref['path'] not in allowed or self.pin(ref['path']) != allowed[ref['path']] or ref != allowed[ref['path']]:
                raise ValueError('UDIM tiles must reference the selected Texture Publish')
        for path in textures:
            ref = self.pin(path)
            tile_match = any(Path(t['path']).name == Path(path).name and t['sha256'] == ref['sha256'] for t in tile_refs)
            if not tile_match and (ref['path'] not in allowed or ref != allowed[ref['path']]):
                raise ValueError('Look must reference the selected Texture Publish')
        version,directory = self.reserve(identity,'look',subset)
        output = self.paths.artifact_file(directory,'look.usd')
        files = {'usd':output.name,'preview':'preview.usda'}
        if tile_refs:
            from smartlib.core.preview_look import write_preview_look
            write_preview_look(output, geo['path'], recipe,
                texture_destination=lambda name: self.paths.artifact_file(directory, name))
            for index, name in enumerate(sorted({Path(ref['path']).name for ref in tile_refs})):
                files[f'udim_{index:04d}'] = name
            apply_preview_look(Usd.Stage.CreateInMemory(), '/Check', geometry, output, mapping)
        else:
            look.GetRootLayer().Export(str(output))
        preview = Usd.Stage.CreateNew(str(self.paths.artifact_file(directory,'preview.usda')))
        preview.GetRootLayer().subLayerPaths = [output.as_posix(),geo['path']]
        preview.SetDefaultPrim(preview.GetPrimAtPath(geometry.GetDefaultPrim().GetPath()))
        UsdGeom.SetStageMetersPerUnit(preview,UsdGeom.GetStageMetersPerUnit(geometry))
        UsdGeom.SetStageUpAxis(preview,UsdGeom.GetStageUpAxis(geometry))
        preview.GetRootLayer().Save()
        for ref in tile_refs:
            if self.pin(ref['path']) != ref:
                raise ValueError('UDIM source changed during publication')
        return self.commit(identity,'look',subset,version,directory,files,
            dcc=dcc,geometry=self.pin(geometry_manifest),textures=self.pin(texture_manifest) if texture_manifest else None,recipe=recipe,
            source_scene=self.pin(source_scene) if source_scene else None, scene_state=scene_state)

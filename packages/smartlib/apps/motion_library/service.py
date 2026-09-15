"""Motion Work registration and immutable Clip Publish using ProjectPaths."""
from dataclasses import asdict
from pathlib import Path
import shutil
import math
from smartlib.core.path_resolver import configured_project_paths
from smartlib.core.metadata import read_json, write_json
from smartlib.apps.asset_manager.environment_pack import digest, next_version


class MotionLibraryService:
    def __init__(self, config):
        self.config = config
        self.paths = configured_project_paths(config.project_root, config)

    def register_work(self, identity, clip, source, *, version='v001', take='t01', fbx=None):
        source = Path(source)
        target = self.paths.motion_work_file(identity, clip, version, take)
        if source.suffix.lower() != '.mb' or not source.is_file():
            raise ValueError('Registration requires a saved Maya binary scene.')
        pairs = [(source, target)]
        if fbx:
            pairs.append((Path(fbx), self.paths.motion_work_file(identity, clip, version, take, 'fbx')))
        for original, destination in pairs:
            if not original.is_file(): raise FileNotFoundError(original)
            if destination.exists(): raise FileExistsError(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        records = []
        for original, destination in pairs:
            with original.open('rb') as src, destination.open('xb') as dst:
                shutil.copyfileobj(src, dst)
            records.append(dict(original=str(original), registered=str(destination), sha256=digest(destination)))
        write_json(self.paths.artifact_file(target.parent, target.stem+'.source.json'),
                   dict(schema='smartpipeline.motion_work.v1', clip=clip, target=asdict(identity), sources=records))
        return target

    def register_received_fbx(self, identity, receipt, source):
        source = Path(source)
        if source.suffix.lower() != '.fbx' or not source.is_file(): raise ValueError('Received source must be FBX.')
        directory = self.paths.motion_reference_dir(identity, receipt)
        target = self.paths.artifact_file(directory, source.name)
        directory.mkdir(parents=True, exist_ok=True)
        with source.open('rb') as src, target.open('xb') as dst: shutil.copyfileobj(src, dst)
        write_json(self.paths.artifact_file(directory, source.name+'.receipt.json'),
                   dict(schema='smartpipeline.motion_receipt.v1', original=str(source), path=str(target), sha256=digest(target)))
        return target

    def publish(self, identity, clip, source, metadata, exporter):
        source = Path(source).resolve()
        source.relative_to(self.paths.motion_work_dir(identity, clip).resolve())
        if not source.is_file() or source.suffix.lower() != '.mb': raise ValueError('Save a registered motion .mb first.')
        start, end = metadata['frame_range']
        if not all(math.isfinite(float(v)) for v in (start,end,metadata['fps'])) or int(start)!=start or int(end)!=end or end < start or metadata['fps'] <= 0: raise ValueError('Invalid clip timing.')
        if metadata.get('root_motion') not in {'in_place','locomotion','stationary'}: raise ValueError('Choose root motion mode.')
        if metadata.get('forward_axis') not in {'+X','-X','+Z','-Z'}: raise ValueError('Choose the character forward axis.')
        if not metadata.get('skeleton_root') or not metadata.get('placement_anchor'): raise ValueError('Skeleton root and placement anchor are required.')
        for reference in metadata.get('rig_references', []):
            if digest(reference['path']) != reference['sha256']: raise ValueError('Rig reference changed since inspection.')
        root = self.paths.motion_publish_dir(identity, clip);root.mkdir(parents=True, exist_ok=True)
        lock = self.paths.artifact_file(root,'_publish.lock')
        with lock.open('x'): pass
        try:
            version = next_version(root)
            directory = self.paths.motion_publish_dir(identity,clip,version);directory.mkdir()
            marker = self.paths.artifact_file(directory,'_building');marker.touch()
            maya = self.paths.motion_publish_file(identity,clip,version,'mb')
            fbx = self.paths.motion_publish_file(identity,clip,version,'fbx')
            before = digest(source);shutil.copy2(source,maya)
            report = exporter(fbx,metadata)
            for reference in metadata.get('rig_references', []):
                if digest(reference['path']) != reference['sha256']: raise ValueError('Rig reference changed during Publish.')
            if not fbx.is_file() or not fbx.stat().st_size: raise ValueError('FBX export did not produce data.')
            if digest(source) != before or digest(maya) != before: raise ValueError('Work scene changed during Publish.')
            manifest = dict(schema='smartpipeline.motion_clip.v1',status='complete',target=asdict(identity),
                clip=clip,version=version,source_work=str(source),source_sha256=before,
                files={'mb':str(maya),'fbx':str(fbx)}, hashes={'mb':digest(maya),'fbx':digest(fbx)},
                animation=metadata,export=report)
            path = self.paths.artifact_file(directory,'manifest.json');write_json(path,manifest)
            marker.unlink()
            write_json(self.paths.artifact_file(root,'latest.json'),dict(version=version,manifest=str(path)))
            return path
        finally: lock.unlink()

    def resolve_clip(self, identity, clip, version='latest'):
        root = self.paths.motion_publish_dir(identity,clip)
        if version == 'latest': version = read_json(self.paths.artifact_file(root,'latest.json'),{}).get('version','')
        directory = self.paths.motion_publish_dir(identity,clip,version)
        manifest = read_json(self.paths.artifact_file(directory,'manifest.json'),{})
        if self.paths.artifact_file(directory,'_building').exists() or manifest.get('status') != 'complete': raise ValueError('Clip is not published.')
        for key,path in manifest['files'].items():
            if digest(path) != manifest['hashes'][key]: raise ValueError('Published clip was modified.')
        return manifest

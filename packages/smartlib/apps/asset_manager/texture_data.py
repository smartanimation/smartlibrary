"""Immutable texture intake batches and a cumulative Data catalog."""
from pathlib import Path
import re
import shutil

from .preview_publish import PreviewPublishService
from .texture_intake import TextureInput, validate_inputs
from smartlib.core.metadata import read_json, write_json


class TextureDataService(PreviewPublishService):
    def catalog(self, identity, subset='low'):
        root = self.paths.asset_data_dir(identity, 'texture', subset)
        latest = read_json(self.paths.artifact_file(root, 'latest.json'), {}) or {}
        if not latest:
            return {}
        directory = self.paths.asset_data_version_dir(identity, 'texture', subset, latest['version'])
        return read_json(self.paths.artifact_file(directory, 'textures.json'), {})['textures']

    def ingest(self, identity, entries, subset='low'):
        entries = validate_inputs(entries)
        catalog = self.catalog(identity, subset)
        refs = [self.pin(entry.path) for entry in entries]
        root = self.paths.asset_data_dir(identity, 'texture', subset)
        for entry in entries:
            self.paths.artifact_file(root, entry.path.name)
        root.mkdir(parents=True, exist_ok=True)
        number = max((int(p.name[1:]) for p in root.iterdir()
                      if p.is_dir() and re.fullmatch(r'v[0-9]+', p.name)), default=0) + 1
        while True:
            version = f'v{number:03d}'
            directory = self.paths.asset_data_version_dir(identity, 'texture', subset, version)
            try:
                directory.mkdir()
                break
            except FileExistsError:
                number += 1
        for entry, source in zip(entries, refs):
            target = self.paths.artifact_file(directory, entry.path.name)
            shutil.copy2(entry.path, target)
            if self.pin(target)['sha256'] != source['sha256'] or self.pin(entry.path) != source:
                raise ValueError('Texture changed during intake')
            catalog[entry.path.name.casefold()] = dict(path=target.as_posix(), sha256=source['sha256'],
                source=source, version=version, usage=entry.usage, color_space=entry.color_space)
        manifest = self.paths.artifact_file(directory, 'textures.json')
        write_json(manifest, dict(schema='smartpipeline.texture_data.v1', textures=catalog))
        write_json(self.paths.artifact_file(root, 'latest.json'), dict(version=version))
        return manifest

    def publish_data(self, identity, names, subset='low'):
        catalog = self.catalog(identity, subset)
        entries = []
        for name in names:
            item = catalog[name.casefold()]
            if self.pin(item['path'])['sha256'] != item['sha256']:
                raise ValueError(f'Ingested texture changed: {name}')
            entries.append(TextureInput(Path(item['path']), item['usage'], item['color_space']))
        return self.publish_textures(identity, subset=subset, entries=entries)

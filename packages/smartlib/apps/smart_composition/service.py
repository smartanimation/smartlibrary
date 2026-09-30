"""Resolver-backed discovery and non-destructive USD previews."""
import re
from pathlib import Path

from smartlib.apps.shot_manager import ShotIdentity
from smartlib.apps.shot_manager.usd_handoff import (
    COMPOSITION, PRODUCT, KINDS, UsdHandoffService, compose_layers,
)
from smartlib.core.metadata import read_json


class CompositionSession:
    def __init__(self, shots, source, *, sections=False):
        self.handoff = UsdHandoffService(shots)
        self.paths = shots.paths
        source = self.paths.project_dependency(source)
        if source.is_dir() or source.suffix.lower() in {'.usd', '.usda', '.usdc'}:
            source = self.paths.artifact_file(source if source.is_dir() else source.parent, 'manifest.json')
        snapshot = self.handoff.load_handoff(source)
        if snapshot['schema'] != COMPOSITION:
            raise ValueError('Open a Shot Manager USD composition snapshot')
        self.identity = ShotIdentity(**snapshot['shot'])
        expected = self.paths.artifact_file(self.paths.composition_dir(
            *self.handoff._identity(self.identity), 'usd', snapshot['version']), 'manifest.json')
        if expected.resolve() != source.resolve():
            raise ValueError('Composition does not belong to the selected project configuration')
        self.preview_refs = None
        self.source = source
        self.snapshot = snapshot
        self.section_mode = bool(sections and 'sections' in snapshot)
        self.initial = {}
        if self.section_mode:
            self.initial = {(kind, 'Section'): ref['path'] for kind, ref in snapshot['sections'].items()}
            return
        for ref in snapshot['products']:
            product = self.handoff.load_handoff(ref['path'])
            self.initial[(product['kind'], product['target'])] = ref['path']

    def versions(self):
        if self.section_mode:
            return {(kind, 'Section'): rows for kind, rows in self.handoff.section_versions(self.identity).items()}
        result = {}
        identity = self.handoff._identity(self.identity)
        for kind in sorted(KINDS):
            root = self.paths.shot_publish_dir(*identity, 'usd', kind)
            if not root.is_dir():
                continue
            for target in sorted(root.iterdir()):
                if not target.is_dir():
                    continue
                directory = self.paths.usd_handoff_dir(*identity, kind, target.name)
                for version in sorted(directory.iterdir(), reverse=True):
                    if not version.is_dir() or not re.fullmatch(r'v[0-9]{3,}', version.name):
                        continue
                    manifest = self.paths.artifact_file(version, 'manifest.json')
                    data = read_json(manifest, {})
                    if (data.get('schema') == PRODUCT and data.get('status') == 'published'
                            and data.get('shot') == self.snapshot['shot']
                            and data.get('kind') == kind and data.get('target') == target.name):
                        result.setdefault((kind, target.name), []).append((version.name, manifest.as_posix()))
        for key, path in self.initial.items():
            if not any(Path(p).resolve() == Path(path).resolve() for _, p in result.get(key, [])):
                raise ValueError(f'Original product is missing from discovery: {path}')
        return result

    def preview(self, manifests):
        section_refs = None
        if self.section_mode:
            section_refs, manifests = self.handoff.section_products(self.identity, manifests)
        refs, products, plan, _ = self.handoff.select_products(self.identity, manifests)
        stage, layers, deps = compose_layers(
            dict.fromkeys(('shot', 'animation', 'camera', 'assets', 'layout')),
            products, plan, self.handoff, in_memory=True)
        for ref in refs + deps:
            self.handoff.check(ref)
        self.preview_refs = section_refs if self.section_mode else refs
        return stage, layers

    def save(self, manifests):
        if self.preview_refs is None or sorted(str(p) for p in manifests) != sorted(r['path'] for r in self.preview_refs):
            raise ValueError('Preview this selection before saving')
        for ref in self.preview_refs:
            self.handoff.check(ref)
        if self.section_mode:
            _, manifests = self.handoff.section_products(self.identity, manifests)
        return self.handoff.compose_products(self.identity, manifests)

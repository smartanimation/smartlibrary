from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import os
import re

from smartlib.apps.smart_casting.service import SmartCastingService
from smartlib.core.asset_publish_resolver import AssetPublishResolver
from smartlib.core.path_resolver import AssetIdentity


def path_key(path):
    return os.path.normcase(os.path.abspath(str(path))).replace('\\', '/')


def identity_scope(identity):
    """Use identity fields across Maya module reloads, not Python class identity."""
    if not getattr(identity, 'episode', '') or not getattr(identity, 'sequence', ''):
        raise ValueError('Casting identity requires episode and sequence.')
    if not hasattr(identity, 'shot'):
        return 'sequence'
    if not identity.shot:
        raise ValueError('Shot Casting identity requires a shot.')
    return 'shot'


def cast_namespace(value):
    # Same namespace normalization as the Maya shot builder.
    from smartlib.dcc.maya.shot_builder import _clean_namespace
    return _clean_namespace(value)


@dataclass(frozen=True)
class Reference:
    node: str
    namespace: str
    path: str
    loaded: bool = True
    nested: bool = False
    error: str = ''


@dataclass(frozen=True)
class Target:
    label: str
    path: str


@dataclass(frozen=True)
class Difference:
    cast_key: str
    namespace: str
    target: str
    reference: Reference | None
    status: str
    message: str = ''
    asset: str = ''
    variant: str = ''


def compare_cast(items, references, occupied=()):
    """Match cast instances by namespace, never collapse repeated assets by file."""
    items = list(items)
    counts = Counter(cast_namespace(item.namespace or item.cast_key) for item in items)
    result, used = [], set()
    for item in items:
        namespace = cast_namespace(item.namespace or item.cast_key)
        matches = [ref for ref in references if ref.namespace == namespace]
        used.update(ref.node for ref in matches)
        ref = matches[0] if len(matches) == 1 else None
        target = str(item.publish_path or '')
        message = str(item.message or '')
        if ref and ref.error:
            status = 'Unreadable'
            message = ref.error
        elif counts[namespace] > 1 or len(matches) > 1 or (ref and ref.nested):
            status = 'Conflict'
        elif not matches and namespace in occupied:
            status = 'Conflict'
            message = 'Namespace already contains scene nodes.'
        elif item.status != 'resolved' or not target or Path(target).suffix.lower() not in {'.ma', '.mb'}:
            status = 'Unresolved'
        elif not Path(target).is_file():
            status = 'Unresolved'
            message = 'Published file is missing.'
        elif ref is None:
            status = 'Added'
        elif path_key(ref.path) == path_key(target):
            status = 'In sync'
        else:
            status = 'Changed'
        result.append(Difference(item.cast_key, namespace, target, ref, status, message,
                                 str(getattr(item, 'asset', '') or item.cast_key),
                                 str(getattr(item, 'variant', '') or 'default')))
    for ref in references:
        if ref.node not in used:
            result.append(Difference('', ref.namespace, '', ref, 'Unreadable' if ref.error else ('Nested' if ref.nested else 'Not in casting'), ref.error or 'Kept in scene.'))
    return result


def suggest_target(reference, targets):
    """Suggest only a unique exact path or filename; user confirms selection."""
    exact = [i for i, target in enumerate(targets) if path_key(target.path) == path_key(reference.path)]
    if len(exact) == 1:
        return exact[0]
    name = re.split(r'[/\\]', reference.path)[-1].casefold()
    matches = [i for i, target in enumerate(targets) if Path(target.path).name.casefold() == name]
    return matches[0] if len(matches) == 1 else None


class ReferenceEditorService:
    def __init__(self, config):
        self.casting = SmartCastingService(config)
        self.shots = self.casting.shot_service
        self.resolver = AssetPublishResolver(config)

    def scene_identity(self, scene_path):
        if not scene_path:
            return None
        shot = self.shots.shot_identity_from_path(scene_path)
        if shot is not None:
            return shot
        roots = set()
        for department in dict.fromkeys(['layout', *self.shots.shot_departments]):
            root = self.casting.paths.sequence_root_from_scene_path(scene_path, department)
            if root is not None:
                roots.add(path_key(root))
        matches = [identity for identity in self.casting.sequences()
                   if path_key(self.casting.paths.sequence_workspace_root(identity.episode, identity.sequence)) in roots]
        return matches[0] if len(matches) == 1 else None

    def preview(self, identity, department='anim'):
        if identity_scope(identity) == 'sequence':
            return self.shots.build_sequence_preview(identity)
        return self.shots.build_preview(identity, department=department)

    def targets(self, consumer='shot', department='anim'):
        targets = {}
        for asset in self.casting.list_assets():
            identity = AssetIdentity(asset.category, asset.group, asset.asset, asset.variant)
            root = self.casting.paths.asset_variant_root(identity)
            path = self.resolver.resolve(root, consumer=consumer, department=department)
            if path and path.is_file() and path.suffix.lower() in {'.ma', '.mb'}:
                label = f'{asset.category} / {asset.group} / {asset.asset} / {asset.variant} — {path.name}'
                targets[path_key(path)] = Target(label, str(path))
        return sorted(targets.values(), key=lambda row: row.label.casefold())

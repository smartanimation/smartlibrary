"""Immutable section layers, reused by their fixed product selection and timing."""
import hashlib
import json
from pathlib import Path
import re

from smartlib.core.metadata import read_json
from .usd_handoff import SECTION, compose_layers, composition_errors

ORDER = ('animation', 'layout', 'camera', 'assets')


def section_definition(kind, products, plan):
    fields = ('kind', 'target', 'version', 'inputs', 'entrypoint', 'changes', 'placement_data')
    members = [{key: product[key] for key in fields if key in product}
               for product in products if product['kind'] == kind]
    # Layout order is significant when several products author the same property.
    if kind != 'layout':
        members.sort(key=lambda row: row['target'])
    return dict(kind=kind, members=members,
                **{key: plan[key] for key in ('frame_range', 'fps', 'usd')})


def signature(definition):
    return hashlib.sha256(json.dumps(definition, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True).encode('utf8')).hexdigest()


def frozen_inputs(value):
    if isinstance(value, dict):
        if 'path' in value and 'sha256' in value:
            yield value
        else:
            for item in value.values():
                yield from frozen_inputs(item)
    elif isinstance(value, list):
        for item in value:
            yield from frozen_inputs(item)


def build_sections(service, identity, directory, products, plan):
    from pxr import Sdf, Usd, UsdUtils
    # Validate the complete selection, including Layout targets and duplicate
    # geometry, before allocating any new section version. No disk layer drafts.
    shot, stages, deps = compose_layers(dict.fromkeys(('shot',) + ORDER),
                                       products, plan, service, in_memory=True)
    paths, manifests, pending = {}, {}, []
    shot_id = dict(zip(('episode', 'sequence', 'shot'), service._identity(identity)))
    for kind in ORDER:
        definition = section_definition(kind, products, plan)
        if not definition['members']:
            continue
        # Sparse Layout opinions may depend on the selected Asset transform stack.
        # Do not reuse a layer if validation authored different opinions.
        # Anonymous sublayer IDs are process-specific; only authored opinions enter the signature.
        opinions = None
        if kind == 'layout':
            opinions = {}
            for key, stage in stages.items():
                if key == 'layout' or key.startswith('layout:'):
                    copy = Sdf.Layer.CreateAnonymous()
                    copy.TransferContent(stage.GetRootLayer())
                    copy.subLayerPaths = []
                    opinions[key] = copy.ExportToString()
        fingerprint = signature(dict(definition=definition, layout_opinions=opinions))
        root = service.paths.usd_section_dir(*service._identity(identity), kind)
        found = None
        candidates = sorted((p for p in root.glob('v*') if re.fullmatch(r'v[0-9]+', p.name)),
                            key=lambda p: int(p.name[1:]), reverse=True)
        for candidate in candidates:
            manifest = service._file(candidate, 'manifest.json')
            data = read_json(manifest, {})
            if (data.get('schema') == SECTION and data.get('status') == 'published'
                    and data.get('signature') == fingerprint and data.get('shot') == shot_id
                    and data.get('definition') == definition):
                found = service.load_handoff(manifest)
                manifests[kind] = manifest
                paths[kind] = Path(found['entrypoint']['path'])
                break
        if found:
            continue
        version, destination = service._reserve(root)
        output = service._file(destination, kind + '.usda')
        if kind == 'layout':
            groups = {}
            for member in definition['members']:
                category = member.get('inputs', {}).get('layout_type')
                if not category:
                    continue
                folder = service._file(destination, category)
                folder.mkdir(exist_ok=True)
                path = service._file(folder, member['inputs']['layout_name'] + '.usd')
                stages['layout:' + member['target']].GetRootLayer().Export(str(path))
                groups.setdefault(category, []).append((member.get('inputs', {}).get('layer_order', 0), path))
            group_paths = []
            for category in ('setdress', 'placement'):
                if category not in groups:
                    continue
                group_path = service._file(destination, category + '.usda')
                group = Sdf.Layer.CreateNew(str(group_path))
                group.subLayerPaths = [p.as_posix() for _, p in sorted(groups[category], key=lambda pair: pair[0])]
                group.Save()
                group_paths.append(group_path.as_posix())
            stages[kind].GetRootLayer().subLayerPaths = group_paths
        stages[kind].GetRootLayer().Export(str(output))
        # Capture dependencies for this section only, including inactive variants.
        layers, assets, unresolved = UsdUtils.ComputeAllDependencies(Sdf.AssetPath(str(output)))
        if unresolved:
            raise ValueError('Unresolved section dependencies: ' + str(unresolved))
        dependencies = {}
        for layer in layers:
            if not layer.anonymous and Path(layer.realPath).resolve() != output.resolve():
                ref = service.pin(layer.realPath)
                dependencies[ref['path']] = ref
        for path in assets:
            ref = service.pin(path)
            dependencies[ref['path']] = ref
        for ref in frozen_inputs(definition):
            service.check(ref)
            dependencies[ref['path']] = ref
        data = dict(schema=SECTION, status='published', approval='not_reviewed',
                    kind=kind, shot=shot_id, version=version, signature=fingerprint,
                    definition=definition, entrypoint=service.pin(output),
                    dependencies=list(dependencies.values()),
                    **{key: plan[key] for key in ('frame_range', 'fps', 'usd')})
        manifests[kind] = service._file(destination, 'manifest.json')
        pending.append((manifests[kind], data))
        paths[kind] = output
    # Composition contains only its entrypoint and receipt. It never copies layers.
    shot.GetRootLayer().subLayerPaths = [paths[k].as_posix() for k in ORDER if k in paths]
    if composition_errors(shot):
        raise ValueError('Invalid fixed section composition')
    paths['shot'] = service._file(directory, 'shot.usda')
    shot.GetRootLayer().Export(str(paths['shot']))
    for ref in deps:
        service.check(ref)
    return dict(paths, _dependencies=deps, _sections=manifests, _pending_sections=pending)

"""Evaluated camera packages: no DAG-path identity or pipeline path ownership.

This is a Maya Build input, never an AE Render Manifest. Samples deliberately
represent evaluated cameras, not their source rigs or sub-frame animation.
"""
from __future__ import annotations

import copy
import json
import math
import re

from . import camera_output as co

SCHEMA = "smartpipeline.camera_package.v1"
SUPPORTED_SCHEMAS = (SCHEMA, 'smartpipeline.camera_package.v2')
SETTINGS_NODE = ":smartCameraPlayblastInfo"
ROLE_ATTR = "smartCameraRole"
KEY_ATTR = "smartCameraKey"
ATTRS = tuple(dict.fromkeys(co.SHAPE_ATTRS + (
    "filmFit", "filmFitOffset", "overscan", "cameraScale", "lensSqueezeRatio",
    "shutterAngle", "orthographic", "panZoomEnabled", "filmRollValue",
    "filmTranslateH", "filmTranslateV", "postScale", "preScale",
)))


def _close(actual, expected, label):
    if len(actual) != len(expected) or any(
        not math.isfinite(float(a)) or not math.isfinite(float(b)) or
        abs(a - b) > 1e-5 * max(1., abs(b)) for a, b in zip(actual, expected)
    ):
        raise ValueError(f"Camera validation failed: {label}")


def collect_package(primary, rows, reference_resolution, *, cmds=None, progress=None):
    """Read and validate every published frame; never change source cameras."""
    if cmds is None:
        import maya.cmds as cmds
    source, source_shape = co.camera_nodes(primary, cmds)
    if cmds.objExists(f"{source}.{co.OWNER_ATTR}"):
        raise ValueError("Primary must not be a generated camera.")
    rows = copy.deepcopy(rows)
    if not rows or len({r['layer'] for r in rows}) != len(rows):
        raise ValueError("Choose at least one layer; layer names must be unique.")
    rw, rh = map(int, reference_resolution)
    if min(rw, rh) <= 0 or not math.isclose(cmds.getAttr('defaultResolution.pixelAspect'), 1.):
        raise ValueError("Positive reference resolution and square pixels are required.")
    start, end = min(int(r['start']) for r in rows), max(int(r['end']) for r in rows)
    entries = [dict(key="primary", role="primary", name=source.rsplit('|', 1)[-1].split(':')[-1],
                    frame_range=[start, end], resolution=[rw, rh], samples=[])]
    nodes = [(source, source_shape)]
    for row in rows:
        node, shape = co.camera_nodes(row['camera'], cmds)
        if (not cmds.objExists(f"{node}.{co.OWNER_ATTR}") or
                cmds.getAttr(f"{node}.{co.OWNER_ATTR}") != co.OWNER or
                co.primary_camera(node, cmds) != source):
            raise ValueError(f"{row['layer']}: generate from the selected Primary before publishing.")
        spec = json.loads(cmds.getAttr(f"{node}.{co.SPEC_ATTR}"))
        expected = {k: int(row[k]) for k in ('width', 'height', 'start', 'end')}
        expected.update(layer=row['layer'], policy=row.get('camera_fit', 'horizontal'),
                        reference_resolution=[rw, rh])
        if any(spec.get(k) != v for k, v in expected.items()):
            raise ValueError(f"{row['layer']}: camera settings are stale; Generate / Update first.")
        key = 'layer:' + row['layer']
        row['camera_key'] = key
        # Scene-local paths / output receipts must not become package authority.
        row['camera'] = node.rsplit('|', 1)[-1]
        row['output_override'] = ''
        entries.append(dict(key=key, role='generated', primary_key='primary',
                            name=row['camera'], spec=expected,
                            frame_range=[expected['start'], expected['end']],
                            resolution=[expected['width'], expected['height']], samples=[]))
        nodes.append((node, shape))
    original = cmds.currentTime(query=True)
    total = sum(e['frame_range'][1] - e['frame_range'][0] + 1 for e in entries)
    done = 0
    try:
        for entry, (node, shape) in zip(entries, nodes):
            aspect = entry['resolution'][0] / entry['resolution'][1]
            for frame in range(entry['frame_range'][0], entry['frame_range'][1] + 1):
                if progress and progress(done, total) is False:
                    raise ValueError('Camera Publish cancelled; nothing was published.')
                cmds.currentTime(frame, edit=True)
                co._check_supported(cmds, shape)
                matrix = cmds.xform(node, query=True, worldSpace=True, matrix=True)
                frustum = co._camera_fn(shape).getRenderingFrustum(aspect)
                attrs = {a: cmds.getAttr(f'{shape}.{a}') for a in ATTRS}
                if entry['role'] == 'generated':
                    expected = co.output_frustum(co._camera_fn(source_shape).getRenderingFrustum(rw / rh),
                                                 [rw, rh], entry['resolution'], entry['spec']['policy'])
                    _close(frustum, expected, f"{entry['key']} frame {frame} frustum")
                    _close(matrix, cmds.xform(source, query=True, worldSpace=True, matrix=True), entry['key'])
                    _close([attrs['focalLength']], [cmds.getAttr(f'{source_shape}.focalLength')], entry['key'])
                entry['samples'].append(dict(frame=frame, world_matrix=list(matrix),
                                             attributes=attrs, frustum=list(frustum)))
                done += 1
    finally:
        cmds.currentTime(original, edit=True)
    payload = dict(schema=SCHEMA, camera=entries[0]['name'], frame_range=[start, end],
                   units={u: cmds.currentUnit(query=True, **{u: True}) for u in ('time', 'linear', 'angle')},
                   reference_resolution=[rw, rh], cameras=entries, rows=rows)
    validate_package(payload)
    return payload


def validate_package(data):
    """Strict preflight before any Maya scene mutation."""
    if data.get('schema') != SCHEMA:
        raise ValueError('Unsupported camera package schema.')
    if set(data.get('units', {})) != {'time', 'linear', 'angle'}:
        raise ValueError('Camera package must declare time, linear and angle units.')
    entries, rows = data['cameras'], data['rows']
    keys = [e['key'] for e in entries]
    names = [e['name'] for e in entries]
    if len(set(keys)) != len(keys) or len(set(names)) != len(names):
        raise ValueError('Duplicate camera key or name.')
    if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', n) for n in names):
        raise ValueError('Camera package names must be namespace-free Maya identifiers.')
    primary = [e for e in entries if e['role'] == 'primary']
    if len(primary) != 1 or primary[0]['key'] != 'primary':
        raise ValueError('Exactly one Primary is required.')
    if primary[0]['frame_range'] != data['frame_range'] or primary[0]['resolution'] != data['reference_resolution']:
        raise ValueError('Primary sampling range or reference resolution is inconsistent.')
    for entry in entries:
        if entry['role'] not in ('primary', 'generated'):
            raise ValueError('Unknown camera role.')
        if entry['role'] == 'generated' and entry.get('primary_key') != 'primary':
            raise ValueError('Missing Primary relationship.')
        start, end = entry['frame_range']
        if start < data['frame_range'][0] or end > data['frame_range'][1]:
            raise ValueError('Output samples extend beyond the Primary range.')
        if end < start or [s['frame'] for s in entry['samples']] != list(range(start, end + 1)):
            raise ValueError('Camera samples must cover every integer frame.')
        if len(entry['resolution']) != 2 or min(entry['resolution']) <= 0:
            raise ValueError('Invalid camera resolution.')
        for sample in entry['samples']:
            if len(sample['world_matrix']) != 16 or len(sample['frustum']) != 4 or set(sample['attributes']) != set(ATTRS):
                raise ValueError('Incomplete camera sample.')
            values = sample['world_matrix'] + sample['frustum'] + list(sample['attributes'].values())
            if not all(math.isfinite(float(v)) for v in values):
                raise ValueError('Non-finite camera sample.')
    if {r['camera_key'] for r in rows} != set(keys) - {'primary'} or len(rows) != len(entries) - 1:
        raise ValueError('Layer / camera relationship is incomplete.')
    by_key = {e['key']: e for e in entries}
    for row in rows:
        entry = by_key[row['camera_key']]
        spec = entry['spec']
        if (entry['key'] != 'layer:' + row['layer'] or
                any(spec[k] != row[k] for k in ('layer', 'width', 'height', 'start', 'end')) or
                spec['policy'] not in co.OUTPUT_POLICIES or
                spec['reference_resolution'] != data['reference_resolution'] or
                entry['frame_range'] != [row['start'], row['end']] or
                entry['resolution'] != [row['width'], row['height']]):
            raise ValueError('Inconsistent layer generation recipe.')


def restore_package(data, *, cmds=None, frame_offset=0., provenance=''):
    """Create a self-contained baked package; fail on collisions, never overwrite."""
    if cmds is None:
        import maya.cmds as cmds
    if data.get('schema') == 'smartpipeline.camera_package.v2':
        from .camera_native import restore
        return restore(data, cmds=cmds, provenance=provenance, frame_offset=frame_offset)
    from .review_playblast import save_scene_playblast_settings
    validate_package(data)
    if not math.isfinite(frame_offset) or frame_offset != int(frame_offset):
        raise ValueError('Camera package Build supports integer frame offsets only.')
    for unit, value in data['units'].items():
        if unit not in ('time', 'linear', 'angle') or cmds.currentUnit(query=True, **{unit: True}) != value:
            raise ValueError(f'Camera package unit mismatch: {unit}={value}. Match scene units first.')
    if not cmds.undoInfo(query=True, state=True):
        raise ValueError('Enable Maya Undo before restoring a camera package.')
    for name in ['smartCameraPublish'] + [e['name'] for e in data['cameras']]:
        if cmds.ls(':' + name, long=True):
            raise ValueError(f'Camera package name collision: {name}. Existing nodes were not changed.')
    if cmds.objExists(f'{SETTINGS_NODE}.settingsJson'):
        prefs = json.loads(cmds.getAttr(f'{SETTINGS_NODE}.settingsJson'))
        if prefs.get('publish_source'):
            raise ValueError('A camera package is already active. Build into a fresh scene.')
    original = cmds.currentTime(query=True)
    selection = cmds.ls(selection=True, long=True) or []
    cmds.undoInfo(openChunk=True, chunkName='Restore Smart Camera Publish')
    failed = False
    try:
        root = cmds.group(empty=True, name=':smartCameraPublish')
        mapped = {}
        for entry in data['cameras']:
            node, shape = cmds.camera(name=':' + entry['name'])
            node = cmds.parent(node, root)[0]
            node = cmds.rename(node, ':' + entry['name'], ignoreShape=True)
            node, shape = co.camera_nodes(node, cmds)
            if node.rsplit('|', 1)[-1] != entry['name']:
                raise ValueError('Could not assign exact published camera name.')
            mapped[entry['key']] = node
            cmds.setAttr(f'{shape}.renderable', 0)
            for sample in entry['samples']:
                frame = sample['frame'] + frame_offset
                cmds.currentTime(frame, edit=True)
                cmds.xform(node, worldSpace=True, matrix=sample['world_matrix'])
                cmds.setKeyframe(node, attribute=list(co.TRANSFORM_ATTRS), time=frame)
                for attr, value in sample['attributes'].items():
                    cmds.setAttr(f'{shape}.{attr}', value)
                    cmds.setKeyframe(shape, attribute=attr, time=frame)
                _close(cmds.xform(node, query=True, worldSpace=True, matrix=True), sample['world_matrix'], entry['key'])
                _close(co._camera_fn(shape).getRenderingFrustum(entry['resolution'][0] / entry['resolution'][1]),
                       sample['frustum'], entry['key'])
            cmds.keyTangent(node, attribute=list(co.TRANSFORM_ATTRS), inTangentType='linear', outTangentType='linear')
            cmds.keyTangent(shape, attribute=list(ATTRS), inTangentType='linear', outTangentType='linear')
            cmds.filterCurve([f'{node}.rx', f'{node}.ry', f'{node}.rz'])
            co._string_attr(cmds, node, ROLE_ATTR, entry['role'])
            co._string_attr(cmds, node, KEY_ATTR, entry['key'])
            if entry['role'] == 'primary':
                co._string_attr(cmds, node, co.SAMPLE_RANGE_ATTR, json.dumps(
                    [value + frame_offset for value in entry['frame_range']]))
        policies = {}
        for entry in data['cameras']:
            if entry['role'] != 'generated':
                continue
            node = mapped[entry['key']]
            spec = dict(entry['spec'])
            spec.update(start=spec['start'] + frame_offset, end=spec['end'] + frame_offset)
            co._string_attr(cmds, node, co.OWNER_ATTR, co.OWNER)
            co._string_attr(cmds, node, co.SPEC_ATTR, json.dumps(spec))
            cmds.addAttr(node, longName=co.PRIMARY_ATTR, attributeType='message')
            cmds.connectAttr(f"{mapped['primary']}.message", f'{node}.{co.PRIMARY_ATTR}')
            policies[spec['layer']] = spec['policy']
        rows = copy.deepcopy(data['rows'])
        for row in rows:
            row.update(camera=mapped[row['camera_key']].rsplit('|', 1)[-1],
                       start=row['start'] + frame_offset, end=row['end'] + frame_offset, mode='Custom')
        prefs = dict(primary=mapped['primary'], primary_uuid=cmds.ls(mapped['primary'], uuid=True)[0],
                     reference_resolution=data['reference_resolution'], layer_policies=policies,
                     publish_source=provenance or 'camera_package', auto_update=False)
        if not cmds.objExists(SETTINGS_NODE):
            cmds.createNode('network', name=SETTINGS_NODE, skipSelect=True)
        co._string_attr(cmds, SETTINGS_NODE, 'settingsJson', json.dumps(prefs))
        save_scene_playblast_settings(dict(rows=rows, department=data.get('department', ''),
                                          layer_order=[r['layer'] for r in rows],
                                          camera_package_source=provenance or 'camera_package'), cmds)
        return root
    except Exception:
        failed = True
        raise
    finally:
        cmds.currentTime(original, edit=True)
        cmds.select(selection, replace=True) if selection else cmds.select(clear=True)
        cmds.undoInfo(closeChunk=True)
        if failed:
            cmds.undo()


def merge_build_settings(settings, cmds):
    """The camera package owns its rows; legacy drafts cannot replace bindings."""
    from .review_playblast import load_scene_playblast_settings
    current = load_scene_playblast_settings(cmds)
    if not current.get('camera_package_source'):
        return settings
    result = dict(settings)
    owned = {row['layer'] for row in current['rows']}
    result['rows'] = current['rows'] + [r for r in settings.get('rows', []) if r['layer'] not in owned]
    result['layer_order'] = [r['layer'] for r in result['rows']]
    result['camera_package_source'] = current['camera_package_source']
    return result

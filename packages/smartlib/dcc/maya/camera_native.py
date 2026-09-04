"""Unbaked Primary dependency graph + live layer recipes (camera package v2)."""
import json
import re
from pathlib import Path

from . import camera_output as co, camera_live

SCHEMA = 'smartpipeline.camera_package.v2'
IMPORT_NAMESPACE = 'smartPrimary'


def dependencies(primary, cmds):
    """Explicit input closure, including DAG ancestors but NOT their other children."""
    transform, shape = co.camera_nodes(primary, cmds)
    pending, found = [transform, shape], set()
    defaults = set(cmds.ls(defaultNodes=True, long=True) or [])
    while pending:
        node = pending.pop()
        resolved = cmds.ls(node, long=True) or []
        if len(resolved) != 1:
            raise ValueError('Ambiguous dependency: ' + node)
        node = resolved[0]
        if node in found or node in defaults:
            continue
        kind = cmds.nodeType(node)
        if cmds.nodeType(node, apiType=True).startswith('kPlugin'):
            raise ValueError('Plugin dependency needs a dedicated publishing adapter: ' + node)
        if kind in {'script', 'unknown', 'unknownDag', 'AlembicNode', 'cacheFile', 'gpuCache',
                    'mayaUsdProxyShape', 'file', 'audio'}:
            raise ValueError(f'Cannot embed dependency {node} ({kind}); resolve its external dependency before publishing.')
        if kind == 'expression':
            expression = cmds.expression(node, query=True, string=True)
            if re.search(r'`|\b(getAttr|eval|evalDeferred|python|system|file|xform)\b', expression):
                raise ValueError('Expression uses dynamic dependencies: ' + node)
        found.add(node)
        if cmds.objectType(node, isAType='dagNode'):
            if len(cmds.ls(node, allPaths=True, long=True) or []) != 1:
                raise ValueError('Instanced camera dependency is not supported: ' + node)
            pending.extend(cmds.listRelatives(node, parent=True, fullPath=True) or [])
        pairs = cmds.listConnections(node, source=True, destination=False, connections=True, plugs=True) or []
        for i in range(0, len(pairs), 2):
            destination, source = pairs[i:i + 2]
            if cmds.getAttr(destination, type=True) == 'message':
                continue
            pending.append(source.split('.', 1)[0])
    return sorted(found)


def collect(primary, rows, reference, cmds):
    primary, shape = co.camera_nodes(primary, cmds)
    if cmds.objExists(primary + '.' + co.OWNER_ATTR):
        raise ValueError('Primary cannot be a derived output camera.')
    co._check_supported(cmds, shape)
    nodes = dependencies(primary, cmds)
    if primary not in nodes or shape not in nodes:
        raise ValueError('Choose a shot camera, not a Maya default camera.')
    if not rows or len({r['layer'] for r in rows}) != len(rows):
        raise ValueError('Choose unique output layers.')
    output_rows = []
    for incoming in rows:
        row = dict(incoming)
        rule = row.get('camera_rule') or {'mode': 'shared'}
        width, height = camera_live.output_size(reference, rule)
        row.update(camera_rule=rule, width=width, height=height, output_override='',
                   camera=primary.rsplit('|', 1)[-1] if rule['mode'] == 'shared' else 'smartCam_' + re.sub(r'[^A-Za-z0-9_]', '_', row['layer']))
        output_rows.append(row)
    return dict(schema=SCHEMA, camera=primary.rsplit('|', 1)[-1],
                primary_path=primary, dependency_nodes=nodes,
                cameras=[dict(key='primary', role='primary', name=primary.rsplit('|', 1)[-1])],
                reference_resolution=list(reference), rows=output_rows,
                frame_range=[min(r['start'] for r in rows), max(r['end'] for r in rows)],
                units={u: cmds.currentUnit(query=True, **{u: True}) for u in ('time', 'linear', 'angle')})


def export_native(payload, directory, cmds):
    # Called by the existing publish service BEFORE camera.json / latest are committed.
    path = Path(directory) / 'primary.ma'
    selection = cmds.ls(selection=True, long=True) or []
    try:
        cmds.select(payload['dependency_nodes'], replace=True, noExpand=True)
        cmds.file(str(path), force=True, type='mayaAscii', options='v=0;',
                  exportSelectedStrict=True, preserveReferences=False,
                  channels=True, constraints=True, expressions=True, constructionHistory=True)
    finally:
        cmds.select(selection, replace=True) if selection else cmds.select(clear=True)
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError('Primary native export failed.')
    return {'ma': path.name}


def restore(data, *, cmds, provenance, frame_offset=0.):
    from .review_playblast import save_scene_playblast_settings
    if frame_offset:
        raise ValueError('Unbaked camera dependencies require original timing; nonzero Build offset is not supported.')
    for unit, value in data['units'].items():
        if unit not in ('time', 'linear', 'angle') or cmds.currentUnit(query=True, **{unit: True}) != value:
            raise ValueError('Primary package scene units do not match: ' + unit)
    filename = (data.get('files') or {}).get('ma', '')
    if not filename or Path(filename).name != filename:
        raise ValueError('Primary native file is missing or invalid.')
    path = Path(provenance).parent / filename
    if not path.is_file():
        raise FileNotFoundError(path)
    if cmds.namespace(exists=IMPORT_NAMESPACE):
        raise ValueError('Primary package namespace is occupied: ' + IMPORT_NAMESPACE)
    if cmds.objExists(':smartCameraPublish'):
        raise ValueError('A Primary package already exists in this scene.')
    for row in data['rows']:
        camera_live.output_size(data['reference_resolution'], row['camera_rule'])
        if row['camera_rule']['mode'] != 'shared' and cmds.ls(':' + row['camera'], long=True):
            raise ValueError('Output camera name collision: ' + row['camera'])
    if not cmds.undoInfo(query=True, state=True):
        raise ValueError('Enable Undo for native camera import.')
    selection = cmds.ls(selection=True, long=True) or []
    cmds.undoInfo(openChunk=True, chunkName='Restore Native Camera Package')
    failed = False
    try:
        imported = cmds.file(str(path), i=True, type='mayaAscii', namespace=IMPORT_NAMESPACE,
                             mergeNamespacesOnClash=False, returnNewNodes=True, executeScriptNodes=False)
        for original in data['dependency_nodes']:
            expected = ('|' + '|'.join(IMPORT_NAMESPACE + ':' + part for part in original.split('|') if part)
                        if original.startswith('|') else IMPORT_NAMESPACE + ':' + original)
            if not cmds.objExists(expected):
                raise ValueError('Native export lost a required dependency: ' + original)
        imported_primary = '|' + '|'.join(IMPORT_NAMESPACE + ':' + part for part in data['primary_path'].split('|') if part)
        primary, _ = co.camera_nodes(imported_primary, cmds)
        if primary not in (cmds.ls(imported, long=True) or []):
            raise ValueError('Primary was not uniquely restored from this native package.')
        root = cmds.group(empty=True, name=':smartCameraPublish')
        # Preserve original paths inside the package; moving only complete DAG
        # roots keeps internal constraints and expression connections intact.
        imported_set = set(cmds.ls(imported, long=True) or [])
        roots = [n for n in imported_set if cmds.objectType(n, isAType='dagNode') and not cmds.listRelatives(n, parent=True)]
        primary_uuid = cmds.ls(primary, uuid=True)[0]
        for node in roots:
            cmds.parent(node, root, relative=True)
        primary = cmds.ls(primary_uuid, long=True)[0]
        results = camera_live.configure(primary, data['rows'], data['reference_resolution'], cmds=cmds)
        rows = []
        for incoming, result in zip(data['rows'], results):
            rows.append({**incoming, 'camera': result['camera'].rsplit('|', 1)[-1], 'mode': 'Custom'})
        prefs = dict(primary=primary, primary_uuid=primary_uuid, reference_resolution=data['reference_resolution'],
                     layer_rules={r['layer']: r['camera_rule'] for r in rows}, publish_source=provenance,
                     live=True, auto_update=True)
        node = ':smartCameraPlayblastInfo'
        if not cmds.objExists(node):
            cmds.createNode('network', name=node, skipSelect=True)
        co._string_attr(cmds, node, 'settingsJson', json.dumps(prefs))
        save_scene_playblast_settings(dict(rows=rows, department=data.get('department', ''),
                                          layer_order=[r['layer'] for r in rows], camera_package_source=provenance), cmds)
        return root
    except Exception:
        failed = True
        raise
    finally:
        cmds.select(selection, replace=True) if selection else cmds.select(clear=True)
        cmds.undoInfo(closeChunk=True)
        if failed:
            cmds.undo()

"""Capture camera batches before portable exports replace the worker scene."""
import hashlib
import json

from smartlib.core.metadata import read_json, write_json


def primary_fingerprint(node, bounds, cmds):
    from .camera_output import camera_nodes
    from .camera_portable import SHAPE_ATTRIBUTES
    node, shape = camera_nodes(node, cmds)
    previous = cmds.currentTime(query=True)
    values = []
    try:
        for frame in range(int(bounds[0]), int(bounds[1]) + 1):
            cmds.currentTime(frame, edit=True)
            values.append([round(float(v), 8) for v in
                list(cmds.xform(node, query=True, worldSpace=True, matrix=True)) +
                [cmds.getAttr(shape + '.' + attr) for attr in SHAPE_ATTRIBUTES]])
    finally:
        cmds.currentTime(previous, edit=True)
    return hashlib.sha256(json.dumps(values, separators=(',', ':')).encode()).hexdigest()


def capture(service, identity, request, cmds):
    from .shot_publish_sources import camera_sources
    from . import primary_camera, camera_portable
    sources = camera_sources(cmds)
    selected = set(request['scene_options']['targets'])
    if not selected or not selected <= {r['target'] for r in sources}:
        raise ValueError('Selected cameras are no longer in Smart Camera Playblast')
    primary = sources[0]
    from .camera_output import PRIMARY_ATTR, camera_nodes
    for source in sources[1:]:
        linked = cmds.listConnections(source['node'] + '.' + PRIMARY_ATTR, source=True, destination=False) or []
        if len(linked) != 1 or camera_nodes(linked[0], cmds)[0] != primary['node']:
            raise ValueError('Output Camera is not linked to the selected Primary: ' + source['node'])
    fingerprint = primary_fingerprint(primary['node'], request['frame_range'], cmds)
    primary_ref = None
    if 'primary' in selected:
        selected = {r['target'] for r in sources}
    else:
        base = request.get('base_composition')
        if not base:
            raise ValueError('Publish Primary and all used cameras first')
        data = service.load_handoff(service.check(base))
        products = [service.load_handoff(service.check(r)) for r in data['products']]
        originals = [p for p in products if p['kind'] == 'camera' and
                     p['inputs'].get('camera_role', 'primary') == 'primary']
        if len(originals) != 1 or originals[0]['inputs'].get('primary_fingerprint') != fingerprint:
            raise ValueError('Primary has changed or has no provenance. Publish Primary and all cameras.')
        primary_ref = originals[0]['inputs']['camera_snapshot']
        service.check(primary_ref)
    captured = []
    # Re-evaluate live camera rules from the fixed scene settings before capture.
    from . import camera_live
    live_rows = []
    for source in sources:
        for layer in source['layers']:
            live_rows.append(dict(layer=layer['layer'], start=layer['frame_range'][0], end=layer['frame_range'][1],
                                  camera_rule=layer['camera_rule']))
    if live_rows:
        cmds.undoInfo(state=True)
        configured = {row['layer']: row for row in
            camera_live.configure(primary['node'], live_rows, primary['reference_resolution'], cmds=cmds)}
        for source in sources:
            for layer in source['layers']:
                actual = configured[layer['layer']]
                if (camera_nodes(actual['camera'], cmds)[0] != source['node'] or
                        [actual['width'], actual['height']] != layer['resolution']):
                    raise ValueError('Apply Smart Camera Playblast live settings before publishing: ' + layer['layer'])
    for source in sources:
        if source['target'] not in selected:
            continue
        # Derived cameras use the same world bake exporter, with an explicit role.
        payload = primary_camera.collect(source['node'], request['frame_range'], cmds,
            motion_mode=request['scene_options'].get('motion_mode', 'animated'),
            sample_frame=request['scene_options'].get('sample_frame'), role=source['role'])
        payload['camera_settings'] = source
        payload['primary_fingerprint'] = fingerprint
        snapshot = service.shots.publish_shot_scene_snapshot(identity, payload, data_type='camera',
            target=source['target'], source_workfile=request['scene_input']['source']['path'],
            comment=request['scene_options'].get('comment', ''),
            native_exporter=lambda directory, p=payload: primary_camera.export_native(p, directory, cmds))
        data = read_json(snapshot, {})
        captured.append((source, snapshot, service.pin(service.paths.manifest_source(snapshot, data['files']['ma']))))
    rows = []
    for source, snapshot, native in captured:
        payload = read_json(snapshot, {})
        try:
            cmds.file(str(service.check(native)), open=True, force=True, executeScriptNodes=False)
            files = camera_portable.export_portable(payload, snapshot.parent, cmds)
            camera_portable.validate_portable(files, snapshot.parent, cmds)
            service.check(native)
            camera_portable.update_publish(snapshot, status='complete', files=files)
            published = read_json(snapshot, {})
            if source['role'] == 'derived':
                published['primary_source'] = primary_ref
                published['primary_version'] = read_json(service.check(primary_ref), {})['version']
                write_json(snapshot, published)
            receipt_path = service._file(snapshot.parent, 'publish.json')
            receipt = read_json(receipt_path, {})
            receipt.update(camera_role=source['role'], camera_settings=source,
                           primary_fingerprint=fingerprint)
            if source['role'] == 'derived':
                receipt.update(primary_source=primary_ref, primary_version=published['primary_version'])
            write_json(receipt_path, receipt)
        except Exception as exc:
            camera_portable.update_publish(snapshot, status='failed', error=str(exc))
            raise
        snapshot_ref = service.pin(snapshot)
        if source['role'] == 'primary':
            primary_ref = snapshot_ref
        rows.append(dict(kind='camera', target=source['target'],
            source=str(service.paths.manifest_source(snapshot, files['usd'])),
            camera_role=source['role'], camera_snapshot=snapshot_ref,
            primary_source=primary_ref, primary_fingerprint=fingerprint,
            camera_settings=source, camera_motion=payload['camera_motion']))
    return service.plan(identity, rows, frame_range=request['frame_range'])

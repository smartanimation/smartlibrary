"""Scene-owned source discovery shared by Shot Publish and its saved-scene worker."""
import json
import re


def token(name):
    value = re.sub(r'[^A-Za-z0-9_]', '_', name.rsplit('|', 1)[-1])
    return value if value and not value[0].isdigit() else '_' + value


def camera_sources(cmds=None):
    if cmds is None:
        import maya.cmds as cmds
    from .camera_output import camera_nodes
    plug = ':smartCameraPlayblastInfo.settingsJson'
    if not cmds.objExists(plug):
        return []
    prefs = json.loads(cmds.getAttr(plug) or '{}')
    primary, _ = camera_nodes(prefs.get('primary_uuid') or prefs.get('primary', ''), cmds)
    reference = list(prefs.get('reference_resolution') or [1280, 720])
    # Read the saved scene, never another session's optionVar.
    plug = 'smartPlayblastInfo.settingsJson'
    settings = json.loads(cmds.getAttr(plug) or '{}') if cmds.objExists(plug) else {}
    result = [dict(target='primary', node=primary, role='primary', resolution=reference,
                   reference_resolution=reference, layers=[])]
    by_node = {primary: result[0]}
    for row in settings.get('rows', []):
        if not row.get('enabled', True):
            continue
        node, _ = camera_nodes(row.get('camera', ''), cmds)
        layer = row['layer']
        info = dict(layer=layer, resolution=[int(row['width']), int(row['height'])],
                    frame_range=[int(row['start']), int(row['end'])],
                    camera_rule=(prefs.get('layer_rules') or {}).get(layer, {'mode': 'shared'}))
        if min(info['resolution']) <= 0:
            raise ValueError('Camera resolution must be positive: ' + layer)
        if node not in by_node:
            by_node[node] = dict(target=token(node), node=node, role='derived',
                resolution=info['resolution'], reference_resolution=reference, layers=[])
            result.append(by_node[node])
        by_node[node]['layers'].append(info)
    if len({row['target'] for row in result}) != len(result):
        raise ValueError('Camera names produce duplicate Publish targets')
    return result


def placement_sources():
    from .placement import list_placement_locators
    return [dict(target=token(row.name), node=row.node, label=row.name,
                 member=row.member, motion=row.motion) for row in list_placement_locators()]


def setdress_sources(cmds=None):
    from .set_dress import load_package_from_scene
    package, _ = load_package_from_scene(cmds)
    return [dict(target=token(layer.name), layer_id=layer.id, label=layer.name,
                 muted=layer.muted, changes=len(layer.changes))
            for layer in package.layers if layer.scope == 'shot'] if package else []

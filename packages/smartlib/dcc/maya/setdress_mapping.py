"""Asset export correspondence and shot-scene mapping preferences."""
import json

ATTR = 'smartSetDressUsdMappings'


def scene_nodes(nodes, cmds=None):
    if cmds is None:
        import maya.cmds as cmds
    resolved, ids = dict(nodes), {}
    from .set_dress import _resolve_node
    for key, name in nodes.items():
        found = _resolve_node(cmds, key, name)
        matches = cmds.ls(found, long=True) if found else []
        if len(matches) == 1:
            resolved[key] = matches[0]
            ids[key] = (cmds.ls(matches[0], uuid=True) or [key])[0]
            plug = matches[0] + '.smartSetDressId'
            if cmds.objExists(plug):
                ids[key] = str(cmds.getAttr(plug))
    return resolved, ids


def load(identity, cmds=None):
    if cmds is None:
        import maya.cmds as cmds
    from .set_dress import _scene_data_node
    node = _scene_data_node(cmds)
    if not node or not cmds.objExists(node + '.' + ATTR):
        return {}
    data = json.loads(cmds.getAttr(node + '.' + ATTR) or '{}')
    return data.get('/'.join((identity.episode, identity.sequence, identity.shot)), {})


def save(identity, records, cmds=None, *, replace_keys=()):
    if cmds is None:
        import maya.cmds as cmds
    from .set_dress import _ensure_scene_data_node
    node = _ensure_scene_data_node(cmds)
    plug = node + '.' + ATTR
    if not cmds.objExists(plug):
        cmds.addAttr(node, longName=ATTR, dataType='string')
    data = json.loads(cmds.getAttr(plug) or '{}')
    scope = '/'.join((identity.episode, identity.sequence, identity.shot))
    stored = data.setdefault(scope, {})
    for key in replace_keys:
        stored.pop(key, None)
    stored.update(records)
    encoded = json.dumps(data, sort_keys=True)
    if cmds.getAttr(plug) != encoded:
        cmds.setAttr(plug, encoded, type='string')


def stamp_asset(stage, transforms, cmds, *, flattened=None):
    """Record exact exporter paths only; omitted/flattened groups are not guessed."""
    from pxr import Tf
    from smartlib.apps.shot_manager.setdress_mapping import source_path
    written = {}
    for node in sorted(transforms):
        path = (flattened or {}).get(node) if flattened is not None else (
            '/' + '/'.join(Tf.MakeValidIdentifier(part) for part in node.split('|') if part))
        if not path:
            continue
        prim = stage.GetPrimAtPath(path)
        if not prim or prim.IsInstanceProxy():
            continue
        if path in written:
            raise ValueError('Ambiguous Maya export mapping: ' + path)
        written[path] = node
        stable = node + '.smartSetDressId'
        source_id = cmds.getAttr(stable) if cmds.objExists(stable) else (cmds.ls(node, uuid=True) or [''])[0]
        prim.SetCustomDataByKey('smartpipeline:setdress:sourceId', str(source_id))
        prim.SetCustomDataByKey('smartpipeline:setdress:sourcePath', source_path(node))
    return len(written)

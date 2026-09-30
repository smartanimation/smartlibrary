"""Capture replacement geometry correspondence without changing the Maya DAG."""
import json

from smartlib.apps.shot_manager.setdress_mapping import source_path
from .assembly_replacement import ATTR, REF_ATTR, _linked, _reference_geometry


def capture(cmds, locator, cast, frames):
    matches = []
    for plug in cmds.ls('*.' + ATTR, recursive=True) or []:
        node = plug.rsplit('.', 1)[0]
        references = _linked(cmds, node, REF_ATTR)
        for ref in references:
            namespace = cmds.referenceQuery(ref, namespace=True).strip(':')
            if namespace == locator.member:
                matches.append((node, ref, json.loads(cmds.getAttr(plug))))
    if not matches:
        if ':' in locator.member:
            raise ValueError('Nested Placement requires Assembly correspondence: ' + locator.member)
        return None
    if len(matches) != 1:
        raise ValueError('Ambiguous Assembly Placement: ' + locator.member)
    node, ref, record = matches[0]
    parent_ref = cmds.referenceQuery(node, referenceNode=True)
    namespace = cmds.referenceQuery(parent_ref, namespace=True).strip(':')
    owners = [key for key, value in cast.items() if value.get('namespace', key).strip(':') == namespace]
    if len(owners) != 1:
        raise ValueError('Assembly Placement requires one parent Cast: ' + namespace)
    full = cmds.ls(node, long=True) or []
    if len(full) != 1:
        raise ValueError('Ambiguous Assembly group: ' + node)
    _, meshes = _reference_geometry(cmds, ref)
    parents = sorted({mesh.rsplit('|', 1)[0] for mesh in meshes})
    if len(parents) != len(meshes):
        raise ValueError('Assembly Placement requires one output mesh per transform')
    mesh_by_parent = {mesh.rsplit('|', 1)[0]: mesh for mesh in meshes}
    geometry = []
    for parent in parents:
        if not parent.startswith(full[0] + '|'):
            raise ValueError('Assembly geometry is outside its public group: ' + parent)
        key = source_path(record['target_path'] + parent[len(full[0]):])
        geometry.append(dict(source_path=key, samples=[]))
    previous = cmds.currentTime(query=True)
    try:
        for frame in frames:
            cmds.currentTime(frame, edit=True)
            for parent, item in zip(parents, geometry):
                item['samples'].append(dict(frame=frame, matrix=list(
                    cmds.xform(parent, query=True, worldSpace=True, matrix=True)),
                    points=list(cmds.xform(mesh_by_parent[parent] + '.vtx[*]',
                                           query=True, worldSpace=True, translation=True))))
    finally:
        cmds.currentTime(previous, edit=True)
    return dict(cast=owners[0], instance_id=record['id'], geometry=geometry)

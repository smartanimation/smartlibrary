"""Bounded constraint detection for the Animation Data transfer contract."""
import hashlib
from pathlib import Path


def constraint_plugs(cmds, controls, candidate_plugs):
    def driven(plug, seen):
        if plug in seen:
            return False
        seen.add(plug)
        for source in cmds.listConnections(plug, source=True, destination=False, plugs=True) or []:
            node = source.rsplit('.', 1)[0]
            kind = cmds.nodeType(node)
            if kind.endswith('Constraint') or kind == 'constraint':
                return True
            if kind in {'pairBlend', 'unitConversion', 'blendWeighted'}:
                if driven(node, seen):
                    return True
        return False
    return sorted({plug for node in controls for plug in candidate_plugs(cmds, node)
                   if driven(plug, set())})


def external_constraint_nodes(cmds, namespace):
    """Include shot-authored constraint targets outside the controller set.

    Rig-internal referenced constraints must not turn all driven joints into
    transfer controls. Their evaluation is reproduced by the fixed rig itself.
    """
    result = set()
    for constraint in cmds.ls(type='constraint') or []:
        try:
            if cmds.referenceQuery(constraint, isNodeReferenced=True):
                continue
        except (RuntimeError, AttributeError):
            continue
        for target in cmds.listConnections(constraint, source=False, destination=True) or []:
            leaf = target.rsplit('|', 1)[-1]
            if leaf.startswith(namespace.strip(':') + ':') and cmds.nodeType(target) in {'transform', 'joint'}:
                result.update(cmds.ls(target, long=True) or [])
    return sorted(result)


def rig_dependencies(cmds, nodes):
    result = {}
    for node in nodes:
        try:
            if not cmds.referenceQuery(node, isNodeReferenced=True):
                continue
            path = Path(cmds.referenceQuery(node, filename=True, withoutCopyNumber=True)).resolve()
        except (AttributeError, RuntimeError):
            continue
        if not path.is_file():
            raise ValueError(f'Missing referenced Rig: {path}')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        result[str(path)] = {'path': path.as_posix(), 'sha256': digest.hexdigest()}
    return [result[key] for key in sorted(result)]


def sample_plugs(cmds, plugs, start, end):
    if not plugs:
        return {}
    previous = cmds.currentTime(query=True)
    values = {plug: [] for plug in plugs}
    try:
        for frame in range(start, end + 1):
            cmds.currentTime(frame, edit=True)
            for plug in plugs:
                value = cmds.getAttr(plug)
                if not isinstance(value, (float, int, bool)):
                    raise ValueError(f'Constraint output is not a scalar channel: {plug}')
                values[plug].append(float(value))
    finally:
        cmds.currentTime(previous, edit=True)
    return values


def scene_timing(cmds):
    try:
        import maya.api.OpenMaya as om
    except ImportError:
        return {}
    return {'fps': om.MTime(1, om.MTime.kSeconds).asUnits(om.MTime.uiUnit()),
            'linear_unit': cmds.currentUnit(query=True, linear=True),
            'up_axis': cmds.upAxis(query=True, axis=True).upper()}

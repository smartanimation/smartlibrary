"""Native DG output cameras. No frame scans, keyframes, callbacks or plug-ins."""
import json
import math
import re

from . import camera_output as co

LIVE_ATTR = 'smartCameraLiveNodes'
LIVE_SCHEMA = 'smartpipeline.camera_live.v1'


def _rebindable_named_output(cmds, name, layer):
    """Return an exact-name Live Camera that is safe to move to a new Primary.

    An arbitrary, baked, referenced, or differently-owned node is never
    adopted.  This keeps the existing collision guard while allowing the
    tool's own per-layer Live Camera to follow an intentional Primary change.
    """
    occupied = cmds.ls(':' + name, long=True) or []
    if len(occupied) != 1:
        return None
    node = occupied[0]
    required = (co.OWNER_ATTR, co.SPEC_ATTR, LIVE_ATTR)
    if any(not cmds.objExists(node + '.' + attr) for attr in required):
        return None
    if cmds.getAttr(node + '.' + co.OWNER_ATTR) != co.OWNER:
        return None
    try:
        spec = json.loads(cmds.getAttr(node + '.' + co.SPEC_ATTR))
    except (TypeError, ValueError, RuntimeError):
        return None
    if spec.get('layer') != layer:
        return None
    if cmds.referenceQuery(node, isNodeReferenced=True):
        raise ValueError('Cannot rebind a referenced output camera: ' + node)
    return node


def output_size(reference, rule):
    width, height = map(int, reference)
    if min(width, height) <= 0:
        raise ValueError('Final output size must be positive.')
    mode = rule.get('mode', 'shared')
    if mode == 'shared':
        return width, height
    if mode == 'scale':
        scale = float(rule.get('scale', 1.1))
        if not math.isfinite(scale) or scale < 1 or scale > 10:
            raise ValueError('Expansion must be between 1.0 and 10.0.')
        return int(math.floor(width * scale + .5)), int(math.floor(height * scale + .5))
    if mode == 'resolution':
        result = int(rule['width']), int(rule['height'])
        if result[0] < width or result[1] < height:
            raise ValueError('Expanded material resolution cannot be smaller than the final output.')
        return result
    raise ValueError('Unknown output camera rule.')


class Graph:
    def __init__(self, cmds, camera):
        self.cmds, self.camera, self.nodes = cmds, camera, []

    def node(self, kind):
        node = self.cmds.createNode(kind, name=':smartCameraMath', skipSelect=True)
        self.nodes.append(node)
        return node

    def put(self, value, plug):
        if isinstance(value, str):
            if not self.cmds.isConnected(value, plug):
                self.cmds.connectAttr(value, plug, force=True)
        else:
            self.cmds.setAttr(plug, value)

    def mul(self, a, b, divide=False):
        n = self.node('multiplyDivide')
        self.cmds.setAttr(n + '.operation', 2 if divide else 1)
        self.put(a, n + '.input1X'); self.put(b, n + '.input2X')
        return n + '.outputX'

    def add(self, a, b, subtract=False):
        n = self.node('plusMinusAverage')
        self.cmds.setAttr(n + '.operation', 2 if subtract else 1)
        self.put(a, n + '.input1D[0]'); self.put(b, n + '.input1D[1]')
        return n + '.output1D'

    def choose(self, a, b, yes, no, operation=0):
        n = self.node('condition')
        self.cmds.setAttr(n + '.operation', operation)
        for value, attr in [(a, 'firstTerm'), (b, 'secondTerm'), (yes, 'colorIfTrueR'), (no, 'colorIfFalseR')]:
            self.put(value, n + '.' + attr)
        return n + '.outColorR'


def configure(primary, rows, reference, *, cmds=None):
    if cmds is None:
        import maya.cmds as cmds
    source, shape = co.camera_nodes(primary, cmds)
    if cmds.objExists(source + '.' + co.OWNER_ATTR):
        raise ValueError('Choose an original Primary, not a generated camera.')
    co._check_supported(cmds, shape)
    if not math.isclose(cmds.getAttr('defaultResolution.pixelAspect'), 1.):
        raise ValueError('Live camera output currently requires square pixels.')
    if not cmds.undoInfo(query=True, state=True):
        raise ValueError('Enable Maya Undo for safe camera setup.')
    if len({r['layer'] for r in rows}) != len(rows) or not rows:
        raise ValueError('Select unique nonempty layers.')
    specs = []
    names = set()
    for row in rows:
        rule = row.get('camera_rule') or {'mode': 'shared'}
        width, height = output_size(reference, rule)
        name = 'smartCam_' + re.sub(r'[^A-Za-z0-9_]', '_', row['layer'])
        if name in names:
            raise ValueError('Layer names produce duplicate camera names.')
        names.add(name)
        existing = None
        if rule.get('mode', 'shared') != 'shared':
            existing = co._find_output(cmds, source, row['layer'])
            occupied = cmds.ls(':' + name, long=True) or []
            if any(n != existing for n in occupied):
                rebind = (
                    _rebindable_named_output(cmds, name, row['layer'])
                    if existing is None else None
                )
                if rebind is None:
                    raise ValueError('Camera name collision: ' + name)
                existing = rebind
        spec = dict(layer=row['layer'], width=width, height=height, start=row['start'], end=row['end'],
                    reference_resolution=list(reference), rule=rule, schema=LIVE_SCHEMA)
        specs.append((spec, existing, name))
    selection = cmds.ls(selection=True, long=True) or []
    selection_ids = [(cmds.ls(n.split('.', 1)[0], uuid=True) or [''])[0] for n in selection]
    cmds.undoInfo(openChunk=True, chunkName='Configure Live Cameras')
    failed = False
    results = []
    try:
        for spec, node, name in specs:
            was_live = bool(node and cmds.objExists(node + '.' + LIVE_ATTR))
            if spec['rule'].get('mode', 'shared') == 'shared':
                results.append(dict(layer=spec['layer'], camera=source, width=spec['width'], height=spec['height']))
                continue
            if node and cmds.objExists(node + '.' + LIVE_ATTR):
                old = json.loads(cmds.getAttr(node + '.' + co.SPEC_ATTR))
                if old == spec and (cmds.listRelatives(node, parent=True, fullPath=True) or []) == [source]:
                    results.append(dict(layer=spec['layer'], camera=node, width=spec['width'], height=spec['height']))
                    continue
                utilities = cmds.listConnections(node + '.' + LIVE_ATTR, source=True, destination=False) or []
                if utilities:
                    cmds.delete(utilities)
            if node is None:
                node, _ = cmds.camera(name=':' + name)
            node, dest = co.camera_nodes(node, cmds)
            # Only tool-owned outputs are converted; never Primary or arbitrary cameras.
            if not was_live:
                cmds.cutKey(node, attribute=list(co.TRANSFORM_ATTRS), clear=True)
                cmds.cutKey(dest, clear=True)
            if (cmds.listRelatives(node, parent=True, fullPath=True) or []) != [source]:
                node = cmds.parent(node, source, relative=True)[0]
            node = cmds.rename(node, ':' + name, ignoreShape=True)
            node, dest = co.camera_nodes(node, cmds)
            for attr, value in [('translate', (0, 0, 0)), ('rotate', (0, 0, 0)), ('scale', (1, 1, 1)), ('shear', (0, 0, 0))]:
                cmds.setAttr(node + '.' + attr, *value)
            graph = Graph(cmds, node)
            # Normalize the Primary gate at the FINAL aspect ratio. Fill=min,
            # Overscan=max; Horizontal/Vertical explicitly choose the fit axis.
            aspect = reference[0] / reference[1]
            hw = graph.mul(shape + '.horizontalFilmAperture', shape + '.lensSqueezeRatio')
            vw = graph.mul(shape + '.verticalFilmAperture', aspect)
            minimum = graph.choose(hw, vw, hw, vw, 4)
            maximum = graph.choose(hw, vw, hw, vw, 2)
            fit = shape + '.filmFit'
            gate_w = graph.choose(fit, 1, hw, graph.choose(fit, 2, vw, graph.choose(fit, 0, minimum, maximum)))
            gate_h = graph.mul(gate_w, aspect, True)
            # Maya's film-fit shift applies only to the explicitly expanded axis.
            xdelta = graph.choose(vw, hw, graph.mul(graph.add(hw, vw, True), .5), 0, 2)
            ydelta = graph.choose(gate_h, shape + '.verticalFilmAperture',
                                 graph.mul(graph.add(shape + '.verticalFilmAperture', gate_h, True), .5), 0, 2)
            xoffset = graph.add(graph.mul(shape + '.horizontalFilmOffset', shape + '.lensSqueezeRatio'),
                                graph.choose(fit, 2, graph.mul(xdelta, shape + '.filmFitOffset'), 0))
            yoffset = graph.add(shape + '.verticalFilmOffset',
                                graph.choose(fit, 1, graph.mul(ydelta, shape + '.filmFitOffset'), 0))
            values = {
                'horizontalFilmAperture': graph.mul(gate_w, spec['width'] / reference[0]),
                'verticalFilmAperture': graph.mul(gate_h, spec['height'] / reference[1]),
                'horizontalFilmOffset': xoffset, 'verticalFilmOffset': yoffset,
            }
            for attr, value in values.items():
                graph.put(graph.mul(value, shape + '.cameraScale'), dest + '.' + attr)
            for attr in co.SHAPE_ATTRS + ('shutterAngle',):
                if attr not in values:
                    graph.put(shape + '.' + attr, dest + '.' + attr)
            for attr, value in {'filmFit': 1, 'filmFitOffset': 0, 'overscan': 1, 'cameraScale': 1,
                                'lensSqueezeRatio': 1, 'renderable': 0, 'orthographic': 0}.items():
                cmds.setAttr(dest + '.' + attr, value)
            if not cmds.objExists(node + '.' + LIVE_ATTR):
                cmds.addAttr(node, longName=LIVE_ATTR, attributeType='message', multi=True)
            for i, utility in enumerate(graph.nodes):
                cmds.connectAttr(utility + '.message', f'{node}.{LIVE_ATTR}[{i}]', force=True)
            co._string_attr(cmds, node, co.OWNER_ATTR, co.OWNER)
            co._string_attr(cmds, node, co.SPEC_ATTR, json.dumps(spec))
            if not cmds.objExists(node + '.' + co.PRIMARY_ATTR):
                cmds.addAttr(node, longName=co.PRIMARY_ATTR, attributeType='message')
            if not cmds.isConnected(source + '.message', node + '.' + co.PRIMARY_ATTR):
                cmds.connectAttr(source + '.message', node + '.' + co.PRIMARY_ATTR, force=True)
            results.append(dict(layer=spec['layer'], camera=node, width=spec['width'], height=spec['height']))
    except Exception:
        failed = True
        raise
    finally:
        cmds.undoInfo(closeChunk=True)
        if failed:
            cmds.undo()
        restored = []
        for old, uuid in zip(selection, selection_ids):
            if cmds.objExists(old):
                restored.append(old)
            else:
                found = cmds.ls(uuid, long=True) or []
                if found:
                    restored.append(found[0] + ('.' + old.split('.', 1)[1] if '.' in old else ''))
        cmds.select(restored, replace=True) if restored else cmds.select(clear=True)
    return results

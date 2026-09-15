"""Background-derived SOP workspace. Crowd working coordinates are meters, Y-up."""
import math


def conversion(settings):
    unit = float(settings['meters_per_unit'])
    axis = str(settings['up_axis']).upper()
    if not math.isfinite(unit) or unit <= 0 or axis not in {'Y', 'Z'}:
        raise ValueError('Crowd workspace requires positive USD units and Y/Z up axis.')
    return {'meters_per_unit': 1.0, 'up_axis': 'Y', 'source_meters_per_unit': unit,
            'source_up_axis': axis, 'scale_to_work': unit,
            'rotate_x_to_work': -90.0 if axis == 'Z' else 0.0,
            'scale_to_stage': 1.0 / unit, 'rotate_x_to_stage': 90.0 if axis == 'Z' else 0.0}


def _note(node, text):
    node.setComment(text)
    node.setGenericFlag(__import__('hou').nodeFlag.DisplayComment, True)


def create(snapshot, background):
    import hou
    settings = conversion(snapshot['usd'])
    obj = hou.node('/obj')
    geo = obj.createNode('geo', 'crowd_environment')
    for child in geo.children():
        child.destroy()
    imported = geo.createNode('lopimport::2.0', 'BACKGROUND_USD')
    imported.parm('loppath').set(background.path())
    imported.parm('primpattern').set(' '.join(a['prim_path'] for a in snapshot['assets']))
    imported.parm('timesample').set('static')
    imported.parm('staticimportframe').set(snapshot['frame_range'][0])
    imported.parm('purpose').set('proxy render')
    imported.parm('importtraversal').set('std:boundables')
    convert = geo.createNode('xform', 'TO_METERS_Y_UP')
    convert.setInput(0, imported)
    convert.parm('scale').set(settings['scale_to_work'])
    convert.parm('rx').set(settings['rotate_x_to_work'])
    _note(convert, 'Crowd working coordinates: 1 unit = 1 meter, Y up. USD source units are converted here only.')
    full = geo.createNode('null', 'OUT_BACKGROUND_METERS');full.setInput(0, convert)
    for role in ('floor', 'obstacles'):
        group = geo.createNode('groupexpression', 'SELECT_' + role.upper())
        group.setInput(0, full)
        group.parm('groupname1').set('crowd_' + role)
        group.parm('snippet1').set('0')
        _note(group, 'Set the primitive group expression for '+role+'. Default is empty. Use the preserved path attribute to select meshes; 1 selects everything.')
        selected = geo.createNode('blast', 'KEEP_' + role.upper())
        selected.setInput(0, group);selected.parm('group').set('crowd_' + role)
        selected.parm('negate').set(True)
        unpack = geo.createNode('unpackusd::2.0', 'UNPACK_' + role.upper())
        unpack.setInput(0, selected);unpack.parm('output').set('polygons')
        unpack.parm('addpathattrib').set(True)
        _note(unpack, 'Only the selected USD meshes are unpacked for simulation. Display background stays packed.')
        out = geo.createNode('null', 'OUT_' + role.upper());out.setInput(0, unpack)
    full.setDisplayFlag(True);full.setRenderFlag(True)
    geo.layoutChildren()
    geo.setUserData('smartpipeline_units', 'meters_y_up')
    _note(geo, 'Read-only background input for crowd work. Select floor/obstacles before sourcing or simulation. Static at shot start.')
    source = obj.createNode('geo', 'crowd_source')
    for child in source.children():child.destroy()
    floor = source.createNode('object_merge', 'FLOOR_METERS')
    floor.parm('objpath1').set(geo.node('OUT_FLOOR').path())
    out = source.createNode('null', 'OUT_PLACEMENT_SURFACE');out.setInput(0, floor)
    _note(out, 'Connect the selected floor to Crowd Source or placement-point tools. No agents are created by Build.')
    source.setDisplayFlag(False);source.layoutChildren()
    # Explicit inverse conversion for future crowd publishing. No connection to the background input.
    publish = obj.createNode('geo', 'crowd_publish')
    for child in publish.children():child.destroy()
    incoming = publish.createNode('null', 'IN_CROWD_METERS')
    _note(incoming, 'Connect or Object Merge the crowd cache here (meters, Y up).')
    inverse = publish.createNode('xform', 'TO_STAGE_UNITS');inverse.setInput(0, incoming)
    inverse.parm('scale').set(settings['scale_to_stage']);inverse.parm('rx').set(settings['rotate_x_to_stage'])
    outgoing = publish.createNode('null', 'OUT_CROWD_STAGE');outgoing.setInput(0, inverse)
    publish.setDisplayFlag(False);publish.layoutChildren()
    obj.layoutChildren()
    return dict(settings, background_input=background.path(), geometry=full.path(),
                floor=geo.node('OUT_FLOOR').path(), obstacles=geo.node('OUT_OBSTACLES').path(),
                placement_surface=out.path(), crowd_stage_output=outgoing.path())


def validate(info):
    import hou
    node = hou.node(info['geometry'])
    geometry = node.geometry()
    if node.errors() or not geometry or not geometry.prims():
        raise RuntimeError('Crowd background SOP could not produce geometry: ' + str(node.errors()))
    if geometry.findPrimAttrib('path') is None:
        raise RuntimeError('Crowd background lost USD prim paths.')
    for key in ('floor', 'obstacles', 'placement_surface', 'crowd_stage_output'):
        output = hou.node(info[key]);output.geometry()
        if output.errors():raise RuntimeError(str(output.errors()))
    return {'packed_primitive_count': len(geometry.prims()), 'bounds': list(geometry.boundingBox().minvec()) + list(geometry.boundingBox().maxvec())}

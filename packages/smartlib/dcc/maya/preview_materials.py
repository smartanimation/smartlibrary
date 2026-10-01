"""Explicit Maya-to-Preview adapter for whole-mesh Lambert/file materials."""
from pathlib import Path
from smartlib.apps.shot_manager.animation_publish import file_hash


def extract_recipe(geometry_path, texture_record):
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
    from pxr import Tf, Usd, UsdGeom
    from .assembly_replacement import collect_meshes
    published = list((texture_record or {}).get('artifacts', {}).values())
    stage = Usd.Stage.Open(str(geometry_path))
    paths = {str(p.GetPath()) for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)}
    recipe = dict(materials={}, bindings={})
    for mesh in collect_meshes(cmds, 'cache_geo_set'):
        transform = cmds.listRelatives(mesh, parent=True, fullPath=True)[0]
        path = '/Geometry/' + Tf.MakeValidIdentifier(transform.rsplit('|', 1)[-1].rsplit(':', 1)[-1])
        if path not in paths:
            raise ValueError('Geometry path mismatch: ' + mesh)
        selection = om.MSelectionList()
        selection.add(mesh)
        shaders, indices = om.MFnMesh(selection.getDagPath(0)).getConnectedShaders(0)
        used = set(indices)
        if len(used) != 1 or -1 in used:
            raise ValueError('Preview Look requires one material per whole mesh: ' + mesh)
        sg = om.MFnDependencyNode(shaders[next(iter(used))]).name()
        connected = cmds.listConnections(sg + '.surfaceShader', source=True, destination=False) or []
        if len(connected) != 1:
            raise ValueError('Missing surface shader: ' + sg)
        shader = connected[0]
        name = Tf.MakeValidIdentifier(shader.replace(':', '_'))
        recipe['bindings'][path] = name
        if name in recipe['materials']:
            if recipe['materials'][name]['maya_shader'] != shader:
                raise ValueError('Duplicate material ID: ' + name)
            continue
        if cmds.nodeType(shader) != 'lambert':
            raise ValueError('Preview adapter currently supports Lambert only: ' + shader)
        for attr in ('normalCamera', 'diffuse', 'incandescence', 'ambientColor'):
            if cmds.listConnections(shader + '.' + attr, source=True, destination=False):
                raise ValueError('Unsupported Lambert input: ' + shader + '.' + attr)
        for attr in ('incandescence', 'ambientColor'):
            if any(cmds.getAttr(shader + '.' + attr)[0]):
                raise ValueError('Unsupported Lambert input: ' + shader + '.' + attr)
        connections = cmds.listConnections(shader + '.color', source=True, destination=False, plugs=True) or []
        if cmds.listConnections(shader + '.transparency', source=True, destination=False) or any(cmds.getAttr(shader + '.transparency')[0]):
            raise ValueError('Transparent Lambert is not supported yet: ' + shader)
        if not connections:
            recipe['materials'][name] = dict(maya_shader=shader,
                diffuse_color=list(cmds.getAttr(shader + '.color')[0]))
            continue
        if len(connections) != 1 or not connections[0].endswith('.outColor'):
            raise ValueError('Lambert color requires a direct file.outColor connection: ' + shader)
        file_node = connections[0].rsplit('.', 1)[0]
        if cmds.nodeType(file_node) != 'file':
            raise ValueError('Unsupported color network: ' + shader)
        tiling = cmds.getAttr(file_node + '.uvTilingMode')
        if tiling not in (0, 3) or cmds.getAttr(file_node + '.useFrameExtension'):
            raise ValueError('Preview supports static files or UDIM (Mari), not animated/other tiling modes: ' + file_node)
        source = Path(cmds.getAttr(file_node + '.fileTextureName'))
        if not source.is_absolute():
            source = Path(cmds.workspace(expandName=str(source)))
        tiles = None
        if tiling == 3:
            from smartlib.core.udim import published_udim
            texture, tiles = published_udim(source, published)
        else:
            digest = file_hash(source)
            matches = [ref for ref in published if Path(ref['path']).name.casefold() == source.name.casefold()
                       and ref['sha256'] == digest]
            if len(matches) != 1 or file_hash(Path(matches[0]['path'])) != digest:
                available = [Path(ref['path']).name for ref in published]
                reason = 'not included' if source.name.casefold() not in {n.casefold() for n in available} else 'content differs'
                raise ValueError(f'Texture {reason} in selected Texture Publish: {source.name}\n'
                    f'Material: {shader}\nSource: {source}\n'
                    'Import this image in Data > Texture, publish it, then select that Texture version.')
            texture = matches[0]['path']
        color = cmds.getAttr(file_node + '.colorSpace')
        spaces = {'Raw': 'raw', 'raw': 'raw', 'sRGB': 'sRGB'}
        if color not in spaces:
            raise ValueError('Unsupported Preview texture color space: ' + color)
        uv_sources = cmds.listConnections(file_node + '.uvCoord', source=True, destination=False) or []
        if len(uv_sources) != 1 or cmds.nodeType(uv_sources[0]) != 'place2dTexture':
            raise ValueError('Expected standard place2dTexture: ' + file_node)
        place = uv_sources[0]
        for attr, expected in [('rotateUV', 0), ('rotateFrame', 0), ('mirrorU', False), ('mirrorV', False),
                               ('stagger', False)]:
            if cmds.getAttr(place + '.' + attr) != expected:
                raise ValueError('Unsupported UV placement: ' + place + '.' + attr)
        for attr, expected in [('coverage', (1., 1.)), ('translateFrame', (0., 0.)), ('noiseUV', (0., 0.))]:
            if tuple(cmds.getAttr(place + '.' + attr)[0]) != expected:
                raise ValueError('Unsupported UV placement: ' + place + '.' + attr)
        for attr, expected in [('colorGain', (1., 1., 1.)), ('colorOffset', (0., 0., 0.))]:
            if tuple(cmds.getAttr(file_node + '.' + attr)[0]) != expected:
                raise ValueError('Unsupported texture adjustment: ' + file_node + '.' + attr)
        if cmds.listConnections(shader + '.transparency', source=True, destination=False) or any(cmds.getAttr(shader + '.transparency')[0]):
            raise ValueError('Transparent Lambert is not supported yet: ' + shader)
        recipe['materials'][name] = dict(maya_shader=shader, texture=texture,
            color_scale=[cmds.getAttr(shader + '.diffuse')] * 3 + [1.],
            color_space=spaces[color], uv='st', scale=list(cmds.getAttr(place + '.repeatUV')[0]),
            translation=list(cmds.getAttr(place + '.offset')[0]),
            wrap_s='repeat' if cmds.getAttr(file_node + '.wrapU') else 'black',
            wrap_t='repeat' if cmds.getAttr(file_node + '.wrapV') else 'black')
        if tiles:
            recipe['materials'][name]['texture_tiles'] = tiles
    return recipe

"""Apply a shading-only USD look using an explicit, topology-checked mesh map."""
from pathlib import Path


def write_preview_look(destination, geometry_path, recipe, *, texture_destination=None):
    """Author the same portable Preview Surface contract in either DCC runtime.

    Recipes contain explicit mesh bindings and texture/UV settings. They do not
    infer shading from mesh names or alter the source geometry.
    """
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade
    geometry = Usd.Stage.Open(str(geometry_path))
    root = geometry.GetDefaultPrim()
    if not root:
        raise ValueError('Geometry requires a defaultPrim')
    meshes = {str(p.GetPath()): p for p in geometry.Traverse() if p.IsA(UsdGeom.Mesh)}
    if not meshes or set(recipe['bindings']) != set(meshes):
        raise ValueError('Preview bindings must cover every geometry mesh exactly')
    stage = Usd.Stage.CreateInMemory()
    stage.SetDefaultPrim(stage.DefinePrim(root.GetPath()))
    materials = {}
    udim_tiles = {}
    scope = root.GetPath().AppendChild('Looks')
    UsdGeom.Scope.Define(stage, scope)
    for name, settings in recipe['materials'].items():
        if not Sdf.Path.IsValidIdentifier(name):
            raise ValueError('Invalid material ID: ' + name)
        path = scope.AppendChild(name)
        material = UsdShade.Material.Define(stage, path)
        surface = UsdShade.Shader.Define(stage, path.AppendChild('surface'))
        surface.CreateIdAttr('UsdPreviewSurface')
        surface.CreateInput('roughness', Sdf.ValueTypeNames.Float).Set(float(settings.get('roughness', .7)))
        surface.CreateInput('metallic', Sdf.ValueTypeNames.Float).Set(0.)
        surface.CreateInput('opacity', Sdf.ValueTypeNames.Float).Set(1.)
        if 'texture' not in settings:
            surface.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*settings['diffuse_color']))
            surface.CreateOutput('surface', Sdf.ValueTypeNames.Token)
            material.CreateSurfaceOutput().ConnectToSource(surface.ConnectableAPI(), 'surface')
            materials[name] = material
            continue
        from .udim import asset_files, materialize_tiles
        texture_value = settings['texture']
        if settings.get('texture_tiles'):
            # The default is for disposable staging; publishers pass ProjectPaths.
            target = texture_destination or (lambda name: Path(destination).parent / name)
            texture_value = materialize_tiles(texture_value, settings['texture_tiles'], target)
        texture = Path(texture_value).resolve()
        resolved_tiles = asset_files(texture)
        if '<UDIM>' in str(texture):
            udim_tiles[texture.as_posix()] = ','.join(sorted(Path(p).name for p in resolved_tiles))
        if settings.get('uv', 'st') != 'st':
            raise ValueError('Preview Look supports only the st UV set')
        reader = UsdShade.Shader.Define(stage, path.AppendChild('uv'))
        reader.CreateIdAttr('UsdPrimvarReader_float2')
        reader.CreateInput('varname', Sdf.ValueTypeNames.Token).Set(settings.get('uv', 'st'))
        reader.CreateOutput('result', Sdf.ValueTypeNames.Float2)
        transform = UsdShade.Shader.Define(stage, path.AppendChild('uv_transform'))
        transform.CreateIdAttr('UsdTransform2d')
        transform.CreateInput('in', Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(), 'result')
        transform.CreateInput('translation', Sdf.ValueTypeNames.Float2).Set(Gf.Vec2f(*settings.get('translation', [0,0])))
        transform.CreateInput('scale', Sdf.ValueTypeNames.Float2).Set(Gf.Vec2f(*settings.get('scale', [1,1])))
        transform.CreateOutput('result', Sdf.ValueTypeNames.Float2)
        image = UsdShade.Shader.Define(stage, path.AppendChild('texture'))
        image.CreateIdAttr('UsdUVTexture')
        image.CreateInput('file', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(texture.as_posix()))
        image.CreateInput('sourceColorSpace', Sdf.ValueTypeNames.Token).Set(settings.get('color_space', 'sRGB'))
        image.CreateInput('scale', Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(*settings.get('color_scale', [1,1,1,1])))
        image.CreateInput('wrapS', Sdf.ValueTypeNames.Token).Set(settings.get('wrap_s', 'repeat'))
        image.CreateInput('wrapT', Sdf.ValueTypeNames.Token).Set(settings.get('wrap_t', 'clamp'))
        image.CreateInput('st', Sdf.ValueTypeNames.Float2).ConnectToSource(transform.ConnectableAPI(), 'result')
        image.CreateOutput('rgb', Sdf.ValueTypeNames.Float3)
        surface.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).ConnectToSource(image.ConnectableAPI(), 'rgb')
        surface.CreateOutput('surface', Sdf.ValueTypeNames.Token)
        material.CreateSurfaceOutput().ConnectToSource(surface.ConnectableAPI(), 'surface')
        materials[name] = material
    for path, name in recipe['bindings'].items():
        UsdShade.MaterialBindingAPI.Apply(stage.OverridePrim(path)).Bind(materials[name])
    stage.GetRootLayer().customLayerData = {
        'preview_meshes': {path: mesh_fingerprint(p) for path,p in meshes.items()},
        'preview_udim_tiles': udim_tiles}
    stage.GetRootLayer().Export(str(destination))
    return str(destination)


def apply_preview_look(stage, target_root, geometry, look_path, mesh_map):
    """Copy sparse look opinions to a composition, never modifying its geometry.

    mesh_map maps full look mesh paths to full paths in the source geometry.
    A geometry contract in the look layer's customLayerData records
    face connectivity and UVs via a fingerprint, not geometry opinions.
    """
    from pxr import Sdf, Usd, UsdGeom, UsdShade
    from smartlib.dcc.maya.animation_build import validate_look_usd
    look = Usd.Stage.Open(str(look_path))
    root = look.GetDefaultPrim() if look else None
    if not root:
        raise ValueError('Preview Look requires a defaultPrim')
    dependencies = validate_look_usd(Path(look_path), str(root.GetPath()), {})
    if any(p.IsA(UsdGeom.Subset) for p in look.Traverse()):
        raise ValueError('Preview Look mesh mapping currently requires whole-mesh bindings')
    contract = look.GetRootLayer().customLayerData.get('preview_meshes', {})
    if not contract or set(mesh_map) != set(contract):
        raise ValueError('Preview Look mesh map must cover its geometry contract exactly')
    if len(set(mesh_map.values())) != len(mesh_map):
        raise ValueError('Preview Look mesh map contains duplicate destinations')
    source_root = geometry.GetDefaultPrim()
    if not source_root:
        raise ValueError('Preview geometry requires a defaultPrim')
    # Build and validate a temporary sparse layer before touching the composition.
    layer = Sdf.Layer.CreateAnonymous('preview_look.usda')
    output = Usd.Stage.Open(layer)
    materials = root.GetChild('Looks')
    if not materials:
        raise ValueError('Preview Look requires a Looks scope')
    material_dest = Sdf.Path(target_root).AppendChild('Looks')
    Sdf.CreatePrimInLayer(layer, material_dest.GetParentPath())
    Sdf.CopySpec(look.GetRootLayer(), materials.GetPath(), layer, material_dest)
    for prim in output.TraverseAll():
        for attr in prim.GetAttributes():
            if attr.GetTypeName() == Sdf.ValueTypeNames.Asset:
                original = look.GetPrimAtPath(prim.GetPath().ReplacePrefix(material_dest, materials.GetPath())).GetAttribute(attr.GetName()).Get()
                if original and original.path:
                    from .udim import anchored_identifier
                    anchored = anchored_identifier(original.path, look.GetRootLayer())
                    attr.Set(Sdf.AssetPath(anchored if '<UDIM>' in original.path else original.resolvedPath))
    for look_mesh, geometry_mesh in mesh_map.items():
        prim = geometry.GetPrimAtPath(geometry_mesh)
        if not prim or not prim.IsA(UsdGeom.Mesh) or not prim.GetPath().HasPrefix(source_root.GetPath()):
            raise ValueError('Preview Look target is not a source mesh: ' + geometry_mesh)
        if mesh_fingerprint(prim) != contract[look_mesh]:
            raise ValueError('Preview Look topology/UV mismatch: ' + geometry_mesh)
        source = look.GetPrimAtPath(look_mesh)
        mat = UsdShade.MaterialBindingAPI(source).ComputeBoundMaterial()[0]
        if not mat or not mat.GetPath().HasPrefix(materials.GetPath()):
            raise ValueError('Preview Look requires an internal material binding: ' + look_mesh)
        shader, _, _ = mat.ComputeSurfaceSource()
        if not shader or shader.GetIdAttr().Get() != 'UsdPreviewSurface':
            raise ValueError('Preview Look requires UsdPreviewSurface: ' + str(mat.GetPath()))
        dest = prim.GetPath().ReplacePrefix(source_root.GetPath(), Sdf.Path(target_root))
        out = output.OverridePrim(dest)
        material = UsdShade.Material(output.GetPrimAtPath(mat.GetPath().ReplacePrefix(materials.GetPath(), material_dest)))
        UsdShade.MaterialBindingAPI.Apply(out).Bind(material)
    # CopySpec remaps internal connection targets along with the material scope.
    for child in list(layer.rootPrims):
        _merge_sparse(layer, child.path, stage.GetEditTarget().GetLayer())
    return dependencies


def _merge_sparse(source, path, destination):
    """Merge leaf specs without replacing existing ancestor geometry specs."""
    from pxr import Sdf
    spec = source.GetPrimAtPath(path)
    if spec.name == 'Looks':
        if destination.GetPrimAtPath(path):
            raise ValueError('Preview Looks scope already exists: ' + str(path))
        Sdf.CreatePrimInLayer(destination, path.GetParentPath())
        Sdf.CopySpec(source, path, destination, path)
        return
    out = Sdf.CreatePrimInLayer(destination, path)
    if spec.HasInfo('apiSchemas'):
        # An explicit list would mask APIs supplied by the referenced geometry,
        # notably SkelBindingAPI, and silently turn animated meshes into bind pose.
        operation = out.GetInfo('apiSchemas') if out.HasInfo('apiSchemas') else Sdf.TokenListOp()
        additions = list(spec.GetInfo('apiSchemas').ApplyOperations([]))
        if operation.isExplicit:
            operation.explicitItems = list(dict.fromkeys(list(operation.explicitItems) + additions))
        else:
            operation.prependedItems = list(dict.fromkeys(additions + list(operation.prependedItems)))
            operation.deletedItems = [item for item in operation.deletedItems if item not in additions]
        out.SetInfo('apiSchemas', operation)
    for prop in spec.properties:
        Sdf.CopySpec(source, prop.path, destination, prop.path)
    for child in spec.nameChildren:
        _merge_sparse(source, child.path, destination)


def mesh_fingerprint(prim):
    """Connectivity and flattened UVs, independent of point motion and names."""
    import hashlib
    import json
    from pxr import Usd, UsdGeom
    mesh = UsdGeom.Mesh(prim)
    time = Usd.TimeCode.EarliestTime()
    data = {'vertices': len(mesh.GetPointsAttr().Get(time) or []),
            'counts': list(mesh.GetFaceVertexCountsAttr().Get(time) or []),
            'indices': list(mesh.GetFaceVertexIndicesAttr().Get(time) or []), 'uv': {}}
    for pv in UsdGeom.PrimvarsAPI(prim).GetPrimvars():
        if str(pv.GetTypeName()) in {'texCoord2f[]', 'float2[]'}:
            data['uv'][pv.GetPrimvarName()] = [pv.GetInterpolation(),
                [list(value) for value in (pv.ComputeFlattened(time) or [])]]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

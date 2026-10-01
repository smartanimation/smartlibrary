"""Separate static mesh data from skeletal deformation opinions."""
from pathlib import Path


def split_skel_layers(source, *, geometry_path, rig_path, entry_path):
    from pxr import Sdf, Usd, UsdGeom, UsdSkel
    stage = Usd.Stage.Open(str(source))
    if not stage or not stage.GetDefaultPrim():
        raise ValueError('Skel source requires a defaultPrim')
    if any(a.GetNumTimeSamples() for p in stage.Traverse() for a in p.GetAttributes()):
        raise ValueError('Static Skel package cannot contain animation time samples')
    meshes = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
    if not meshes:
        raise ValueError('Skel source contains no meshes')
    flattened = stage.Flatten()
    geo = Sdf.Layer.CreateAnonymous('geo.usda')
    rig = Sdf.Layer.CreateAnonymous('rig.usda')
    geo.TransferContent(flattened)
    rig.TransferContent(flattened)
    geo_stage, rig_stage = Usd.Stage.Open(geo), Usd.Stage.Open(rig)
    for prim in list(stage.Traverse())[::-1]:
        path = prim.GetPath()
        if prim.IsA(UsdSkel.Animation):
            geo_stage.RemovePrim(path)
            rig_stage.RemovePrim(path)
            continue
        if prim.IsA(UsdSkel.Skeleton) or prim.IsA(UsdSkel.BlendShape):
            geo_stage.RemovePrim(path)
            continue
        geo_prim = geo_stage.GetPrimAtPath(path)
        rig_spec = rig.GetPrimAtPath(path)
        for prop in list(geo_prim.GetProperties()):
            if prop.GetName().startswith(('skel:', 'primvars:skel:')):
                geo_prim.RemoveProperty(prop.GetName())
        if geo_prim.HasAPI(UsdSkel.BindingAPI):
            geo_prim.RemoveAPI(UsdSkel.BindingAPI)
        if prim.IsA(UsdSkel.Root):
            geo_prim.SetTypeName('Xform')
        for prop in list(rig_spec.properties):
            if not prop.name.startswith(('skel:', 'primvars:skel:')):
                rig_spec.RemoveProperty(prop)
        if not prim.IsA(UsdSkel.Root):
            rig_spec.specifier = Sdf.SpecifierOver
            rig_spec.typeName = ''
    for prim in rig_stage.TraverseAll():
        prim.RemoveProperty('skel:animationSource')
    geo.Export(str(geometry_path))
    rig.Export(str(rig_path))
    entry = Usd.Stage.CreateNew(str(entry_path))
    import os
    entry.GetRootLayer().subLayerPaths = [os.path.relpath(p, Path(entry_path).parent).replace('\\', '/') for p in (rig_path, geometry_path)]
    entry.SetDefaultPrim(entry.GetPrimAtPath(stage.GetDefaultPrim().GetPath()))
    UsdGeom.SetStageMetersPerUnit(entry, UsdGeom.GetStageMetersPerUnit(stage))
    UsdGeom.SetStageUpAxis(entry, UsdGeom.GetStageUpAxis(stage))
    entry.GetRootLayer().Save()
    return dict(meshes=len(meshes), geometry=str(geometry_path), rig=str(rig_path), entry=str(entry_path))

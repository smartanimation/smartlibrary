"""Compose separately published geometry with a static binding layer."""
from .preview_look import mesh_fingerprint, apply_preview_look


def compose_skel_release(geometry, rig_geometry, rig_path, look_path):
    from pxr import Sdf, Usd, UsdGeom, UsdSkel
    import math

    def meshes(stage):
        result = {}
        for prim in stage.Traverse():
            if prim.IsA(UsdGeom.Mesh):
                name = prim.GetName()
                if name in result:
                    raise ValueError('Ambiguous Rig/Geometry mesh name: ' + name)
                result[name] = prim
        return result

    source, basis = meshes(geometry), meshes(rig_geometry)
    if not source or set(source) != set(basis):
        raise ValueError('Rig and Geometry mesh sets do not match')
    if (UsdGeom.GetStageMetersPerUnit(geometry) != UsdGeom.GetStageMetersPerUnit(rig_geometry)
            or UsdGeom.GetStageUpAxis(geometry) != UsdGeom.GetStageUpAxis(rig_geometry)):
        raise ValueError('Rig and Geometry units/up axis do not match')
    tolerance = .0001 / UsdGeom.GetStageMetersPerUnit(geometry)  # 0.01 cm
    mapping = {}
    for name, prim in source.items():
        target = basis[name]
        if mesh_fingerprint(prim) != mesh_fingerprint(target):
            raise ValueError('Rig topology/UV mismatch: ' + name)
        a, b = UsdGeom.Mesh(prim).GetPointsAttr(), UsdGeom.Mesh(target).GetPointsAttr()
        if a.GetNumTimeSamples() or b.GetNumTimeSamples():
            raise ValueError('Release requires static Geometry: ' + name)
        points, rest = a.Get(), b.Get()
        if not points or not rest or len(points) != len(rest):
            raise ValueError('Rig bind geometry missing: ' + name)
        errors = [(p - q).GetLength() for p, q in zip(points, rest)]
        if any(not math.isfinite(error) or error > tolerance for error in errors):
            raise ValueError('Geometry differs from Rig bind shape: ' + name)
        mapping[str(prim.GetPath())] = str(target.GetPath())

    stage = Usd.Stage.CreateInMemory()
    stage.GetRootLayer().subLayerPaths = [str(rig_path)]
    flat = rig_geometry.Flatten()
    # Rig owns the bind coordinate frames. Geometry owns mesh data, via references.
    for prim in rig_geometry.Traverse():
        if not (prim.IsA(UsdGeom.Xform) or prim.IsA(UsdGeom.Mesh)):
            continue
        dest = stage.DefinePrim(prim.GetPath())
        if not dest.IsA(UsdSkel.Root):
            dest.SetTypeName(prim.GetTypeName())
        for attr in prim.GetAttributes():
            if (attr.GetName().startswith('xformOp') or attr.GetName() in ('visibility', 'purpose')) and flat.GetAttributeAtPath(attr.GetPath()):
                Sdf.CopySpec(flat, attr.GetPath(), stage.GetRootLayer(), attr.GetPath())
    for name, prim in source.items():
        dest = stage.GetPrimAtPath(basis[name].GetPath())
        dest.GetReferences().AddReference(geometry.GetRootLayer().realPath, prim.GetPath())
        if not UsdSkel.BindingAPI(dest).GetInheritedSkeleton():
            raise ValueError('Rig has no skeleton binding: ' + name)
    root = stage.GetPrimAtPath(rig_geometry.GetDefaultPrim().GetPath())
    if not root.IsA(UsdSkel.Root):
        raise ValueError('Rig requires a SkelRoot')
    stage.SetDefaultPrim(root)
    apply_preview_look(stage, str(root.GetPath()), stage, look_path, mapping)
    return stage, mapping

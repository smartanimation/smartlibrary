"""Author bounds for evaluated skinning without publishing baked mesh points."""


def author_skel_extents(stage, frame_range):
    from pxr import Usd, UsdGeom, UsdSkel, Gf
    roots = [p for p in stage.Traverse() if p.IsA(UsdSkel.Root)]
    if not roots:
        return
    evaluated = Usd.Stage.Open(stage.Flatten())
    for root in roots:
        baked_root = evaluated.GetPrimAtPath(root.GetPath())
        if not UsdSkel.BakeSkinning(Usd.PrimRange(baked_root), Gf.Interval(*frame_range)):
            raise ValueError('Cannot evaluate skeletal bounds: ' + str(root.GetPath()))
        meshes = [p for p in Usd.PrimRange(baked_root) if p.IsA(UsdGeom.Mesh)]
        for frame in range(int(frame_range[0]), int(frame_range[1]) + 1):
            cache = UsdGeom.XformCache(frame)
            inverse = cache.GetLocalToWorldTransform(baked_root).GetInverse()
            bounds = Gf.Range3d()
            for prim in meshes:
                points = UsdGeom.Mesh(prim).GetPointsAttr().Get(frame)
                if not points:
                    raise ValueError('Missing points for skeletal bounds: ' + str(prim.GetPath()))
                extent = UsdGeom.PointBased.ComputeExtent(points)
                UsdGeom.Mesh(stage.GetPrimAtPath(prim.GetPath())).CreateExtentAttr().Set(extent, frame)
                matrix = cache.GetLocalToWorldTransform(prim) * inverse
                box = Gf.BBox3d(Gf.Range3d(Gf.Vec3d(extent[0]), Gf.Vec3d(extent[1])), matrix)
                bounds.UnionWith(box.ComputeAlignedRange())
            if not bounds.IsEmpty():
                UsdSkel.Root(root).CreateExtentAttr().Set(
                    [Gf.Vec3f(bounds.GetMin()), Gf.Vec3f(bounds.GetMax())], frame)

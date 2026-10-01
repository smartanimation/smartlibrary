"""Compose and verify skeletal animation against evaluated deformation."""
import os
import math
from pathlib import Path


def compose_animation(destination, animation, rig, bindings, frame_range, fps):
    from pxr import Usd, UsdGeom, UsdSkel
    source = Usd.Stage.Open(str(rig))
    stage = Usd.Stage.CreateNew(str(destination))
    references = []
    for path in (animation, rig):
        try:
            reference = os.path.relpath(path, Path(destination).parent)
        except ValueError:  # Staging and Production can be on different Windows drives.
            reference = str(Path(path).resolve())
        references.append(reference.replace('\\', '/'))
    stage.GetRootLayer().subLayerPaths = references
    stage.SetDefaultPrim(stage.GetPrimAtPath(source.GetDefaultPrim().GetPath()))
    stage.SetStartTimeCode(frame_range[0])
    stage.SetEndTimeCode(frame_range[1])
    stage.SetFramesPerSecond(fps)
    stage.SetTimeCodesPerSecond(fps)
    UsdGeom.SetStageMetersPerUnit(stage, UsdGeom.GetStageMetersPerUnit(source))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.GetStageUpAxis(source))
    for binding in bindings:
        prim = stage.GetPrimAtPath(binding['target_skeleton'])
        UsdSkel.BindingAPI.Apply(prim).CreateAnimationSourceRel().SetTargets([binding['animation_source']])
    from .skel_extents import author_skel_extents
    author_skel_extents(stage, frame_range)
    stage.GetRootLayer().Save()


def compare_deformation(candidate, reference, frame_range, *, namespace='', tolerance_cm=.01):
    """Check all integer frames, topology/UVs, visibility and world-space points."""
    from pxr import Usd, UsdGeom, UsdSkel, Gf
    from .preview_look import mesh_fingerprint
    stages = [Usd.Stage.Open(str(p)) for p in (candidate, reference)]
    if not all(stages):
        raise ValueError('Cannot open deformation comparison inputs')
    unit = UsdGeom.GetStageMetersPerUnit(stages[1])
    if (UsdGeom.GetStageMetersPerUnit(stages[0]) != unit or
            UsdGeom.GetStageUpAxis(stages[0]) != UsdGeom.GetStageUpAxis(stages[1])):
        raise ValueError('Skeletal and deform units/up-axis differ')
    maps = []
    for stage in stages:
        meshes = {}
        for prim in stage.Traverse():
            if not prim.IsA(UsdGeom.Mesh):
                continue
            name = prim.GetName()
            prefix = namespace.replace(':', '_') + '_'
            if namespace and name.startswith(prefix):
                name = name[len(prefix):]
            if name in meshes:
                raise ValueError('Ambiguous deformation mesh: ' + name)
            meshes[name] = prim
        maps.append(meshes)
    if not maps[0] or set(maps[0]) != set(maps[1]):
        raise ValueError('Skeletal/deform mesh sets differ')
    for name, prim in maps[0].items():
        if not prim.HasAPI(UsdSkel.BindingAPI):
            raise ValueError('Missing skin binding: ' + name)
        if mesh_fingerprint(prim) != mesh_fingerprint(maps[1][name]):
            raise ValueError('Skeletal/deform topology or UV differs: ' + name)
    # Bake into an anonymous copy: neither input is modified.
    stages[0] = Usd.Stage.Open(stages[0].Flatten())
    roots = [p for p in stages[0].Traverse() if p.IsA(UsdSkel.Root)]
    if not roots:
        raise ValueError('No SkelRoot to evaluate')
    for root in roots:
        if not UsdSkel.BakeSkinning(Usd.PrimRange(root), Gf.Interval(*frame_range)):
            raise ValueError('USD skin evaluation failed')
    maps[0] = {name: stages[0].GetPrimAtPath(prim.GetPath()) for name, prim in maps[0].items()}
    if any(not UsdGeom.Mesh(p).GetPointsAttr().GetTimeSamples() for p in maps[0].values()):
        raise ValueError('Skin evaluation did not produce point samples for every mesh')
    maximum = 0.
    for frame in range(int(frame_range[0]), int(frame_range[1]) + 1):
        caches = [UsdGeom.XformCache(frame), UsdGeom.XformCache(frame)]
        for name in maps[0]:
            prims = [mapping[name] for mapping in maps]
            for getter in ('GetFaceVertexCountsAttr', 'GetFaceVertexIndicesAttr'):
                if getattr(UsdGeom.Mesh(prims[0]), getter)().Get(frame) != getattr(UsdGeom.Mesh(prims[1]), getter)().Get(frame):
                    raise ValueError(f'Topology differs at frame {frame}: {name}')
            if (UsdGeom.Imageable(prims[0]).ComputeVisibility(frame) !=
                    UsdGeom.Imageable(prims[1]).ComputeVisibility(frame)):
                raise ValueError('Visibility differs: ' + name)
            arrays = [UsdGeom.Mesh(p).GetPointsAttr().Get(frame) for p in prims]
            if not arrays[0] or not arrays[1] or len(arrays[0]) != len(arrays[1]):
                raise ValueError('Point count differs: ' + name)
            matrices = [cache.GetLocalToWorldTransform(p) for cache, p in zip(caches, prims)]
            for a, b in zip(*arrays):
                error = (matrices[0].Transform(Gf.Vec3d(a)) - matrices[1].Transform(Gf.Vec3d(b))).GetLength() * unit * 100
                if not math.isfinite(error):
                    raise ValueError('Non-finite deformation point: ' + name)
                maximum = max(maximum, error)
                if maximum > tolerance_cm:
                    raise ValueError(f'Deformation differs at frame {frame}, {name}: {maximum:g} cm')
    return dict(ok=True, mesh_count=len(maps[0]), frame_range=list(frame_range),
                frames_checked=int(frame_range[1] - frame_range[0] + 1),
                max_error_cm=maximum, tolerance_cm=tolerance_cm)

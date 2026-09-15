"""Optional post-deform object-space point deltas, pinned to Animation Data.

Every integer frame is explicit; no implicit interpolation or latest lookup.
This format represents final-deform corrections, not rig blendShape controls.
"""
import hashlib
import json
import math

SCHEMA = 'smartpipeline.shot_sculpt.v1'


def topology_signature(mesh):
    from pxr import Usd
    time = Usd.TimeCode.EarliestTime()
    data = [list(mesh.GetFaceVertexCountsAttr().Get(time) or []),
            list(mesh.GetFaceVertexIndicesAttr().Get(time) or []),
            len(mesh.GetPointsAttr().Get(time) or [])]
    return hashlib.sha256(json.dumps(data, separators=(',', ':')).encode()).hexdigest()


def collect_from_usd(base_path, sculpted_path, *, curve_sha256, frame_range):
    """Capture final object-space corrections without replacing the base mesh.

    Both exports must use the same prim paths, topology, timing and units. Only
    changed meshes are recorded. The original USD files are never modified.
    """
    from pxr import Usd, UsdGeom
    from smartlib.apps.shot_manager.usd_handoff import composition_errors
    base = Usd.Stage.Open(str(base_path))
    sculpted = Usd.Stage.Open(str(sculpted_path))
    if not base or not sculpted or composition_errors(base) or composition_errors(sculpted):
        raise ValueError('Cannot open Shot Sculpt comparison stages')
    if (UsdGeom.GetStageMetersPerUnit(base) != UsdGeom.GetStageMetersPerUnit(sculpted)
            or UsdGeom.GetStageUpAxis(base) != UsdGeom.GetStageUpAxis(sculpted)
            or base.GetTimeCodesPerSecond() != sculpted.GetTimeCodesPerSecond()):
        raise ValueError('Shot Sculpt stage units/timing differ')
    meshes = []
    for prim in base.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        original = UsdGeom.Mesh(prim)
        corrected = UsdGeom.Mesh(sculpted.GetPrimAtPath(prim.GetPath()))
        if not corrected or topology_signature(original) != topology_signature(corrected):
            raise ValueError(f'Shot Sculpt topology/target mismatch: {prim.GetPath()}')
        samples, changed = {}, False
        for frame in range(int(frame_range[0]), int(frame_range[1]) + 1):
            a, b = original.GetPointsAttr().Get(frame), corrected.GetPointsAttr().Get(frame)
            if a is None or b is None or len(a) != len(b):
                raise ValueError(f'Shot Sculpt point count mismatch: {prim.GetPath()} at {frame}')
            if original.ComputeLocalToWorldTransform(frame) != corrected.ComputeLocalToWorldTransform(frame):
                raise ValueError('Shot Sculpt may change points, not placement transforms')
            deltas = [[float(v) for v in right-left] for left, right in zip(a, b)]
            changed |= any(abs(v) > 1e-7 for delta in deltas for v in delta)
            samples[str(frame)] = deltas
        if changed:
            meshes.append(dict(prim_path=str(prim.GetPath()), topology_signature=topology_signature(original), samples=samples))
    if not meshes:
        raise ValueError('No Shot Sculpt point corrections found')
    return dict(schema=SCHEMA, space='post_deform_object', curve_sha256=curve_sha256,
                frame_range=list(frame_range), meshes=meshes)


def apply_to_usd(path, data, *, curve_sha256, frame_range):
    from pxr import Usd, UsdGeom, Vt, Gf
    if data.get('schema') != SCHEMA or data.get('space') != 'post_deform_object':
        raise ValueError('Unsupported Shot Sculpt contract')
    if data.get('curve_sha256') != curve_sha256 or data.get('frame_range') != list(frame_range):
        raise ValueError('Shot Sculpt belongs to different Animation Data/range')
    stage = Usd.Stage.Open(str(path))
    edits = []
    if not data.get('meshes'):
        raise ValueError('Published Shot Sculpt contains no mesh corrections')
    for item in data['meshes']:
        mesh = UsdGeom.Mesh(stage.GetPrimAtPath(item['prim_path']))
        if not mesh or topology_signature(mesh) != item['topology_signature']:
            raise ValueError(f"Shot Sculpt topology/target mismatch: {item['prim_path']}")
        for frame in range(int(frame_range[0]), int(frame_range[1]) + 1):
            points = mesh.GetPointsAttr().Get(frame)
            deltas = item['samples'].get(str(frame))
            if deltas is None or len(points) != len(deltas):
                raise ValueError(f'Missing Shot Sculpt point samples at frame {frame}')
            if any(len(d) != 3 or not all(math.isfinite(float(v)) for v in d) for d in deltas):
                raise ValueError('Invalid Shot Sculpt deltas')
            edits.append((mesh, frame, Vt.Vec3fArray([p + Gf.Vec3f(*d) for p, d in zip(points, deltas)])))
    for mesh, frame, points in edits:
        mesh.GetPointsAttr().Set(points, frame)
        mesh.GetExtentAttr().Set(UsdGeom.PointBased.ComputeExtent(points), frame)
    stage.GetRootLayer().Save()

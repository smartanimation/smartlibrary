"""Resolve Assembly identity and rigid motion against the selected USD Asset."""


def resolve(stage, binding, data):
    from pxr import Gf, Usd, UsdGeom
    from .placement_motion import validate_placement

    root = stage.GetPrimAtPath('/Shot/Assets/' + binding['cast'])
    matches = [p for p in Usd.PrimRange(root) if
               p.GetCustomDataByKey('smartpipeline:assembly:instanceId') == binding['instance_id']] if root else []
    if len(matches) != 1:
        raise ValueError('Expected one Assembly Placement ID in selected background: ' + binding['instance_id'])
    group = matches[0]
    if group.IsInstanceProxy():
        raise ValueError('Cannot place an instance proxy: ' + str(group.GetPath()))
    sources = {}
    for prim in Usd.PrimRange(group):
        key = prim.GetCustomDataByKey('smartpipeline:setdress:sourcePath')
        if key:
            sources.setdefault(key, []).append(prim)
    geometry = binding.get('geometry', [])
    if not geometry:
        raise ValueError('Assembly Placement has no geometry samples')
    for item in geometry:
        validate_placement(dict(mode=data['mode'], samples=item['samples']),
                           [data['samples'][0]['frame'], data['samples'][-1]['frame']])
    samples = []
    for index, sample in enumerate(data['samples']):
        cache = UsdGeom.XformCache(Usd.TimeCode(sample['frame']))
        deltas = []
        for item in geometry:
            candidates = sources.get(item['source_path'], [])
            if len(candidates) != 1:
                raise ValueError('Missing or ambiguous Assembly geometry mapping: ' + item['source_path'])
            values = item['samples']
            if len(values) != len(data['samples']) or values[index]['frame'] != sample['frame']:
                raise ValueError('Assembly geometry sample timing mismatch')
            base = cache.GetLocalToWorldTransform(candidates[0])
            if abs(base.GetDeterminant()) < 1e-12:
                raise ValueError('Singular Assembly geometry transform')
            if 'points' in values[index]:
                mesh_prims = [p for p in Usd.PrimRange(candidates[0]) if p.IsA(UsdGeom.Mesh)]
                if len(mesh_prims) != 1:
                    raise ValueError('Assembly correspondence requires exactly one USD mesh')
                mesh = mesh_prims[0]
                matrix = cache.GetLocalToWorldTransform(mesh)
                points = [matrix.Transform(Gf.Vec3d(p)) for p in UsdGeom.Mesh(mesh).GetPointsAttr().Get(sample['frame'])]
                deltas.append(_rigid_delta(points, values[index]['points']))
            else:
                deltas.append(base.GetInverse() * Gf.Matrix4d(*values[index]['matrix']))
        if any(not Gf.IsClose(deltas[0], delta, 1e-5) for delta in deltas[1:]):
            raise ValueError('Assembly Placement requires rigid geometry motion; publish deformation as Animation')
        matrix = cache.GetLocalToWorldTransform(group) * deltas[0]
        samples.append(dict(frame=sample['frame'], matrix=[v for row in matrix for v in row]))
    return str(group.GetPath()), dict(data, samples=samples)


def _rigid_delta(source, flattened):
    """Fit a rigid frame from vertices and verify all evaluated points."""
    import math
    from pxr import Gf

    if len(flattened) != len(source) * 3 or len(source) < 3 or not all(math.isfinite(v) for v in flattened):
        raise ValueError('Assembly Placement geometry topology or points differ')
    target = [Gf.Vec3d(*flattened[i:i + 3]) for i in range(0, len(flattened), 3)]
    a = 0
    b = max(range(1, len(source)), key=lambda i: (source[i] - source[a]).GetLength())
    axis = source[b] - source[a]
    c = max(range(len(source)), key=lambda i: Gf.Cross(axis, source[i] - source[a]).GetLength())

    def frame(points):
        x = points[b] - points[a]
        z = Gf.Cross(x, points[c] - points[a])
        if x.GetLength() < 1e-8 or z.GetLength() < 1e-8:
            raise ValueError('Degenerate Assembly Placement geometry')
        x.Normalize()
        z.Normalize()
        y = Gf.Cross(z, x)
        return Gf.Matrix4d(*(list(x) + [0] + list(y) + [0] + list(z) + [0] + list(points[a]) + [1]))

    delta = frame(source).GetInverse() * frame(target)
    if any(not Gf.IsClose(delta.Transform(p), q, 1e-4) for p, q in zip(source, target)):
        raise ValueError('Assembly Placement requires rigid geometry motion; publish deformation as Animation')
    return delta

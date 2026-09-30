"""Validated, DCC-independent Smart Maker placement samples for Assets USD."""
import math


def validate_placement(data, frame_range):
    if data.get('mode') not in ('STATIC', 'CURVE'):
        raise ValueError('Placement motion must be STATIC or CURVE')
    samples = data.get('samples', [])
    start, end = map(float, frame_range)
    expected = [start]
    if data['mode'] == 'CURVE':
        expected += [start + i for i in range(1, math.ceil(end - start))]
        if end > start:
            expected.append(end)
    if [s.get('frame') for s in samples] != expected:
        raise ValueError('Placement samples must cover the exact shot range')
    for sample in samples:
        matrix = sample.get('matrix', [])
        if len(matrix) != 16 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in matrix):
            raise ValueError('Placement requires a finite 4x4 world matrix')
    return data


def author_placement(prim, data):
    from pxr import Gf, Usd, UsdGeom
    # Keep placement evaluable with LoadNone, when the payload supplies no type.
    prim.SetTypeName('Xform')
    xform = UsdGeom.Xformable(prim)
    # The attachment root is evaluated in world space by Maya. Replace the
    # referenced root stack rather than adding the Asset's root transform twice.
    op = xform.AddTransformOp(opSuffix='smartPlacement')
    xform.SetXformOpOrder([op], resetXformStack=True)
    for sample in data['samples']:
        time = Usd.TimeCode.Default() if data['mode'] == 'STATIC' else Usd.TimeCode(sample['frame'])
        op.Set(Gf.Matrix4d(*sample['matrix']), time)
    prim.SetCustomDataByKey('smartpipeline:placement_motion', data['mode'])

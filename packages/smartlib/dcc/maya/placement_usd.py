"""Capture Smart Maker attachment transforms, never deforming geometry."""
import math

from smartlib.apps.shot_manager.placement_motion import validate_placement


def capture_placements(plan, cmds=None, locators=None):
    if cmds is None:
        import maya.cmds as cmds
    if locators is None:
        from .placement import list_placement_locators
        locators = list_placement_locators()
    meters = {'mm': .001, 'cm': .01, 'm': 1., 'km': 1000., 'in': .0254, 'ft': .3048, 'yd': .9144}
    if (not math.isclose(meters.get(cmds.currentUnit(query=True, linear=True), -1), plan['usd']['meters_per_unit'])
            or cmds.upAxis(query=True, axis=True).upper() != plan['usd']['up_axis']):
        raise ValueError('Placement scene units/up-axis differ from the USD plan')
    from .animation_data_bake import scene_timing
    if not math.isclose(scene_timing(cmds)['fps'], plan['fps'], abs_tol=1e-6):
        raise ValueError('Placement scene FPS differs from the USD plan')
    by_member = {}
    for locator in locators:
        if locator.member:
            by_member.setdefault(locator.member, []).append(locator)
    start, end = map(float, plan['frame_range'])
    previous = cmds.currentTime(query=True)
    result = {}
    try:
        for row in plan['rows']:
            if row.get('geometry_source', 'asset') != 'asset':
                continue  # Animation owns final world-space geometry, including placement.
            matches = by_member.get(row['target'], [])
            if len(matches) != 1:
                raise ValueError(row['target'] + ': assign exactly one Smart Maker placement')
            locator = matches[0]
            # Sampling the driven root includes constraints and parent hierarchy.
            node = locator.attach_root
            if not node or not cmds.objExists(node):
                raise ValueError(row['target'] + ': attach the asset root in Smart Maker first')
            frames = [start]
            if locator.motion == 'CURVE':
                frames += [start + i for i in range(1, math.ceil(end - start))]
                if end > start:
                    frames.append(end)
            samples = []
            for frame in frames:
                cmds.currentTime(frame, edit=True)
                matrix = list(cmds.xform(node, query=True, worldSpace=True, matrix=True))
                samples.append(dict(frame=frame, matrix=matrix))
            data = dict(mode=locator.motion, locator=locator.node, attach_root=node, samples=samples)
            result[row['target']] = validate_placement(data, plan['frame_range'])
    finally:
        cmds.currentTime(previous, edit=True)
    return result

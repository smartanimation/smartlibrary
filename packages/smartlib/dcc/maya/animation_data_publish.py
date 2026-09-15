"""Shared current-scene ATOM Data publication for Data and USD Publish."""


def publish_current_animation_data(shots, identity, *, target, frame_range=None, comment=''):
    import maya.cmds as cmds
    from smartlib.dcc.maya.animation_curves import export_animation_atom_for_cast

    cast = (shots.load_cast(identity).get('cast') or {}).get(target)
    if not cast:
        raise ValueError(f'Cast was not found: {target}')
    bounds = tuple(frame_range or shots.shot_frame_range(identity))
    if bounds[1] < bounds[0]:
        raise ValueError('End frame must not precede Start frame')
    source = cmds.file(query=True, sceneName=True) or ''
    plan = shots.plan_animation_atom_export(identity, target=target, subset='curves')
    manifest = export_animation_atom_for_cast(
        plan['atom_path'], cast_key=target, asset=cast.get('asset', ''),
        namespace=cast.get('namespace') or target, source_workfile=source, frame_range=bounds)
    return shots.finalize_animation_atom_export(
        identity, manifest, target=target, subset='curves', version=plan['version'],
        source_workfile=source, comment=comment)

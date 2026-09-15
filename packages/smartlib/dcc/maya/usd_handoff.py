"""Isolated Maya worker: fixed Rig + ATOM Data + optional post-deform sculpt."""
import json
import math
from pathlib import Path

from smartlib.core.metadata import read_json, write_json


def export_animation(row, plan, output):
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
    from smartlib.dcc.maya.animation_curves import (
        apply_animation_atom_from_file, export_animation_geometry_cache,
        _remap_node_namespace, _resolve_scene_node,
    )
    from smartlib.dcc.maya.animation_data_bake import sample_plugs, rig_dependencies
    from smartlib.apps.shot_manager.animation_publish import file_hash
    data = read_json(row['source']['path'], {})
    namespace = row['target']
    cmds.file(new=True, force=True)
    # Mayapy-only worker: never replace the artist's open scene.
    fps = plan['fps']
    units = {24: 'film', 25: 'pal', 30: 'ntsc', 48: 'show', 50: 'palf', 60: 'ntscf'}
    cmds.currentUnit(time=units.get(fps, f'{fps:g}fps'))
    linear = {0.001: 'mm', 0.01: 'cm', 1.0: 'm'}
    if plan['usd']['meters_per_unit'] not in linear:
        raise ValueError('Unsupported Maya project linear unit')
    cmds.currentUnit(linear=linear[plan['usd']['meters_per_unit']])
    cmds.upAxis(axis=plan['usd']['up_axis'].lower())
    cmds.file(row['rig']['path'], reference=True, namespace=namespace, mergeNamespacesOnClash=False)
    for ref in data['rig_dependencies']:
        if file_hash(Path(ref['path'])) != ref['sha256']:
            raise ValueError('Rig changed since Animation Data publication')
    mapped = {}
    for source in data.get('constraint_bake', {}).get('plugs', []):
        node, attr = source.rsplit('.', 1)
        node = _resolve_scene_node(cmds, _remap_node_namespace(node, data['namespace'], namespace))
        if not node:
            raise ValueError(f'Missing baked controller: {source}')
        plug = node + '.' + attr
        mapped[source] = plug
        # These channels explicitly contain evaluated motion in ATOM. Do not
        # retain the original rig constraint on top of that baked result.
        for incoming in cmds.listConnections(plug, source=True, destination=False, plugs=True) or []:
            cmds.disconnectAttr(incoming, plug)
    apply_animation_atom_from_file(row['source']['path'], namespace=namespace)
    bounds = data['frame_range']
    actual = sample_plugs(cmds, list(mapped.values()), *bounds)
    for source, plug in mapped.items():
        expected = data['constraint_bake'].get('samples', {}).get(source)
        if expected is None or len(expected) != len(actual[plug]) or any(
                not math.isclose(a, b, rel_tol=1e-7, abs_tol=1e-5) for a, b in zip(expected, actual[plug])):
            raise ValueError(f'ATOM rebuild differs from constrained source: {source}')
    cmds.playbackOptions(minTime=plan['frame_range'][0], maxTime=plan['frame_range'][1])
    export_animation_geometry_cache(namespace=namespace, output_dir=output.parent,
        frame_range=tuple(plan['frame_range']), formats=('usd',), final_deform=True,
        resolved_files={'usd': output})
    if row.get('sculpt'):
        from smartlib.dcc.maya.shot_sculpt import apply_to_usd
        apply_to_usd(output, read_json(row['sculpt']['path'], {}),
                     curve_sha256=row['source']['sha256'], frame_range=plan['frame_range'])
    return {'ok': True, 'source': 'fixed_data_rebuild', 'constraint_channels': len(mapped),
            'sculpt': row.get('sculpt'), 'sample_by': 1.0}


def main():
    import argparse
    parser = argparse.ArgumentParser()
    for key in ('config', 'plan', 'result'):
        parser.add_argument('--' + key, required=True)
    args = parser.parse_args()
    import maya.standalone
    maya.standalone.initialize(name='python')
    try:
        from smartlib.core.config_loader import ProjectConfig
        from smartlib.apps.shot_manager import ShotIdentity, ShotManagerService
        from smartlib.apps.shot_manager.usd_handoff import UsdHandoffService
        service = UsdHandoffService(ShotManagerService(ProjectConfig(args.config)))
        import maya.cmds as cmds
        from smartlib.core.maya_runtime import software_config_name
        from smartlib.apps.review_build_manager.worker import _load_build_plugins
        maya_config = software_config_name(service.config)
        print('USD Build Maya configuration: ' + maya_config, flush=True)
        plugin_report = _load_build_plugins(cmds, service.config, 'WORK STAGE', maya_config)
        print('USD Build plugins: ' + json.dumps(plugin_report), flush=True)
        plan = read_json(args.plan, {})
        identity = ShotIdentity(**plan['shot'])
        result = service.publish(identity, plan, animation_exporter=export_animation)
        write_json(args.result, {'ok': True, 'manifest': str(result),
            'maya_config': maya_config, 'plugins': plugin_report})
    except Exception as exc:
        write_json(args.result, {'ok': False, 'error': str(exc)})
        raise
    finally:
        maya.standalone.uninitialize()


if __name__ == '__main__':
    main()

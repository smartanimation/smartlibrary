"""Read a saved project plan; rebuild ONLY into a unique repository .tmp folder."""
from pathlib import Path
import argparse
import os
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--plan', required=True)
    args = parser.parse_args()
    from smartlib.core.config_loader import ProjectConfig
    from smartlib.core.maya_runtime import process_environment, software_config_name
    config = ProjectConfig(args.config)
    values, paths = process_environment(config)
    for key, value in values.items():
        os.environ[key] = os.path.expandvars(value)
    for key, entries in paths.items():
        os.environ[key] = os.pathsep.join([os.path.expandvars(v) for v in entries] + [os.environ.get(key, '')])
    import maya.standalone
    maya.standalone.initialize(name='python')
    try:
        import maya.cmds as cmds
        from smartlib.apps.review_build_manager.worker import _load_build_plugins
        from smartlib.apps.shot_manager import ShotIdentity, ShotManagerService
        from smartlib.apps.shot_manager.usd_handoff import UsdHandoffService
        from smartlib.core.metadata import read_json
        from smartlib.dcc.maya.usd_handoff import export_animation
        from smartlib.dcc.maya.animation_build import validate_deform_usd
        from smartlib.dcc.maya.animation_curves import _cache_export_roots, _cache_mesh_shapes
        from pxr import Usd, UsdGeom
        selected = software_config_name(config)
        print('CONFIG=' + selected, flush=True)
        print('PLUGINS=' + str(_load_build_plugins(cmds, config, 'WORK STAGE', selected)), flush=True)
        plan = read_json(args.plan, {})
        service = UsdHandoffService(ShotManagerService(config))
        identity = ShotIdentity(**plan['shot'])
        service.validate_plan(plan, identity)
        output = ROOT / '.tmp' / ('usd-verify-' + uuid.uuid4().hex)
        output.mkdir(parents=True)
        for row in plan['rows']:
            if row['kind'] != 'animation':
                continue
            path = output / (row['target'] + '.usdc')
            export_animation(row, plan, path)
            validate_deform_usd(path)
            stage = Usd.Stage.Open(str(path))
            meshes = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
            source_meshes = set(s for r in _cache_export_roots(cmds, row['target']) for s in _cache_mesh_shapes(cmds, r))
            assert len(meshes) == len(source_meshes), (len(meshes), len(source_meshes))
            assert stage.GetDefaultPrim().GetPath() == '/Geometry'
            print(f'PASS: {len(meshes)} meshes; frames {plan["frame_range"]}; {path.stat().st_size} bytes; {path}', flush=True)
        service.validate_plan(plan, identity)
    finally:
        maya.standalone.uninitialize()


if __name__ == '__main__':
    main()

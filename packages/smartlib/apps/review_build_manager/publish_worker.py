"""Immutable Publish job worker; no dependency on either manager window."""
import argparse
import hashlib
from pathlib import Path
import traceback

from smartlib.core.metadata import read_json, write_json


def run(request_path, expected_hash):
    from smartlib.core.path_resolver import ProjectPaths
    from smartlib.core.config_loader import ProjectConfig
    from smartlib.apps.shot_manager import ShotIdentity, ShotManagerService
    from smartlib.apps.shot_manager.usd_handoff import UsdHandoffService
    request_path = Path(request_path)
    # Paths remain owned by the existing resolver even for job receipts.
    paths = ProjectPaths(request_path.parent)
    status = paths.artifact_file(request_path.parent, 'status.json')
    result_path = paths.artifact_file(request_path.parent, 'result.json')
    def progress(percent, task):
        write_json(status, dict(state='BUILDING', progress=percent, task=task))
        print(task, flush=True)
    initialized = False
    snapshot = None
    camera_export_started = False
    camera_export_complete = False
    try:
        if hashlib.sha256(request_path.read_bytes()).hexdigest() != expected_hash:
            raise ValueError('Publish request changed after enqueue')
        request = read_json(request_path, {})
        if request.get('schema') != 'smartpipeline.publish_job.v1':
            raise ValueError('Invalid Publish request')
        config = ProjectConfig(request['config'])
        service = UsdHandoffService(ShotManagerService(config))
        identity = ShotIdentity(**request['shot'])
        if request.get('merge_animation'):
            # Detached supervisor already owns the project execution lease.
            # Resolve once here, not at enqueue time, so prior queued publishes survive.
            versions = service.composition_versions(identity)
            request['base_composition'] = service.pin(versions[0]['path']) if versions else None
            if request['base_composition']:
                service.load_handoff(service.check(request['base_composition']))
            write_json(paths.artifact_file(request_path.parent, 'resolved_inputs.json'),
                       dict(base_composition=request['base_composition'], policy='merge_animation'))
        progress(5, 'Initialize project Maya')
        import maya.standalone
        maya.standalone.initialize(name='python')
        initialized = True
        import maya.cmds as cmds
        from .worker import _load_build_plugins
        class PinnedSoftware:
            def load(self, name):
                if name != request['maya_config_name']:
                    raise ValueError('Unexpected Maya configuration request')
                return request['maya_config']
        plugins = _load_build_plugins(cmds, PinnedSoftware(), 'WORK STAGE', request['maya_config_name'])
        print('Maya config: ' + request['maya_config_name'], flush=True)
        print('Plugins: ' + str(plugins), flush=True)
        if request.get('scene_input'):
            from smartlib.dcc.maya.publish_scene_input import validate_scene_input
            validate_scene_input(request['scene_input'])
            workspace = request['scene_input'].get('workspace')
            if workspace:
                cmds.workspace(workspace, openWorkspace=True)
            progress(10, 'Open fixed saved scene')
            cmds.file(request['scene_input']['scene']['path'], open=True, force=True, executeScriptNodes=False)
            options = request['scene_options']
            if request['kind'] == 'camera_batch_usd':
                from smartlib.dcc.maya.camera_batch_publish import capture
                request['plan'] = capture(service, identity, request, cmds)
            elif request['kind'] == 'layout_usd':
                from smartlib.dcc.maya.layout_publish import capture
                request['plan'] = capture(service, identity, request, cmds)
            elif request['kind'] == 'assets_usd':
                from smartlib.dcc.maya.placement_usd import capture_placements
                service.validate_plan(request['plan'], identity)
                placements = capture_placements(request['plan'], cmds)
                for row in request['plan']['rows']:
                    if row['target'] in placements:
                        row['placement'] = placements[row['target']]
                        row['placement']['source_scene'] = request['scene_input']['source']['path']
                        row['placement']['source_sha256'] = request['scene_input']['scene']['sha256']
            elif request['kind'] == 'animation_usd':
                from smartlib.dcc.maya.animation_data_publish import publish_current_animation_data
                selections = options.get('targets', [options])
                names = [item['target'] for item in selections]
                if not names or len(names) != len(set(names)):
                    raise ValueError('Select unique Animation targets')
                rows = []
                # Capture ALL Data before rebuild/export opens a different scene.
                for item in selections:
                    source = publish_current_animation_data(service.shots, identity,
                        target=item['target'], frame_range=request['frame_range'],
                        cast_entry=item['cast'], comment='Captured from fixed saved scene for USD Publish')
                    print('Animation Data published: ' + str(source), flush=True)
                    rows.append(dict(kind='animation', target=item['target'], source=str(source),
                        rig=str(service.check(item['rig'])), rig_context=item['rig_context'],
                        skel=str(service.check(item['skel'])) if item.get('skel') else None,
                        **({'cast_asset': item['cast_asset']} if item.get('cast_asset') else {}),
                        sculpt=str(service.check(item['sculpt'])) if item.get('sculpt') else None))
                request['plan'] = service.plan(identity, rows, frame_range=request['frame_range'])
            elif request['kind'] == 'primary_camera_usd':
                from smartlib.dcc.maya import primary_camera
                payload = primary_camera.collect(options['target'], request['frame_range'], cmds,
                    motion_mode=options.get('motion_mode', 'animated'), sample_frame=options.get('sample_frame'))
                snapshot = service.shots.publish_shot_scene_snapshot(identity, payload,
                    data_type='camera', target=options['publish_target'], subset='main',
                    source_workfile=request['scene_input']['source']['path'],
                    comment=options.get('comment', ''),
                    native_exporter=lambda directory: primary_camera.export_native(payload, directory, cmds))
                request['snapshot'] = service.pin(snapshot)
                data = read_json(snapshot, {})
                request['native'] = service.pin(service.paths.manifest_source(snapshot, data['files']['ma']))
            validate_scene_input(request['scene_input'])
        if request['kind'] in ('camera_batch_usd', 'layout_usd'):
            progress(80, 'Validate batch / compose shot')
            manifest = service.publish(identity, request['plan'], base_composition=request.get('base_composition'))
        elif request['kind'] == 'assets_usd':
            progress(20, 'Validate fixed Asset USD / compose Cast registration')
            manifest = service.publish(identity, request['plan'],
                base_composition=request.get('base_composition'))
        elif request['kind'] == 'animation_usd':
            from smartlib.dcc.maya.usd_handoff import export_animation
            progress(20, 'Rebuild fixed Animation Data / export USD')
            def exporter(row, plan, path):
                print('Export Animation USD: ' + row['target'], flush=True)
                receipt = export_animation(row, plan, path, paths=service.paths)
                if request.get('scene_input'):
                    validate_scene_input(request['scene_input'])
                progress(80, 'Validate USD / compose shot')
                return receipt
            manifest = service.publish(identity, request['plan'], animation_exporter=exporter,
                base_composition=request.get('base_composition'))
        elif request['kind'] == 'primary_camera_usd':
            from smartlib.dcc.maya import camera_portable
            snapshot = service.check(request['snapshot'])
            native = service.check(request['native'])
            payload = read_json(snapshot, {})
            camera_export_started = True
            progress(20, 'Load fixed Primary Camera / world bake USD')
            cmds.file(str(native), open=True, force=True, executeScriptNodes=False)
            files = camera_portable.export_portable(payload, snapshot.parent, cmds)
            camera_portable.validate_portable(files, snapshot.parent, cmds)
            service.check(request['native'])
            camera_portable.update_publish(snapshot, status='complete', files=files)
            camera_export_complete = True
            progress(80, 'Validate Camera USD / compose shot')
            source = service.paths.manifest_source(snapshot, files['usd'])
            plan = service.plan(identity, [dict(kind='camera', target='primary', source=str(source),
                camera_motion=payload.get('camera_motion') or dict(mode='animated', animation_required=True))],
                frame_range=request['frame_range'])
            manifest = service.publish(identity, plan, base_composition=request.get('base_composition'))
        else:
            raise ValueError('Unknown Publish job kind')
        service.load_handoff(manifest)
        if request.get('scene_input'):
            validate_scene_input(request['scene_input'])
        write_json(result_path, dict(ok=True, manifest=str(manifest), plugins=plugins,
            maya_config=request['maya_config_name']))
        progress(100, 'Publish complete')
        return 0
    except Exception as exc:
        if camera_export_started and not camera_export_complete:
            try:
                camera_portable.update_publish(snapshot, status='failed', error=str(exc))
            except Exception:
                traceback.print_exc()
        write_json(result_path, dict(ok=False, error=str(exc)))
        traceback.print_exc()
        return 1
    finally:
        if initialized:
            maya.standalone.uninitialize()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.request, args.sha256))

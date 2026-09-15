"""Hython worker; consumes the queued snapshot without resolving latest again."""
import json
from pathlib import Path
import sys
import traceback
from smartlib.core.metadata import write_json
from .houdini_build import write_stage, verify


def run(request):
    import hou
    snapshot = request['snapshot']
    scene = Path(request['scene']); stage_path = Path(request['stage'])
    manifest = Path(request['manifest'])
    if scene.exists() or stage_path.exists() or manifest.exists():
        raise FileExistsError('Build output already exists; allocate a new Build version.')
    verify(snapshot)
    hou.hipFile.clear(suppress_save_prompt=True)
    write_stage(snapshot, stage_path)
    node = hou.node('/stage').createNode('sublayer', 'shot_inputs')
    node.parm('filepath1').set(str(stage_path))
    node.setDisplayFlag(True)
    if node.errors() or node.stage() is None:
        raise RuntimeError('Solaris failed to load the shot USD: ' + str(node.errors()))
    hou.setFps(snapshot['fps'])
    start, end = snapshot['frame_range']
    hou.playbar.setFrameRange(start, end); hou.playbar.setPlaybackRange(start, end); hou.setFrame(start)
    from smartlib.dcc.houdini.crowd_workspace import create, validate
    background = hou.node('/stage').createNode('null', 'OUT_BACKGROUND')
    background.setInput(0, node)
    review = hou.node('/stage').createNode('null', 'OUT_REVIEW')
    review.setInput(0, background); review.setDisplayFlag(True)
    hou.node('/stage').layoutChildren()
    workspace = create(snapshot, background)
    validate(workspace)
    hou.hipFile.save(str(scene))
    hou.hipFile.load(str(scene), suppress_save_prompt=True)
    if hou.node('/stage/shot_inputs').stage() is None:
        raise RuntimeError('Saved Houdini scene could not be reopened.')
    workspace_validation = validate(workspace)
    verify(snapshot)
    write_json(manifest, {'schema': 'smartpipeline.houdini_build.v1', 'status': 'complete',
        'scene': str(scene), 'stage': str(stage_path), 'snapshot': snapshot,
        'crowd_workspace': workspace, 'crowd_workspace_validation': workspace_validation})
    return scene


def main():
    request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    status = Path(request['status_file'])
    try:
        write_json(status, dict(state='RUNNING', progress=10, task='Build Solaris scene'))
        scene = run(request)
        write_json(status, dict(state='COMPLETE', progress=100, task='Complete', message=str(scene)))
        return 0
    except Exception:
        message = traceback.format_exc()
        write_json(status, dict(state='FAILED', progress=100, task='Build failed', message=message))
        print(message, file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())

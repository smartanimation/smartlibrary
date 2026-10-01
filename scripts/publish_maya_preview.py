"""Isolated Maya Geometry / Preview Look publisher for Asset Manager."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packages'))


from smartlib.dcc.maya.current_preview import publish_scene as run


if __name__ == '__main__':
    import maya.standalone
    maya.standalone.initialize(name='python')
    try:
        job = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
        manifest = run(job)
        Path(job['result']).write_text(json.dumps({'manifest': str(manifest)}), encoding='utf-8')
        print('PUBLISHED:', manifest)
    finally:
        import maya.cmds as cmds
        cmds.file(new=True, force=True)
        maya.standalone.uninitialize()

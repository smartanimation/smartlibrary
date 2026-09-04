"""Run with the configured mayapy. Only writes to a new temporary directory.

Example: mayapy validate_gui_review_playblast.py --scene BUILT.ma
         --camera '|camera_grp|cam' --layers CHA BGA --frame 630 --plugin clgIKNode
"""
import argparse
from pathlib import Path
import tempfile
import time

parser = argparse.ArgumentParser()
parser.add_argument("--scene", required=True)
parser.add_argument("--camera", required=True)
parser.add_argument("--layers", nargs="+", required=True)
parser.add_argument("--frame", type=int, required=True)
parser.add_argument("--plugin", action="append", default=[])
args = parser.parse_args()

import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
from smartlib.dcc.maya.gui_review_playblast import launch_playblast

try:
    plugins = []
    for plugin in args.plugin:
        cmds.loadPlugin(plugin, quiet=True)
        plugins.append({"path": cmds.pluginInfo(plugin, query=True, path=True), "required": True})
    cmds.file(args.scene, open=True, force=True, prompt=False, executeScriptNodes=False)
    root = Path(tempfile.mkdtemp(prefix="review_gui_acceptance_"))
    print("DIAG_ROOT " + str(root), flush=True)
    layers = [{"name": name, "display_layer": name, "camera": args.camera,
               "frame_range": [args.frame, args.frame + 1], "resolution": [640, 360],
               "output_dir": str(root / name), "output_pattern": name + ".####.png",
               "overscan": 1.0, "display": {"display_lights": "all", "display_textures": True,
                                           "use_default_material": False, "shadows": True}}
              for name in args.layers]
    before = cmds.file(query=True, sceneName=True)
    began = time.monotonic()
    result = launch_playblast(cmds, scene=args.scene, layers=layers, all_layers=args.layers,
                             status_path=root / "job.json", plugins=plugins,
                             progress=lambda f, m: print(f"DIAG_PROGRESS {f:.2f} {m}", flush=True))
    assert cmds.file(query=True, sceneName=True) == before
    print(f"DIAG_RESULT {result} elapsed={time.monotonic() - began:.1f}s", flush=True)
finally:
    maya.standalone.uninitialize()

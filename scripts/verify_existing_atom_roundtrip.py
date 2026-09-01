"""Compare an existing ATOM payload with the open source scene, without saving."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scene")
    parser.add_argument("manifest")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--namespace", default="DLI")
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.repo) / "packages"))

    import maya.standalone
    maya.standalone.initialize(name="python")
    import maya.cmds as cmds
    from smartlib.dcc.maya.animation_curves import (
        apply_animation_atom_from_file,
        _contract_nodes, _controller_members, _resolve_controller_root,
        _resolve_scene_node,
    )

    try:
        cmds.file(args.scene, open=True, force=True, prompt=False)
    except RuntimeError:
        # Production rigs can declare optional custom evaluation plugins that
        # are unavailable in standalone. Maya has already loaded the scene;
        # continue so transfer/import diagnostics can still be reported.
        if not cmds.objExists(f"{args.namespace}:allRigSet"):
            raise
    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    atom_path = manifest_path.parent / manifest.get("payload", "animation.atom")
    root = _resolve_controller_root(cmds, args.namespace, "allRigSet")
    controls = _contract_nodes(cmds, _controller_members(cmds, root),
                               traverse_descendants=True, namespace=args.namespace)
    fingers = [str(n) for n in cmds.ls(f"{args.namespace}:A_*Finger*", long=True) or []
               if cmds.nodeType(n) in {"transform", "joint"}]
    observed_nodes = sorted(set(controls + fingers))
    attrs = []
    for node in observed_nodes:
        for attr in sorted(set((cmds.listAttr(node, keyable=True) or []) +
                               (cmds.listAttr(node, channelBox=True) or []))):
            plug = f"{node}.{attr}"
            try:
                value = cmds.getAttr(plug, time=args.start)
                if isinstance(value, (bool, int, float)):
                    attrs.append(plug)
            except (RuntimeError, TypeError, ValueError):
                pass
    attrs = sorted(set(attrs))
    frames = [args.start + i * .25 for i in range((args.end - args.start) * 4 + 1)]
    before = {p: [float(cmds.getAttr(p, time=f)) for f in frames] for p in attrs}

    transfer = [_resolve_scene_node(cmds, str(n)) for n in manifest.get("transfer_nodes", [])]
    transfer = [n for n in transfer if n and cmds.objExists(n)]
    cmds.cutKey(transfer, clear=True)
    for plug in attrs:
        node, attr = plug.rsplit(".", 1)
        try:
            default = cmds.attributeQuery(attr, node=node, listDefault=True) or []
            if default and cmds.getAttr(plug, settable=True):
                cmds.setAttr(plug, default[0])
        except (RuntimeError, TypeError, ValueError):
            pass
    apply_animation_atom_from_file(manifest_path, namespace=args.namespace, clear_existing=True)

    differences = []
    per_plug = {}
    for plug, expected_values in before.items():
        for frame, expected in zip(frames, expected_values):
            actual = float(cmds.getAttr(plug, time=frame))
            if not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-7):
                per_plug[plug] = per_plug.get(plug, 0) + 1
                if len(differences) < 1000:
                    differences.append({"plug": plug, "frame": frame,
                                        "expected": expected, "actual": actual})
    report = {
        "transfer_manifest_count": len(manifest.get("transfer_nodes", [])),
        "transfer_resolved_count": len(transfer), "observed_node_count": len(observed_nodes),
        "attribute_count": len(attrs), "sample_count": len(attrs) * len(frames),
        "difference_count": sum(per_plug.values()), "different_plug_count": len(per_plug),
        "worst_plugs": sorted(per_plug.items(), key=lambda item: item[1], reverse=True)[:100],
        "differences": differences,
    }
    Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "differences"}, indent=2))
    sys.stdout.flush()
    os._exit(1 if per_plug else 0)


if __name__ == "__main__":
    main()

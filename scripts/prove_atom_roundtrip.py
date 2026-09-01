"""Prove an ATOM round trip against one Maya scene without saving it."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path


def _scalar(value):
    if isinstance(value, (bool, int, float)):
        return float(value)
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scene")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--atom", required=True)
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
        _contract_nodes,
        _controller_members,
        _resolve_controller_root,
        _upstream_anim_curves,
    )

    cmds.file(args.scene, open=True, force=True, prompt=False)
    print("STAGE scene_opened", flush=True)
    cmds.loadPlugin("atomImportExport", quiet=True)
    root = _resolve_controller_root(cmds, args.namespace, "allRigSet")
    controls = _contract_nodes(
        cmds,
        _controller_members(cmds, root),
        traverse_descendants=True,
        namespace=args.namespace,
    )
    finger_nodes = []
    for node in cmds.ls(f"{args.namespace}:A_*Finger*", long=True) or []:
        try:
            if cmds.nodeType(node) in {"transform", "joint"}:
                finger_nodes.append(str(node))
        except RuntimeError:
            pass
    transfer_nodes = sorted(set(controls + finger_nodes))
    print(
        f"STAGE controls_collected {len(controls)} finger_nodes {len(finger_nodes)}",
        flush=True,
    )
    attributes = []
    for node in transfer_nodes:
        for attr in sorted(set((cmds.listAttr(node, keyable=True) or []) + (cmds.listAttr(node, channelBox=True) or []))):
            plug = f"{node}.{attr}"
            try:
                if cmds.getAttr(plug, type=True) in {"message", "string", "matrix"}:
                    continue
                if _scalar(cmds.getAttr(plug, time=args.start)) is not None:
                    attributes.append(plug)
            except (RuntimeError, TypeError, ValueError):
                pass
    attributes = sorted(set(attributes))
    print(f"STAGE attributes_collected {len(attributes)}", flush=True)

    frames = [args.start + step * 0.25 for step in range((args.end - args.start) * 4 + 1)]
    before = {
        plug: [float(cmds.getAttr(plug, time=frame)) for frame in frames]
        for plug in attributes
    }
    print("STAGE source_sampled", flush=True)
    finger_plugs = [plug for plug in attributes if "finger" in plug.lower()]

    keyed_static_plugs = []
    for plug, values in before.items():
        try:
            if not _upstream_anim_curves(cmds, plug) and cmds.getAttr(plug, settable=True):
                cmds.setKeyframe(plug, time=args.start, value=values[0])
                cmds.setKeyframe(plug, time=args.end, value=values[-1])
                keyed_static_plugs.append(plug)
        except (RuntimeError, TypeError, ValueError):
            pass

    atom_path = Path(args.atom)
    atom_path.parent.mkdir(parents=True, exist_ok=True)
    cmds.select(transfer_nodes, replace=True, noExpand=True)
    export_options = (
        f"precision=17;statics=1;baked=0;sdk=0;constraint=0;animLayers=0;"
        f"selected=selectedOnly;whichRange=2;range={args.start}:{args.end};"
        "hierarchy=none;controlPoints=0;useChannelBox=0;options=keys;copyKeyCmd="
    )
    cmds.file(str(atom_path), force=True, options=export_options, type="atomExport", exportSelected=True)
    print("STAGE atom_exported", flush=True)

    cmds.cutKey(transfer_nodes, clear=True)
    for plug in attributes:
        node, attr = plug.rsplit(".", 1)
        try:
            defaults = cmds.attributeQuery(attr, node=node, listDefault=True) or []
            if defaults and cmds.getAttr(plug, settable=True):
                cmds.setAttr(plug, defaults[0])
        except (RuntimeError, TypeError, ValueError):
            pass

    cmds.select(transfer_nodes, replace=True, noExpand=True)
    import_options = (
        ";targetTime=3;option=scaleReplace;match=string;selected=selectedOnly;"
        "search=;replace=;prefix=;suffix=;mapFile=;"
    )
    cmds.file(str(atom_path), i=True, type="atomImport", options=import_options, returnNewNodes=True)
    print("STAGE atom_imported", flush=True)

    differences = []
    for plug, expected_values in before.items():
        for frame, expected in zip(frames, expected_values):
            actual = float(cmds.getAttr(plug, time=frame))
            if not math.isclose(actual, expected, rel_tol=1.0e-10, abs_tol=1.0e-7):
                differences.append(
                    {"plug": plug, "frame": frame, "expected": expected, "actual": actual}
                )
    report = {
        "format": "ATOM",
        "control_count": len(controls),
        "finger_node_count": len(finger_nodes),
        "transfer_node_count": len(transfer_nodes),
        "attribute_count": len(attributes),
        "finger_attribute_count": len(finger_plugs),
        "keyed_static_count": len(keyed_static_plugs),
        "frame_range": [args.start, args.end],
        "sample_count": len(attributes) * len(frames),
        "difference_count": len(differences),
        "differences": differences,
    }
    Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "differences"}, indent=2))
    exit_code = 1 if differences else 0
    sys.stdout.flush()
    os._exit(exit_code)


if __name__ == "__main__":
    main()

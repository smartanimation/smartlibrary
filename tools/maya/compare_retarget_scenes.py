"""Compare world transforms of profile transfer nodes between two Maya scenes."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "packages"))
from smartlib.retarget.profile import load_retarget_profile  # noqa: E402

import maya.standalone

maya.standalone.initialize(name="python")
from maya import cmds  # noqa: E402


def leaf(node: str) -> str:
    return node.rsplit("|", 1)[-1].rsplit(":", 1)[-1]


def capture(path: str, names: list[str], frames: list[int], plugin: str) -> dict:
    cmds.file(new=True, force=True)
    cmds.loadPlugin(plugin, quiet=True)
    cmds.file(path, open=True, force=True, prompt=False)
    nodes = {}
    for name in names:
        matches = [node for node in cmds.ls(type="transform", long=True) or [] if leaf(node) == name]
        if len(matches) == 1:
            nodes[name] = matches[0]
    result = {}
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        result[str(frame)] = {
            name: cmds.xform(node, query=True, worldSpace=True, matrix=True) for name, node in nodes.items()
        }
    return result


def main() -> None:
    profile_path, expected_path, actual_path, output_path = sys.argv[1:5]
    profile = load_retarget_profile(profile_path)
    names = [name for group in profile["transfer_nodes"].values() for name in group]
    start, end = profile["frame_range"]
    frames = [start, 250, 500, 750, end]
    plugin = profile["required_plugins"][0]
    expected = capture(expected_path, names, frames, plugin)
    actual = capture(actual_path, names, frames, plugin)
    errors = []
    detailed = []
    per_frame = {}
    for frame in map(str, frames):
        frame_errors = []
        for name in sorted(set(expected[frame]) & set(actual[frame])):
            error = math.sqrt(sum((a - b) ** 2 for a, b in zip(expected[frame][name], actual[frame][name])))
            errors.append(error)
            frame_errors.append(error)
            detailed.append({"frame": int(frame), "node": name, "matrix_error": error})
        per_frame[frame] = {"max_matrix_error": max(frame_errors), "mean_matrix_error": sum(frame_errors) / len(frame_errors)}
    report = {
        "frames": frames,
        "nodes_compared": len(names),
        "max_matrix_error": max(errors),
        "mean_matrix_error": sum(errors) / len(errors),
        "per_frame": per_frame,
        "largest_errors": sorted(detailed, key=lambda item: item["matrix_error"], reverse=True)[:20],
    }
    with open(output_path, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()

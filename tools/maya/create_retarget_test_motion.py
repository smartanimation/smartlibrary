"""Create a compact project-standard retarget test FBX from captured MC_* motion."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import maya.standalone

maya.standalone.initialize(name="python")
from maya import cmds, mel  # noqa: E402


def leaf(node: str) -> str:
    return node.rsplit("|", 1)[-1].rsplit(":", 1)[-1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--time-scale", type=float, default=0.25)
    args = parser.parse_args()

    cmds.loadPlugin("fbxmaya", quiet=True)
    cmds.file(args.source, open=True, force=True, prompt=False)
    cmds.currentUnit(time="film", linear="cm")

    joints = cmds.ls(type="joint", long=True) or []
    mc_joints = [joint for joint in joints if leaf(joint).startswith("MC_")]
    if len(mc_joints) < 20:
        raise RuntimeError(f"Expected an MC_* skeleton, found only {len(mc_joints)} joints")

    source_start, source_end = 1, 1012
    output_start = 1
    output_end = round(output_start + (source_end - source_start) * args.time_scale)
    cmds.scaleKey(
        mc_joints,
        time=(source_start, source_end),
        timeScale=args.time_scale,
        timePivot=source_start,
        valueScale=1.0,
        valuePivot=0.0,
    )
    cmds.bakeResults(
        mc_joints,
        time=(output_start, output_end),
        sampleBy=1.0,
        simulation=True,
        preserveOutsideKeys=False,
        sparseAnimCurveBake=False,
        disableImplicitControl=True,
        minimizeRotation=True,
        attribute=[
            "translateX", "translateY", "translateZ",
            "rotateX", "rotateY", "rotateZ",
            "scaleX", "scaleY", "scaleZ",
        ],
    )
    cmds.cutKey(mc_joints, time=(output_end + 1, 100000), clear=True)

    namespaces = cmds.namespaceInfo(listOnlyNamespaces=True, recurse=True) or []
    for namespace in sorted(namespaces, key=lambda value: value.count(":"), reverse=True):
        if namespace in {"UI", "shared"}:
            continue
        try:
            cmds.namespace(removeNamespace=namespace, mergeNamespaceWithRoot=True)
        except RuntimeError:
            pass

    mc_joints = [joint for joint in cmds.ls(type="joint", long=True) or [] if leaf(joint).startswith("MC_")]
    roots = [joint for joint in mc_joints if not (cmds.listRelatives(joint, parent=True, type="joint") or [])]
    if len(roots) != 1:
        raise RuntimeError(f"Expected one exported skeleton root, found {roots}")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    cmds.playbackOptions(min=output_start, max=output_end, animationStartTime=output_start, animationEndTime=output_end)
    cmds.select(roots[0], hierarchy=True, replace=True)
    mel.eval("FBXResetExport")
    mel.eval("FBXExportFileVersion -v FBX202000")
    mel.eval("FBXExportBakeComplexAnimation -v true")
    mel.eval(f"FBXExportBakeComplexStart -v {output_start}")
    mel.eval(f"FBXExportBakeComplexEnd -v {output_end}")
    mel.eval("FBXExportBakeComplexStep -v 1")
    mel.eval("FBXExportConstraints -v false")
    mel.eval("FBXExportSkins -v false")
    mel.eval("FBXExportShapes -v false")
    mel.eval(f'FBXExport -f "{output.as_posix()}" -s')

    manifest = {
        "schema_version": 1,
        "name": "humanoid_retarget_test",
        "version": 1,
        "project": "ELCD",
        "file": output.name,
        "source_capture": args.source.replace("\\", "/"),
        "time_unit": "film",
        "fps": 24,
        "frame_range": [output_start, output_end],
        "linear_unit": "cm",
        "skeleton_prefix": "MC_",
        "joint_count": len(mc_joints),
        "time_scale": args.time_scale,
        "purpose": ["body", "arms", "legs", "wrists", "pole_vectors", "fingers"],
    }
    manifest_path = Path(args.manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()

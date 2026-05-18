from __future__ import annotations

DEFAULT_CAMERAS = {"persp", "top", "front", "side"}


def collect_scene_info(cmds_module=None) -> dict:
    if cmds_module is None:
        try:
            import maya.cmds as cmds_module
        except ImportError:
            return {}

    cmds = cmds_module
    renderer = cmds.getAttr("defaultRenderGlobals.currentRenderer") if cmds.objExists("defaultRenderGlobals") else ""
    cameras = []
    for shape in cmds.ls(type="camera") or []:
        try:
            if cmds.getAttr(f"{shape}.renderable"):
                parent = cmds.listRelatives(shape, parent=True, fullPath=False) or [shape]
                camera_name = parent[0].split("|")[-1]
                if camera_name not in DEFAULT_CAMERAS:
                    cameras.append(camera_name)
        except Exception:
            continue

    layers = []
    for layer in cmds.ls(type="renderLayer") or []:
        if layer != "defaultRenderLayer":
            layers.append(layer)

    references = []
    for ref_node in cmds.ls(type="reference") or []:
        if ref_node == "sharedReferenceNode":
            continue
        try:
            namespace = cmds.referenceQuery(ref_node, namespace=True)
            references.append(namespace.lstrip(":"))
        except Exception:
            continue

    width = int(cmds.getAttr("defaultResolution.width")) if cmds.objExists("defaultResolution") else 0
    height = int(cmds.getAttr("defaultResolution.height")) if cmds.objExists("defaultResolution") else 0

    return {
        "unit": cmds.currentUnit(query=True, linear=True),
        "rendersize": [width, height],
        "renderer": renderer,
        "timerange": [
            float(cmds.playbackOptions(query=True, minTime=True)),
            float(cmds.playbackOptions(query=True, maxTime=True)),
        ],
        "cameras": sorted(set(cameras)),
        "layers": sorted(set(layers)),
        "references": sorted(set(references)),
    }

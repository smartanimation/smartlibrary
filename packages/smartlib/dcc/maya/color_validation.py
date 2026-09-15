"""Maya-native color readback and isolated Arnold smoke render."""
from __future__ import annotations

from pathlib import Path
from smartlib.core.color_settings import config_fingerprint, require_compatible, environment_contract
from smartlib.color.validation import sha256, save_json


def snapshot(cmds):
    query = lambda flag: cmds.colorManagementPrefs(query=True, **{flag: True})
    path = query("configFilePath")
    return {"host": "Maya", "host_version": cmds.about(version=True),
            "ocio_version": query("colorManagementSDKVersion"), "config": path,
            "config_sha256": sha256(path), "bundle_sha256": config_fingerprint(path),
            "working_space": query("renderingSpaceName"), "display": query("displayName"),
            "view": query("viewName"), "enabled": query("cmEnabled"),
            "config_enabled": query("cmConfigFileEnabled"), "state_source": "host_readback"}


def apply(contract=None):
    import maya.cmds as cmds
    contract = contract or environment_contract()
    if not contract:
        return None
    require_compatible(contract["config"], cmds.colorManagementPrefs(q=True, colorManagementSDKVersion=True))
    cmds.colorManagementPrefs(e=True, cmEnabled=True, cmConfigFileEnabled=True)
    cmds.colorManagementPrefs(e=True, configFilePath=contract["config"])
    cmds.colorManagementPrefs(e=True, renderingSpaceName=contract["working_space"])
    cmds.colorManagementPrefs(e=True, displayName=contract["display"])
    cmds.colorManagementPrefs(e=True, viewName=contract["view"])
    cmds.colorManagementPrefs(e=True, outputTarget="renderer", outputTransformEnabled=False)
    state = snapshot(cmds)
    if not state["enabled"] or not state["config_enabled"]:
        raise RuntimeError("Maya color management is disabled.")
    for key in ("config_sha256", "bundle_sha256", "working_space", "display", "view"):
        if state[key] != contract[key]:
            raise RuntimeError(f"Maya color readback mismatch: {key}")
    return state


def install():
    """Apply on startup and after scene loads, including stale scene overrides."""
    import maya.cmds as cmds
    if not environment_contract():
        return
    apply()
    if not cmds.about(batch=True):
        for event in ("NewSceneOpened", "SceneOpened"):
            cmds.scriptJob(event=[event, apply], protected=True)


def render_smoke(manifest):
    """Call only from a disposable mayapy process; never discard a GUI scene."""
    import maya.cmds as cmds
    if not cmds.about(batch=True):
        raise RuntimeError("Validation scene generation requires a disposable mayapy process.")
    cmds.file(new=True, force=True)
    state = apply(manifest["contract"])
    cmds.loadPlugin("mtoa", quiet=True)
    from mtoa.core import createOptions
    createOptions()
    cmds.setAttr("defaultRenderGlobals.currentRenderer", "arnold", type="string")
    cmds.setAttr("defaultArnoldDriver.aiTranslator", "exr", type="string")
    cmds.setAttr("defaultArnoldDriver.halfPrecision", 0)
    cmds.setAttr("defaultArnoldRenderOptions.AASamples", 3)
    cmds.setAttr("defaultResolution.width", 64)
    cmds.setAttr("defaultResolution.height", 64)
    cmds.setAttr("defaultResolution.deviceAspectRatio", 1)
    card = cmds.polyPlane(name="validationGreyCard", width=20, height=20, axis=(0, 0, 1), subdivisionsX=1, subdivisionsY=1)[0]
    shader = cmds.shadingNode("aiStandardSurface", asShader=True, name="validationEmission")
    cmds.setAttr(shader + ".base", 0)
    cmds.setAttr(shader + ".specular", 0)
    cmds.setAttr(shader + ".emission", 1)
    cmds.setAttr(shader + ".emissionColor", 0.18, 0.18, 0.18, type="double3")
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="validationSG")
    cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader")
    cmds.sets(card, edit=True, forceElement=sg)
    camera, shape = cmds.camera(name="validationCamera", orthographic=True, orthographicWidth=2)
    cmds.setAttr(camera + ".translateZ", 10)
    for other in cmds.ls(type="camera"):
        cmds.setAttr(other + ".renderable", other == shape)
    output = manifest["paths"]["Maya_render.exr"]
    cmds.setAttr("defaultRenderGlobals.imageFilePrefix", manifest["paths"]["Maya_render"].replace("\\", "/"), type="string")
    cmds.file(rename=manifest["paths"]["color_validation.ma"])
    cmds.file(save=True, type="mayaAscii")
    state["mtoa_version"] = cmds.pluginInfo("mtoa", query=True, version=True)
    import arnold
    state["arnold_version"] = str(arnold.AiGetVersion())
    cmds.arnoldRender(seq="", w=64, h=64, cam=camera, srv=False)
    if not Path(output).is_file():
        raise RuntimeError(f"Arnold did not produce the expected EXR: {output}")
    state.update(run_id=manifest["run_id"], status="partial", artifacts={"Maya_render.exr": sha256(output)},
                 note="Arnold emission smoke render. Viewer framebuffer and input roundtrip remain required.")
    save_json(manifest["paths"]["Maya_state.json"], state)
    return state


def probe(manifest):
    import maya.cmds as cmds
    try:
        state = apply(manifest["contract"])
        state.update(status="partial", run_id=manifest["run_id"])
    except Exception as exc:
        state = {"host": "Maya", "run_id": manifest["run_id"], "status": "failed", "error": str(exc),
                 "host_version": cmds.about(version=True),
                 "ocio_version": cmds.colorManagementPrefs(q=True, colorManagementSDKVersion=True)}
    save_json(manifest["paths"]["Maya_state.json"], state)
    return state


def export_images(manifest):
    """Maya MImage float roundtrip plus native OCIO display conversion.

    This tests Maya's display transform API, not a viewport framebuffer.
    """
    import ctypes
    import json
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
    state = apply(manifest["contract"])
    p = manifest["paths"]
    image = om.MImage()
    image.readFromFile(p["reference.exr"], om.MImage.kFloat)
    width, height = image.getSize()
    channels = image.depth() // ctypes.sizeof(ctypes.c_float)
    if channels not in (3, 4):
        raise ValueError("Unexpected Maya image channel count.")
    image.writeToFile(p["Maya_output.exr"], "exr")
    pixels = (ctypes.c_float * (width * height * channels)).from_address(image.floatPixels())
    cache = {}
    for offset in range(0, len(pixels), channels):
        rgb = tuple(pixels[offset:offset + 3])
        if rgb not in cache:
            cache[rgb] = cmds.colorManagementConvert(toDisplaySpace=rgb)
        pixels[offset:offset + 3] = cache[rgb]
    image.writeToFile(p["Maya_display.exr"], "exr")
    old = json.loads(Path(p["Maya_state.json"]).read_text()) if Path(p["Maya_state.json"]).exists() else {}
    artifacts = dict(old.get("artifacts", {}))
    artifacts.update({n: sha256(p[n]) for n in ("Maya_output.exr", "Maya_display.exr")})
    state.update(run_id=manifest["run_id"], status="partial", artifacts=artifacts,
                 capture_method="Maya MImage / colorManagementConvert CPU", framebuffer_verified=False)
    for key in ("mtoa_version", "arnold_version"):
        if key in old:
            state[key] = old[key]
    save_json(p["Maya_state.json"], state)
    return state

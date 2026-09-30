"""Retarget received MCR animation onto an ANIM rig in an isolated Maya process."""
from pathlib import Path

from smartlib.retarget.direct import validate_mappings


def bake_received_mcr(profile, output=None, cmds=None, *, target_namespace=None):
    if cmds is None:
        import maya.cmds as cmds
    from .shot_builder import _import_file

    errors = validate_mappings(profile)
    if errors:
        raise ValueError("; ".join(errors))
    output = Path(output).resolve() if output else None
    if output and output.suffix.lower() != ".ma":
        raise ValueError("Direct MCR retarget output must be a .ma file")
    source = Path(profile["mocap_fbx"])
    rig = Path(profile["animation_rig_scene"])
    start, end = profile["frame_range"]
    if end < start:
        raise ValueError("Bake end frame must be >= start frame")
    if source.suffix.lower() != ".fbx":
        raise ValueError("Received MCR input must be FBX")
    if not source.is_file() or not rig.is_file():
        raise ValueError("MCR FBX or ANIM rig is missing")
    if output and output in {source.resolve(), rig.resolve()}:
        raise ValueError("Output must not overwrite an input")
    for plugin in profile.get("required_plugins", []):
        cmds.loadPlugin(plugin, quiet=True)
    if target_namespace is None:
        cmds.file(new=True, force=True)
        target_namespace = "anim_target"
        cmds.file(str(rig), reference=True, namespace=target_namespace, mergeNamespacesOnClash=False)
        cmds.currentUnit(time=profile["time_unit"])
    elif cmds.currentUnit(query=True, time=True) != profile["time_unit"]:
        raise ValueError("Sequence FPS differs from the published Retarget time unit")
    target_namespace = target_namespace.strip(":")
    all_nodes = cmds.ls(type="transform", long=True) or []
    target_nodes = [node for node in all_nodes
                    if node.rsplit("|", 1)[-1].startswith(target_namespace + ":")]
    if not target_nodes:
        raise ValueError(f"ANIM namespace is missing: {target_namespace}")
    before = {(cmds.ls(node, uuid=True) or [None])[0] for node in all_nodes}
    namespace = "received_mcr"
    while cmds.namespace(exists=namespace):
        namespace += "_input"
    _import_file(cmds, source, namespace)
    source_nodes = [node for node in (cmds.ls(type="transform", long=True) or [])
                    if (cmds.ls(node, uuid=True) or [None])[0] not in before]

    def resolve(nodes, name, side):
        matches = [node for node in nodes if node == name or node.rsplit("|", 1)[-1].rsplit(":", 1)[-1] == name]
        if len(matches) != 1:
            raise ValueError(f"{side}: {name}: expected one node, found {len(matches)}")
        return matches[0]

    applied_settings = {}
    for name, setting in profile.get("rig_settings", {}).items():
        node_name, attribute = name.rsplit(".", 1)
        plug = resolve(target_nodes, node_name, "ANIM") + "." + attribute
        value = setting["value"] if isinstance(setting, dict) else setting
        if not cmds.objExists(plug) or not cmds.getAttr(plug, settable=True):
            raise ValueError(f"ANIM rig setting is missing, locked or connected: {name}")
        cmds.setAttr(plug, value)
        applied_settings[name] = cmds.getAttr(plug)

    resolved = []
    for row in profile["mappings"]:
        if not row.get("enabled", True):
            continue
        src, dst = resolve(source_nodes, row["source"], "MCR"), resolve(target_nodes, row["target"], "ANIM")
        attrs = (["rotateX", "rotateY", "rotateZ"] if row["method"] == "orient" else
                 ["translateX", "translateY", "translateZ"] if row["method"] == "point" else
                 ["translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"])
        for attr in attrs:
            plug = dst + "." + attr
            if cmds.getAttr(plug, lock=True) or not cmds.getAttr(plug, settable=True):
                raise ValueError(f"ANIM channel is locked or connected: {plug}")
        resolved.append((row, src, dst, attrs))
    cmds.currentTime(float(profile["reference_frame"]), edit=True)
    constraints = []
    try:
        for row, src, dst, attrs in resolved:
            command = {"orient": cmds.orientConstraint, "point": cmds.pointConstraint, "parent": cmds.parentConstraint}[row["method"]]
            constraints.extend(command(src, dst, maintainOffset=bool(row.get("maintain_offset", True))))
        plugs = [dst + "." + attr for _, _, dst, attrs in resolved for attr in attrs]
        cmds.bakeResults(plugs, time=(start, end), simulation=True, sampleBy=1,
                         preserveOutsideKeys=True, sparseAnimCurveBake=False)
    finally:
        if constraints:
            cmds.delete(constraints)
    # Remove only the received FBX nodes, retaining the ANIM rig and its keys.
    # Anim curves created by Bake belong to ANIM and must be retained.
    source_roots = [node for node in source_nodes if not any(node.startswith(other + "|") for other in source_nodes if other != node)]
    if source_roots:
        cmds.delete(source_roots)
    for plug in plugs:
        if not cmds.keyframe(plug, query=True, keyframeCount=True):
            raise RuntimeError(f"Bake produced no animation keys: {plug}")
    cmds.playbackOptions(min=start, max=end, animationStartTime=start, animationEndTime=end)
    cmds.currentTime(start, edit=True)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        cmds.file(rename=str(output))
        cmds.file(save=True, force=True, type="mayaAscii")
    return {"keyed_plugs": len(plugs), "missing_plugs": [], "failed_plugs": [], "skipped_plugs": [],
            "output": str(output) if output else "", "target_namespace": target_namespace, "input_mode": "mcr_to_anim", "mappings": profile["mappings"],
            "reference_frame": profile["reference_frame"], "frame_range": [start, end],
            "rig_settings": applied_settings}

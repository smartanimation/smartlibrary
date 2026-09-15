"""Run only in an isolated mayapy process; never changes an artist's open scene."""
from __future__ import annotations

from pathlib import Path

from smartlib.apps.asset_manager.studio_delivery import digest, read, write_new


def set_members(cmds, name, node_type):
    sets = cmds.ls(name, type="objectSet", recursive=True) or []
    if len(sets) != 1:
        raise ValueError(f"Expected exactly one export set {name!r}; found {sets}")
    members = cmds.sets(sets[0], query=True) or []
    result = set()
    for member in members:
        nodes = cmds.ls(member, long=True) or []
        for node in nodes:
            if "." in node:
                raise ValueError(f"Export sets must contain whole objects: {node}")
            if cmds.nodeType(node) == node_type:
                result.add(node)
            result.update(cmds.listRelatives(node, allDescendents=True, fullPath=True, type=node_type) or [])
    if node_type == "mesh":
        result = {node for node in result if not cmds.getAttr(node + ".intermediateObject")}
    if not result:
        raise ValueError(f"No {node_type} nodes in {name}")
    return sorted(result)


def mesh_signature(cmds, meshes):
    import maya.api.OpenMaya as om
    import maya.api.OpenMayaAnim as oma
    result = {}
    for mesh in meshes:
        parent = cmds.listRelatives(mesh, parent=True, fullPath=True)[0]
        name = parent.rsplit("|", 1)[-1]
        if name in result:
            raise ValueError(f"Duplicate mesh name cannot be verified across FBX: {name}")
        selection = om.MSelectionList()
        selection.add(mesh)
        dag = selection.getDagPath(0)
        fn = om.MFnMesh(dag)
        clusters = cmds.ls(cmds.listHistory(mesh), type="skinCluster") or []
        if len(clusters) > 1:
            raise ValueError(f"Multiple skinClusters are not supported: {name}")
        weights = {}
        if clusters:
            selection.add(clusters[0])
            skin = oma.MFnSkinCluster(selection.getDependNode(1))
            component = om.MFnSingleIndexedComponent()
            obj = component.create(om.MFn.kMeshVertComponent)
            component.addElements(range(fn.numVertices))
            values, count = skin.getWeights(dag, obj)
            for index, influence in enumerate(skin.influenceObjects()):
                weights[influence.partialPathName().rsplit("|", 1)[-1]] = list(values[index::count])
        counts, indices = fn.getVertices()
        result[name] = {"vertices": fn.numVertices, "faces": list(counts), "indices": list(indices),
                        "points": [(p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kWorld)],
                        "weights": weights}
    return result


def verify_meshes(before, after):
    if set(before) != set(after):
        raise ValueError("FBX roundtrip changed the mesh names/count.")
    for name, original in before.items():
        restored = after[name]
        if any(original[key] != restored[key] for key in ("vertices", "faces", "indices")):
            raise ValueError(f"FBX roundtrip changed topology: {name}")
        if any(abs(a-b) > 1e-4 for p, q in zip(original["points"], restored["points"]) for a, b in zip(p, q)):
            raise ValueError(f"FBX roundtrip changed vertex positions: {name}")
        if original["weights"].keys() != restored["weights"].keys():
            raise ValueError(f"FBX roundtrip changed skin influences: {name}")
        for joint, weights in original["weights"].items():
            restored_weights = restored["weights"][joint]
            if len(weights) != len(restored_weights) or any(abs(a-b) > 1e-5 for a, b in zip(weights, restored_weights)):
                raise ValueError(f"FBX roundtrip changed skin weights: {name}/{joint}")


def export_job(job_path):
    from maya import cmds, mel
    job = read(job_path)
    report = {"run_id": job["run_id"], "status": "failed"}
    try:
        if digest(job["source"]["path"]) != job["source"]["sha256"]:
            raise ValueError("Source changed before export.")
        cmds.file(job["source"]["path"], open=True, force=True, prompt=False, executeScriptNodes=False)
        original_unit = cmds.currentUnit(query=True, linear=True)
        original_up = cmds.upAxis(query=True, axis=True)
        dependencies = []
        for ref in cmds.ls(type="reference") or []:
            if ref == "sharedReferenceNode":
                continue
            if not cmds.referenceQuery(ref, isLoaded=True):
                raise ValueError(f"Source contains an unloaded reference: {ref}")
            source = cmds.referenceQuery(ref, filename=True, withoutCopyNumber=True)
            dependencies.append({"path": source, "sha256": digest(source)})
        recipe = job["settings"]["recipe"]
        settings = job["settings"]["fbx"]
        meshes = set_members(cmds, recipe["geometry_set"], "mesh")
        joints = set_members(cmds, recipe["skeleton_set"], "joint") if recipe.get("skins") else []
        original = mesh_signature(cmds, meshes)
        selected_joint_names = {j.rsplit("|", 1)[-1] for j in joints}
        if len(selected_joint_names) != len(joints):
            raise ValueError("Skeleton contains duplicate joint names.")
        for mesh in meshes:
            clusters = cmds.ls(cmds.listHistory(mesh), type="skinCluster") or []
            if recipe.get("skins"):
                if not clusters:
                    raise ValueError(f"Character mesh has no skinCluster: {mesh}")
                influences = cmds.ls(cmds.skinCluster(clusters[0], query=True, influence=True), long=True)
                if not set(influences).issubset(joints):
                    raise ValueError(f"Skeleton export set omits skin influences: {mesh}")
        for joint in joints:
            parents = cmds.listRelatives(joint, parent=True, fullPath=True, type="joint") or []
            if any(parent not in joints for parent in parents):
                raise ValueError(f"Skeleton set omits an ancestor joint: {joint}")
        original_parents = {j.rsplit("|", 1)[-1]: [p.rsplit("|", 1)[-1] for p in (cmds.listRelatives(j, parent=True, type="joint") or [])] for j in joints}
        original_matrices = {j.rsplit("|", 1)[-1]: cmds.xform(j, query=True, worldSpace=True, matrix=True) for j in joints}
        if not recipe.get("skins"):
            for signature in original.values():
                signature["weights"] = {}
        cmds.loadPlugin("fbxmaya", quiet=True)
        mel.eval("FBXResetExport;")
        options = {"FBXExportSmoothingGroups": settings.get("smoothing_groups", True),
                   "FBXExportSkins": recipe.get("skins", False),
                   "FBXExportShapes": settings.get("blend_shapes", False),
                   "FBXExportEmbeddedTextures": settings.get("embed_textures", False),
                   "FBXExportCameras": False, "FBXExportLights": False,
                   "FBXExportConstraints": False, "FBXExportInputConnections": False,
                   "FBXExportIncludeChildren": False, "FBXExportInAscii": False}
        for command, value in options.items():
            mel.eval(f"{command} -v {'true' if value else 'false'};")
        mel.eval('FBXProperty Export|IncludeGrp|Animation -v false;')
        mel.eval(f'FBXExportFileVersion -v "{settings["file_version"]}";')
        mel.eval(f'FBXExportUpAxis {settings["up_axis"]};')
        mel.eval(f'FBXExportConvertUnitString "{settings["units"]}";')
        # Explicit output units (ConvertUnitString only queries the conversion factor).
        mel.eval('FBXProperty Export|AdvOptGrp|UnitsGrp|DynamicScaleConversion -v false;')
        unit_labels = {"mm": "Millimeters", "cm": "Centimeters", "m": "Meters"}
        mel.eval(f'FBXProperty Export|AdvOptGrp|UnitsGrp|UnitsSelector -v "{unit_labels[settings["units"]]}";')
        transforms = [cmds.listRelatives(mesh, parent=True, fullPath=True)[0] for mesh in meshes]
        cmds.select(list(dict.fromkeys(transforms + joints)), replace=True, noExpand=True)
        output = Path(job["output"])
        if output.exists():
            raise FileExistsError(output)
        # cmds.file avoids interpolating filesystem paths into MEL code.
        cmds.file(str(output), force=False, options="v=0;", type="FBX export", exportSelected=True)
        if not output.is_file() or not output.stat().st_size:
            raise RuntimeError("FBX exporter did not produce a file.")
        cmds.file(new=True, force=True)
        cmds.currentUnit(linear=original_unit)
        cmds.upAxis(axis=original_up)
        mel.eval("FBXResetImport;")
        cmds.file(str(output), i=True, type="FBX", ignoreVersion=True, prompt=False)
        restored_meshes = [m for m in (cmds.ls(type="mesh", long=True) or []) if not cmds.getAttr(m + ".intermediateObject")]
        verify_meshes(original, mesh_signature(cmds, restored_meshes))
        restored_joints = cmds.ls(type="joint", long=True) or []
        restored_parents = {j.rsplit("|", 1)[-1]: [p.rsplit("|", 1)[-1] for p in (cmds.listRelatives(j, parent=True, type="joint") or [])] for j in restored_joints}
        if original_parents != restored_parents:
            raise ValueError("FBX roundtrip changed skeleton names or hierarchy.")
        for joint in restored_joints:
            matrix = cmds.xform(joint, query=True, worldSpace=True, matrix=True)
            if any(abs(a-b) > 1e-4 for a, b in zip(original_matrices[joint.rsplit("|", 1)[-1]], matrix)):
                raise ValueError(f"FBX roundtrip changed joint pose: {joint}")
        if cmds.ls(type="animCurve"):
            raise ValueError("Static asset FBX unexpectedly contains animation.")
        for dependency in dependencies:
            if digest(dependency["path"]) != dependency["sha256"]:
                raise ValueError("Source reference changed during export.")
        report.update(status="passed", sha256=digest(output), source_sha256=job["source"]["sha256"],
                      dependencies=dependencies, meshes=len(original), joints=len(joints),
                      skin_weights_verified=bool(recipe.get("skins")), maya_version=cmds.about(version=True),
                      fbx_plugin_version=cmds.pluginInfo("fbxmaya", query=True, version=True))
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        write_new(job["report"], report)
    return report

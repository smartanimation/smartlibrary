"""Render published animation using the original Review camera and layer setup."""
from pathlib import Path

from smartlib.apps.shot_manager.animation_publish import AnimationCompositionService
from smartlib.core.metadata import read_json


def review_context(manager, identity, snapshot_path):
    service = AnimationCompositionService(manager.shots)
    data = service.load(snapshot_path, identity=identity)
    receipt = data.get("review_source") or {}
    source = Path(receipt.get("scene") or "")
    if not source.is_file():
        raise ValueError("The submitted Construct is required to retain Review cameras and layers")
    from .composition_sources import validate_review_source
    validate_review_source(receipt, source)
    manifest = read_json(receipt["build_manifest"], {})
    return data, receipt, manifest


def bake_review_cameras(cmds, manager, receipt):
    """Freeze the recorded layer cameras in the workspace Construct before rig replacement."""
    paths = manager.shots.paths
    source = read_json(receipt["source_manifest"], {})
    build = read_json(paths.project_dependency(source["review_build"]), {})
    from smartlib.dcc.maya.camera_portable import SHAPE_ATTRIBUTES
    result = {}
    original_time = cmds.currentTime(query=True)
    try:
        for layer, entry in build.get("review_layers", {}).items():
            source_camera = entry["camera"]
            matches = cmds.ls(source_camera, long=True) or []
            if len(matches) != 1:
                raise ValueError("Recorded Review Layer camera was not uniquely found: " + source_camera)
            source_camera = matches[0]
            source_shape = cmds.listRelatives(source_camera, shapes=True, fullPath=True, type="camera")[0]
            camera, shape = cmds.camera(name="reviewWork_" + layer + "_cam")
            layer_range = entry.get("frame_range") or build["frame_range"]
            start = min(int(layer_range[0]), int(build["frame_range"][0]))
            end = max(int(layer_range[1]), int(build["frame_range"][1]))
            attributes = tuple(SHAPE_ATTRIBUTES) + ("filmFitOffset", "panZoomEnabled", "renderPanZoom", "horizontalPan", "verticalPan", "zoom")
            for frame in range(int(start), int(end) + 1):
                cmds.currentTime(frame, edit=True)
                cmds.xform(camera, worldSpace=True, matrix=cmds.xform(source_camera, query=True, worldSpace=True, matrix=True))
                cmds.setKeyframe(camera, attribute=["translate", "rotate", "scale"], time=frame)
                for attribute in attributes:
                    if cmds.objExists(source_shape + "." + attribute):
                        cmds.setAttr(shape + "." + attribute, cmds.getAttr(source_shape + "." + attribute))
                        cmds.setKeyframe(shape, attribute=attribute, time=frame)
            result[layer] = {"source":source_camera, "work":camera, "frame_range":[start,end]}
    finally:
        cmds.currentTime(original_time, edit=True)
    if not result:
        raise ValueError("The source Review has no recorded Review Layer cameras")
    return result


def apply_snapshot(cmds, manager, identity, snapshot_path):
    """Replace only animation cast references in an opened submitted Construct."""
    data, receipt, manifest = review_context(manager, identity, snapshot_path)
    data["review_cameras"] = bake_review_cameras(cmds, manager, receipt)
    cast = data["cast"]
    from smartlib.dcc.maya.shot_builder import _namespace_nodes, ensure_scene_references_loaded
    ensure_scene_references_loaded(cmds)
    remove = set()
    for target, entry in cast.items():
        if not entry.get("animation_required", True):
            continue
        namespace = entry.get("namespace") or target
        nodes = _namespace_nodes(cmds, namespace)
        if not nodes:
            raise ValueError(f"Original Review cast namespace was not found: {namespace}")
        for node in nodes:
            if not cmds.referenceQuery(node, isNodeReferenced=True):
                raise ValueError(f"Review cast must be referenced to replace it safely: {node}")
            remove.add(cmds.referenceQuery(node, referenceNode=True, topReference=True))
    for node in sorted(remove):
        cmds.file(removeReference=True, referenceNode=node)
    rows = []
    for member in data["members"]:
        target = member["instance_id"]
        products = member["products"]
        if "rend" in products:
            path = products["rend"]["path"]
            cmds.file(path, reference=True, namespace=target, mergeNamespacesOnClash=False, loadReferenceDepth="all")
        else:
            path = products["deform"]["path"]
            if not cmds.namespace(exists=target):
                cmds.namespace(add=target)
            root = cmds.createNode("transform", name=target + ":published_animation")
            if data["profile"]["representation"] == "usd":
                cmds.loadPlugin("mayaUsdPlugin", quiet=True)
                proxy = cmds.createNode("mayaUsdProxyShape", name=target + ":stageShape", parent=root)
                cmds.setAttr(proxy + ".filePath", path, type="string")
                cmds.connectAttr("time1.outTime", proxy + ".time", force=True)
            else:
                cmds.loadPlugin("AbcImport", quiet=True)
                cmds.AbcImport(path, mode="import", reparent=root)
        rows.append({"type":"animation", "name":target, "enabled":True,
                     "version":", ".join(k + " " + v["version"] for k,v in products.items()),
                     "path":path, "context":{"maya":"REND", "usd":"USD", "abc":"ABC"}[data["profile"]["representation"]]})
    ensure_scene_references_loaded(cmds)
    # Snapshot/cast provenance is recorded independently of the source Construct.
    return data, rows


def enqueue(window, snapshot_path):
    from .composition_ui import QtCore
    from smartlib.apps.shot_manager import ShotIdentity
    service = AnimationCompositionService(window.service.shots)
    snapshot = service.load(snapshot_path)
    identity = ShotIdentity(**snapshot["shot"])
    data, receipt, manifest = review_context(window.service, identity, snapshot_path)
    department, task = receipt["department"], receipt["task"]
    workflow = window.service.review_workflow(identity)
    version = workflow.next_construct_version(department, "maya", task)
    # Also account for queued jobs that have not created their directories yet.
    pending_versions = [int(j["version"][1:]) for j in window.queue_jobs
        if j.get("state") not in {"COMPLETE", "FAILED"}
        and tuple(j["identity"]) == (identity.episode, identity.sequence, identity.shot)
        and j.get("department") == department and j.get("task_name") == task]
    version = f"v{max([int(version[1:]), *[v+1 for v in pending_versions]]):03d}"
    root = window.service.shots.paths.animation_build_dir(identity.episode, identity.sequence, identity.shot)
    _, directory = service._reserve(root)
    window.job_counter += 1
    job = {"kind":"composition_review", "id":f"#{window.job_counter:04d}",
        "identity":(identity.episode, identity.sequence, identity.shot), "scope":"shot",
        "version":version, "mode":"WORK STAGE", "department":department, "task_name":task,
        "construct":manifest.get("construct") or {}, "reuse_construct":receipt["scene"],
        "composition_snapshot":str(snapshot_path), "generate_review":True,
        "review_profile":window.review_profile_combo.currentText(), "delivery_profile":"internal",
        "precomp":"latest_approved", "review_cache_policy":"ignore_all",
        "planned_snapshot":{"inputs":[]}, "state":"QUEUED", "progress":0,
        "task":"Snapshot Internal Review / Movie + Report", "elapsed":QtCore.QElapsedTimer(),
        "row":window.queue_table.rowCount(), "stderr":"",
        "status_file":str(window.service.shots.paths.artifact_file(directory,"review_status.json"))}
    window.pending_jobs.append(job)
    window.queue_jobs.append(job)
    window._append_queue_row(job)
    if not window.active_job:
        window._start_next_job()
    return job

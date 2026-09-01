from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from smartlib.apps.shot_manager.service import ShotIdentity, ShotManagerService
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json, write_json
from smartlib.dcc.maya.animation_curves import (
    apply_animation_curves_from_file,
    export_animation_atom_for_cast,
)


def publish_c001_validation_package(
    config_dir: str | Path,
    source_scene: str | Path,
    *,
    comment: str = "c001 publish reconstruction validation",
) -> Path:
    """Publish the ELCD c001 validation components from the supplied Maya scene."""

    import maya.cmds as cmds

    config = ProjectConfig(Path(config_dir))
    service = ShotManagerService(config)
    identity = ShotIdentity("ep02", "s027", "c001")
    source_path = Path(source_scene)
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    cmds.file(str(source_path), open=True, force=True)

    animation_publishes: dict[str, str] = {}
    for target, source_namespace in (("DLI", "DLI"), ("JIN", "JIN")):
        plan = service.plan_animation_atom_export(
            identity,
            target=target,
            subset="curves",
        )
        atom_manifest = export_animation_atom_for_cast(
            plan["atom_path"],
            cast_key=f"{target}_main",
            asset=target,
            namespace=source_namespace,
            controller_root="allRigSet",
            source_workfile=source_path,
            frame_range=(278, 411),
        )
        manifest_path = service.finalize_animation_atom_export(
            identity,
            atom_manifest,
            target=target,
            subset="curves",
            version=plan["version"],
            source_workfile=source_path,
            comment=comment,
        )
        animation_publishes[target] = service._relative_to_project(manifest_path.parent)

    placement_publish = _publish_placement(
        service,
        identity,
        locator="DeleinChair_place_loc",
        member="DeleinChair_main",
        asset="DeleinChair",
        comment=comment,
    )
    setdress_publish = _publish_setdress(
        service,
        identity,
        node="DeleinRoomB:chair_1_geo",
        attributes={"translateY": -200.0},
        comment=comment,
    )
    camera_publishes = {
        "BGA": _publish_camera(
            service,
            identity,
            camera="cam_BGA_FIX",
            publish_camera="cam_BGA",
            target="BGA",
            resolution=(1766, 1836),
            frame_range=(278, 278),
            display_layer="BGA",
            comment=comment,
        ),
        "CHA": _publish_camera(
            service,
            identity,
            camera="cam_CHA_baked",
            publish_camera="cam_CHA",
            target="CHA",
            resolution=(1280, 720),
            frame_range=(278, 411),
            display_layer="CHA",
            comment=comment,
        ),
    }

    shot_root = service.shot_root(identity)
    package_root = shot_root / "publish" / "anim" / "package"
    version = _next_version(package_root)
    version_dir = package_root / version
    version_dir.mkdir(parents=True, exist_ok=True)
    shot_data = read_json(shot_root / "shot.json", {}) or {}
    cast_data = read_json(shot_root / "cast.json", {}) or {}
    write_json(version_dir / "shot.json", shot_data)
    write_json(version_dir / "cast.json", cast_data)
    manifest = {
        "package_type": "maya_reconstruction",
        "department": "anim",
        "episode": identity.episode,
        "sequence": identity.sequence,
        "shot": identity.shot,
        "version": version,
        "source_scene": service._relative_to_project(source_path),
        "frame_range": [278, 411],
        "animation": animation_publishes,
        "placements": service._relative_to_project(placement_publish),
        "setdress": service._relative_to_project(setdress_publish),
        "cameras": {
            key: service._relative_to_project(path)
            for key, path in camera_publishes.items()
        },
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "comment": comment,
    }
    write_json(version_dir / "build_manifest.json", manifest)
    write_json(
        version_dir / "publish.json",
        {
            "publish_type": "anim",
            "subset": "package",
            "version": version,
            "files": {
                "manifest": "build_manifest.json",
                "shot": "shot.json",
                "cast": "cast.json",
            },
            "source_scene": manifest["source_scene"],
            "comment": comment,
        },
    )
    _update_latest(package_root, version, "build_manifest.json")
    return version_dir / "build_manifest.json"


def build_c001_validation_scene(
    config_dir: str | Path,
    manifest_path: str | Path,
    output_scene: str | Path,
) -> Path:
    """Build a new Maya scene using only fixed publishes in the package manifest."""

    import maya.cmds as cmds

    from smartlib.dcc.maya.shot_builder import stage_shot_from_preview

    config = ProjectConfig(Path(config_dir))
    service = ShotManagerService(config)
    identity = ShotIdentity("ep02", "s027", "c001")
    manifest = read_json(manifest_path, {}) or {}
    shot_data = read_json(Path(manifest_path).parent / "shot.json", {}) or service.load_shot(identity)
    preview = service.build_preview(identity, department="anim")
    missing = [item for item in preview if item.required and item.status != "resolved"]
    if missing:
        details = ", ".join(f"{item.cast_key}: {item.message or item.status}" for item in missing)
        raise RuntimeError(f"Required cast could not be resolved: {details}")
    stage_shot_from_preview(
        [item for item in preview if item.status == "resolved"],
        shot_data,
        department="anim",
        project_root=service.paths.project_root,
    )

    placement_dir = service.paths.project_root / str(manifest.get("placements") or "")
    _apply_placement_publish(cmds, placement_dir)
    setdress_dir = service.paths.project_root / str(manifest.get("setdress") or "")
    _apply_setdress_publish(cmds, setdress_dir)
    for target, relative in (manifest.get("cameras") or {}).items():
        camera_dir = service.paths.project_root / str(relative)
        _import_camera_publish(cmds, camera_dir, str(target))
        _restore_display_layer(cmds, camera_dir)

    for target, target_namespace in (("DLI", "DLI_main"), ("JIN", "JIN_main")):
        relative = str((manifest.get("animation") or {}).get(target) or "")
        curve_path = service.paths.project_root / relative / "animation_manifest.json"
        apply_animation_curves_from_file(curve_path, namespace=target_namespace, clear_existing=True)

    frame_range = manifest.get("frame_range") or [278, 411]
    cmds.playbackOptions(
        minTime=float(frame_range[0]),
        maxTime=float(frame_range[1]),
        animationStartTime=float(frame_range[0]),
        animationEndTime=float(frame_range[1]),
    )
    cmds.currentTime(float(frame_range[0]), edit=True)
    output = Path(output_scene)
    output.parent.mkdir(parents=True, exist_ok=True)
    cmds.file(rename=str(output))
    file_type = "mayaBinary" if output.suffix.lower() == ".mb" else "mayaAscii"
    cmds.file(save=True, type=file_type, force=True)
    return output


def _publish_placement(
    service: ShotManagerService,
    identity: ShotIdentity,
    *,
    locator: str,
    member: str,
    asset: str,
    comment: str,
) -> Path:
    import maya.cmds as cmds

    if not cmds.objExists(locator):
        raise RuntimeError(f"Placement locator was not found: {locator}")
    base = service.shot_root(identity) / "publish" / "layout" / "placements"
    version = _next_version(base)
    version_dir = base / version
    version_dir.mkdir(parents=True, exist_ok=True)
    transform = {
        "translate": [float(v) for v in cmds.xform(locator, query=True, worldSpace=True, translation=True)],
        "rotate": [float(v) for v in cmds.xform(locator, query=True, worldSpace=True, rotation=True)],
        "scale": [float(v) for v in cmds.xform(locator, query=True, relative=True, scale=True)],
    }
    write_json(
        version_dir / "placements.json",
        {
            "placements": [
                {
                    "cast_id": member,
                    "locator": locator,
                    **transform,
                }
            ]
        },
    )
    write_json(
        version_dir / "placement_members.json",
        {
            "placements": [
                {
                    "locator": locator,
                    "member": member,
                    "asset": asset,
                    "attach_root": "",
                }
            ]
        },
    )
    write_json(
        version_dir / "publish.json",
        {
            "publish_type": "layout",
            "subset": "placements",
            "version": version,
            "files": {
                "placements": "placements.json",
                "placement_members": "placement_members.json",
            },
            "source_scene": service._relative_to_project(Path(cmds.file(query=True, sceneName=True) or "")),
            "comment": comment,
        },
    )
    _update_latest(base, version, "placements.json")
    return version_dir


def _publish_camera(
    service: ShotManagerService,
    identity: ShotIdentity,
    *,
    camera: str,
    publish_camera: str,
    target: str,
    resolution: tuple[int, int],
    frame_range: tuple[int, int],
    display_layer: str,
    comment: str,
) -> Path:
    import maya.cmds as cmds

    from smartlib.dcc.maya import smart_shot

    if not cmds.objExists(camera):
        raise RuntimeError(f"Camera was not found: {camera}")
    shapes = cmds.listRelatives(camera, shapes=True, type="camera", fullPath=True) or []
    if not shapes:
        raise RuntimeError(f"Camera shape was not found: {camera}")
    shape = shapes[0]
    base = service.shot_root(identity) / "publish" / "camera" / target / "main"
    version = _next_version(base)
    version_dir = base / version
    version_dir.mkdir(parents=True, exist_ok=True)
    baked_camera, baked_shape = _bake_world_camera(
        cmds,
        source_camera=camera,
        source_shape=shape,
        publish_camera=publish_camera,
        frame_range=frame_range,
    )
    smart_shot._export_camera_ma(cmds, baked_camera, version_dir / "camera.ma")
    usd_error = smart_shot._export_camera_usd(cmds, baked_camera, version_dir / "camera.usd")
    members = []
    if cmds.objExists(display_layer) and cmds.nodeType(display_layer) == "displayLayer":
        members = cmds.editDisplayLayerMembers(display_layer, query=True, fullNames=True) or []
    camera_data: dict[str, Any] = {
        "publish_type": "camera",
        "target": target,
        "subset": "main",
        "version": version,
        "camera": publish_camera,
        "camera_shape": baked_shape,
        "source_camera": camera,
        "resolution": [int(resolution[0]), int(resolution[1])],
        "frame_range": [int(frame_range[0]), int(frame_range[1])],
        "single_frame": frame_range[0] == frame_range[1],
        "display_layer": display_layer,
        "display_layer_members": list(members),
        "animation": smart_shot._camera_animation_samples(
            cmds, baked_camera, baked_shape, int(frame_range[0]), int(frame_range[1])
        ),
        "source_scene": service._relative_to_project(Path(cmds.file(query=True, sceneName=True) or "")),
    }
    if usd_error:
        camera_data["usd_export_error"] = usd_error
    write_json(version_dir / "camera.json", camera_data)
    cmds.delete(baked_camera)
    files = {"ma": "camera.ma", "json": "camera.json"}
    if (version_dir / "camera.usd").exists():
        files["usd"] = "camera.usd"
    write_json(
        version_dir / "publish.json",
        {
            "publish_type": "camera",
            "target": target,
            "subset": "main",
            "version": version,
            "files": files,
            "resolution": camera_data["resolution"],
            "frame_range": camera_data["frame_range"],
            "display_layer": display_layer,
            "source_scene": camera_data["source_scene"],
            "comment": comment,
        },
    )
    _update_latest(base, version, "camera.json")
    return version_dir


def _bake_world_camera(
    cmds: Any,
    *,
    source_camera: str,
    source_shape: str,
    publish_camera: str,
    frame_range: tuple[int, int],
) -> tuple[str, str]:
    if cmds.objExists(publish_camera):
        cmds.delete(publish_camera)
    baked_camera, baked_shape = cmds.camera(name=publish_camera)
    if baked_camera != publish_camera:
        baked_camera = cmds.rename(baked_camera, publish_camera)
    baked_shape = (cmds.listRelatives(baked_camera, shapes=True, fullPath=False) or [baked_shape])[0]
    expected_shape = f"{publish_camera}Shape"
    if baked_shape != expected_shape:
        baked_shape = cmds.rename(baked_shape, expected_shape)
    for attribute in (
        "focalLength",
        "fStop",
        "focusDistance",
        "nearClipPlane",
        "farClipPlane",
        "horizontalFilmAperture",
        "verticalFilmAperture",
        "filmFit",
        "lensSqueezeRatio",
    ):
        source_plug = f"{source_shape}.{attribute}"
        target_plug = f"{baked_shape}.{attribute}"
        if cmds.objExists(source_plug) and cmds.objExists(target_plug):
            try:
                cmds.setAttr(target_plug, cmds.getAttr(source_plug))
            except Exception:
                pass
    constraint = cmds.parentConstraint(source_camera, baked_camera, maintainOffset=False)[0]
    start, end = int(frame_range[0]), int(frame_range[1])
    cmds.bakeResults(
        baked_camera,
        simulation=True,
        time=(start, end),
        sampleBy=1,
        preserveOutsideKeys=False,
        sparseAnimCurveBake=False,
        disableImplicitControl=True,
        attribute=[
            "translateX", "translateY", "translateZ",
            "rotateX", "rotateY", "rotateZ",
        ],
    )
    cmds.delete(constraint)
    for attribute in ("focalLength", "fStop", "focusDistance"):
        source_plug = f"{source_shape}.{attribute}"
        target_plug = f"{baked_shape}.{attribute}"
        if cmds.objExists(source_plug) and cmds.objExists(target_plug):
            try:
                cmds.copyKey(source_plug, time=(start, end))
                cmds.pasteKey(target_plug, option="replaceCompletely")
            except Exception:
                pass
    return baked_camera, baked_shape


def _publish_setdress(
    service: ShotManagerService,
    identity: ShotIdentity,
    *,
    node: str,
    attributes: dict[str, float],
    comment: str,
) -> Path:
    import maya.cmds as cmds

    if not cmds.objExists(node):
        raise RuntimeError(f"Setdress node was not found: {node}")
    base = service.shot_root(identity) / "publish" / "layout" / "setdress"
    version = _next_version(base)
    version_dir = base / version
    version_dir.mkdir(parents=True, exist_ok=True)
    edits = []
    for attribute, value in attributes.items():
        plug = f"{node}.{attribute}"
        if not cmds.objExists(plug):
            raise RuntimeError(f"Setdress attribute was not found: {plug}")
        edits.append(
            {
                "node": node,
                "attribute": attribute,
                "value": float(value),
            }
        )
    write_json(
        version_dir / "setdress.json",
        {
            "publish_type": "layout",
            "subset": "setdress",
            "version": version,
            "edits": edits,
        },
    )
    write_json(
        version_dir / "publish.json",
        {
            "publish_type": "layout",
            "subset": "setdress",
            "version": version,
            "files": {"setdress": "setdress.json"},
            "source_scene": service._relative_to_project(
                Path(cmds.file(query=True, sceneName=True) or "")
            ),
            "comment": comment,
        },
    )
    _update_latest(base, version, "setdress.json")
    return version_dir


def _apply_setdress_publish(cmds: Any, version_dir: Path) -> None:
    data = read_json(version_dir / "setdress.json", {}) or {}
    namespace_map = {
        "DLI": "DLI_main",
        "JIN": "JIN_main",
        "DeleinChair": "DeleinChair_main",
        "DeleinRoomB": "DeleinRoomB_main",
    }
    for edit in data.get("edits") or []:
        node = str(edit.get("node") or "")
        if ":" in node:
            source_namespace, leaf = node.split(":", 1)
            node = f"{namespace_map.get(source_namespace, source_namespace)}:{leaf}"
        attribute = str(edit.get("attribute") or "")
        plug = f"{node}.{attribute}"
        if not cmds.objExists(plug):
            raise RuntimeError(f"Setdress build target was not found: {plug}")
        if cmds.getAttr(plug, lock=True):
            cmds.setAttr(plug, lock=False)
        cmds.setAttr(plug, float(edit.get("value") or 0.0))


def _apply_placement_publish(cmds: Any, version_dir: Path) -> None:
    placements = read_json(version_dir / "placements.json", {}) or {}
    members = read_json(version_dir / "placement_members.json", {}) or {}
    placement_by_locator = {
        str(item.get("locator") or ""): item
        for item in placements.get("placements") or []
    }
    for link in members.get("placements") or []:
        locator = str(link.get("locator") or "")
        member = str(link.get("member") or "")
        transform = placement_by_locator.get(locator) or {}
        node = locator if cmds.objExists(locator) else cmds.spaceLocator(name=locator)[0]
        root = _namespace_root_transform(cmds, member)
        if root:
            if cmds.referenceQuery(root, isNodeReferenced=True):
                reference_node = cmds.referenceQuery(root, referenceNode=True)
                reference_path = cmds.referenceQuery(
                    reference_node,
                    filename=True,
                    withoutCopyNumber=True,
                )
                cmds.file(removeReference=True, referenceNode=reference_node)
                if cmds.objExists(node):
                    cmds.delete(node)
                cmds.file(
                    reference_path,
                    reference=True,
                    namespace=member,
                    groupReference=True,
                    groupName=locator,
                    mergeNamespacesOnClash=True,
                    ignoreVersion=True,
                    options="v=0;",
                )
                node = locator
                root = _namespace_root_transform(cmds, member)
        layout_group = _ensure_transform(cmds, "layout_grp")
        current_parent = cmds.listRelatives(node, parent=True, fullPath=True) or []
        if not current_parent or current_parent[0].rsplit("|", 1)[-1] != layout_group:
            node = cmds.parent(node, layout_group, absolute=True)[0]
            root = _namespace_root_transform(cmds, member)
        cmds.xform(node, worldSpace=True, translation=transform.get("translate") or [0, 0, 0])
        cmds.xform(node, worldSpace=True, rotation=transform.get("rotate") or [0, 0, 0])
        cmds.xform(node, relative=True, scale=transform.get("scale") or [1, 1, 1])
        if root and not cmds.referenceQuery(root, isNodeReferenced=True):
            cmds.xform(
                root,
                worldSpace=True,
                translation=[0, 0, 0],
            )
            cmds.xform(
                root,
                worldSpace=True,
                rotation=[0, 0, 0],
            )


def _namespace_root_transform(cmds: Any, namespace: str) -> str:
    """Return the shallowest transform belonging to a referenced cast namespace."""

    transforms = cmds.ls(f"{namespace}:*", type="transform", long=True) or []
    if not transforms:
        return ""
    transforms.sort(key=lambda path: (path.count("|"), len(path)))
    return str(transforms[0])


def _import_camera_publish(cmds: Any, version_dir: Path, temporary_namespace: str) -> None:
    camera_scene = version_dir / "camera.ma"
    if not camera_scene.exists():
        return
    namespace = f"__smartCamera_{temporary_namespace}"
    cameras_before = set(cmds.ls(type="camera", long=True) or [])
    cmds.file(
        str(camera_scene),
        i=True,
        namespace=namespace,
        mergeNamespacesOnClash=False,
        ignoreVersion=True,
        options="v=0;",
    )
    camera_data = read_json(version_dir / "camera.json", {}) or {}
    camera_name = str(camera_data.get("camera") or "")
    cameras_after = set(cmds.ls(type="camera", long=True) or [])
    imported_shapes = sorted(cameras_after - cameras_before)
    imported_transforms = []
    for shape in imported_shapes:
        imported_transforms.extend(
            cmds.listRelatives(shape, parent=True, fullPath=True) or []
        )
    if camera_name and imported_transforms:
        camera = imported_transforms[0]
        if not cmds.objExists(camera_name):
            camera = cmds.rename(camera, camera_name)
        shots_group = _ensure_transform(cmds, "shots_grp")
        current_parent = cmds.listRelatives(camera, parent=True, fullPath=True) or []
        if not current_parent or current_parent[0].rsplit("|", 1)[-1] != shots_group:
            cmds.parent(camera, shots_group, absolute=True)


def _ensure_transform(cmds: Any, name: str) -> str:
    if cmds.objExists(name):
        if cmds.nodeType(name) != "transform":
            raise RuntimeError(f"Required build group is not a transform: {name}")
        return name
    return str(cmds.createNode("transform", name=name))


def _restore_display_layer(cmds: Any, version_dir: Path) -> None:
    camera_data = read_json(version_dir / "camera.json", {}) or {}
    layer_name = str(camera_data.get("display_layer") or "")
    if not layer_name:
        return
    if cmds.objExists(layer_name):
        if cmds.nodeType(layer_name) != "displayLayer":
            return
        layer = layer_name
    else:
        layer = cmds.createDisplayLayer(name=layer_name, empty=True)

    namespace_map = {
        "DLI": "DLI_main",
        "JIN": "JIN_main",
        "DeleinChair": "DeleinChair_main",
        "DeleinRoomB": "DeleinRoomB_main",
    }
    resolved_roots = []
    source_namespaces = set()
    for source_member in camera_data.get("display_layer_members") or []:
        member = str(source_member)
        for source_namespace in namespace_map:
            if f"|{source_namespace}:" in member:
                source_namespaces.add(source_namespace)
                break
    for source_namespace in sorted(source_namespaces):
        root = _namespace_root_transform(cmds, namespace_map[source_namespace])
        if root:
            resolved_roots.append(root)
    if resolved_roots:
        cmds.editDisplayLayerMembers(layer, resolved_roots, noRecurse=False)


def _next_version(base: Path) -> str:
    versions = [
        int(path.name[1:])
        for path in base.glob("v*")
        if path.is_dir() and path.name[1:].isdigit()
    ] if base.exists() else []
    return f"v{(max(versions) if versions else 0) + 1:03d}"


def _update_latest(base: Path, version: str, filename: str) -> None:
    write_json(base / "latest.json", {"version": version, "path": f"{version}/{filename}"})
    rows = read_json(base / "versions.json", []) or []
    next_rows = []
    for row in rows:
        if isinstance(row, dict) and row.get("version") != version:
            next_rows.append({**row, "status": "approved"})
    next_rows.append({"version": version, "status": "latest"})
    write_json(base / "versions.json", next_rows)

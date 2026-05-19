from __future__ import annotations

from pathlib import Path
from typing import Iterable


def build_shot_from_preview(preview_items: Iterable, shot_data: dict | None = None) -> list[str]:
    """Reference resolved cast publishes into the current Maya scene."""

    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Build Shot From Cast is available inside Maya.") from exc

    referenced = []
    for item in preview_items:
        if getattr(item, "status", "") != "resolved":
            continue
        publish_path = Path(getattr(item, "publish_path", ""))
        if not publish_path.exists():
            continue
        namespace = _clean_namespace(getattr(item, "namespace", "") or getattr(item, "cast_key", "") or publish_path.stem)
        _reference_file(cmds, publish_path, namespace)
        referenced.append(str(publish_path))

    _apply_shot_timing(cmds, shot_data or {})
    return referenced


def save_current_scene(path: str | Path, shot_data: dict | None = None) -> dict:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Shot work scene save is available inside Maya.") from exc
    from smartlib.dcc.maya.scene_info import collect_scene_info

    scene_path = Path(path)
    scene_path.parent.mkdir(parents=True, exist_ok=True)
    _apply_shot_timing(cmds, shot_data or {})
    scene_type = "mayaBinary" if scene_path.suffix.lower() == ".mb" else "mayaAscii"
    cmds.file(rename=str(scene_path))
    cmds.file(save=True, type=scene_type)
    return collect_scene_info(cmds)


def open_work_scene(path: str | Path, shot_data: dict | None = None) -> None:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Open Work Scene is available inside Maya.") from exc

    scene_path = Path(path)
    if not scene_path.exists():
        raise FileNotFoundError(f"Work scene was not found: {scene_path}")

    if cmds.file(query=True, modified=True):
        result = cmds.confirmDialog(
            title="Open Work Scene",
            message="Current scene has unsaved changes. Open selected work scene?",
            button=["Open", "Cancel"],
            defaultButton="Open",
            cancelButton="Cancel",
            dismissString="Cancel",
        )
        if result != "Open":
            return

    cmds.file(str(scene_path), open=True, force=True)
    _apply_shot_timing(cmds, shot_data or {})


def create_review_display_layers(cast_data: dict) -> dict[str, int]:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Review layer creation is available inside Maya.") from exc

    cast = cast_data.get("cast") or {}
    review_layers = cast_data.get("review_layers") or {}
    created = {}
    for layer_name, layer in review_layers.items():
        layer_node = f"review_{layer_name}"
        if cmds.objExists(layer_node):
            cmds.delete(layer_node)
        cmds.createDisplayLayer(name=layer_node, empty=True)
        members = []
        for cast_key in layer.get("members", []):
            entry = cast.get(cast_key) or {}
            candidates = [
                str(entry.get("namespace") or ""),
                str(cast_key or ""),
                str(entry.get("asset") or ""),
            ]
            members.extend(_nodes_for_cast_entry(cmds, candidates))
        members = _unique_nodes(members)
        if members:
            cmds.editDisplayLayerMembers(layer_node, members, noRecurse=True)
        created[layer_node] = len(members)
    return created


def _nodes_for_cast_entry(cmds, candidates: list[str]) -> list[str]:
    for candidate in candidates:
        nodes = _namespace_nodes(cmds, candidate)
        if nodes:
            return nodes
    return []


def _namespace_nodes(cmds, namespace: str) -> list[str]:
    namespace = namespace.strip(":")
    if not namespace:
        return []

    for resolved_namespace in _matching_namespaces(cmds, namespace):
        nodes = _top_transforms_in_namespace(cmds, resolved_namespace)
        if nodes:
            return nodes
    return []


def _matching_namespaces(cmds, namespace: str) -> list[str]:
    exact = namespace.strip(":")
    matches = []
    if cmds.namespace(exists=exact):
        matches.append(exact)

    try:
        all_namespaces = cmds.namespaceInfo(":", listOnlyNamespaces=True, recurse=True) or []
    except RuntimeError:
        all_namespaces = []

    for item in all_namespaces:
        candidate = str(item).strip(":")
        leaf = candidate.rsplit(":", 1)[-1]
        if candidate == exact or leaf == exact or leaf.startswith(exact):
            if candidate not in matches:
                matches.append(candidate)
    return matches


def _top_transforms_in_namespace(cmds, namespace: str) -> list[str]:
    transforms = cmds.ls(f"{namespace}:*", type="transform", long=True) or []
    if not transforms:
        return []
    transform_set = set(transforms)
    roots = []
    for node in transforms:
        parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
        if not parents or parents[0] not in transform_set:
            roots.append(node)
    return roots or transforms


def _unique_nodes(nodes: list[str]) -> list[str]:
    unique = []
    seen = set()
    for node in nodes:
        if node in seen:
            continue
        seen.add(node)
        unique.append(node)
    return unique


def _reference_file(cmds, path: Path, namespace: str) -> None:
    namespace = _unique_namespace(cmds, namespace)
    cmds.file(
        str(path),
        reference=True,
        namespace=namespace,
        ignoreVersion=True,
        mergeNamespacesOnClash=False,
        options="v=0;",
    )


def _unique_namespace(cmds, namespace: str) -> str:
    namespace = _clean_namespace(namespace)
    if not cmds.namespace(exists=namespace):
        return namespace
    index = 1
    while cmds.namespace(exists=f"{namespace}{index}"):
        index += 1
    return f"{namespace}{index}"


def _clean_namespace(namespace: str) -> str:
    cleaned = "".join(char if char.isalnum() or char == "_" else "_" for char in namespace)
    if not cleaned:
        return "asset"
    if cleaned[0].isdigit():
        cleaned = f"n_{cleaned}"
    return cleaned


def _apply_shot_timing(cmds, shot_data: dict) -> None:
    editorial = shot_data.get("editorial") or {}
    cut_in = editorial.get("cut_in")
    cut_out = editorial.get("cut_out")
    fps = editorial.get("fps")
    if fps:
        fps_map = {
            24: "film",
            25: "pal",
            30: "ntsc",
            48: "show",
            50: "palf",
            60: "ntscf",
        }
        cmds.currentUnit(time=fps_map.get(int(fps), f"{int(fps)}fps"))
    if cut_in is not None and cut_out is not None:
        cmds.playbackOptions(minTime=float(cut_in), animationStartTime=float(cut_in))
        cmds.playbackOptions(maxTime=float(cut_out), animationEndTime=float(cut_out))

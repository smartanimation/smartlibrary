from __future__ import annotations

import json
import os
import re
from pathlib import Path

from smartlib.preflight import PreflightContext


class MayaPreflightAdapter:
    def __init__(self, cmds_module=None):
        if cmds_module is None:
            import maya.cmds as cmds_module
        self.cmds = cmds_module

    def scene_path(self) -> str:
        return str(self.cmds.file(query=True, sceneName=True) or "")

    def scene_modified(self) -> bool:
        return bool(self.cmds.file(query=True, modified=True))

    def missing_references(self) -> list[str]:
        missing = []
        for reference in self.cmds.file(query=True, reference=True) or []:
            clean = str(reference).split("{")[0]
            if not Path(clean).exists():
                missing.append(clean)
        return missing

    def unknown_nodes(self) -> list[str]:
        return list(self.cmds.ls(type="unknown", long=True) or [])

    def non_manifold_meshes(self) -> list[str]:
        meshes = []
        for shape in self.cmds.ls(type="mesh", long=True, noIntermediate=True) or []:
            try:
                components = self.cmds.polyInfo(shape, nonManifoldEdges=True) or []
            except RuntimeError:
                components = []
            if components:
                meshes.append(str(shape))
        return meshes

    def asset_roots(self) -> list[str]:
        defaults = {"persp", "top", "front", "side"}
        candidates = [
            node for node in self.cmds.ls(assemblies=True, type="transform", long=True) or []
            if str(node).rsplit("|", 1)[-1].split(":")[-1] not in defaults
        ]
        official = [
            node for node in candidates
            if str(node).rsplit("|", 1)[-1].split(":")[-1].casefold() == "root"
        ]
        return official or candidates

    def renderable_cameras(self) -> list[str]:
        cameras = []
        for shape in self.cmds.ls(type="camera", long=True) or []:
            try:
                if not self.cmds.getAttr(f"{shape}.renderable"):
                    continue
            except RuntimeError:
                continue
            parents = self.cmds.listRelatives(shape, parent=True, fullPath=True) or [shape]
            cameras.append(str(parents[0]))
        return cameras

    def frame_range(self) -> tuple[int, int]:
        return (
            int(round(self.cmds.playbackOptions(query=True, minTime=True))),
            int(round(self.cmds.playbackOptions(query=True, maxTime=True))),
        )

    def animation_curve_count(self) -> int:
        return len(self.cmds.ls(type="animCurve") or [])

    def resolution(self) -> tuple[int, int]:
        return (
            int(self.cmds.getAttr("defaultResolution.width")),
            int(self.cmds.getAttr("defaultResolution.height")),
        )

    def non_horizontal_cameras(self) -> list[str]:
        invalid = []
        for camera in self.renderable_cameras():
            shapes = self.cmds.listRelatives(camera, shapes=True, type="camera", fullPath=True) or []
            for shape in shapes:
                try:
                    if int(self.cmds.getAttr(f"{shape}.filmFit")) != 1:
                        invalid.append(str(camera))
                except (RuntimeError, TypeError, ValueError):
                    invalid.append(str(camera))
        return sorted(set(invalid), key=str.lower)

    def object_set_exists(self, name: str) -> bool:
        return bool(self.cmds.objExists(name) and self.cmds.nodeType(name) == "objectSet")

    def object_set_members(self, name: str) -> list[str]:
        return list(self.cmds.sets(name, query=True) or []) if self.object_set_exists(name) else []

    def reference_records(self) -> list[dict[str, str]]:
        rows = []
        for node in self.cmds.ls(type="reference") or []:
            if node == "sharedReferenceNode":
                continue
            try:
                path = str(self.cmds.referenceQuery(node, filename=True, withoutCopyNumber=True) or "")
                namespace = str(self.cmds.referenceQuery(node, namespace=True) or "").lstrip(":")
            except RuntimeError:
                continue
            rows.append({"node": str(node), "namespace": namespace, "path": path})
        return rows

    def missing_cast(self, context: PreflightContext) -> list[str]:
        records = {row["namespace"].casefold(): row for row in self.reference_records()}
        issues = []
        for key, entry in (context.metadata.get("cast") or {}).items():
            namespace = str(entry.get("namespace") or key)
            record = records.get(namespace.casefold())
            if not record:
                issues.append(f"{namespace}: reference not found")
            elif not Path(record["path"]).is_file():
                issues.append(f"{namespace}: {record['path']}")
        return issues

    def cast_version_issues(self, context: PreflightContext) -> list[str]:
        records = {row["namespace"].casefold(): row for row in self.reference_records()}
        issues = []
        for key, entry in (context.metadata.get("cast") or {}).items():
            namespace = str(entry.get("namespace") or key)
            record = records.get(namespace.casefold())
            if not record:
                continue
            requested = str(entry.get("asset_publish") or "approved").lower()
            actual, version_dir = _reference_version(record["path"])
            expected = _resolved_version(version_dir.parent if version_dir else None, requested)
            if expected and actual.lower() != expected.lower():
                issues.append(f"{namespace}: {actual or 'unknown'} (expected {expected})")
        return issues

    def duplicate_namespaces(self, context: PreflightContext) -> list[str]:
        names = [
            str(entry.get("namespace") or key)
            for key, entry in (context.metadata.get("cast") or {}).items()
        ]
        names.extend(row["namespace"] for row in self.reference_records())
        counts = {}
        spellings = {}
        for name in names:
            folded = name.casefold()
            counts[folded] = counts.get(folded, 0) + 1
            spellings.setdefault(folded, name)
        # A namespace normally appears once in Cast and once in the scene.
        return sorted(spellings[key] for key, count in counts.items() if count > 2)

    def select_nodes(self, nodes) -> None:
        existing = [node for node in nodes if self.cmds.objExists(node)]
        if existing:
            self.cmds.select(existing, replace=True)


def resolve_context(cmds_module=None) -> PreflightContext:
    adapter = MayaPreflightAdapter(cmds_module)
    path = adapter.scene_path().replace("\\", "/")
    forced = os.environ.get("SMART_PREFLIGHT_CONTEXT", "").strip().lower()
    asset_match = re.search(r"/assets/(.+)", path, re.IGNORECASE)
    shot_match = re.search(r"/shots/(?:([^/]+)/)?([^/]+)/([^/]+)", path, re.IGNORECASE)
    version_match = re.search(r"[._-](v\d+)(?:[._-]|$)", Path(path).stem, re.IGNORECASE)
    version = version_match.group(1) if version_match else ""
    if forced == "asset" or (asset_match and forced != "shot"):
        entity, task = _asset_identity(path)
        return PreflightContext(
            kind="asset",
            entity=entity,
            task=task,
            version=version,
            scene_path=path,
        )
    entity = shot_match.group(3) if shot_match else Path(path).stem
    task = _task_from_scene(path)
    metadata = {"sequence": shot_match.group(2) if shot_match else ""}
    metadata.update(_shot_metadata(path))
    return PreflightContext(
        kind="shot",
        entity=entity,
        task=task,
        version=version,
        scene_path=path,
        metadata=metadata,
    )


def _task_from_scene(path: str) -> str:
    lowered = path.lower()
    for task in ("layout", "animation", "anim", "lighting", "fx", "model", "rig", "lookdev"):
        if re.search(rf"(?:/|_|-){task}(?:/|_|-|\.|$)", lowered):
            return "Animation" if task == "anim" else task.title()
    return ""


def _asset_identity(path: str) -> tuple[str, str]:
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    lowered = [part.lower() for part in parts]
    try:
        start = lowered.index("assets") + 1
    except ValueError:
        return Path(path).stem, _task_from_scene(path)
    tasks = {"model", "rig", "look", "lookdev", "groom", "texture", "surfacing"}
    for index in range(start, len(parts)):
        if lowered[index] in tasks:
            entity = parts[index - 1] if index > start else Path(path).stem
            task = "Lookdev" if lowered[index] == "look" else parts[index].title()
            return entity, task
    candidates = parts[start:]
    if len(candidates) >= 2:
        return candidates[1], ""
    return (candidates[0] if candidates else Path(path).stem), ""


def _shot_metadata(scene_path: str) -> dict:
    current = Path(scene_path).parent
    for parent in (current, *current.parents):
        cast_path = parent / "cast.json"
        shot_path = parent / "shot.json"
        if not cast_path.is_file() and not shot_path.is_file():
            continue
        cast_data = _read_json(cast_path)
        shot_data = _read_json(shot_path)
        metadata = {"cast": cast_data.get("cast") or {}}
        editorial = shot_data.get("editorial") or {}
        start = editorial.get("cut_in", shot_data.get("start"))
        end = editorial.get("cut_out", shot_data.get("end"))
        if start is not None and end is not None:
            metadata["frame_range"] = [int(start), int(end)]
        resolution = shot_data.get("resolution")
        if isinstance(resolution, dict):
            resolution = [resolution.get("width"), resolution.get("height")]
        if isinstance(resolution, (list, tuple)) and len(resolution) == 2 and all(resolution):
            metadata["resolution"] = [int(resolution[0]), int(resolution[1])]
        return metadata
    return {"cast": {}}


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _reference_version(path: str) -> tuple[str, Path | None]:
    source = Path(path)
    for parent in (source.parent, *source.parents):
        if re.fullmatch(r"v\d+", parent.name, re.IGNORECASE):
            return parent.name, parent
    return "", None


def _resolved_version(context_root: Path | None, requested: str) -> str:
    if re.fullmatch(r"v\d+", requested, re.IGNORECASE):
        return requested
    if context_root is None:
        return ""
    if requested == "latest":
        return str(_read_json(context_root / "latest.json").get("version") or "")
    if requested in {"approved", "released", "stable"}:
        data = _read_json_any(context_root / "versions.json", [])
        matches = [
            str(row.get("version") or "")
            for row in data if isinstance(row, dict)
            and str(row.get("status") or "").lower() in {requested, "latest"}
        ] if isinstance(data, list) else []
        return matches[-1] if matches else ""
    return ""


def _read_json_any(path: Path, default):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


_WINDOW = None


def show_smart_preflight():
    global _WINDOW
    from smartlib.apps.smart_preflight.window import SmartPreflightWindow

    adapter = MayaPreflightAdapter()
    _WINDOW = SmartPreflightWindow(
        adapter=adapter,
        context=resolve_context(),
        parent=_maya_main_window(),
    )
    _WINDOW.show()
    _WINDOW.raise_()
    return _WINDOW


def _maya_main_window():
    from maya import OpenMayaUI
    from smartlib.apps.smart_preflight.window import QtWidgets

    pointer = OpenMayaUI.MQtUtil.mainWindow()
    if not pointer:
        return None
    try:
        from shiboken6 import wrapInstance
    except ImportError:
        from shiboken2 import wrapInstance
    return wrapInstance(int(pointer), QtWidgets.QWidget)

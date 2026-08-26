from __future__ import annotations

import json
import os
import re
import glob
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

    def maya_version(self) -> str:
        try:
            installed = str(self.cmds.about(installedVersion=True) or "").strip()
            if installed:
                match = re.search(r"\d{4}(?:\.\d+)?", installed)
                return match.group(0) if match else installed
        except (RuntimeError, TypeError):
            pass
        return str(self.cmds.about(version=True))

    def linear_unit(self) -> str:
        return str(self.cmds.currentUnit(query=True, linear=True))

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

    def non_default_cameras(self) -> list[str]:
        cameras = []
        for shape in self.cmds.ls(type="camera", long=True) or []:
            parents = self.cmds.listRelatives(shape, parent=True, fullPath=True) or []
            transform = str(parents[0]) if parents else str(shape)
            if not self._is_default_camera(transform):
                cameras.append(transform)
        return sorted(set(cameras), key=str.casefold)

    def nonempty_display_layers(self) -> list[str]:
        issues = []
        for layer in self.cmds.ls(type="displayLayer") or []:
            if str(layer).split(":")[-1] in {"defaultLayer", "defaultDisplayLayer"}:
                continue
            members = self.cmds.editDisplayLayerMembers(layer, query=True, fullNames=True) or []
            issues.extend(f"{layer}: {member}" for member in members)
        return issues

    def publish_geometry_visibility_issues(self, set_name: str) -> list[str]:
        issues = []
        for shape in self._set_mesh_shapes(set_name):
            reason = self._hidden_reason(shape)
            if reason:
                issues.append(f"{shape}: {reason}")
        return sorted(set(issues), key=str.casefold)

    def _set_mesh_shapes(self, set_name: str) -> list[str]:
        shapes = set()
        for member in self.cmds.sets(set_name, query=True) or []:
            node = str(member).split(".", 1)[0]
            if not self.cmds.objExists(node):
                continue
            try:
                node_type = self.cmds.nodeType(node)
            except RuntimeError:
                continue
            if node_type == "mesh":
                shapes.add(node)
                continue
            direct = self.cmds.listRelatives(
                node, shapes=True, type="mesh", fullPath=True, noIntermediate=True
            ) or []
            # Do not expand every descendant of a group member. Rig setup and
            # blendShape targets often live below the same hierarchy without
            # being explicit cache_geo_set publish members.
            shapes.update(str(shape) for shape in direct)
        return sorted(shapes, key=str.casefold)

    def _hidden_reason(self, node: str) -> str:
        current = node
        while current:
            for attribute in ("visibility", "lodVisibility"):
                plug = f"{current}.{attribute}"
                if self.cmds.objExists(plug):
                    try:
                        if not bool(self.cmds.getAttr(plug)):
                            return f"{plug} is off"
                    except RuntimeError:
                        pass
            override = f"{current}.overrideEnabled"
            override_visibility = f"{current}.overrideVisibility"
            if self.cmds.objExists(override) and self.cmds.objExists(override_visibility):
                try:
                    if self.cmds.getAttr(override) and not self.cmds.getAttr(override_visibility):
                        return f"{override_visibility} is off"
                except RuntimeError:
                    pass
            parents = self.cmds.listRelatives(current, parent=True, fullPath=True) or []
            current = str(parents[0]) if parents else ""
        for layer in self.cmds.listConnections(node, type="displayLayer") or []:
            if str(layer).split(":")[-1] in {"defaultLayer", "defaultDisplayLayer"}:
                continue
            plug = f"{layer}.visibility"
            try:
                if self.cmds.objExists(plug) and not bool(self.cmds.getAttr(plug)):
                    return f"hidden by Display Layer {layer}"
            except RuntimeError:
                pass
        return ""

    def asset_lights(self) -> list[str]:
        return sorted({str(node) for node in self.cmds.ls(lights=True, long=True) or []}, key=str.casefold)

    def meshes_without_uvs(self, set_name: str | None = None) -> list[str]:
        missing = []
        shapes = (
            self._set_mesh_shapes(set_name)
            if set_name
            else self.cmds.ls(type="mesh", long=True, noIntermediate=True) or []
        )
        for shape in shapes:
            try:
                count = int(self.cmds.polyEvaluate(shape, uvcoord=True) or 0)
            except (RuntimeError, TypeError, ValueError):
                count = 0
            if count <= 0:
                missing.append(str(shape))
        return sorted(set(missing), key=str.casefold)

    def texture_records(self) -> list[dict[str, str]]:
        rows = []
        for node in self.cmds.ls(type="file") or []:
            try:
                path = str(self.cmds.getAttr(f"{node}.fileTextureName") or "").strip()
            except RuntimeError:
                path = ""
            if path and not Path(os.path.expandvars(path)).is_absolute():
                try:
                    path = str(self.cmds.workspace(expandName=path) or path)
                except RuntimeError:
                    pass
            rows.append({"node": str(node), "path": path})
        return rows

    def missing_texture_nodes(self) -> list[str]:
        return [row["node"] for row in self.texture_records() if not _texture_path_exists(row["path"])]

    def local_texture_nodes(self) -> list[str]:
        markers = ("/users/", "/documents and settings/", "/desktop/", "/downloads/", "/appdata/local/temp/")
        result = []
        for row in self.texture_records():
            normalized = os.path.expandvars(row["path"]).replace("\\", "/").casefold()
            if any(marker in normalized for marker in markers):
                result.append(row["node"])
        return result

    def outside_project_texture_nodes(self, project_root: str) -> list[str]:
        if not project_root:
            return []
        root = Path(os.path.expandvars(project_root)).resolve()
        result = []
        for row in self.texture_records():
            expanded = os.path.expandvars(row["path"])
            if not expanded or not Path(expanded).is_absolute():
                continue
            candidate = Path(expanded.replace("<UDIM>", "1001")).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                result.append(row["node"])
        return result

    def invalid_node_names(self, forbidden) -> list[str]:
        forbidden = tuple(str(value) for value in forbidden if str(value))
        invalid = []
        for node in self.cmds.ls(long=True) or []:
            leaf = str(node).rsplit("|", 1)[-1].rsplit(":", 1)[-1]
            if (
                any(char.isspace() for char in leaf)
                or any(ord(char) > 127 for char in leaf)
                or any(token in leaf for token in forbidden)
                or not re.fullmatch(r"[A-Za-z0-9_]+", leaf)
            ):
                invalid.append(str(node))
        return sorted(set(invalid), key=str.casefold)

    def asset_namespaces(self) -> list[str]:
        namespaces = self.cmds.namespaceInfo(listOnlyNamespaces=True, recurse=True) or []
        return sorted({
            str(namespace) for namespace in namespaces
            if str(namespace).lstrip(":") not in {"UI", "shared"}
        }, key=str.casefold)

    def asset_roots(self) -> list[str]:
        defaults = {"persp", "top", "front", "side"}
        candidates = sorted({
            str(node) for node in self.cmds.ls(
                assemblies=True, type="transform", long=True
            ) or []
            if str(node).rsplit("|", 1)[-1].split(":")[-1] not in defaults
        }, key=str.casefold)
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
        for camera in self.preflight_cameras():
            shapes = self.cmds.listRelatives(camera, shapes=True, type="camera", fullPath=True) or []
            for shape in shapes:
                try:
                    if int(self.cmds.getAttr(f"{shape}.filmFit")) != 1:
                        invalid.append(str(camera))
                except (RuntimeError, TypeError, ValueError):
                    invalid.append(str(camera))
        return sorted(set(invalid), key=str.lower)

    def preflight_cameras(self) -> list[str]:
        cameras = set(self.renderable_cameras())
        focused_panel = self.cmds.getPanel(withFocus=True)
        panels = []
        if focused_panel and self.cmds.getPanel(typeOf=focused_panel) == "modelPanel":
            panels.append(focused_panel)
        for panel in panels:
            try:
                camera = self.cmds.modelEditor(panel, query=True, camera=True)
            except RuntimeError:
                continue
            transform = self._camera_transform(camera)
            if transform and not self._is_default_camera(transform):
                cameras.add(transform)
        for node in self.cmds.ls(selection=True, long=True) or []:
            transform = self._camera_transform(node)
            if transform and not self._is_default_camera(transform):
                cameras.add(transform)
        return sorted(cameras, key=str.casefold)

    @staticmethod
    def _is_default_camera(camera: str) -> bool:
        leaf = str(camera).rsplit("|", 1)[-1].split(":")[-1]
        return leaf in {"persp", "top", "front", "side"}

    def _camera_transform(self, node: str) -> str:
        if not node or not self.cmds.objExists(node):
            return ""
        try:
            node_type = self.cmds.nodeType(node)
        except RuntimeError:
            return ""
        if node_type == "camera":
            parents = self.cmds.listRelatives(node, parent=True, fullPath=True) or []
            return str(parents[0]) if parents else ""
        shapes = self.cmds.listRelatives(node, shapes=True, type="camera", fullPath=True) or []
        return str(node) if shapes else ""

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

    def outside_project_references(self, project_root: str) -> list[str]:
        if not project_root:
            return []
        root = Path(os.path.expandvars(project_root)).resolve()
        issues = []
        for row in self.reference_records():
            path = Path(os.path.expandvars(row["path"])).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                issues.append(f"{row['namespace'] or row['node']}: {row['path']}")
        return issues

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
        metadata = _asset_metadata(path)
        data_root = _asset_data_root(path)
        if data_root:
            metadata["data_root"] = data_root.as_posix()
        metadata["policy"] = _preflight_policy()
        return PreflightContext(
            kind="asset",
            entity=str(metadata.get("asset") or entity),
            task=task,
            version=version,
            scene_path=path,
            metadata=metadata,
        )
    entity = shot_match.group(3) if shot_match else Path(path).stem
    task = _task_from_scene(path)
    metadata = {
        "episode": shot_match.group(1) if shot_match else "",
        "sequence": shot_match.group(2) if shot_match else "",
        "policy": _preflight_policy(),
    }
    metadata.update(_shot_metadata(path))
    data_root = _shot_data_root(path)
    if data_root:
        metadata["data_root"] = data_root.as_posix()
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
    # Canonical layout: assets/{category}/{group}/{asset}/{variant}/...
    # Resolve this before looking for task folders because paths commonly contain
    # .../{variant}/work/rig/... and "work" is not the Asset name.
    structural = {"work", "publish", "data", "model", "rig", "look", "lookdev", "groom", "texture", "surfacing"}
    if len(parts) >= start + 4 and lowered[start + 3] not in structural:
        return parts[start + 2], _task_from_scene(path)
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


def _asset_metadata(scene_path: str) -> dict:
    current = Path(scene_path).parent
    for parent in (current, *current.parents):
        data = _read_json(parent / "asset.json")
        if data:
            return data
    return {}


def _asset_data_root(scene_path: str) -> Path | None:
    asset_root = _descriptor_root(scene_path, "asset.json")
    if asset_root is not None:
        try:
            relative = Path(scene_path).resolve().relative_to(asset_root.resolve())
        except (OSError, ValueError):
            relative = None
        if relative and relative.parts:
            return asset_root / relative.parts[0] / "data"
    parts = Path(scene_path).parts
    lowered = [part.lower() for part in parts]
    try:
        start = lowered.index("assets") + 1
    except ValueError:
        return None
    if len(parts) < start + 4:
        return None
    structural = {"work", "publish", "data", "model", "rig", "look", "lookdev", "groom", "texture", "surfacing"}
    if lowered[start + 3] in structural:
        return None
    return Path(*parts[: start + 4]) / "data"


def _shot_data_root(scene_path: str) -> Path | None:
    shot_root = _descriptor_root(scene_path, "shot.json")
    if shot_root:
        return shot_root / "data"
    parts = Path(scene_path).parts
    lowered = [part.lower() for part in parts]
    try:
        start = lowered.index("shots") + 1
    except ValueError:
        return None
    # Canonical layout: shots/{episode}/{sequence}/{shot}/...
    if len(parts) >= start + 3:
        return Path(*parts[: start + 3]) / "data"
    return None


def _descriptor_root(scene_path: str, filename: str) -> Path | None:
    current = Path(scene_path).parent
    for parent in (current, *current.parents):
        if (parent / filename).is_file():
            return parent
    return None


def _preflight_policy() -> dict:
    from smartlib.core.config_loader import ProjectConfig, default_config_dir

    config_dir = Path(os.environ.get("PROJECT_CONFIG_DIR") or default_config_dir())
    config = ProjectConfig(config_dir)
    data = dict(config.load("preflight.yml").get("preflight") or {})
    if config.project_root:
        data["project_root"] = config.project_root.as_posix()
    return data


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


def _texture_path_exists(path: str) -> bool:
    expanded = os.path.expandvars(str(path or "").strip())
    if not expanded:
        return False
    pattern = expanded.replace("\\", "/")
    pattern = re.sub(
        r"<UDIM>|%\(UDIM\)d",
        "[0-9][0-9][0-9][0-9]",
        pattern,
        flags=re.IGNORECASE,
    )
    pattern = re.sub(r"#+", lambda match: "[0-9]" * len(match.group(0)), pattern)
    pattern = re.sub(r"%0\dd", lambda match: "[0-9]" * int(match.group(0)[2]), pattern)
    return bool(glob.glob(pattern))


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

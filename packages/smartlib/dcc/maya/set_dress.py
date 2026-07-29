from __future__ import annotations

import json
import math
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


FORMAT = "smart-set-dress"
VERSION = 1
ATTRIBUTES = (
    "translateX", "translateY", "translateZ",
    "rotateX", "rotateY", "rotateZ",
    "scaleX", "scaleY", "scaleZ", "visibility",
)


@dataclass
class NodeState:
    node_id: str
    node: str
    values: dict[str, float | bool]


@dataclass
class Change:
    node_id: str
    node: str
    attribute: str
    before: float | bool
    after: float | bool


@dataclass
class SetDressLayer:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    name: str = "new_layer"
    scope: str = "shot"
    muted: bool = False
    changes: list[Change] = field(default_factory=list)


@dataclass
class SetDressPackage:
    layers: list[SetDressLayer] = field(default_factory=list)
    context: dict[str, str] = field(default_factory=dict)
    format: str = FORMAT
    version: int = VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SetDressPackage":
        if data.get("format") != FORMAT:
            raise ValueError("Not a Smart Set Dress package.")
        layers = []
        for raw_layer in data.get("layers") or []:
            changes = [Change(**item) for item in raw_layer.get("changes") or []]
            layers.append(SetDressLayer(
                id=str(raw_layer.get("id") or uuid.uuid4().hex),
                name=str(raw_layer.get("name") or "layer"),
                scope=str(raw_layer.get("scope") or "shot"),
                muted=bool(raw_layer.get("muted", False)),
                changes=changes,
            ))
        return cls(
            layers=layers,
            context={str(k): str(v) for k, v in (data.get("context") or {}).items()},
            format=FORMAT,
            version=int(data.get("version") or VERSION),
        )


def diff_states(
    before: Iterable[NodeState],
    after: Iterable[NodeState],
    *,
    tolerance: float = 1e-6,
) -> list[Change]:
    before_map = {item.node_id: item for item in before}
    after_map = {item.node_id: item for item in after}
    changes: list[Change] = []
    for node_id in sorted(before_map.keys() & after_map.keys()):
        old, new = before_map[node_id], after_map[node_id]
        for attribute in ATTRIBUTES:
            if attribute not in old.values or attribute not in new.values:
                continue
            if _different(old.values[attribute], new.values[attribute], tolerance):
                changes.append(Change(
                    node_id=node_id,
                    node=new.node or old.node,
                    attribute=attribute,
                    before=old.values[attribute],
                    after=new.values[attribute],
                ))
    return changes


def composed_values(layers: Iterable[SetDressLayer]) -> dict[tuple[str, str], Change]:
    """Compose top-first layers. Earlier layers have override priority."""
    result: dict[tuple[str, str], Change] = {}
    enabled = [layer for layer in layers if not layer.muted]
    for layer in reversed(enabled):
        for change in layer.changes:
            result[(change.node_id, change.attribute)] = change
    return result


def base_values(layers: Iterable[SetDressLayer]) -> dict[tuple[str, str], Change]:
    """Return the base under the stack; bottom layers are closest to scene base."""
    result: dict[tuple[str, str], Change] = {}
    for layer in reversed(list(layers)):
        for change in layer.changes:
            result.setdefault((change.node_id, change.attribute), change)
    return result


def save_package(package: SetDressPackage, path: str | os.PathLike[str]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(package.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.write("\n")
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return target


def load_package(path: str | os.PathLike[str]) -> SetDressPackage:
    with Path(path).open("r", encoding="utf-8") as stream:
        return SetDressPackage.from_dict(json.load(stream))


def suggested_path(scope: str, context: dict[str, str] | None = None) -> Path:
    context = context or {}
    explicit_shot_root = str(context.get("shot_root") or "").strip()
    package = _safe_name(context.get("package") or context.get("shot") or "main")
    if scope == "shot" and explicit_shot_root:
        return Path(explicit_shot_root) / "data" / "setdress" / f"{package}.setdress.json"
    root = Path(
        os.environ.get("SMART_SET_DRESS_ROOT")
        or os.environ.get("PROJECT_ROOT")
        or Path.cwd()
    )
    sequence = _safe_name(context.get("sequence") or "sequence")
    shot = _safe_name(context.get("shot") or Path(context.get("scene") or "untitled").stem)
    episode = _safe_name(context.get("episode") or "episode")
    if scope == "sequence":
        return root / "data" / "setdress" / "sequence" / f"{sequence}.setdress.json"
    return root / "shots" / episode / sequence / shot / "data" / "setdress" / f"{package}.setdress.json"


def capture_scene(selection_only: bool = True, cmds=None) -> list[NodeState]:
    cmds = cmds or _maya_cmds()
    nodes = _capture_nodes(cmds, selection_only)
    states = []
    for node in nodes:
        values = {}
        for attribute in ATTRIBUTES:
            plug = f"{node}.{attribute}"
            try:
                if cmds.objExists(plug):
                    values[attribute] = cmds.getAttr(plug)
            except Exception:
                continue
        if values:
            states.append(NodeState(_node_id(cmds, node), node, values))
    return states


def apply_changes(changes: Iterable[Change], *, use_after: bool = True, cmds=None) -> list[str]:
    cmds = cmds or _maya_cmds()
    warnings = []
    try:
        cmds.undoInfo(openChunk=True, chunkName="Smart Set Dress")
    except Exception:
        pass
    try:
        for change in changes:
            node = _resolve_node(cmds, change.node_id, change.node)
            plug = f"{node}.{change.attribute}" if node else ""
            if not plug or not cmds.objExists(plug):
                warnings.append(f"Missing: {change.node}.{change.attribute}")
                continue
            try:
                if cmds.getAttr(plug, lock=True):
                    warnings.append(f"Locked: {plug}")
                    continue
                cmds.setAttr(plug, change.after if use_after else change.before)
            except Exception as exc:
                warnings.append(f"{plug}: {exc}")
    finally:
        try:
            cmds.undoInfo(closeChunk=True)
        except Exception:
            pass
    return warnings


def apply_stack(layers: Iterable[SetDressLayer], cmds=None) -> list[str]:
    layers = list(layers)
    warnings = apply_changes(base_values(layers).values(), use_after=False, cmds=cmds)
    warnings.extend(apply_changes(composed_values(layers).values(), use_after=True, cmds=cmds))
    return warnings


def restore_base(layers: Iterable[SetDressLayer], cmds=None) -> list[str]:
    return apply_changes(base_values(layers).values(), use_after=False, cmds=cmds)


def scene_context(cmds=None) -> dict[str, str]:
    cmds = cmds or _maya_cmds()
    scene = str(cmds.file(query=True, sceneName=True) or "")
    stem = Path(scene).stem
    tokens = [token for token in re.split(r"[_\-.]", stem) if token]
    parts = list(Path(scene).parts)
    lower_parts = [part.lower() for part in parts]
    path_context = {}
    if "shots" in lower_parts:
        index = lower_parts.index("shots")
        if len(parts) > index + 3:
            path_context = {"episode": parts[index + 1], "sequence": parts[index + 2], "shot": parts[index + 3]}
    episode_token = next((token for token in tokens if re.fullmatch(r"ep\d+", token, re.I)), "")
    sequence_token = next((token for token in tokens if re.fullmatch(r"(?:sq|seq|s)\d+", token, re.I)), "")
    shot_token = next((token for token in tokens if re.fullmatch(r"(?:c|sh|shot)\d+", token, re.I)), "")
    return {
        "scene": scene,
        "episode": path_context.get("episode") or episode_token,
        "sequence": path_context.get("sequence") or sequence_token,
        "shot": path_context.get("shot") or shot_token,
    }


def _capture_nodes(cmds, selection_only: bool) -> list[str]:
    if selection_only:
        selected = cmds.ls(selection=True, long=True, type="transform") or []
        descendants = cmds.listRelatives(selected, allDescendents=True, fullPath=True, type="transform") or []
        nodes = selected + descendants
    else:
        nodes = cmds.ls(long=True, type="transform") or []
    return list(dict.fromkeys(str(node) for node in nodes))


def _node_id(cmds, node: str) -> str:
    try:
        values = cmds.ls(node, uuid=True) or []
        if values:
            return str(values[0])
    except Exception:
        pass
    return node


def _resolve_node(cmds, node_id: str, fallback: str) -> str:
    try:
        # Maya accepts a UUID as an ls argument. The uuid flag itself changes
        # the return type to UUID strings and must not be used for resolution.
        nodes = cmds.ls(node_id, long=True) or []
        if nodes:
            return str(nodes[0])
    except Exception:
        pass
    return fallback if cmds.objExists(fallback) else ""


def _different(left: Any, right: Any, tolerance: float) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) != bool(right)
    try:
        return not math.isclose(float(left), float(right), rel_tol=tolerance, abs_tol=tolerance)
    except (TypeError, ValueError):
        return left != right


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value).strip()).strip("._") or "untitled"


def _maya_cmds():
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Smart Set Dress can only access a scene inside Maya.") from exc
    return cmds

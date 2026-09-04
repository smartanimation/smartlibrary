from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import tempfile
import uuid
import zlib
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from smartlib.core.path_resolver import configured_project_paths


FORMAT = "smart-set-dress"
VERSION = 1
DEFAULT_HISTORY_LIMIT = 30
SCENE_DATA_NODE = "smartSetDressData"
SCENE_DATA_ATTRS = {
    "format": ("smartSetDressFormat", "string"),
    "version": ("smartSetDressVersion", "long"),
    "payload": ("smartSetDressPayload", "string"),
    "checksum": ("smartSetDressChecksum", "string"),
    "external_path": ("smartSetDressExternalPath", "string"),
    "updated_at": ("smartSetDressUpdatedAt", "string"),
    "dirty": ("smartSetDressDirty", "bool"),
}
TRANSFORM_ATTRIBUTES = (
    "translateX", "translateY", "translateZ",
    "rotateX", "rotateY", "rotateZ",
    "scaleX", "scaleY", "scaleZ", "visibility",
)


@dataclass
class NodeState:
    node_id: str
    node: str
    values: dict[str, Any]


@dataclass
class Change:
    node_id: str
    node: str
    attribute: str
    before: Any
    after: Any


@dataclass
class SetDressLayer:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    name: str = "new_layer"
    scope: str = "shot"
    target: str = "maya"
    muted: bool = False
    changes: list[Change] = field(default_factory=list)


@dataclass
class SetDressPackage:
    layers: list[SetDressLayer] = field(default_factory=list)
    context: dict[str, str] = field(default_factory=dict)
    base: list[NodeState] = field(default_factory=list)
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
                target=str(raw_layer.get("target") or "maya"),
                muted=bool(raw_layer.get("muted", False)),
                changes=changes,
            ))
        return cls(
            layers=layers,
            base=[NodeState(**item) for item in data.get("base") or []],
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
        for attribute in sorted(old.values.keys() & new.values.keys()):
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


def remember_base(
    base: Iterable[NodeState],
    captured: Iterable[NodeState],
) -> list[NodeState]:
    """Keep the first captured value for every node attribute."""
    result = {
        item.node_id: NodeState(item.node_id, item.node, dict(item.values))
        for item in base
    }
    for item in captured:
        stored = result.setdefault(item.node_id, NodeState(item.node_id, item.node, {}))
        if item.node:
            stored.node = item.node
        for attribute, value in item.values.items():
            stored.values.setdefault(attribute, value)
    return list(result.values())


def layer_package(package: SetDressPackage, layer: SetDressLayer) -> SetDressPackage:
    """Export one layer with only the base plugs owned by that layer."""
    originals = {
        (state.node_id, attribute): value
        for state in package.base for attribute, value in state.values.items()
    }
    states = {}
    for change in layer.changes:
        state = states.setdefault(
            change.node_id, NodeState(change.node_id, change.node, {})
        )
        state.values[change.attribute] = originals.get(
            (change.node_id, change.attribute), change.before
        )
    context = dict(package.context)
    context.update({
        "package": layer.name, "scope": layer.scope, "target": layer.target,
        "layer_id": layer.id,
        "layer_order": (
            package.context.get("layer_order", "0")
            if len(package.layers) == 1 and package.context.get("layer_id") == layer.id
            else str(package.layers.index(layer))
        ),
    })
    return SetDressPackage.from_dict(SetDressPackage(
        layers=[layer], base=list(states.values()), context=context,
    ).to_dict())


def layer_ids(data: dict) -> set[str]:
    return {
        str(layer["id"]) for layer in data.get("layers", [])
        if layer.get("id") and layer.get("changes")
    }


def visible_work_packages(paths: Iterable[Path]) -> list[Path]:
    """Hide superseded names/legacy bundles without deleting recovery files."""
    entries = []
    for path in paths:
        try:
            package = load_package(path)
            entries.append((path, package))
        except (OSError, ValueError, TypeError):
            continue
    newest = {}
    for path, package in sorted(entries, key=lambda row: row[0].stat().st_mtime_ns):
        if package.context.get("layer_id") and len(package.layers) == 1:
            newest[package.layers[0].id] = path
    covered = set(newest)
    result = []
    for path, package in entries:
        if package.context.get("layer_id") and len(package.layers) == 1:
            if newest.get(package.layers[0].id) == path:
                result.append(path)
        else:
            ids = layer_ids(package.to_dict())
            if not ids or not ids <= covered:
                result.append(path)
    return result


def compose_packages(packages: Iterable[SetDressPackage]) -> SetDressPackage:
    """Compose resolved Build inputs into one recoverable scene package."""
    result = SetDressPackage()
    packages = list(packages)
    if packages and all("layer_order" in item.context for item in packages):
        packages.sort(key=lambda item: int(item.context["layer_order"]))
    for package in packages:
        result.layers.extend(package.layers)
        result.base = remember_base(result.base, package.base)
        result.context.update(package.context)
    return result


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


def create_history_revision(
    package: SetDressPackage,
    working_path: str | os.PathLike[str],
    *,
    keep: int = DEFAULT_HISTORY_LIMIT,
) -> Path:
    working = Path(working_path)
    package_name = working.name
    if package_name.endswith(".setdress.json"):
        package_name = package_name[: -len(".setdress.json")]
    else:
        package_name = working.stem
    history_dir = working.parent / ".history" / _safe_name(package_name)
    revisions = _history_revisions(history_dir)
    next_number = max((number for number, _path in revisions), default=0) + 1
    revision = save_package(
        package,
        history_dir / f"r{next_number:04d}.setdress.json",
    )
    if keep > 0:
        revisions = _history_revisions(history_dir)
        for _number, stale in revisions[:-keep]:
            stale.unlink()
    return revision


def list_history_revisions(
    working_path: str | os.PathLike[str],
) -> list[Path]:
    working = Path(working_path)
    package_name = working.name
    if package_name.endswith(".setdress.json"):
        package_name = package_name[: -len(".setdress.json")]
    else:
        package_name = working.stem
    history_dir = working.parent / ".history" / _safe_name(package_name)
    return [path for _number, path in reversed(_history_revisions(history_dir))]


def encode_scene_payload(package: SetDressPackage) -> tuple[str, str]:
    raw = json.dumps(
        package.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    checksum = hashlib.sha256(raw).hexdigest()
    return base64.b64encode(zlib.compress(raw, level=9)).decode("ascii"), checksum


def decode_scene_payload(payload: str, checksum: str = "") -> SetDressPackage:
    try:
        raw = zlib.decompress(base64.b64decode(payload.encode("ascii")))
    except Exception as exc:
        raise ValueError("Embedded Set Dress payload is damaged.") from exc
    actual = hashlib.sha256(raw).hexdigest()
    if checksum and actual != checksum:
        raise ValueError("Embedded Set Dress payload checksum does not match.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError("Embedded Set Dress payload is not valid JSON.") from exc
    return SetDressPackage.from_dict(data)


def embed_package_in_scene(
    package: SetDressPackage,
    *,
    external_path: str | os.PathLike[str] = "",
    dirty: bool = False,
    cmds=None,
) -> str:
    cmds = cmds or _maya_cmds()
    node = _ensure_scene_data_node(cmds)
    payload, checksum = encode_scene_payload(package)
    values = {
        "format": FORMAT,
        "version": VERSION,
        "payload": payload,
        "checksum": checksum,
        "external_path": str(external_path),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "dirty": bool(dirty),
    }
    for key, value in values.items():
        attr, attr_type = SCENE_DATA_ATTRS[key]
        plug = f"{node}.{attr}"
        if attr_type == "string":
            cmds.setAttr(plug, str(value), type="string")
        else:
            cmds.setAttr(plug, value)
    return node


def load_package_from_scene(cmds=None) -> tuple[SetDressPackage | None, str]:
    cmds = cmds or _maya_cmds()
    node = _scene_data_node(cmds)
    if not node:
        return None, ""
    payload = str(cmds.getAttr(f"{node}.{SCENE_DATA_ATTRS['payload'][0]}") or "")
    if not payload:
        return None, ""
    checksum = str(cmds.getAttr(f"{node}.{SCENE_DATA_ATTRS['checksum'][0]}") or "")
    external_path = str(
        cmds.getAttr(f"{node}.{SCENE_DATA_ATTRS['external_path'][0]}") or ""
    )
    return decode_scene_payload(payload, checksum), external_path


def scene_has_embedded_package(cmds=None) -> bool:
    cmds = cmds or _maya_cmds()
    node = _scene_data_node(cmds)
    return bool(node and cmds.getAttr(f"{node}.{SCENE_DATA_ATTRS['payload'][0]}"))


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
    return configured_project_paths(root).shot_data_root(episode, sequence, shot) / "setdress" / f"{package}.setdress.json"


def capture_scene(selection_only: bool = True, cmds=None) -> list[NodeState]:
    cmds = cmds or _maya_cmds()
    nodes = _capture_nodes(cmds, selection_only)
    states = []
    for node in nodes:
        values = {}
        for attribute in recordable_attributes(cmds, node):
            plug = f"{node}.{attribute}"
            try:
                if cmds.objExists(plug):
                    value = cmds.getAttr(plug)
                    if _recordable_value(value):
                        values[attribute] = value
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
                value = change.after if use_after else change.before
                if isinstance(value, str):
                    cmds.setAttr(plug, value, type="string")
                else:
                    cmds.setAttr(plug, value)
            except Exception as exc:
                warnings.append(f"{plug}: {exc}")
    finally:
        try:
            cmds.undoInfo(closeChunk=True)
        except Exception:
            pass
    return warnings


def apply_stack(
    layers: Iterable[SetDressLayer],
    cmds=None,
    *,
    base: Iterable[NodeState] | None = None,
) -> list[str]:
    layers = list(layers)
    warnings = restore_base(layers, cmds=cmds, base=base)
    warnings.extend(apply_changes(composed_values(layers).values(), use_after=True, cmds=cmds))
    return warnings


def restore_base(
    layers: Iterable[SetDressLayer],
    cmds=None,
    *,
    base: Iterable[NodeState] | None = None,
) -> list[str]:
    if base:
        changes = (
            Change(state.node_id, state.node, attribute, value, value)
            for state in base
            for attribute, value in state.values.items()
        )
        return apply_changes(changes, use_after=True, cmds=cmds)
    # Version 1 packages did not store a dedicated base snapshot.
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
        selected = cmds.ls(selection=True, long=True) or []
        descendants = (
            cmds.listRelatives(selected, allDescendents=True, fullPath=True) or []
        )
        candidates = selected + descendants
        transforms = cmds.ls(candidates, long=True, type="transform") or []
        shapes = cmds.ls(candidates, long=True, shapes=True) or []
    else:
        transforms = cmds.ls(long=True, type="transform") or []
        shapes = cmds.ls(long=True, shapes=True) or []
    return list(dict.fromkeys(str(node) for node in transforms + shapes))


def recordable_attributes(cmds, node: str) -> list[str]:
    """Return user-editable scalar plugs exposed by Maya's normal authoring UI."""
    attributes = set(TRANSFORM_ATTRIBUTES)
    for query in ({"keyable": True}, {"channelBox": True}):
        try:
            attributes.update(str(item) for item in (cmds.listAttr(node, **query) or []))
        except Exception:
            continue
    result = []
    for attribute in sorted(attributes):
        plug = f"{node}.{attribute}"
        try:
            if not cmds.objExists(plug) or cmds.getAttr(plug, lock=True):
                continue
            attr_type = str(cmds.getAttr(plug, type=True) or "")
            if attr_type not in {
                "bool", "byte", "char", "short", "long", "enum",
                "float", "double", "doubleAngle", "doubleLinear", "string",
            }:
                continue
            result.append(attribute)
        except Exception:
            continue
    return result


def _recordable_value(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _scene_data_node(cmds) -> str:
    if cmds.objExists(SCENE_DATA_NODE):
        try:
            if cmds.nodeType(SCENE_DATA_NODE) == "network":
                return SCENE_DATA_NODE
        except Exception:
            pass
    nodes = cmds.ls(type="network") or []
    for node in nodes:
        if cmds.objExists(f"{node}.{SCENE_DATA_ATTRS['format'][0]}"):
            try:
                if cmds.getAttr(f"{node}.{SCENE_DATA_ATTRS['format'][0]}") == FORMAT:
                    return str(node)
            except Exception:
                continue
    return ""


def _ensure_scene_data_node(cmds) -> str:
    node = _scene_data_node(cmds)
    if not node:
        node = str(cmds.createNode("network", name=SCENE_DATA_NODE))
    for attr, attr_type in SCENE_DATA_ATTRS.values():
        if cmds.objExists(f"{node}.{attr}"):
            continue
        if attr_type == "string":
            cmds.addAttr(node, longName=attr, dataType="string")
        else:
            cmds.addAttr(node, longName=attr, attributeType=attr_type)
    for attr, value in (("isHistoricallyInteresting", 0), ("hiddenInOutliner", True)):
        plug = f"{node}.{attr}"
        try:
            if cmds.objExists(plug):
                cmds.setAttr(plug, value)
        except Exception:
            pass
    return node


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


def _history_revisions(history_dir: Path) -> list[tuple[int, Path]]:
    rows = []
    if not history_dir.exists():
        return rows
    for path in history_dir.glob("r*.setdress.json"):
        match = re.fullmatch(r"r(\d+)\.setdress\.json", path.name)
        if match:
            rows.append((int(match.group(1)), path))
    return sorted(rows, key=lambda item: item[0])


def _maya_cmds():
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Smart Set Dress can only access a scene inside Maya.") from exc
    return cmds

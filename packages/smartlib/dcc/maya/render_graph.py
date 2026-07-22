from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import shutil
from typing import Any
import uuid

from smartlib.core.metadata import read_json, write_json


PORT_OBJECTS = "objects"
PORT_CAMERA = "camera"
PORT_RENDER_SETTINGS = "render_settings"
PORT_OUTPUT = "output"
SCENE_STATE_NODE = "smartRender_STATE"
SCENE_STATE_ATTR = "statesJson"
AE_SLOT_MIN_COUNT = 1
AE_SLOT_MAX_COUNT = 64
FRAME_MODE_EDITORIAL = "Editorial Frame Range"
FRAME_MODE_SINGLE = "Single"
FRAME_MODE_TIME_RANGE = "TimeRange"
FRAME_MODE_RENDER_GLOBAL = "RenderGlobal"
FRAME_MODE_CUSTOM = "Custom"
FRAME_MODES = (FRAME_MODE_EDITORIAL, FRAME_MODE_SINGLE, FRAME_MODE_TIME_RANGE, FRAME_MODE_RENDER_GLOBAL, FRAME_MODE_CUSTOM)


@dataclass
class RenderNode:
    id: str
    type: str
    name: str
    x: float = 0.0
    y: float = 0.0
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_data(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "attrs": self.attrs,
        }

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> "RenderNode":
        node_type = str(data.get("type") or "object")
        raw_attrs = dict(data.get("attrs") or {})
        name = str(data.get("name") or "Node")
        if node_type == "output":
            legacy_layer = str(raw_attrs.get("layer") or "").strip()
            if legacy_layer and _is_default_output_name(name):
                name = legacy_layer
        return cls(
            id=str(data.get("id") or new_node_id()),
            type=node_type,
            name=name,
            x=float(data.get("x") or 0.0),
            y=float(data.get("y") or 0.0),
            attrs=normalized_attrs(node_type, raw_attrs),
        )


@dataclass
class RenderEdge:
    source: str
    source_port: str
    target: str
    target_port: str

    def to_data(self) -> dict[str, str]:
        return {
            "source": self.source,
            "source_port": self.source_port,
            "target": self.target,
            "target_port": self.target_port,
        }

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> "RenderEdge":
        return cls(
            source=str(data.get("source") or ""),
            source_port=str(data.get("source_port") or ""),
            target=str(data.get("target") or ""),
            target_port=str(data.get("target_port") or ""),
        )


@dataclass
class RenderGraph:
    nodes: list[RenderNode] = field(default_factory=list)
    edges: list[RenderEdge] = field(default_factory=list)

    def to_data(self) -> dict[str, Any]:
        self.update_ae_slot_orders()
        return {
            "schema": "smart_render_graph",
            "version": 1,
            "nodes": [node.to_data() for node in self.nodes],
            "edges": [edge.to_data() for edge in self.edges],
        }

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> "RenderGraph":
        graph = cls(
            nodes=[RenderNode.from_data(row) for row in data.get("nodes") or []],
            edges=[RenderEdge.from_data(row) for row in data.get("edges") or []],
        )
        graph.restore_ae_slot_edges_from_order()
        graph.edges = [edge for edge in graph.edges if graph.can_connect(edge.source, edge.source_port, edge.target, edge.target_port)]
        graph.migrate_output_paths()
        graph.update_ae_slot_orders()
        return graph

    def node(self, node_id: str) -> RenderNode | None:
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    def add_node(self, node_type: str, name: str | None = None, x: float = 0.0, y: float = 0.0) -> RenderNode:
        spec = NODE_TYPES[node_type]
        node = RenderNode(
            id=new_node_id(),
            type=node_type,
            name=name or str(spec["label"]),
            x=x,
            y=y,
            attrs=default_attrs(node_type),
        )
        self.nodes.append(node)
        return node

    def remove_node(self, node_id: str) -> None:
        self.nodes = [node for node in self.nodes if node.id != node_id]
        self.edges = [edge for edge in self.edges if edge.source != node_id and edge.target != node_id]
        self.update_ae_slot_orders()

    def can_connect(self, source: str, source_port: str, target: str, target_port: str) -> bool:
        source_node = self.node(source)
        target_node = self.node(target)
        if not source_node or not target_node or source == target:
            return False
        source_ports = self.output_port_types(source)
        target_ports = self.input_port_types(target)
        source_type = source_ports.get(source_port)
        target_type = target_ports.get(target_port)
        return bool(source_type and target_type and source_type == target_type)

    def connect(self, source: str, source_port: str, target: str, target_port: str) -> RenderEdge:
        if not self.can_connect(source, source_port, target, target_port):
            raise ValueError("Incompatible node ports.")
        self.edges = [
            edge
            for edge in self.edges
            if not (edge.source == source and edge.source_port == source_port and edge.target == target and edge.target_port == target_port)
        ]
        target_node = self.node(target)
        if target_port in {PORT_CAMERA, PORT_RENDER_SETTINGS} or (target_node and target_node.type == "ae_slots"):
            self.edges = [edge for edge in self.edges if not (edge.target == target and edge.target_port == target_port)]
        if target_node and target_node.type == "ae_slots":
            self.edges = [
                edge
                for edge in self.edges
                if not (edge.source == source and edge.source_port == source_port and edge.target == target)
            ]
        edge = RenderEdge(source, source_port, target, target_port)
        self.edges.append(edge)
        self.update_ae_slot_orders()
        return edge

    def disconnect_edge(self, index: int) -> None:
        if 0 <= index < len(self.edges):
            self.edges.pop(index)
            self.update_ae_slot_orders()

    def input_edges(self, node_id: str, port: str | None = None) -> list[RenderEdge]:
        return [edge for edge in self.edges if edge.target == node_id and (port is None or edge.target_port == port)]

    def output_nodes(self) -> list[RenderNode]:
        return [node for node in self.nodes if node.type == "output"]

    def input_port_types(self, node_id: str) -> dict[str, str]:
        node = self.node(node_id)
        return node_input_port_types(node) if node else {}

    def output_port_types(self, node_id: str) -> dict[str, str]:
        node = self.node(node_id)
        return node_output_port_types(node) if node else {}

    def migrate_output_paths(self) -> None:
        for output in self.output_nodes():
            legacy_layer = str(output.attrs.get("layer") or "").strip()
            if legacy_layer and _is_default_output_name(output.name):
                output.name = legacy_layer
            output.attrs = normalized_attrs("output", output.attrs)
            current = str(output.attrs.get("output_path") or "").strip()
            for edge in self.input_edges(output.id, PORT_RENDER_SETTINGS):
                settings = self.node(edge.source)
                if not settings or settings.type != "render_settings":
                    continue
                legacy_path = str(settings.attrs.get("output_path") or "").strip()
                if legacy_path and not current:
                    output.attrs["output_path"] = legacy_path
                    current = legacy_path
                settings.attrs.pop("output_path", None)
        for node in self.nodes:
            if node.type == "render_settings":
                node.attrs.pop("output_path", None)

    def ae_slot_order(self, node_id: str) -> list[str]:
        node = self.node(node_id)
        if not node or node.type != "ae_slots":
            return []
        order: list[str] = []
        for port in ae_slot_input_ports(node.attrs):
            edge = next((item for item in self.edges if item.target == node_id and item.target_port == port), None)
            if edge and self.node(edge.source):
                order.append(edge.source)
        return order

    def update_ae_slot_orders(self) -> None:
        for node in self.nodes:
            if node.type == "ae_slots":
                node.attrs = normalized_attrs(node.type, node.attrs)
                slots: dict[str, str] = {}
                for port in ae_slot_input_ports(node.attrs):
                    edge = next((item for item in self.edges if item.target == node.id and item.target_port == port), None)
                    if edge and self.node(edge.source):
                        slots[port] = edge.source
                node.attrs["slots"] = slots
                node.attrs["order"] = self.ae_slot_order(node.id)

    def restore_ae_slot_edges_from_order(self) -> None:
        for node in self.nodes:
            if node.type != "ae_slots":
                continue
            node.attrs = normalized_attrs(node.type, node.attrs)
            slots = node.attrs.get("slots") if isinstance(node.attrs.get("slots"), dict) else {}
            if slots:
                max_slot = max((_slot_index(port) for port in slots), default=AE_SLOT_MIN_COUNT)
                node.attrs["slot_count"] = max(int(node.attrs.get("slot_count") or AE_SLOT_MIN_COUNT), max_slot)
            for port, output_id in slots.items():
                port_name = str(port)
                if any(edge.target == node.id and edge.target_port == port_name for edge in self.edges):
                    continue
                if self.can_connect(str(output_id), "out", node.id, port_name):
                    self.edges.append(RenderEdge(str(output_id), "out", node.id, port_name))
            if slots:
                continue
            order = [str(item) for item in (node.attrs.get("order") or []) if str(item)]
            if order:
                node.attrs["slot_count"] = max(int(node.attrs.get("slot_count") or AE_SLOT_MIN_COUNT), len(order))
            for index, output_id in enumerate(order, start=1):
                port = ae_slot_port_name(index)
                if any(edge.target == node.id and edge.target_port == port for edge in self.edges):
                    continue
                if self.can_connect(output_id, "out", node.id, port):
                    self.edges.append(RenderEdge(output_id, "out", node.id, port))


NODE_TYPES: dict[str, dict[str, Any]] = {
    "object": {
        "label": "Object",
        "group": "Objects",
        "inputs": {"in": PORT_OBJECTS},
        "outputs": {"out": PORT_OBJECTS},
        "attrs": {
            "mode": "selection",
            "objects": [],
            "display_layer": "",
            "set": "",
        },
    },
    "cast": {
        "label": "Reference",
        "group": "Objects",
        "inputs": {},
        "outputs": {"out": PORT_OBJECTS},
        "attrs": {
            "namespace": "",
            "reference_node": "",
        },
    },
    "material": {
        "label": "Material",
        "group": "Objects",
        "inputs": {"in": PORT_OBJECTS},
        "outputs": {"out": PORT_OBJECTS},
        "attrs": {"material_name": "red_plastic_MAT", "shader": "lambert", "color": [1.0, 0.02, 0.02]},
    },
    "visibility": {
        "label": "geomAttr",
        "group": "Objects",
        "inputs": {"in": PORT_OBJECTS},
        "outputs": {"out": PORT_OBJECTS},
        "attrs": {
            "visibility": True,
            "template": False,
            "castsShadows": True,
            "receiveShadows": True,
            "motionBlur": True,
        },
    },
    "transform_override": {
        "label": "Transform Override",
        "group": "Objects",
        "inputs": {"in": PORT_OBJECTS},
        "outputs": {"out": PORT_OBJECTS},
        "attrs": {
            "recording": False,
            "record_start_transform": {},
            "match_source": "",
            "translate_enabled": False,
            "translate": [0.0, 0.0, 0.0],
            "rotate_enabled": False,
            "rotate": [0.0, 0.0, 0.0],
            "scale_enabled": False,
            "scale": [1.0, 1.0, 1.0],
        },
    },
    "camera": {
        "label": "Camera",
        "group": "Camera",
        "inputs": {},
        "outputs": {"out": PORT_CAMERA},
        "attrs": {
            "camera": "Camera001",
            "set_viewport": True,
            "override_overscan": True,
            "overscan": 1.0,
            "override_depthOfField": False,
            "depthOfField": False,
            "override_focusDistance": False,
            "focusDistance": 5.0,
            "override_fStop": False,
            "fStop": 5.6,
            "override_nearClipPlane": False,
            "nearClipPlane": 0.1,
            "override_farClipPlane": False,
            "farClipPlane": 10000.0,
        },
    },
    "render_settings": {
        "label": "Render Settings",
        "group": "Render",
        "inputs": {},
        "outputs": {"out": PORT_RENDER_SETTINGS},
        "attrs": {
            "width": 1920,
            "height": 1080,
            "frame_mode": FRAME_MODE_EDITORIAL,
            "start_frame": 100,
            "end_frame": 300,
            "format": "image",
            "compression": "png",
            "percent": 100,
        },
    },
    "output": {
        "label": "Output",
        "group": "Outputs",
        "inputs": {
            "objects": PORT_OBJECTS,
            "camera": PORT_CAMERA,
            "render_settings": PORT_RENDER_SETTINGS,
        },
        "outputs": {"out": PORT_OUTPUT},
        "attrs": {
            "dept": "layout",
            "version": "v001",
            "take": "01",
            "quality_preset": "layout_material",
            "output_path": "",
            "save_file": True,
        },
    },
    "ae_slots": {
        "label": "AE Slots",
        "group": "Outputs",
        "inputs": {"slot01": PORT_OUTPUT, "slot02": PORT_OUTPUT, "slot03": PORT_OUTPUT},
        "outputs": {},
        "attrs": {"slot_count": 3, "order": []},
    },
}


def new_node_id() -> str:
    return f"node_{uuid.uuid4().hex[:10]}"


def default_attrs(node_type: str) -> dict[str, Any]:
    return _copy_value(NODE_TYPES[node_type].get("attrs") or {})


def normalized_attrs(node_type: str, attrs: dict[str, Any]) -> dict[str, Any]:
    if node_type not in NODE_TYPES:
        return attrs
    merged = default_attrs(node_type)
    merged.update(attrs)
    if node_type == "object":
        if "mode" not in attrs and "use_selection" in attrs:
            merged["mode"] = "selection" if attrs.get("use_selection") else "objects"
        merged["objects"] = _object_refs_from_value(merged.get("objects"))
    elif node_type == "visibility":
        if "visibility" not in attrs and "visible" in attrs:
            merged["visibility"] = bool(attrs.get("visible"))
        merged.pop("visible", None)
    elif node_type == "render_settings":
        merged["frame_mode"] = _normalize_frame_mode(merged.get("frame_mode"))
    elif node_type == "output":
        merged["dept"] = _clean_name(merged.get("dept"), "layout")
        merged["version"] = _normalize_label(merged.get("version"), "v", 3)
        merged["take"] = _normalize_take_number(merged.get("take"))
        merged.pop("layer", None)
    elif node_type == "ae_slots":
        merged["slot_count"] = _clamped_int(merged.get("slot_count"), 3, AE_SLOT_MIN_COUNT, AE_SLOT_MAX_COUNT)
        order = merged.get("order")
        merged["order"] = [str(item) for item in order] if isinstance(order, list) else []
    return merged


def node_input_port_types(node: RenderNode | None) -> dict[str, str]:
    if not node:
        return {}
    if node.type == "ae_slots":
        return ae_slot_input_ports(node.attrs)
    return dict(NODE_TYPES.get(node.type, {}).get("inputs") or {})


def node_output_port_types(node: RenderNode | None) -> dict[str, str]:
    if not node:
        return {}
    return dict(NODE_TYPES.get(node.type, {}).get("outputs") or {})


def ae_slot_port_name(index: int) -> str:
    return f"slot{max(AE_SLOT_MIN_COUNT, int(index)):02d}"


def _slot_index(port_name: Any) -> int:
    text = str(port_name or "").strip().lower()
    if text.startswith("slot") and text[4:].isdigit():
        return int(text[4:])
    return AE_SLOT_MIN_COUNT


def ae_slot_input_ports(attrs: dict[str, Any]) -> dict[str, str]:
    count = _clamped_int((attrs or {}).get("slot_count"), 3, AE_SLOT_MIN_COUNT, AE_SLOT_MAX_COUNT)
    return {ae_slot_port_name(index): PORT_OUTPUT for index in range(1, count + 1)}


def load_graph(path: str | Path) -> RenderGraph:
    return RenderGraph.from_data(read_json(path, default={}) or {})


def save_graph(path: str | Path, graph: RenderGraph) -> Path:
    graph.migrate_output_paths()
    return write_json(path, graph.to_data())


def apply_graph(
    graph: RenderGraph,
    output_node_id: str | None = None,
    *,
    playblast: bool = False,
    restore_after_playblast: bool = True,
    project_config: Any = None,
    progress_callback: Any = None,
) -> dict[str, Any]:
    cmds = _maya_cmds()
    output = _output_node(graph, output_node_id)
    if not output:
        raise RuntimeError("Add an Output node before applying the graph.")

    master_state = capture_master_state(graph, output.id) if playblast and restore_after_playblast else None
    try:
        _emit_progress(progress_callback, "Checking object inputs...", 5)
        has_object_input = bool(graph.input_edges(output.id, "objects"))
        preflight = _ApplyState(cmds=cmds, graph=graph, project_config=project_config)
        objects = preflight.collect_objects(output.id, "objects") if has_object_input else []
        if has_object_input:
            _warn_duplicate_short_names(objects, preflight.warnings)
        if preflight.errors:
            raise RuntimeError(_format_apply_warnings(preflight.errors, "Object name ambiguity detected."))

        _emit_progress(progress_callback, "Applying object graph...", 15)
        state = _ApplyState(cmds=cmds, graph=graph, project_config=project_config)
        objects = state.evaluate_objects(output.id, "objects") if has_object_input else []
        camera, camera_attrs = state.evaluate_camera(output.id)
        settings = state.evaluate_render_settings(output.id)
        warnings = _dedupe([*preflight.warnings, *state.warnings])

        _emit_progress(progress_callback, "Applying camera and render settings...", 30)
        camera_panel = ""
        if camera:
            camera_panel = _set_viewport_camera(cmds, camera, bool(camera_attrs.get("set_viewport", True)))
            _apply_camera_overrides(cmds, camera, camera_attrs)
        _apply_render_settings(cmds, settings, camera, camera_panel)
        _apply_quality_preset(settings, project_config)
        if has_object_input:
            _isolate_final_objects(cmds, objects)

        playblast_path = ""
        if playblast:
            playblast_path = _playblast(cmds, settings, camera, project_config, camera_panel, progress_callback)
        _emit_progress(progress_callback, "Render graph finished.", 92 if playblast else 100)

        return {
            "objects": objects,
            "camera": camera,
            "render_settings": settings,
            "playblast": playblast_path,
            "restored": False,
            "warnings": warnings,
        }
    finally:
        if master_state is not None:
            _emit_progress(progress_callback, "Restoring Master state...", 96)
            restore_master_state(master_state)


def capture_master_state(graph: RenderGraph, output_node_id: str | None = None) -> dict[str, Any]:
    cmds = _maya_cmds()
    output = _output_node(graph, output_node_id)
    if not output:
        raise RuntimeError("Add an Output node before capturing Master state.")
    state = _ApplyState(cmds=cmds, graph=graph)
    objects = state.collect_objects(output.id, "objects")
    camera, _camera_attrs = state.evaluate_camera(output.id)
    return _capture_scene_state(cmds, objects, camera)


def restore_master_state(master_state: dict[str, Any]) -> None:
    _restore_scene_state(_maya_cmds(), master_state)


def save_master_state_to_scene(output_id: str, master_state: dict[str, Any]) -> None:
    if not output_id:
        return
    cmds = _maya_cmds()
    data = _read_scene_state_data(cmds)
    master_states = data.setdefault("master_states", {})
    master_states[str(output_id)] = master_state
    _write_scene_state_data(cmds, data)


def load_master_state_from_scene(output_id: str) -> dict[str, Any] | None:
    if not output_id:
        return None
    data = _read_scene_state_data(_maya_cmds())
    state = (data.get("master_states") or {}).get(str(output_id))
    return state if isinstance(state, dict) else None


def delete_master_state_from_scene(output_id: str) -> None:
    if not output_id:
        return
    cmds = _maya_cmds()
    data = _read_scene_state_data(cmds)
    master_states = data.setdefault("master_states", {})
    master_states.pop(str(output_id), None)
    _write_scene_state_data(cmds, data)


def scene_has_master_state(output_id: str) -> bool:
    return load_master_state_from_scene(output_id) is not None


def save_graph_to_scene(graph: RenderGraph) -> None:
    cmds = _maya_cmds()
    data = _read_scene_state_data(cmds)
    graph.migrate_output_paths()
    data["graph"] = graph.to_data()
    _write_scene_state_data(cmds, data)


def load_graph_from_scene() -> RenderGraph | None:
    data = _read_scene_state_data(_maya_cmds())
    graph_data = data.get("graph")
    if not isinstance(graph_data, dict):
        return None
    try:
        graph = RenderGraph.from_data(graph_data)
    except Exception:
        return None
    return graph if graph.nodes else None


def scene_has_graph() -> bool:
    return load_graph_from_scene() is not None


def evaluate_output_render_settings(graph: RenderGraph, output_node_id: str | None = None, project_config: Any = None) -> dict[str, Any]:
    output = _output_node(graph, output_node_id)
    if not output:
        raise RuntimeError("Select or add an Output node.")
    return _ApplyState(cmds=_maya_cmds_or_none(), graph=graph, project_config=project_config).evaluate_render_settings(output.id)


def publish_ae_slots(graph: RenderGraph, ae_slots_id: str, project_config: Any = None) -> list[str]:
    result = _prepare_ae_slots_review_build(graph, ae_slots_id, project_config, area="publish", snapshot_layers=True)
    return [str(path) for path in result.get("published_roots", [])]


def export_ae_slots_build_data(graph: RenderGraph, ae_slots_id: str, project_config: Any = None, *, area: str = "output") -> list[str]:
    result = _prepare_ae_slots_review_build(graph, ae_slots_id, project_config, area=area, snapshot_layers=False)
    manifest = result.get("manifest")
    return [str(manifest)] if manifest else []


def build_ae_slots(graph: RenderGraph, ae_slots_id: str, project_config: Any = None) -> list[dict[str, Any]]:
    if project_config is None:
        raise RuntimeError("Project config is required to launch After Effects.")
    manifests = export_ae_slots_build_data(graph, ae_slots_id, project_config, area="output")
    from smartlib.review.ae import launch_after_effects_build

    results = []
    for manifest in manifests:
        result = launch_after_effects_build(manifest, project_config)
        results.append(
            {
                "build_root": str(result.manifest.parent),
                "manifest": str(result.manifest),
                "script": str(result.script),
                "project": str(result.project),
                "log": str(result.log),
                "launched": result.launched,
                "message": result.message,
            }
        )
    return results


def _prepare_ae_slots_review_build(
    graph: RenderGraph,
    ae_slots_id: str,
    project_config: Any,
    *,
    area: str,
    snapshot_layers: bool,
) -> dict[str, Any]:
    if project_config is None:
        raise RuntimeError("Project config is required.")
    cmds = _maya_cmds_or_none()
    ae_slots = graph.node(ae_slots_id)
    if not ae_slots or ae_slots.type != "ae_slots":
        raise RuntimeError("Select an AE Slots node.")
    state = _ApplyState(cmds=cmds, graph=graph, project_config=project_config)
    rows: list[tuple[int, RenderNode, dict[str, Any], Path, Path]] = []
    published_roots: list[Path] = []
    published_by_source: dict[str, Path] = {}
    for index, output_id in enumerate(graph.ae_slot_order(ae_slots_id), start=1):
        output = graph.node(output_id)
        if not output:
            continue
        settings = state.evaluate_render_settings(output_id)
        source_root_text = str(settings.get("package_root") or "")
        if not source_root_text:
            raise RuntimeError(f"Output package path could not be resolved: {output.name}")
        source_root = Path(source_root_text)
        target_root = source_root
        if snapshot_layers:
            publish_root_text = str(settings.get("publish_package_root") or "")
            if not publish_root_text:
                raise RuntimeError(f"Publish package path could not be resolved: {output.name}")
            source_key = source_root.as_posix()
            if source_key not in published_by_source:
                from smartlib.review.playblast_package import snapshot_output_to_publish

                target_root = snapshot_output_to_publish(source_root, publish_root_text, unique=True)
                published_by_source[source_key] = target_root
                published_roots.append(target_root)
            else:
                target_root = published_by_source[source_key]
        rows.append((index, output, settings, source_root, target_root))
    if not rows:
        return {"published_roots": published_roots}

    first_settings = rows[0][2]
    build_paths = _review_build_paths(project_config, first_settings, area)
    build_root = build_paths.root
    slot_rows = [
        _ae_slot_row_for_build(index, output, settings, source_root, target_root, build_root)
        for index, output, settings, source_root, target_root in rows
    ]
    build_paths.slots.parent.mkdir(parents=True, exist_ok=True)
    write_json(build_paths.slots, {"created_at": datetime.now().isoformat(timespec="seconds"), "area": area, "slots": slot_rows})
    from smartlib.review.ae import prepare_review_ae_build

    build_result = prepare_review_ae_build(
        publish_root=build_root,
        slots=slot_rows,
        project_config=project_config,
        shot_root=first_settings.get("shot_root") or build_root,
        department=str(first_settings.get("dept") or "review"),
        stage={
            "width": int(first_settings.get("width") or 1920),
            "height": int(first_settings.get("height") or 1080),
            "fps": int(first_settings.get("fps") or 24),
            "frame_range": [int(first_settings.get("start_frame") or 1), int(first_settings.get("end_frame") or first_settings.get("start_frame") or 1)],
        },
        context={
            "project": getattr(project_config, "project_name", "") if project_config is not None else "",
            "projectRoot": str(getattr(project_config, "project_root", "") or ""),
            "configDir": str(getattr(project_config, "config_dir", "") or ""),
            "episode": first_settings.get("episode", ""),
            "sequence": first_settings.get("sequence", ""),
            "shot": first_settings.get("shot", ""),
        },
        manifest_path=build_paths.manifest,
        script_path=build_paths.script,
        log_path=build_paths.log,
        template_project_path=build_paths.template_project,
        template_used_json_path=build_paths.template_used,
        update_review_json=False,
    )
    return {
        "published_roots": published_roots,
        "build_root": build_root,
        "manifest": build_result.manifest,
        "script": build_result.script,
        "log": build_result.log,
    }


def _review_build_paths(project_config: Any, settings: dict[str, Any], area: str):
    from smartlib.review.playblast_package import next_available_package_root, resolve_review_build_package

    base_paths = resolve_review_build_package(
        project_config,
        area=area,
        shot_root=settings.get("shot_root") or "",
        shot=str(settings.get("shot") or "shot"),
        dept=str(settings.get("dept") or "review"),
        version=str(settings.get("version") or "v001"),
        take="01",
    )
    root = next_available_package_root(base_paths.root)
    return resolve_review_build_package(
        project_config,
        area=area,
        shot_root=settings.get("shot_root") or "",
        shot=str(settings.get("shot") or "shot"),
        dept=str(settings.get("dept") or "review"),
        version=str(settings.get("version") or "v001"),
        take=root.name,
    )


def _ae_slot_row_for_build(index: int, output: RenderNode, settings: dict[str, Any], source_root: Path, target_root: Path, build_root: Path) -> dict[str, Any]:
    image_sequence = _relocated_package_path(settings.get("image_sequence"), source_root, target_root)
    slate_sequence = _relocated_package_path(settings.get("slate_sequence"), source_root, target_root) if settings.get("slate_sequence") else Path()
    movie = _relocated_package_path(settings.get("movie_path"), source_root, target_root)
    start = int(settings.get("start_frame") or 1)
    end = int(settings.get("end_frame") or start)
    return {
        "slot": index,
        "output": output.name,
        "output_id": output.id,
        "project": getattr(settings.get("project_config", None), "project_name", ""),
        "episode": settings.get("episode", ""),
        "sequence": settings.get("sequence", ""),
        "shot": settings.get("shot", ""),
        "layer": settings.get("layer", ""),
        "image_sequence": _relative_to(build_root, image_sequence),
        "slate_sequence": _relative_to(build_root, slate_sequence) if str(slate_sequence) else "",
        "movie": _relative_to(build_root, movie),
        "start_frame": start,
        "end_frame": end,
        "frame_range": [start, end],
        "width": int(settings.get("width") or 1920),
        "height": int(settings.get("height") or 1080),
        "fps": int(settings.get("fps") or 24),
    }


def _relocated_package_path(path: Any, source_root: Path, target_root: Path) -> Path:
    source = Path(str(path or ""))
    try:
        return target_root / source.relative_to(source_root)
    except Exception:
        return source


@dataclass
class _ApplyState:
    cmds: Any
    graph: RenderGraph
    project_config: Any = None
    visiting: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def evaluate_objects(self, node_id: str, port: str = "in") -> list[str]:
        incoming = self.graph.input_edges(node_id, port)
        objects: list[str] = []
        for edge in incoming:
            objects.extend(self.evaluate_node_objects(edge.source))
        return _dedupe(objects)

    def evaluate_node_objects(self, node_id: str) -> list[str]:
        node = self.graph.node(node_id)
        if not node:
            return []
        if node.id in self.visiting:
            raise RuntimeError("Cycle detected in object graph.")
        self.visiting.add(node.id)
        try:
            if node.type == "object":
                objects = self.evaluate_objects(node.id)
                objects.extend(_objects_from_node(self.cmds, node, self.warnings, self.errors))
            elif node.type == "cast":
                objects = _objects_from_cast_node(self.cmds, node, self.project_config)
            elif node.type == "material":
                objects = self.evaluate_objects(node.id)
                _apply_material(self.cmds, objects, node.attrs)
            elif node.type == "visibility":
                objects = self.evaluate_objects(node.id)
                _apply_geom_attrs(self.cmds, objects, node.attrs)
            elif node.type == "transform_override":
                objects = self.evaluate_objects(node.id)
                _apply_transform_override(self.cmds, objects, node.attrs)
            else:
                objects = []
            return _dedupe(objects)
        finally:
            self.visiting.remove(node.id)

    def collect_objects(self, node_id: str, port: str = "in") -> list[str]:
        incoming = self.graph.input_edges(node_id, port)
        objects: list[str] = []
        for edge in incoming:
            objects.extend(self.collect_node_objects(edge.source))
        return _dedupe(objects)

    def collect_node_objects(self, node_id: str) -> list[str]:
        node = self.graph.node(node_id)
        if not node:
            return []
        if node.id in self.visiting:
            raise RuntimeError("Cycle detected in object graph.")
        self.visiting.add(node.id)
        try:
            if node.type == "object":
                objects = self.collect_objects(node.id)
                objects.extend(_objects_from_node(self.cmds, node, self.warnings, self.errors))
                return _dedupe(objects)
            if node.type == "cast":
                return _objects_from_cast_node(self.cmds, node, self.project_config)
            if node.type in {"material", "visibility", "transform_override"}:
                return self.collect_objects(node.id)
            return []
        finally:
            self.visiting.remove(node.id)

    def evaluate_camera(self, output_id: str) -> tuple[str, dict[str, Any]]:
        edges = self.graph.input_edges(output_id, "camera")
        if not edges:
            return "", {}
        node = self.graph.node(edges[0].source)
        if not node:
            return "", {}
        camera = str(node.attrs.get("camera") or "").strip()
        return camera, node.attrs

    def evaluate_render_settings(self, output_id: str) -> dict[str, Any]:
        edges = self.graph.input_edges(output_id, "render_settings")
        settings = default_attrs("render_settings")
        if edges:
            node = self.graph.node(edges[0].source)
            if node:
                settings.update(normalized_attrs("render_settings", node.attrs))
        legacy_output_path = str(settings.get("output_path") or "").strip()
        settings.pop("output_path", None)
        output = self.graph.node(output_id)
        output_attrs = normalized_attrs("output", output.attrs if output else {})
        output_path = str(output_attrs.get("output_path") or "").strip() or legacy_output_path
        if output and self.project_config is not None:
            settings.update(_package_settings(self.cmds, self.project_config, output, settings))
            output_path = str(settings.get("output_path") or output_path)
        start, end = _resolve_frame_range(self.cmds, settings, self.project_config)
        settings["start_frame"] = start
        settings["end_frame"] = end
        settings["output_path"] = output_path
        settings["save_file"] = bool(output_attrs.get("save_file", True))
        return settings


def _objects_from_node(cmds: Any, node: RenderNode, warnings: list[str] | None = None, errors: list[str] | None = None) -> list[str]:
    mode = str(node.attrs.get("mode") or ("selection" if node.attrs.get("use_selection") else "objects"))
    if mode == "selection":
        return _resolve_object_refs(cmds, [str(item) for item in (cmds.ls(selection=True, long=True) or [])], warnings, errors)
    if mode == "display_layer":
        layer = str(node.attrs.get("display_layer") or "").strip()
        if not layer or not cmds.objExists(layer):
            return []
        try:
            return _resolve_object_refs(cmds, [str(item) for item in (cmds.editDisplayLayerMembers(layer, query=True, fullNames=True) or [])], warnings, errors)
        except Exception:
            return []
    if mode == "set":
        set_name = str(node.attrs.get("set") or "").strip()
        if not set_name or not cmds.objExists(set_name):
            return []
        try:
            return _resolve_object_refs(cmds, [str(item) for item in (cmds.sets(set_name, query=True) or [])], warnings, errors)
        except Exception:
            return []
    objects = _object_refs_from_value(node.attrs.get("objects"))
    return _resolve_object_refs(cmds, objects, warnings, errors)


def _resolve_object_refs(
    cmds: Any,
    refs: list[str],
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
) -> list[str]:
    resolved = []
    for ref in refs:
        text = str(ref or "").strip()
        if not text:
            continue
        matches = _long_dag_matches(cmds, text)
        if len(matches) > 1 and not text.startswith("|"):
            message = f'Ambiguous object name "{text}" matched {len(matches)} DAG nodes. Use full DAG path: {", ".join(matches[:3])}'
            if len(matches) > 3:
                message += ", ..."
            _append_warning(errors, message)
            continue
        if matches:
            resolved.extend(matches)
            continue
        try:
            if cmds.objExists(text):
                resolved.append(text)
        except Exception:
            pass
    return _dedupe(resolved)


def _long_dag_matches(cmds: Any, ref: str) -> list[str]:
    try:
        matches = cmds.ls(ref, long=True) or []
    except Exception:
        matches = []
    return _dedupe([str(item) for item in matches if str(item)])


def _append_warning(target: list[str] | None, message: str) -> None:
    if target is not None and message not in target:
        target.append(message)


def _package_settings(cmds: Any, project_config: Any, output: RenderNode, settings: dict[str, Any]) -> dict[str, Any]:
    if project_config is None:
        return {}
    try:
        from smartlib.review.playblast_package import resolve_playblast_package
    except Exception:
        return {}
    attrs = normalized_attrs("output", output.attrs)
    scene = _current_scene_path(cmds)
    context = _review_context(project_config, scene)
    version = attrs.get("version") or "v001"
    take = attrs.get("take") or "01"
    dept = attrs.get("dept") or "layout"
    layer = _clean_name(output.name, "CHA")
    output_paths = resolve_playblast_package(
        project_config,
        area="output",
        shot_root=context["shot_root"],
        shot=context["shot"],
        dept=dept,
        version=version,
        take=take,
        layer=layer,
    )
    publish_paths = resolve_playblast_package(
        project_config,
        area="publish",
        shot_root=context["shot_root"],
        shot=context["shot"],
        dept=dept,
        version=version,
        take=take,
        layer=layer,
    )
    return {
        "dept": dept,
        "layer": layer,
        "version": output_paths.root.parts[-2],
        "take": output_paths.root.parts[-1],
        "quality_preset": attrs.get("quality_preset") or "",
        "format": "image",
        "compression": "png",
        "output_path": output_paths.image_prefix.as_posix(),
        "package_root": output_paths.root.as_posix(),
        "publish_package_root": publish_paths.root.as_posix(),
        "image_sequence": output_paths.image_sequence.as_posix(),
        "image_pattern": output_paths.image_pattern,
        "movie_path": output_paths.mov.as_posix(),
        "slate_sequence": output_paths.slate_sequence.as_posix(),
        "slate_prefix": output_paths.slate_prefix.as_posix(),
        "slate_pattern": output_paths.slate_pattern,
        "metadata_review": output_paths.metadata_review.as_posix(),
        "metadata_playblast": output_paths.metadata_playblast.as_posix(),
        "metadata_source_scene": output_paths.metadata_source_scene.as_posix(),
        "thumbnail": output_paths.thumbnail.as_posix(),
        "ae_dir": output_paths.ae_dir.as_posix(),
        "source_scene": scene.as_posix() if scene else "",
        "shot_root": context["shot_root"].as_posix(),
        "shot": context["shot"],
        "episode": context["episode"],
        "sequence": context["sequence"],
        "fps": _project_fps(project_config),
    }


def _review_context(project_config: Any, scene: Path | None) -> dict[str, Any]:
    project_root = project_config.project_root or Path.cwd()
    episode = "unknown"
    sequence = "unknown"
    shot = _clean_name(scene.stem if scene else "", "shot")
    if scene:
        try:
            relative = scene.resolve().relative_to((project_root / "shots").resolve())
            if len(relative.parts) >= 3:
                episode, sequence, shot = relative.parts[0], relative.parts[1], relative.parts[2]
        except Exception:
            pass
    shot_root = project_root / "shots" / episode / sequence / shot
    return {"episode": episode, "sequence": sequence, "shot": shot, "shot_root": shot_root}


def _current_scene_path(cmds: Any) -> Path | None:
    if cmds is None:
        return None
    try:
        scene = cmds.file(query=True, sceneName=True) or ""
    except Exception:
        scene = ""
    return Path(scene) if scene else None


def _project_fps(project_config: Any) -> int:
    try:
        fps = ((project_config.base.get("anchors") or {}).get("fps")) or 24
        return int(fps)
    except Exception:
        return 24


def _resolve_frame_range(cmds: Any, settings: dict[str, Any], project_config: Any = None) -> tuple[int, int]:
    mode = _normalize_frame_mode(settings.get("frame_mode"))
    fallback = _custom_frame_range(settings)
    if mode == FRAME_MODE_EDITORIAL:
        return _editorial_frame_range(cmds, project_config) or _playback_frame_range(cmds) or fallback
    if mode == FRAME_MODE_SINGLE:
        frame = fallback[0]
        return frame, frame
    if mode == FRAME_MODE_TIME_RANGE:
        return _playback_frame_range(cmds) or fallback
    if mode == FRAME_MODE_RENDER_GLOBAL:
        return _render_global_frame_range(cmds) or fallback
    return fallback


def _editorial_frame_range(cmds: Any, project_config: Any) -> tuple[int, int] | None:
    if project_config is None:
        return None
    scene = _current_scene_path(cmds)
    context = _review_context(project_config, scene)
    try:
        data = read_json(Path(context["shot_root"]) / "shot.json", default={}) or {}
    except Exception:
        return None
    editorial = data.get("editorial") if isinstance(data, dict) else {}
    if not isinstance(editorial, dict):
        return None
    cut_in = editorial.get("cut_in")
    cut_out = editorial.get("cut_out")
    if cut_in is None or cut_out is None:
        return None
    start = _int_frame(cut_in, 1)
    end = _int_frame(cut_out, start)
    return start, end


def _custom_frame_range(settings: dict[str, Any]) -> tuple[int, int]:
    start = _int_frame(settings.get("start_frame"), 1)
    end = _int_frame(settings.get("end_frame"), start)
    return start, end


def _current_frame(cmds: Any) -> int | None:
    if cmds is None:
        return None
    try:
        return _int_frame(cmds.currentTime(query=True), 1)
    except Exception:
        return None


def _playback_frame_range(cmds: Any) -> tuple[int, int] | None:
    if cmds is None:
        return None
    try:
        start = cmds.playbackOptions(query=True, minTime=True)
        end = cmds.playbackOptions(query=True, maxTime=True)
    except Exception:
        return None
    return _int_frame(start, 1), _int_frame(end, _int_frame(start, 1))


def _render_global_frame_range(cmds: Any) -> tuple[int, int] | None:
    if cmds is None:
        return None
    try:
        start = cmds.getAttr("defaultRenderGlobals.startFrame")
        end = cmds.getAttr("defaultRenderGlobals.endFrame")
    except Exception:
        return None
    return _int_frame(start, 1), _int_frame(end, _int_frame(start, 1))


def _int_frame(value: Any, fallback: int) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return int(fallback)


def _objects_from_cast_node(cmds: Any, node: RenderNode, project_config: Any = None) -> list[str]:
    reference_node = str(node.attrs.get("reference_node") or "").strip()
    namespace = _namespace_from_reference_node(cmds, reference_node) if reference_node else ""
    namespace = namespace or _cast_namespace(cmds, node.attrs)
    if namespace:
        existing = _geometry_roots_in_namespace(cmds, namespace)
        if existing:
            node.attrs["namespace"] = namespace
            if reference_node:
                node.attrs["reference_node"] = reference_node
            return existing
    return []


def _cast_namespace(cmds: Any, attrs: dict[str, Any]) -> str:
    namespace = str(attrs.get("namespace") or "").strip(": ")
    if namespace:
        return namespace
    asset = str(attrs.get("asset") or "").strip()
    return _clean_namespace(asset) if asset else ""


def _namespace_from_reference_node(cmds: Any, reference_node: str) -> str:
    if not reference_node:
        return ""
    try:
        namespace = cmds.referenceQuery(reference_node, namespace=True)
    except Exception:
        return ""
    return str(namespace or "").strip(": ")


def _geometry_roots_in_namespace(cmds: Any, namespace: str) -> list[str]:
    shapes = []
    for shape_type in ("mesh", "nurbsSurface", "subdiv", "gpuCache", "mayaUsdProxyShape"):
        try:
            shapes.extend(str(item) for item in (cmds.ls(f"{namespace}:*", type=shape_type, long=True) or []))
        except Exception:
            pass
    roots = []
    for shape in shapes:
        try:
            parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        except Exception:
            parents = []
        roots.extend(str(parent) for parent in parents)
    return _dedupe(roots)


def _geometry_roots_from_nodes(cmds: Any, nodes: list[str]) -> list[str]:
    roots = []
    geometry_types = {"mesh", "nurbsSurface", "subdiv", "gpuCache", "mayaUsdProxyShape"}
    for node in nodes:
        if not cmds.objExists(node):
            continue
        try:
            node_type = cmds.nodeType(node)
        except Exception:
            node_type = ""
        if node_type in geometry_types:
            try:
                roots.extend(str(parent) for parent in (cmds.listRelatives(node, parent=True, fullPath=True) or []))
            except Exception:
                pass
            continue
        try:
            descendants = cmds.listRelatives(node, allDescendents=True, fullPath=True) or []
        except Exception:
            descendants = []
        for child in descendants:
            try:
                child_type = cmds.nodeType(child)
            except Exception:
                child_type = ""
            if child_type not in geometry_types:
                continue
            try:
                roots.extend(str(parent) for parent in (cmds.listRelatives(child, parent=True, fullPath=True) or []))
            except Exception:
                pass
    return _dedupe(roots)


def _clean_namespace(name: str) -> str:
    cleaned = "".join(char if char.isalnum() or char == "_" else "_" for char in str(name).strip())
    return cleaned.strip("_") or "cast"


def _apply_material(cmds: Any, objects: list[str], attrs: dict[str, Any]) -> None:
    if not objects:
        return
    material = str(attrs.get("material_name") or "red_plastic_MAT").strip()
    shader = str(attrs.get("shader") or "lambert").strip()
    color = attrs.get("color") or [1.0, 0.02, 0.02]
    if not cmds.objExists(material):
        material = cmds.shadingNode(shader, asShader=True, name=material)
        if cmds.objExists(f"{material}.color"):
            cmds.setAttr(f"{material}.color", float(color[0]), float(color[1]), float(color[2]), type="double3")
    shading_group = f"{material}SG"
    if not cmds.objExists(shading_group):
        shading_group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=shading_group)
        cmds.connectAttr(f"{material}.outColor", f"{shading_group}.surfaceShader", force=True)
    for obj in objects:
        if cmds.objExists(obj):
            cmds.sets(obj, edit=True, forceElement=shading_group)


_GEOM_TRANSFORM_ATTRS = ("visibility", "template")
_GEOM_SHAPE_ATTRS = ("visibility", "template", "castsShadows", "receiveShadows", "motionBlur")
_GEOM_STATE_ATTRS = tuple(dict.fromkeys((*_GEOM_TRANSFORM_ATTRS, *_GEOM_SHAPE_ATTRS)))


def _apply_geom_attrs(cmds: Any, objects: list[str], attrs: dict[str, Any]) -> None:
    for obj in objects:
        for attr in _GEOM_TRANSFORM_ATTRS:
            if attr in attrs:
                _set_bool_attr_if_exists(cmds, obj, attr, attrs.get(attr))
        for shape in _geometry_shapes_for_object(cmds, obj):
            for attr in _GEOM_SHAPE_ATTRS:
                if attr in attrs:
                    _set_bool_attr_if_exists(cmds, shape, attr, attrs.get(attr))


def _apply_visibility(cmds: Any, objects: list[str], attrs: dict[str, Any]) -> None:
    _apply_geom_attrs(cmds, objects, attrs)


def _set_bool_attr_if_exists(cmds: Any, node: str, attr: str, value: Any) -> None:
    full = f"{node}.{attr}"
    if cmds.objExists(full):
        try:
            cmds.setAttr(full, bool(value))
        except Exception:
            pass


def _geometry_shapes_for_object(cmds: Any, obj: str) -> list[str]:
    if not cmds.objExists(obj):
        return []
    geometry_types = {"mesh", "nurbsSurface", "subdiv", "gpuCache", "mayaUsdProxyShape"}
    shapes = []
    try:
        if cmds.nodeType(obj) in geometry_types:
            shapes.append(obj)
    except Exception:
        pass
    try:
        shapes.extend(str(item) for item in (cmds.listRelatives(obj, shapes=True, fullPath=True) or []))
    except Exception:
        pass
    try:
        descendants = cmds.listRelatives(obj, allDescendents=True, fullPath=True) or []
    except Exception:
        descendants = []
    for child in descendants:
        try:
            if cmds.nodeType(child) in geometry_types:
                shapes.append(str(child))
        except Exception:
            pass
    return _dedupe(shapes)


def _capture_geom_attrs(cmds: Any, obj: str) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for node in _dedupe([obj, *_geometry_shapes_for_object(cmds, obj)]):
        node_values: dict[str, Any] = {}
        for attr in _GEOM_STATE_ATTRS:
            full = f"{node}.{attr}"
            if not cmds.objExists(full):
                continue
            try:
                node_values[attr] = cmds.getAttr(full)
            except Exception:
                pass
        if node_values:
            values[node] = node_values
    return values


def _restore_geom_attrs(cmds: Any, values: dict[str, dict[str, Any]]) -> None:
    for node, attrs in values.items():
        for attr, value in attrs.items():
            full = f"{node}.{attr}"
            if not cmds.objExists(full):
                continue
            try:
                cmds.setAttr(full, value)
            except Exception:
                pass


def _apply_transform_override(cmds: Any, objects: list[str], attrs: dict[str, Any]) -> None:
    source = _resolve_transform_source(cmds, str(attrs.get("match_source") or "").strip())
    source_matrix = _query_world_matrix(cmds, source) if source else []
    for obj in objects:
        if not cmds.objExists(obj):
            continue
        if source_matrix:
            try:
                cmds.xform(obj, worldSpace=True, matrix=source_matrix)
            except Exception:
                pass
            continue
        if attrs.get("translate_enabled"):
            cmds.xform(obj, worldSpace=True, translation=_float3(attrs.get("translate"), [0.0, 0.0, 0.0]))
        if attrs.get("rotate_enabled"):
            cmds.xform(obj, worldSpace=True, rotation=_float3(attrs.get("rotate"), [0.0, 0.0, 0.0]))
        if attrs.get("scale_enabled"):
            cmds.xform(obj, worldSpace=True, scale=_float3(attrs.get("scale"), [1.0, 1.0, 1.0]))


def _resolve_transform_source(cmds: Any, ref: str) -> str:
    if not ref:
        return ""
    try:
        if cmds.objExists(ref) and cmds.nodeType(ref) in {"transform", "joint"}:
            return ref
    except Exception:
        pass
    try:
        matches = [str(item) for item in (cmds.ls(ref, long=True) or [])]
    except Exception:
        matches = []
    for item in matches:
        try:
            if cmds.objExists(item) and cmds.nodeType(item) in {"transform", "joint"}:
                return item
        except Exception:
            pass
    return ""


def record_transform_override(cmds: Any, objects: list[str], attrs: dict[str, Any]) -> tuple[bool, str]:
    target = next((obj for obj in objects if cmds.objExists(obj)), "")
    if not target:
        return False, ""
    values = _capture_transform_values(cmds, target)
    if not all(values.values()):
        return False, target
    attrs["translate"] = values["translate"]
    attrs["rotate"] = values["rotate"]
    attrs["scale"] = values["scale"]
    attrs["translate_enabled"] = True
    attrs["rotate_enabled"] = True
    attrs["scale_enabled"] = True
    return True, target


def start_transform_recording(cmds: Any, objects: list[str], attrs: dict[str, Any]) -> tuple[bool, str]:
    target = next((obj for obj in objects if cmds.objExists(obj)), "")
    if not target:
        attrs["recording"] = False
        attrs["record_start_transform"] = {}
        return False, ""
    values = _capture_transform_values(cmds, target)
    if not all(values.values()):
        attrs["recording"] = False
        attrs["record_start_transform"] = {}
        return False, target
    attrs["recording"] = True
    attrs["record_start_transform"] = {"target": target, **values}
    return True, target


def finish_transform_recording(cmds: Any, objects: list[str], attrs: dict[str, Any]) -> tuple[bool, str]:
    recorded, target = record_transform_override(cmds, objects, attrs)
    start_state = attrs.get("record_start_transform") if isinstance(attrs.get("record_start_transform"), dict) else {}
    restore_target = str(start_state.get("target") or target)
    if restore_target and start_state:
        _restore_transform_values(cmds, restore_target, start_state)
    attrs["recording"] = False
    attrs["record_start_transform"] = {}
    return recorded, target


def _capture_transform_values(cmds: Any, obj: str) -> dict[str, list[float]]:
    return {
        "translate": _query_transform3(cmds, obj, {"query": True, "worldSpace": True, "translation": True}),
        "rotate": _query_transform3(cmds, obj, {"query": True, "worldSpace": True, "rotation": True}),
        "scale": _query_transform3(cmds, obj, {"query": True, "worldSpace": True, "scale": True}),
    }


def _restore_transform_values(cmds: Any, obj: str, values: dict[str, Any]) -> None:
    if not cmds.objExists(obj):
        return
    for key, kwargs in (
        ("translate", {"worldSpace": True, "translation": _float3(values.get("translate"), [0.0, 0.0, 0.0])}),
        ("rotate", {"worldSpace": True, "rotation": _float3(values.get("rotate"), [0.0, 0.0, 0.0])}),
        ("scale", {"worldSpace": True, "scale": _float3(values.get("scale"), [1.0, 1.0, 1.0])}),
    ):
        if values.get(key):
            try:
                cmds.xform(obj, **kwargs)
            except Exception:
                pass


def _query_transform3(cmds: Any, obj: str, kwargs: dict[str, Any]) -> list[float]:
    try:
        return [round(float(value), 6) for value in cmds.xform(obj, **kwargs)[:3]]
    except Exception:
        return []


def _query_world_matrix(cmds: Any, obj: str) -> list[float]:
    if not obj:
        return []
    try:
        values = cmds.xform(obj, query=True, worldSpace=True, matrix=True) or []
        return [round(float(value), 6) for value in values[:16]]
    except Exception:
        return []


def _set_viewport_camera(cmds: Any, camera: str, set_viewport: bool) -> str:
    if not camera or not cmds.objExists(camera):
        return ""
    panel = _target_model_panel(cmds)
    if set_viewport:
        _set_model_panel_camera(cmds, panel, camera)
    return panel


def _set_model_panel_camera(cmds: Any, panel: str, camera: str) -> str:
    panel = panel or _target_model_panel(cmds)
    if panel:
        try:
            cmds.modelPanel(panel, edit=True, camera=camera)
            return panel
        except Exception:
            pass
    try:
        cmds.lookThru(camera)
    except Exception:
        pass
    return panel


def _target_model_panel(cmds: Any) -> str:
    try:
        panel = cmds.getPanel(withFocus=True)
    except Exception:
        panel = ""
    if panel:
        try:
            if cmds.getPanel(typeOf=panel) == "modelPanel":
                return str(panel)
        except Exception:
            pass
    try:
        for panel in cmds.getPanel(visiblePanels=True) or []:
            try:
                if cmds.getPanel(typeOf=panel) == "modelPanel":
                    return str(panel)
            except Exception:
                pass
    except Exception:
        pass
    try:
        panels = cmds.getPanel(type="modelPanel") or []
    except Exception:
        panels = []
    return str(panels[0]) if panels else ""


CAMERA_OVERRIDE_ATTRS = (
    ("overscan", "override_overscan", float),
    ("depthOfField", "override_depthOfField", bool),
    ("focusDistance", "override_focusDistance", float),
    ("fStop", "override_fStop", float),
    ("nearClipPlane", "override_nearClipPlane", float),
    ("farClipPlane", "override_farClipPlane", float),
)


def _apply_camera_overrides(cmds: Any, camera: str, attrs: dict[str, Any]) -> None:
    camera_shape = _camera_shape(cmds, camera)
    if not camera_shape:
        return
    for attr, enabled_attr, caster in CAMERA_OVERRIDE_ATTRS:
        if not attrs.get(enabled_attr):
            continue
        full = f"{camera_shape}.{attr}"
        if not cmds.objExists(full):
            continue
        try:
            cmds.setAttr(full, caster(attrs.get(attr)))
        except Exception:
            pass


def _apply_render_settings(cmds: Any, settings: dict[str, Any], camera: str, panel: str = "") -> None:
    width = int(settings.get("width") or 1920)
    height = int(settings.get("height") or 1080)
    start = int(settings.get("start_frame") or 1)
    end = int(settings.get("end_frame") or start)
    cmds.setAttr("defaultResolution.width", width)
    cmds.setAttr("defaultResolution.height", height)
    cmds.playbackOptions(minTime=start, maxTime=end, animationStartTime=start, animationEndTime=end)
    if camera:
        _set_model_panel_camera(cmds, panel, camera)


def _playblast(
    cmds: Any,
    settings: dict[str, Any],
    camera: str,
    project_config: Any = None,
    panel: str = "",
    progress_callback: Any = None,
) -> str:
    output = str(settings.get("output_path") or "").strip()
    if not output:
        raise RuntimeError("Output Path is empty.")
    path = Path(output.replace("\\", "/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    if camera:
        panel = _set_model_panel_camera(cmds, panel, camera)
    start = int(settings.get("start_frame") or 1)
    end = int(settings.get("end_frame") or start)
    width_height = [int(settings.get("width") or 1920), int(settings.get("height") or 1080)]
    compression = str(settings.get("compression") or "png")
    slate_prefix = str(settings.get("slate_prefix") or "").strip()
    slate_roots = _smart_gate_guide_roots(cmds) if slate_prefix else []
    preset = str(settings.get("quality_preset") or "")
    playblast_kwargs = {
        "startTime": start,
        "endTime": end,
        "format": str(settings.get("format") or "image"),
        "filename": str(path),
        "forceOverwrite": True,
        "sequenceTime": False,
        "clearCache": True,
        "viewer": False,
        "showOrnaments": bool(settings.get("show_ornaments", True)),
        "percent": int(settings.get("percent") or 100),
        "compression": compression,
        "widthHeight": width_height,
    }
    if panel:
        playblast_kwargs["editorPanelName"] = panel
    suffix = f".{compression.lstrip('.')}"
    _remove_existing_playblast_frames(path, start, end, suffix)
    _emit_progress(progress_callback, "Running playblast image sequence...", 45)
    beauty_visibility = _hide_slate_objects(cmds, slate_roots)
    try:
        with _playblast_preset_context(project_config, preset):
            _clear_selection(cmds)
            cmds.playblast(**playblast_kwargs)
    finally:
        _restore_visibility_values(cmds, beauty_visibility)
    _emit_progress(progress_callback, "Normalizing image sequence...", 62)
    _normalize_playblast_sequence(path, start, end, suffix)
    slate_written = False
    if slate_prefix and slate_roots:
        slate_path = Path(slate_prefix.replace("\\", "/"))
        slate_path.parent.mkdir(parents=True, exist_ok=True)
        slate_kwargs = dict(playblast_kwargs)
        slate_kwargs.update({"format": "image", "filename": str(slate_path)})
        _remove_existing_playblast_frames(slate_path, start, end, suffix)
        slate_visibility = _isolate_slate_objects(cmds, slate_roots)
        try:
            _emit_progress(progress_callback, "Running slate playblast...", 68)
            with _playblast_preset_context(project_config, preset):
                _clear_selection(cmds)
                cmds.playblast(**slate_kwargs)
        finally:
            _restore_visibility_values(cmds, slate_visibility)
        _emit_progress(progress_callback, "Normalizing slate sequence...", 76)
        _normalize_playblast_sequence(slate_path, start, end, suffix)
        slate_written = True
    _emit_progress(progress_callback, "Writing package metadata...", 82)
    _write_playblast_package_metadata(cmds, settings, camera, start, end, slate_written)
    _emit_progress(progress_callback, "Encoding review movie...", 90)
    _encode_playblast_movie(settings, start, slate_written, project_config)
    _emit_progress(progress_callback, "Creating thumbnail from movie...", 94)
    _create_thumbnail(settings, start, suffix, project_config)
    return str(path)


def _clear_selection(cmds: Any) -> None:
    try:
        cmds.select(clear=True)
    except Exception:
        pass


def _playblast_preset_context(project_config: Any, preset: str):
    if not project_config or not preset:
        return nullcontext()
    try:
        from smartlib.dcc.maya.playblast_preset import applied_playblast_preset

        return applied_playblast_preset(project_config, preset)
    except Exception:
        return nullcontext()


def _apply_quality_preset(settings: dict[str, Any], project_config: Any) -> None:
    preset = str(settings.get("quality_preset") or "")
    if not project_config or not preset:
        return
    try:
        from smartlib.dcc.maya.playblast_preset import apply_playblast_preset

        apply_playblast_preset(project_config, preset)
    except Exception:
        pass


def _normalize_playblast_sequence(prefix: Path, start_frame: int, end_frame: int, suffix: str) -> None:
    for frame in range(start_frame, end_frame + 1):
        frame_text = f"{frame:04d}"
        target = prefix.parent / f"{prefix.name}_{frame_text}{suffix}"
        candidates = [
            prefix.parent / f"{prefix.name}.{frame_text}{suffix}",
            prefix.parent / f"{prefix.name}_.{frame_text}{suffix}",
            prefix.parent / f"{prefix.name}_{frame_text}{suffix}",
        ]
        for source in candidates:
            if source == target or not source.exists():
                continue
            if target.exists():
                target.unlink()
            source.rename(target)
            break


def _remove_existing_playblast_frames(prefix: Path, start_frame: int, end_frame: int, suffix: str) -> None:
    for frame in range(start_frame, end_frame + 1):
        frame_text = f"{frame:04d}"
        candidates = [
            prefix.parent / f"{prefix.name}_{frame_text}{suffix}",
            prefix.parent / f"{prefix.name}.{frame_text}{suffix}",
            prefix.parent / f"{prefix.name}_.{frame_text}{suffix}",
        ]
        for path in candidates:
            try:
                if path.exists() and path.is_file():
                    path.unlink()
            except Exception:
                pass


def _has_smart_gate_guide(cmds: Any) -> bool:
    return bool(_smart_gate_guide_nodes(cmds))


def _smart_gate_guide_nodes(cmds: Any) -> list[str]:
    nodes = []
    for node_type in ("SmartViewportGateGuide", "SmartGateGuide", "SmartGateGuid"):
        try:
            nodes.extend(str(item) for item in (cmds.ls(type=node_type, long=True) or []))
        except Exception:
            pass
    for pattern in ("SmartGateGuide*", "SmartGateGuid*", "*SmartGateGuide*", "*SmartGateGuid*"):
        try:
            nodes.extend(str(item) for item in (cmds.ls(pattern, long=True) or []))
        except Exception:
            pass
    return _dedupe(nodes)


def _smart_gate_guide_roots(cmds: Any) -> list[str]:
    roots = []
    for node in _smart_gate_guide_nodes(cmds):
        if not cmds.objExists(node):
            continue
        try:
            if cmds.nodeType(node) in {"transform", "joint"}:
                roots.append(node)
                continue
        except Exception:
            pass
        try:
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
        except Exception:
            parents = []
        roots.append(str(parents[0] if parents else node))
    return _dedupe(roots)


def _isolate_slate_objects(cmds: Any, slate_roots: list[str]) -> dict[str, Any]:
    slate_set = set(_expand_to_transforms(cmds, slate_roots))
    candidates = _dedupe([*_isolation_candidates(cmds), *slate_set])
    values = {}
    for obj in candidates:
        attr = f"{obj}.visibility"
        if not cmds.objExists(attr):
            continue
        try:
            values[attr] = cmds.getAttr(attr)
        except Exception:
            pass
        try:
            cmds.setAttr(attr, obj in slate_set)
        except Exception:
            pass
    return values


def _hide_slate_objects(cmds: Any, slate_roots: list[str]) -> dict[str, Any]:
    values = {}
    for obj in _expand_to_transforms(cmds, slate_roots):
        attr = f"{obj}.visibility"
        if not cmds.objExists(attr):
            continue
        try:
            values[attr] = cmds.getAttr(attr)
        except Exception:
            pass
        try:
            cmds.setAttr(attr, False)
        except Exception:
            pass
    return values


def _restore_visibility_values(cmds: Any, values: dict[str, Any]) -> None:
    for attr, value in values.items():
        if not cmds.objExists(attr):
            continue
        try:
            cmds.setAttr(attr, value)
        except Exception:
            pass


def _write_playblast_package_metadata(cmds: Any, settings: dict[str, Any], camera: str, start: int, end: int, slate_written: bool) -> None:
    package_root_text = str(settings.get("package_root") or "")
    if not package_root_text:
        return
    package_root = Path(package_root_text)
    now = datetime.now().isoformat(timespec="seconds")
    layer = str(settings.get("layer") or "CHA")
    review_data = {
        "publish_type": "review",
        "status": "output",
        "episode": settings.get("episode", ""),
        "sequence": settings.get("sequence", ""),
        "shot": settings.get("shot", ""),
        "department": settings.get("dept", ""),
        "version": settings.get("version", ""),
        "take": settings.get("take", ""),
        "fps": settings.get("fps", 24),
        "frame_range": [start, end],
        "movie": _relative_to(package_root, Path(str(settings.get("movie_path") or ""))),
        "layers": {
            layer: {
                "file": _relative_to(package_root, Path(str(settings.get("image_sequence") or ""))),
                "camera": camera,
                "resolution": [int(settings.get("width") or 1920), int(settings.get("height") or 1080)],
                "order": 0,
                "ae_slot": layer,
            }
        },
        "slate": _relative_to(package_root, Path(str(settings.get("slate_sequence") or ""))) if slate_written else "",
        "thumbnail": _relative_to(package_root, Path(str(settings.get("thumbnail") or ""))),
        "ae": {"project": "ae/review_project.aep", "layer_order": [layer]},
    }
    playblast_data = {
        "created_at": now,
        "area": "output",
        "quality_preset": settings.get("quality_preset", ""),
        "image_sequence": review_data["layers"][layer]["file"],
        "slate_sequence": review_data["slate"],
        "movie": review_data["movie"],
        "compression": settings.get("compression", "png"),
    }
    source_scene = {
        "created_at": now,
        "scene": settings.get("source_scene", ""),
        "camera": camera,
    }
    for key, data in (
        ("metadata_review", review_data),
        ("metadata_playblast", playblast_data),
        ("metadata_source_scene", source_scene),
    ):
        path = str(settings.get(key) or "")
        if path:
            write_json(path, data)
    ae_dir = str(settings.get("ae_dir") or "")
    if ae_dir:
        Path(ae_dir).mkdir(parents=True, exist_ok=True)


def _create_thumbnail(settings: dict[str, Any], start: int, suffix: str, project_config: Any) -> None:
    thumbnail = str(settings.get("thumbnail") or "")
    movie_path = str(settings.get("movie_path") or "")
    if thumbnail and movie_path:
        try:
            from smartlib.review.playblast_package import extract_thumbnail_from_mov, find_ffmpeg

            ok, _message = extract_thumbnail_from_mov(
                mov_path=movie_path,
                thumbnail_path=thumbnail,
                ffmpeg=find_ffmpeg(project_config),
            )
            if ok:
                return
        except Exception:
            pass
    _copy_thumbnail_from_sequence(settings, start, suffix)


def _copy_thumbnail_from_sequence(settings: dict[str, Any], start: int, suffix: str) -> None:
    thumbnail = str(settings.get("thumbnail") or "")
    output = str(settings.get("output_path") or "")
    if not thumbnail or not output:
        return
    source = Path(output).parent / f"{Path(output).name}_{start:04d}{suffix}"
    if not source.exists():
        return
    target = Path(thumbnail)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(source, target)
    except Exception:
        pass


def _encode_playblast_movie(settings: dict[str, Any], start: int, slate_written: bool, project_config: Any) -> None:
    movie_path = str(settings.get("movie_path") or "")
    image_pattern = str(settings.get("image_pattern") or "")
    if not movie_path or not image_pattern:
        return
    try:
        from smartlib.review.playblast_package import encode_prores_proxy_mov, find_ffmpeg

        encode_prores_proxy_mov(
            image_pattern=image_pattern,
            mov_path=movie_path,
            start_frame=start,
            fps=int(settings.get("fps") or 24),
            ffmpeg=find_ffmpeg(project_config),
            slate_pattern=str(settings.get("slate_pattern") or "") if slate_written else "",
        )
    except Exception:
        pass


def _isolate_final_objects(cmds: Any, objects: list[str]) -> None:
    final_objects = set(_expand_to_transforms(cmds, objects))
    for obj in _isolation_candidates(cmds):
        if not cmds.objExists(f"{obj}.visibility"):
            continue
        try:
            cmds.setAttr(f"{obj}.visibility", obj in final_objects)
        except Exception:
            pass


def _expand_to_transforms(cmds: Any, objects: list[str]) -> list[str]:
    transforms = []
    for obj in objects:
        if not cmds.objExists(obj):
            continue
        node = obj
        try:
            if cmds.nodeType(obj) not in {"transform", "joint"}:
                parents = cmds.listRelatives(obj, parent=True, fullPath=True) or []
                node = parents[0] if parents else obj
        except Exception:
            pass
        transforms.append(str(node))
    return _dedupe(transforms)


def _warn_duplicate_short_names(objects: list[str], warnings: list[str] | None) -> None:
    by_short: dict[str, list[str]] = {}
    for obj in objects:
        short = str(obj).rsplit("|", 1)[-1]
        if not short:
            continue
        by_short.setdefault(short, []).append(str(obj))
    for short, matches in by_short.items():
        unique = _dedupe(matches)
        if len(unique) < 2:
            continue
        _append_warning(
            warnings,
            f'Duplicate DAG short name "{short}" is present in output objects. Full paths are used, but avoid short-name object entries.',
        )


def _format_apply_warnings(messages: list[str], prefix: str = "Smart Render warning.") -> str:
    if not messages:
        return prefix
    first = messages[0]
    suffix = f" (+{len(messages) - 1} more)" if len(messages) > 1 else ""
    return f"{prefix} {first}{suffix}"


def _emit_progress(progress_callback: Any, message: str, value: int) -> None:
    if not progress_callback:
        return
    try:
        progress_callback(message, int(value))
    except Exception:
        pass


def _isolation_candidates(cmds: Any) -> list[str]:
    transforms = []
    geometry_types = ("mesh", "nurbsSurface", "subdiv", "gpuCache", "mayaUsdProxyShape")
    for shape_type in geometry_types:
        try:
            shapes = cmds.ls(type=shape_type, long=True) or []
        except Exception:
            shapes = []
        for shape in shapes:
            try:
                parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
            except Exception:
                parents = []
            transforms.extend(str(parent) for parent in parents)
    return _dedupe(transforms)


def _output_node(graph: RenderGraph, output_node_id: str | None) -> RenderNode | None:
    return graph.node(output_node_id) if output_node_id else (graph.output_nodes()[0] if graph.output_nodes() else None)


def _capture_scene_state(cmds: Any, objects: list[str], camera: str = "") -> dict[str, Any]:
    scene_objects = _dedupe([*_expand_to_transforms(cmds, objects), *_isolation_candidates(cmds)])
    return {
        "objects": {obj: _capture_object_state(cmds, obj) for obj in scene_objects if cmds.objExists(obj)},
        "camera": _capture_camera_state(cmds, camera),
        "render_settings": _capture_render_settings(cmds),
        "viewport": _capture_viewport_state(cmds),
    }


def _restore_scene_state(cmds: Any, state: dict[str, Any]) -> None:
    for obj, object_state in (state.get("objects") or {}).items():
        _restore_object_state(cmds, obj, object_state)
    _restore_camera_state(cmds, state.get("camera") or {})
    _restore_render_settings(cmds, state.get("render_settings") or {})
    _restore_viewport_state(cmds, state.get("viewport") or {})
    try:
        cmds.refresh(force=True)
    except Exception:
        pass


def _read_scene_state_data(cmds: Any) -> dict[str, Any]:
    node = _ensure_scene_state_node(cmds)
    attr = f"{node}.{SCENE_STATE_ATTR}"
    try:
        raw = cmds.getAttr(attr) or ""
    except Exception:
        raw = ""
    if not raw:
        return {"schema": "smart_render_scene_state", "version": 1, "master_states": {}}
    try:
        data = json.loads(raw)
    except Exception:
        return {"schema": "smart_render_scene_state", "version": 1, "master_states": {}}
    if not isinstance(data, dict):
        return {"schema": "smart_render_scene_state", "version": 1, "master_states": {}}
    data.setdefault("schema", "smart_render_scene_state")
    data.setdefault("version", 1)
    data.setdefault("master_states", {})
    return data


def _write_scene_state_data(cmds: Any, data: dict[str, Any]) -> None:
    node = _ensure_scene_state_node(cmds)
    attr = f"{node}.{SCENE_STATE_ATTR}"
    text = json.dumps(data, ensure_ascii=False, sort_keys=True)
    cmds.setAttr(attr, text, type="string")


def _ensure_scene_state_node(cmds: Any) -> str:
    if not cmds.objExists(SCENE_STATE_NODE):
        node = cmds.createNode("network", name=SCENE_STATE_NODE)
    else:
        node = SCENE_STATE_NODE
    attr = f"{node}.{SCENE_STATE_ATTR}"
    if not cmds.objExists(attr):
        cmds.addAttr(node, longName=SCENE_STATE_ATTR, dataType="string")
        cmds.setAttr(attr, "", type="string")
    return node


def _capture_object_state(cmds: Any, obj: str) -> dict[str, Any]:
    state: dict[str, Any] = {}
    if cmds.objExists(f"{obj}.visibility"):
        try:
            state["visibility"] = cmds.getAttr(f"{obj}.visibility")
        except Exception:
            pass
    state["geom_attrs"] = _capture_geom_attrs(cmds, obj)
    for key, kwargs in (
        ("translate", {"query": True, "worldSpace": True, "translation": True}),
        ("rotate", {"query": True, "worldSpace": True, "rotation": True}),
        ("scale", {"query": True, "worldSpace": True, "scale": True}),
    ):
        try:
            state[key] = cmds.xform(obj, **kwargs)
        except Exception:
            pass
    try:
        state["shading_engines"] = [str(item) for item in (cmds.listConnections(obj, type="shadingEngine") or [])]
    except Exception:
        state["shading_engines"] = []
    state["material_assignments"] = _capture_material_assignments(cmds, obj)
    return state


def _restore_object_state(cmds: Any, obj: str, state: dict[str, Any]) -> None:
    if not cmds.objExists(obj):
        return
    if "visibility" in state and cmds.objExists(f"{obj}.visibility"):
        try:
            cmds.setAttr(f"{obj}.visibility", state["visibility"])
        except Exception:
            pass
    _restore_geom_attrs(cmds, state.get("geom_attrs") or {})
    if "translate" in state:
        try:
            cmds.xform(obj, worldSpace=True, translation=state["translate"])
        except Exception:
            pass
    if "rotate" in state:
        try:
            cmds.xform(obj, worldSpace=True, rotation=state["rotate"])
        except Exception:
            pass
    if "scale" in state:
        try:
            cmds.xform(obj, worldSpace=True, scale=state["scale"])
        except Exception:
            pass
    for shading_engine in state.get("shading_engines") or []:
        if cmds.objExists(shading_engine):
            try:
                cmds.sets(obj, edit=True, forceElement=shading_engine)
            except Exception:
                pass
    _restore_material_assignments(cmds, state.get("material_assignments") or {})


def _capture_material_assignments(cmds: Any, obj: str) -> dict[str, list[str]]:
    assignments: dict[str, list[str]] = {}
    object_members = _material_members(cmds, obj)
    for member in object_members:
        try:
            engines = [str(item) for item in (cmds.listConnections(member, type="shadingEngine") or [])]
        except Exception:
            engines = []
        for engine in engines:
            matched_members = _shading_engine_members_for_object(cmds, engine, obj, object_members)
            if not matched_members:
                matched_members = [member]
            for matched_member in matched_members:
                assignments.setdefault(matched_member, []).append(engine)
    return {member: _dedupe(engines) for member, engines in assignments.items()}


def _shading_engine_members_for_object(cmds: Any, shading_engine: str, obj: str, object_members: list[str]) -> list[str]:
    try:
        members = [str(item) for item in (cmds.sets(shading_engine, query=True) or [])]
    except Exception:
        return []
    matched = []
    for member in members:
        if _is_object_material_member(member, obj, object_members):
            matched.append(member)
    return _dedupe(matched)


def _is_object_material_member(member: str, obj: str, object_members: list[str]) -> bool:
    roots = {obj, *object_members}
    component_root = member.split(".", 1)[0]
    for root in roots:
        if member == root or component_root == root or member.startswith(f"{root}."):
            return True
        root_short = root.rsplit("|", 1)[-1]
        component_short = component_root.rsplit("|", 1)[-1]
        if root_short and (component_short == root_short or member.startswith(f"{root_short}.")):
            return True
    return False


def _restore_material_assignments(cmds: Any, assignments: dict[str, Any]) -> None:
    if not isinstance(assignments, dict):
        return
    for member, engines in assignments.items():
        if not cmds.objExists(str(member)):
            continue
        for shading_engine in engines or []:
            if not cmds.objExists(str(shading_engine)):
                continue
            try:
                cmds.sets(str(member), edit=True, forceElement=str(shading_engine))
            except Exception:
                pass


def _material_members(cmds: Any, obj: str) -> list[str]:
    members = [obj]
    try:
        shapes = cmds.listRelatives(obj, shapes=True, fullPath=True) or []
    except Exception:
        shapes = []
    members.extend(str(shape) for shape in shapes)
    return _dedupe(members)


def _capture_camera_state(cmds: Any, camera: str) -> dict[str, Any]:
    camera_shape = _camera_shape(cmds, camera)
    if not camera_shape:
        return {}
    state = {"shape": camera_shape}
    for attr, _enabled_attr, _caster in CAMERA_OVERRIDE_ATTRS:
        full = f"{camera_shape}.{attr}"
        if not cmds.objExists(full):
            continue
        try:
            state[attr] = cmds.getAttr(full)
        except Exception:
            pass
    return state


def _restore_camera_state(cmds: Any, state: dict[str, Any]) -> None:
    camera_shape = str(state.get("shape") or "")
    if not camera_shape or not cmds.objExists(camera_shape):
        return
    for attr, _enabled_attr, caster in CAMERA_OVERRIDE_ATTRS:
        if attr not in state:
            continue
        full = f"{camera_shape}.{attr}"
        if not cmds.objExists(full):
            continue
        try:
            cmds.setAttr(full, caster(state[attr]))
        except Exception:
            pass


def _camera_shape(cmds: Any, camera: str) -> str:
    if not camera or not cmds.objExists(camera):
        return ""
    try:
        if cmds.nodeType(camera) == "camera":
            return camera
    except Exception:
        pass
    try:
        shapes = cmds.listRelatives(camera, shapes=True, type="camera", fullPath=True) or []
    except Exception:
        shapes = []
    return str(shapes[0]) if shapes else ""


def _capture_render_settings(cmds: Any) -> dict[str, Any]:
    state = {}
    for key, attr in (("width", "defaultResolution.width"), ("height", "defaultResolution.height")):
        try:
            state[key] = cmds.getAttr(attr)
        except Exception:
            pass
    for key, flag in (
        ("min_time", "minTime"),
        ("max_time", "maxTime"),
        ("animation_start_time", "animationStartTime"),
        ("animation_end_time", "animationEndTime"),
    ):
        try:
            state[key] = cmds.playbackOptions(query=True, **{flag: True})
        except Exception:
            pass
    return state


def _restore_render_settings(cmds: Any, state: dict[str, Any]) -> None:
    if "width" in state:
        try:
            cmds.setAttr("defaultResolution.width", int(state["width"]))
        except Exception:
            pass
    if "height" in state:
        try:
            cmds.setAttr("defaultResolution.height", int(state["height"]))
        except Exception:
            pass
    kwargs = {}
    mapping = {
        "min_time": "minTime",
        "max_time": "maxTime",
        "animation_start_time": "animationStartTime",
        "animation_end_time": "animationEndTime",
    }
    for key, flag in mapping.items():
        if key in state:
            kwargs[flag] = state[key]
    if kwargs:
        try:
            cmds.playbackOptions(**kwargs)
        except Exception:
            pass


def _capture_viewport_state(cmds: Any) -> dict[str, Any]:
    display_state = _capture_viewport_display_state(cmds)
    try:
        panel = cmds.getPanel(withFocus=True)
    except Exception:
        panel = ""
    if not panel:
        panel = _target_model_panel(cmds)
    try:
        if not panel or cmds.getPanel(typeOf=panel) != "modelPanel":
            return {"display": display_state} if display_state else {}
    except Exception:
        return {"display": display_state} if display_state else {}
    try:
        camera = cmds.modelPanel(panel, query=True, camera=True)
    except Exception:
        return {"panel": panel, "display": display_state}
    return {
        "panel": panel,
        "camera": camera,
        "view_camera": _capture_view_camera_state(cmds, str(camera or "")),
        "display": display_state,
    }


def _restore_viewport_state(cmds: Any, state: dict[str, Any]) -> None:
    _restore_viewport_display_state(cmds, state.get("display") or {})
    panel = str(state.get("panel") or "")
    camera = str(state.get("camera") or "")
    if not panel or not camera or not cmds.objExists(camera):
        return
    try:
        if cmds.getPanel(typeOf=panel) == "modelPanel":
            cmds.modelPanel(panel, edit=True, camera=camera)
    except Exception:
        pass
    _restore_view_camera_state(cmds, state.get("view_camera") or {})


VIEWPORT_PANEL_FLAGS = (
    "displayAppearance",
    "displayTextures",
    "displayLights",
    "shadows",
    "grid",
    "useDefaultMaterial",
    "polymeshes",
    "nurbsSurfaces",
    "subdivSurfaces",
    "cameras",
    "lights",
    "joints",
    "ikHandles",
    "deformers",
    "imagePlane",
)


def _capture_viewport_display_state(cmds: Any) -> dict[str, Any]:
    state: dict[str, Any] = {"panels": {}, "image_planes": {}}
    try:
        panels = cmds.getPanel(type="modelPanel") or []
    except Exception:
        panels = []
    for panel in panels:
        row = {}
        for flag in VIEWPORT_PANEL_FLAGS:
            try:
                row[flag] = cmds.modelEditor(panel, query=True, **{flag: True})
            except Exception:
                pass
        if row:
            state["panels"][str(panel)] = row
    try:
        image_planes = cmds.ls(type="imagePlane") or []
    except Exception:
        image_planes = []
    for image_plane in image_planes:
        row = {}
        for attr in ("visibility", "displayMode"):
            full = f"{image_plane}.{attr}"
            try:
                if not cmds.objExists(full):
                    continue
                row[attr] = cmds.getAttr(full)
            except Exception:
                pass
        if row:
            state["image_planes"][str(image_plane)] = row
    return state if state["panels"] or state["image_planes"] else {}


def _restore_viewport_display_state(cmds: Any, state: dict[str, Any]) -> None:
    if not isinstance(state, dict):
        return
    for panel, attrs in (state.get("panels") or {}).items():
        if not isinstance(attrs, dict):
            continue
        for flag, value in attrs.items():
            try:
                cmds.modelEditor(str(panel), edit=True, **{str(flag): value})
            except Exception:
                pass
    for image_plane, attrs in (state.get("image_planes") or {}).items():
        if not isinstance(attrs, dict):
            continue
        for attr, value in attrs.items():
            full = f"{image_plane}.{attr}"
            try:
                if cmds.objExists(full):
                    cmds.setAttr(full, value)
            except Exception:
                pass


VIEW_CAMERA_ATTRS = (
    "centerOfInterest",
    "focalLength",
    "lensSqueezeRatio",
    "cameraScale",
    "horizontalFilmAperture",
    "verticalFilmAperture",
    "horizontalFilmOffset",
    "verticalFilmOffset",
    "overscan",
    "filmFit",
    "filmFitOffset",
    "nearClipPlane",
    "farClipPlane",
    "panZoomEnabled",
    "horizontalPan",
    "verticalPan",
    "zoom",
)


def _capture_view_camera_state(cmds: Any, camera: str) -> dict[str, Any]:
    if not camera or not cmds.objExists(camera):
        return {}
    transform = _camera_transform(cmds, camera)
    shape = _camera_shape(cmds, camera)
    state = {"camera": camera, "transform": transform, "shape": shape}
    if transform:
        state["xform"] = _capture_transform_values(cmds, transform)
    attrs = {}
    if shape:
        for attr in VIEW_CAMERA_ATTRS:
            full = f"{shape}.{attr}"
            if not cmds.objExists(full):
                continue
            try:
                attrs[attr] = cmds.getAttr(full)
            except Exception:
                pass
    if attrs:
        state["attrs"] = attrs
    return state


def _restore_view_camera_state(cmds: Any, state: dict[str, Any]) -> None:
    if not isinstance(state, dict):
        return
    transform = str(state.get("transform") or "")
    if transform and cmds.objExists(transform):
        _restore_transform_values(cmds, transform, state.get("xform") or {})
    shape = str(state.get("shape") or "")
    if not shape or not cmds.objExists(shape):
        return
    for attr, value in (state.get("attrs") or {}).items():
        full = f"{shape}.{attr}"
        if not cmds.objExists(full):
            continue
        try:
            cmds.setAttr(full, value)
        except Exception:
            pass


def _camera_transform(cmds: Any, camera: str) -> str:
    if not camera or not cmds.objExists(camera):
        return ""
    try:
        if cmds.nodeType(camera) == "camera":
            parents = cmds.listRelatives(camera, parent=True, fullPath=True) or []
            return str(parents[0]) if parents else ""
    except Exception:
        pass
    return camera


def _float3(value: Any, fallback: list[float]) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return list(fallback)
    return [float(value[0]), float(value[1]), float(value[2])]


def _normalize_label(value: Any, prefix: str, width: int) -> str:
    text = str(value or "").strip()
    if not text:
        return f"{prefix}{1:0{width}d}"
    lowered = text.lower()
    prefix_lower = prefix.lower()
    if lowered.startswith(prefix_lower):
        number = text[len(prefix) :]
        return f"{prefix}{int(number):0{width}d}" if number.isdigit() else text
    return f"{prefix}{int(text):0{width}d}" if text.isdigit() else text


def _normalize_take_number(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "01"
    if text.lower().startswith("take"):
        text = text[4:]
    return f"{int(text):02d}" if text.isdigit() else text


def _is_default_output_name(name: Any) -> bool:
    text = str(name or "").strip()
    return text in {"", "Node", "Output"} or (text.startswith("Output ") and text[7:].isdigit())


def _normalize_frame_mode(value: Any) -> str:
    text = str(value or "").strip()
    compact = text.replace("_", "").replace(" ", "").lower()
    aliases = {
        "editorial": FRAME_MODE_EDITORIAL,
        "editorialframerange": FRAME_MODE_EDITORIAL,
        "cut": FRAME_MODE_EDITORIAL,
        "cutrange": FRAME_MODE_EDITORIAL,
        "single": FRAME_MODE_SINGLE,
        "current": FRAME_MODE_SINGLE,
        "currentframe": FRAME_MODE_SINGLE,
        "timerange": FRAME_MODE_TIME_RANGE,
        "playback": FRAME_MODE_TIME_RANGE,
        "playbackrange": FRAME_MODE_TIME_RANGE,
        "renderglobal": FRAME_MODE_RENDER_GLOBAL,
        "renderglobals": FRAME_MODE_RENDER_GLOBAL,
        "global": FRAME_MODE_RENDER_GLOBAL,
        "custom": FRAME_MODE_CUSTOM,
    }
    return aliases.get(compact, FRAME_MODE_EDITORIAL)


def _clean_name(value: Any, fallback: str) -> str:
    text = str(value or "").strip() or fallback
    cleaned = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in text)
    return cleaned.strip("_") or fallback


def _clamped_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = fallback
    return max(minimum, min(maximum, number))


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _object_refs_from_value(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    raw = str(value or "")
    return [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]


def _copy_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _copy_value(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_copy_value(item) for item in value]
    return value


def _relative_to(root: Path, path: Path) -> str:
    if not str(path):
        return ""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _maya_cmds() -> Any:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Smart Render is available inside Maya.") from exc
    return cmds


def _maya_cmds_or_none() -> Any:
    try:
        import maya.cmds as cmds
    except ImportError:
        return None
    return cmds

from __future__ import annotations

import os
import sys
from pathlib import Path
from string import Formatter
from typing import Any

from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json
from smartlib.dcc.maya.render_graph import (
    AE_SLOT_MAX_COUNT,
    AE_SLOT_MIN_COUNT,
    FRAME_MODE_CUSTOM,
    FRAME_MODE_EDITORIAL,
    FRAME_MODE_RENDER_GLOBAL,
    FRAME_MODE_SINGLE,
    FRAME_MODE_TIME_RANGE,
    FRAME_MODES,
    NODE_TYPES,
    RenderEdge,
    RenderGraph,
    RenderNode,
    ae_slot_port_name,
    default_attrs,
    load_graph,
    normalized_attrs,
    save_graph,
)


def _repo_root() -> Path:
    return Path(os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT") or Path(__file__).resolve().parents[4])


def _ensure_nodegraphqt_path() -> None:
    path = _repo_root() / "third_party" / "NodeGraphQt-PySide6"
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)


_ensure_nodegraphqt_path()


def _qt_modules():
    from PySide6 import QtCore, QtGui, QtWidgets

    return QtCore, QtGui, QtWidgets



QtCore, QtGui, QtWidgets = _qt_modules()
from NodeGraphQt import BaseNode, NodeBaseWidget, NodeGraph
from NodeGraphQt.qgraphics.node_base import NodeItem


def _default_config_dir() -> Path:
    env_path = os.environ.get("PROJECT_CONFIG_DIR")
    if env_path:
        return Path(env_path)
    return _repo_root() / "config" / "STKB"


def _layout_ae_slots_view(view) -> None:
    try:
        inputs = [port for port in view.inputs if port.isVisible()]
        if not inputs:
            return
        title_height = float(view._text_item.boundingRect().height()) + 4.0
        port_height = float(inputs[0].boundingRect().height())
        y_offset = title_height + 16.0
        required_height = y_offset + (port_height + 3.0) * len(inputs) + 10.0
        if float(view.height) < required_height:
            view.height = required_height
        view.align_ports(v_offset=y_offset)
        view.update()
        view.reset_pipes()
    except Exception:
        pass


class _SmartRenderAeSlotsItem(NodeItem):
    def draw_node(self):
        super().draw_node()
        _layout_ae_slots_view(self)


class _SmartRenderNode(BaseNode):
    __identifier__ = "smart.render"
    NODE_TYPE = ""
    NODE_NAME = "Smart Render Node"

    def __init__(self, qgraphics_item=None):
        super().__init__(qgraphics_item)
        spec = NODE_TYPES[self.NODE_TYPE]
        for name, port_type in (spec.get("inputs") or {}).items():
            self.add_input(name, multi_input=port_type == "objects")
        for name in (spec.get("outputs") or {}).keys():
            self.add_output(name)
        info_label = {"camera": "Camera Name:", "object": "Mode:", "cast": "Reference:"}.get(self.NODE_TYPE)
        if info_label:
            self.add_custom_widget(_SmartRenderInfoWidget(self.view, "smart_render_info", info_label, ""))


class SmartRenderObjectNode(_SmartRenderNode):
    NODE_TYPE = "object"
    NODE_NAME = "Object"


class SmartRenderReferenceNode(_SmartRenderNode):
    NODE_TYPE = "cast"
    NODE_NAME = "Reference"


class SmartRenderMaterialNode(_SmartRenderNode):
    NODE_TYPE = "material"
    NODE_NAME = "Material"


class SmartRenderVisibilityNode(_SmartRenderNode):
    NODE_TYPE = "visibility"
    NODE_NAME = "geomAttr"


class SmartRenderTransformOverrideNode(_SmartRenderNode):
    NODE_TYPE = "transform_override"
    NODE_NAME = "Transform Override"


class SmartRenderCameraNode(_SmartRenderNode):
    NODE_TYPE = "camera"
    NODE_NAME = "Camera"


class SmartRenderRenderSettingsNode(_SmartRenderNode):
    NODE_TYPE = "render_settings"
    NODE_NAME = "Render Settings"


class SmartRenderOutputNode(_SmartRenderNode):
    NODE_TYPE = "output"
    NODE_NAME = "Output"


class SmartRenderAeSlotsNode(_SmartRenderNode):
    NODE_TYPE = "ae_slots"
    NODE_NAME = "AE Slots"

    def __init__(self):
        super().__init__(_SmartRenderAeSlotsItem)
        self.add_custom_widget(_SmartRenderInfoWidget(self.view, "smart_render_ae_slots", "Slots:", "01: -"))


_NODE_CLASSES = (
    SmartRenderObjectNode,
    SmartRenderReferenceNode,
    SmartRenderMaterialNode,
    SmartRenderVisibilityNode,
    SmartRenderTransformOverrideNode,
    SmartRenderCameraNode,
    SmartRenderRenderSettingsNode,
    SmartRenderOutputNode,
    SmartRenderAeSlotsNode,
)
_NODE_TYPE_TO_CLASS = {cls.NODE_TYPE: cls for cls in _NODE_CLASSES}
_NG_TYPE_TO_NODE_TYPE = {cls.type_: cls.NODE_TYPE for cls in _NODE_CLASSES}
_SHOT_TEMPLATE_POSITIONS = {
    "object": (40.0, 80.0),
    "camera": (40.0, 230.0),
    "render_settings": (40.0, 380.0),
    "output": (460.0, 230.0),
}


class _SmartRenderInfoWidget(NodeBaseWidget):
    def __init__(self, parent=None, name: str = "", label: str = "", text: str = ""):
        super().__init__(parent, name, label)
        value = QtWidgets.QLabel(text)
        value.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        value.setMinimumWidth(120)
        value.setStyleSheet("color: rgb(220, 225, 230); font-weight: 700;")
        self.set_custom_widget(value)

    def get_value(self) -> str:
        return self.get_custom_widget().text()

    def set_value(self, text: str) -> None:
        self.get_custom_widget().setText(str(text or ""))


class _TwoDigitSpinBox(QtWidgets.QSpinBox):
    def textFromValue(self, value: int) -> str:
        return f"{int(value):02d}"


class SmartRenderWindow(QtWidgets.QMainWindow):
    def __init__(self, config_dir: str | os.PathLike[str] | None = None, parent=None):
        super().__init__(parent)
        self.project_config = ProjectConfig(config_dir or _default_config_dir())
        self.graph = RenderGraph()
        self.node_graph = NodeGraph()
        for node_class in _NODE_CLASSES:
            self.node_graph.register_node(node_class)
        self._syncing_node_graph = False
        self.selected_node_id = ""
        self.current_path: Path | None = None
        self.output_master_states: dict[str, dict[str, Any]] = {}
        self.setWindowTitle(f"Smart Render - {self.project_config.project_name}")
        self.resize(1120, 720)
        self._build_ui()
        self._load_or_create_scene_graph()
        self.refresh_graph()

    def _build_ui(self) -> None:
        self._build_menu()
        central = QtWidgets.QWidget()
        root = QtWidgets.QHBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)
        self.setCentralWidget(central)

        root.addWidget(self._build_node_buttons_panel())

        center = QtWidgets.QWidget()
        center_layout = QtWidgets.QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(4)
        toolbar = QtWidgets.QHBoxLayout()
        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.delete_btn = QtWidgets.QPushButton("Delete")
        self.apply_btn = QtWidgets.QPushButton("Apply")
        self.playblast_btn = QtWidgets.QPushButton("Apply + Playblast")
        self.selected_playblast_btn = QtWidgets.QPushButton("Selected Output Playblast")
        self.open_rv_btn = QtWidgets.QPushButton("Open Package in RV")
        toolbar.addWidget(self.connect_btn)
        toolbar.addWidget(self.delete_btn)
        toolbar.addStretch(1)
        toolbar.addWidget(self.apply_btn)
        toolbar.addWidget(self.playblast_btn)
        toolbar.addWidget(self.selected_playblast_btn)
        toolbar.addWidget(self.open_rv_btn)
        center_layout.addLayout(toolbar)
        center_layout.addWidget(self.node_graph.widget, 1)
        root.addWidget(center, 1)

        self.attr_panel = QtWidgets.QWidget()
        self.attr_panel.setMinimumWidth(270)
        self.attr_panel.setMaximumWidth(340)
        self.attr_layout = QtWidgets.QVBoxLayout(self.attr_panel)
        self.attr_layout.setContentsMargins(8, 4, 4, 4)
        self.attr_layout.setSpacing(6)
        root.addWidget(self.attr_panel)

        self.status_label = QtWidgets.QLabel("")
        self.statusBar().addWidget(self.status_label, 1)

        self.connect_btn.clicked.connect(self.connect_selected_nodes)
        self.delete_btn.clicked.connect(self.delete_selected)
        self.apply_btn.clicked.connect(lambda: self.apply_graph(False))
        self.playblast_btn.clicked.connect(lambda: self.apply_graph(True))
        self.selected_playblast_btn.clicked.connect(self.playblast_selected_output)
        self.open_rv_btn.clicked.connect(self.open_selected_output_in_rv)
        self._install_shortcuts()
        self.node_graph.node_selection_changed.connect(self._on_node_selection_changed)
        self.node_graph.port_connected.connect(self._on_port_connected)
        self.node_graph.port_disconnected.connect(self._on_port_disconnected)
        self.node_graph.nodes_deleted.connect(self._on_nodes_deleted)
        self.node_graph.property_changed.connect(self._on_node_property_changed)

    def _install_shortcuts(self) -> None:
        self._shortcuts = []
        for sequence in ("Delete", "Backspace"):
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(sequence), self.node_graph.widget)
            shortcut.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self.delete_selected)
            self._shortcuts.append(shortcut)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        export_action = file_menu.addAction("Export Render Settings Data")
        import_action = file_menu.addAction("Import Render Settings Data")
        file_menu.addSeparator()
        new_action = file_menu.addAction("New")
        export_action.triggered.connect(self.export_graph)
        import_action.triggered.connect(self.import_graph)
        new_action.triggered.connect(self.new_graph)

        template_menu = self.menuBar().addMenu("Template")
        basic_output_action = template_menu.addAction("Shot Template")
        basic_output_action.triggered.connect(self.create_basic_output_template)

    def _build_node_buttons_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QScrollArea()
        panel.setWidgetResizable(True)
        panel.setMaximumWidth(180)
        panel.setMinimumWidth(150)
        content = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        groups: dict[str, list[tuple[str, str]]] = {}
        for node_type, spec in NODE_TYPES.items():
            group_name = str(spec["group"])
            groups.setdefault(group_name, []).append((node_type, str(spec["label"])))
        for group_name, nodes in groups.items():
            label = QtWidgets.QLabel(group_name)
            label.setStyleSheet("font-weight: 700; color: #dbe5ee;")
            layout.addWidget(label)
            for node_type, node_label in nodes:
                button = QtWidgets.QPushButton(node_label)
                button.setMinimumHeight(26)
                button.clicked.connect(lambda _checked=False, item_type=node_type: self.create_node(item_type))
                layout.addWidget(button)
        layout.addStretch(1)
        panel.setWidget(content)
        return panel

    def create_basic_output_template(self) -> None:
        self._sync_from_node_graph()
        offset = len(self.graph.nodes) * 20.0
        objects = self._add_template_node("object", offset)
        camera = self._add_template_node("camera", offset)
        settings = self._add_template_node("render_settings", offset)
        output = self._add_template_node("output", offset)
        self._apply_project_camera_defaults(camera)
        self._apply_project_output_defaults(output)
        self._apply_project_render_settings_defaults(settings)
        self.graph.connect(objects.id, "out", output.id, "objects")
        self.graph.connect(camera.id, "out", output.id, "camera")
        self.graph.connect(settings.id, "out", output.id, "render_settings")
        self.selected_node_id = output.id
        self.refresh_graph()
        self._center_node_ids([objects.id, camera.id, settings.id, output.id])
        self._save_graph_to_scene()
        self.status_label.setText("Created shot template.")

    def _add_template_node(self, node_type: str, offset: float) -> RenderNode:
        x, y = _SHOT_TEMPLATE_POSITIONS[node_type]
        return self.graph.add_node(node_type, x=x + offset, y=y + offset)

    def _center_node_ids(self, node_ids: list[str]) -> None:
        nodes = [self.node_graph.get_node_by_id(node_id) for node_id in node_ids]
        nodes = [node for node in nodes if node]
        if not nodes:
            return
        try:
            self.node_graph.center_on(nodes)
        except Exception:
            pass

    def _load_or_create_scene_graph(self) -> None:
        graph = self._load_graph_from_scene()
        if graph:
            self.graph = graph
            self.current_path = None
            self._apply_scene_output_context()
            self._save_graph_to_scene()
            return
        self.graph = RenderGraph()
        self._apply_scene_output_context()
        self._save_graph_to_scene()

    def new_graph(self) -> None:
        self.graph = RenderGraph()
        self.current_path = None
        self.output_master_states = {}
        self.selected_node_id = ""
        self.refresh_graph()
        self._save_graph_to_scene()

    def create_node(self, node_type: str) -> None:
        self._sync_from_node_graph()
        count = len([node for node in self.graph.nodes if node.type == node_type]) + 1
        node_class = _NODE_TYPE_TO_CLASS.get(str(node_type))
        if node_class is None:
            self.status_label.setText(f"Node type is not registered: {node_type}")
            return
        node = self.node_graph.create_node(
            node_class.type_,
            name=f"{NODE_TYPES[str(node_type)]['label']} {count}",
            pos=[80 + count * 24, 80 + count * 24],
        )
        self.selected_node_id = node.id
        self._sync_from_node_graph()
        render_node = self.graph.node(node.id)
        if render_node:
            self._apply_project_camera_defaults(render_node)
            self._apply_project_render_settings_defaults(render_node)
            self._apply_project_output_defaults(render_node)
            ng_node = self.node_graph.get_node_by_id(node.id)
            if ng_node:
                self._update_node_graph_view(ng_node, render_node)
        self.populate_attrs()
        self._save_graph_to_scene()

    def refresh_graph(self) -> None:
        self._rebuild_node_graph()
        self.populate_attrs()

    def _load_graph_from_scene(self) -> RenderGraph | None:
        if _maya_cmds_or_none() is None:
            return None
        try:
            from smartlib.dcc.maya.render_graph import load_graph_from_scene

            return load_graph_from_scene()
        except Exception as exc:
            self.status_label.setText(f"Scene graph load failed: {exc}")
            return None

    def _save_graph_to_scene(self) -> None:
        if self._syncing_node_graph or _maya_cmds_or_none() is None:
            return
        try:
            from smartlib.dcc.maya.render_graph import save_graph_to_scene

            self.graph.migrate_output_paths()
            save_graph_to_scene(self.graph)
        except Exception as exc:
            self.status_label.setText(f"Scene graph save failed: {exc}")

    def update_node_position(self, node_id: str, x: float, y: float) -> None:
        node = self.graph.node(node_id)
        if node:
            node.x = x
            node.y = y
            self._save_graph_to_scene()

    def select_node(self, node_id: str) -> None:
        self.selected_node_id = node_id
        self.populate_attrs()

    def _rebuild_node_graph(self) -> None:
        self._syncing_node_graph = True
        try:
            self.node_graph.clear_session()
            node_map = {}
            for node in self.graph.nodes:
                node_class = _NODE_TYPE_TO_CLASS[node.type]
                ng_node = node_class()
                ng_node._model.id = node.id
                ng_node._view.id = node.id
                ng_node.NODE_NAME = node.name
                ng_node.model.name = node.name
                self._configure_node_graph_ports(ng_node, node)
                self._update_node_graph_info(ng_node, node)
                self.node_graph.add_node(ng_node, pos=[node.x, node.y], selected=False, push_undo=False)
                if node.type == "ae_slots":
                    self._layout_ae_slot_ports(ng_node)
                node_map[node.id] = ng_node
            for edge in self.graph.edges:
                source = node_map.get(edge.source)
                target = node_map.get(edge.target)
                if not source or not target:
                    continue
                source_port = source.outputs().get(edge.source_port)
                target_port = target.inputs().get(edge.target_port)
                if source_port and target_port:
                    source_port.connect_to(target_port, push_undo=False)
            self._restore_ae_slot_edges_from_attrs()
            self._draw_graph_edges()
            selected = self.node_graph.get_node_by_id(self.selected_node_id)
            if selected:
                selected.set_selected(True)
        finally:
            self._syncing_node_graph = False
        self._sync_from_node_graph(preserve_existing_edges=True, redraw_edges=False)

    def _sync_from_node_graph(
        self,
        preserve_existing_edges: bool = True,
        redraw_edges: bool = True,
        restore_ae_slots: bool = True,
    ) -> None:
        existing_attrs = {node.id: node.attrs for node in self.graph.nodes}
        existing_names = {node.id: node.name for node in self.graph.nodes}
        existing_edges = list(self.graph.edges)
        nodes = []
        for ng_node in self.node_graph.all_nodes():
            node_type = _node_type_from_ng(ng_node)
            if not node_type:
                continue
            pos = ng_node.pos()
            attrs = normalized_attrs(node_type, existing_attrs.get(ng_node.id, default_attrs(node_type)))
            name = ng_node.name()
            existing_name = existing_names.get(ng_node.id)
            if existing_name:
                existing_node = RenderNode(id=ng_node.id, type=node_type, name=existing_name, x=float(pos[0]), y=float(pos[1]), attrs=attrs)
                if name == _legacy_graph_node_display_name(existing_node):
                    name = existing_name
            nodes.append(
                RenderNode(
                    id=ng_node.id,
                    type=node_type,
                    name=name,
                    x=float(pos[0]),
                    y=float(pos[1]),
                    attrs=attrs,
                )
            )
        graph = RenderGraph(nodes=nodes, edges=[])
        edges = []
        for ng_node in self.node_graph.all_nodes():
            target_type = _node_type_from_ng(ng_node)
            if not target_type:
                continue
            for input_port in ng_node.input_ports():
                for output_port in input_port.connected_ports():
                    source = output_port.node()
                    source_type = _node_type_from_ng(source)
                    if not source_type:
                        continue
                    edge = RenderEdge(
                        source=source.id,
                        source_port=output_port.name(),
                        target=ng_node.id,
                        target_port=input_port.name(),
                    )
                    if graph.can_connect(edge.source, edge.source_port, edge.target, edge.target_port):
                        edges.append(edge)
        graph.edges = _merge_preserved_edges(graph, edges, existing_edges) if preserve_existing_edges else edges
        if restore_ae_slots:
            _restore_ae_slot_edges_from_attrs(graph)
        graph.migrate_output_paths()
        graph.update_ae_slot_orders()
        self.graph = graph
        if redraw_edges:
            self._redraw_graph_edges_safely()

    def _on_node_selection_changed(self, selected, deselected) -> None:
        if self._syncing_node_graph:
            return
        self._sync_from_node_graph(preserve_existing_edges=True, redraw_edges=False)
        self.selected_node_id = selected[-1].id if selected else ""
        self.populate_attrs()

    def _on_port_connected(self, input_port, output_port) -> None:
        if self._syncing_node_graph:
            return
        source = output_port.node()
        target = input_port.node()
        self._sync_from_node_graph(preserve_existing_edges=False, redraw_edges=False, restore_ae_slots=False)
        source_type = _node_type_from_ng(source)
        target_type = _node_type_from_ng(target)
        source_port = output_port.name()
        target_port = input_port.name()
        is_valid = bool(source_type and target_type) and self.graph.can_connect(source.id, source_port, target.id, target_port)
        if not is_valid:
            output_port.disconnect_from(input_port, push_undo=False)
            self.status_label.setText("Incompatible node ports.")
            return
        self.graph.connect(source.id, source_port, target.id, target_port)
        self._redraw_graph_edges_safely()
        self.populate_attrs()
        self._save_graph_to_scene()

    def _on_port_disconnected(self, input_port, output_port) -> None:
        if self._syncing_node_graph:
            return
        self._sync_from_node_graph(preserve_existing_edges=False, redraw_edges=False, restore_ae_slots=False)
        source = output_port.node()
        target = input_port.node()
        source_port = output_port.name()
        target_port = input_port.name()
        self.graph.edges = [
            edge
            for edge in self.graph.edges
            if not (edge.source == source.id and edge.source_port == source_port and edge.target == target.id and edge.target_port == target_port)
        ]
        self.graph.update_ae_slot_orders()
        self.populate_attrs()
        self._save_graph_to_scene()

    def _on_nodes_deleted(self, node_ids: list[str]) -> None:
        if self._syncing_node_graph:
            return
        for node_id in node_ids:
            if node_id in self.output_master_states:
                self._restore_output_master(node_id)
        self._sync_from_node_graph(preserve_existing_edges=True)
        if self.selected_node_id in node_ids:
            self.selected_node_id = ""
        self.populate_attrs()
        self._save_graph_to_scene()

    def _on_node_property_changed(self, node, prop_name: str, value: Any) -> None:
        if self._syncing_node_graph:
            return
        if prop_name in {"name", "pos"}:
            self._sync_from_node_graph()
            if prop_name == "name" and node.id == self.selected_node_id:
                self.populate_attrs()
            self._save_graph_to_scene()

    def populate_attrs(self) -> None:
        if hasattr(self, "node_graph") and not self._syncing_node_graph:
            self._sync_from_node_graph()
        self._clear_layout(self.attr_layout)
        node = self.graph.node(self.selected_node_id)
        if not node:
            self.attr_layout.addWidget(QtWidgets.QLabel("Select a node."))
            self.attr_layout.addStretch(1)
            return
        title = QtWidgets.QLabel(node.name)
        title.setStyleSheet("font-weight: 700;")
        self.attr_layout.addWidget(title)
        name_edit = QtWidgets.QLineEdit(node.name)
        name_edit.editingFinished.connect(lambda edit=name_edit, node_id=node.id: self._set_node_name(node_id, edit.text()))
        self.attr_layout.addWidget(self._row("Node Name", name_edit))

        node.attrs = normalized_attrs(node.type, node.attrs)
        if node.type == "object":
            self._populate_object_attrs(node)
        elif node.type == "cast":
            self._populate_cast_attrs(node)
        elif node.type == "material":
            self._populate_material_attrs(node)
        elif node.type == "camera":
            self._populate_camera_attrs(node)
        elif node.type == "transform_override":
            self._populate_transform_attrs(node)
        elif node.type == "render_settings":
            self._populate_render_settings_attrs(node)
        elif node.type == "output":
            self._populate_output_attrs(node)
        elif node.type == "ae_slots":
            self._populate_ae_slots_attrs(node)
        else:
            for key, value in node.attrs.items():
                widget = self._attr_widget(node, key, value)
                self.attr_layout.addWidget(self._row(_label(key), widget))

        self.attr_layout.addStretch(1)

    def _populate_render_settings_attrs(self, node) -> None:
        frame_mode = str(node.attrs.get("frame_mode") or FRAME_MODE_EDITORIAL)
        for key, value in node.attrs.items():
            if key == "frame_mode":
                widget = self._frame_mode_combo(frame_mode)
                widget.currentTextChanged.connect(lambda value, node_id=node.id: self._set_render_frame_mode(node_id, value))
                self.attr_layout.addWidget(self._row("Frame Mode", widget))
                continue
            widget = self._attr_widget(node, key, value)
            if key == "start_frame":
                widget.setEnabled(frame_mode in {FRAME_MODE_SINGLE, FRAME_MODE_CUSTOM})
            if key == "end_frame":
                widget.setEnabled(frame_mode == FRAME_MODE_CUSTOM)
            self.attr_layout.addWidget(self._row(_label(key), widget))

    def _populate_output_attrs(self, node) -> None:
        for key, value in node.attrs.items():
            if key == "output_path":
                continue
            if key == "layer":
                continue
            if key == "take":
                widget = self._take_spinbox(value)
                widget.valueChanged.connect(lambda value, node_id=node.id: self._set_attr(node_id, "take", f"t{int(value):03d}"))
                self.attr_layout.addWidget(self._row("Take", widget))
                continue
            if key == "quality_preset":
                widget = self._quality_preset_combo(str(value or ""))
                widget.currentIndexChanged.connect(
                    lambda _index, combo=widget, node_id=node.id: self._set_attr(node_id, "quality_preset", str(combo.currentData() or ""))
                )
                self.attr_layout.addWidget(self._row("Quality Preset", widget))
                continue
            widget = self._attr_widget(node, key, value)
            self.attr_layout.addWidget(self._row(_label(key), widget))
        layer_label = QtWidgets.QLabel(self._output_layer_name(node.id))
        layer_label.setToolTip(self._resolved_output_path(node.id))
        self.attr_layout.addWidget(self._row("Layer", layer_label))
        take_status = self._output_take_status(node.id)
        take_label = QtWidgets.QLabel(take_status["text"])
        take_label.setWordWrap(True)
        take_label.setStyleSheet(take_status["style"])
        self.attr_layout.addWidget(self._row("Latest", take_label))
        explorer_btn = QtWidgets.QPushButton("Show Output in Explorer")
        explorer_btn.clicked.connect(lambda _checked=False, node_id=node.id: self._show_output_in_explorer(node_id))
        self.attr_layout.addWidget(explorer_btn)

        apply_toggle = QtWidgets.QPushButton("Apply")
        apply_toggle.setCheckable(True)
        apply_toggle.setChecked(node.id in self.output_master_states or self._scene_has_master_state(node.id))
        apply_toggle.toggled.connect(lambda checked, node_id=node.id: self._toggle_output_apply(node_id, checked))
        self.attr_layout.addWidget(self._row("Setting Toggle", apply_toggle))

        playblast_btn = QtWidgets.QPushButton("Playblast")
        playblast_btn.clicked.connect(lambda _checked=False, node_id=node.id: self._playblast_output(node_id))
        self.attr_layout.addWidget(playblast_btn)

    def _populate_ae_slots_attrs(self, node) -> None:
        slot_count = int(node.attrs.get("slot_count") or 3)
        count_label = QtWidgets.QLabel(str(slot_count))
        self.attr_layout.addWidget(self._row("Slot Count", count_label))

        controls = QtWidgets.QWidget()
        controls_layout = QtWidgets.QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(4)
        add_btn = QtWidgets.QPushButton("Add Input")
        remove_btn = QtWidgets.QPushButton("Remove Input")
        add_btn.setEnabled(slot_count < AE_SLOT_MAX_COUNT)
        remove_btn.setEnabled(slot_count > AE_SLOT_MIN_COUNT)
        add_btn.clicked.connect(lambda _checked=False, node_id=node.id: self._set_ae_slot_count(node_id, slot_count + 1))
        remove_btn.clicked.connect(lambda _checked=False, node_id=node.id: self._set_ae_slot_count(node_id, slot_count - 1))
        controls_layout.addWidget(add_btn)
        controls_layout.addWidget(remove_btn)
        self.attr_layout.addWidget(controls)

        self.attr_layout.addWidget(QtWidgets.QLabel("Order"))
        for index in range(1, slot_count + 1):
            source = self._ae_slot_source(node.id, index)
            label = QtWidgets.QLabel(f"{index:02d}: {self._ae_slot_display_text(source)}")
            if source:
                label.setStyleSheet(self._output_take_status(source.id)["style"])
            self.attr_layout.addWidget(label)
        publish_btn = QtWidgets.QPushButton("Publish")
        publish_btn.clicked.connect(lambda _checked=False, node_id=node.id: self._publish_ae_slots(node_id))
        self.attr_layout.addWidget(publish_btn)
        build_data_btn = QtWidgets.QPushButton("Build Review Data")
        build_data_btn.clicked.connect(lambda _checked=False, node_id=node.id: self._export_ae_slots_build_data(node_id))
        self.attr_layout.addWidget(build_data_btn)
        build_ae_btn = QtWidgets.QPushButton("Build AE")
        build_ae_btn.clicked.connect(lambda _checked=False, node_id=node.id: self._build_ae_slots(node_id))
        self.attr_layout.addWidget(build_ae_btn)

    def _populate_camera_attrs(self, node) -> None:
        camera_combo = self._choice_combo(self._cameras(), str(node.attrs.get("camera") or ""))
        camera_combo.currentTextChanged.connect(lambda value, node_id=node.id: self._set_attr(node_id, "camera", value))
        self.attr_layout.addWidget(self._row("Camera", camera_combo))
        for key, value in node.attrs.items():
            if key == "camera":
                continue
            widget = self._attr_widget(node, key, value)
            self.attr_layout.addWidget(self._row(_label(key), widget))

    def _populate_transform_attrs(self, node) -> None:
        record_toggle = QtWidgets.QPushButton("Record Transform")
        record_toggle.setCheckable(True)
        record_toggle.setChecked(bool(node.attrs.get("recording")))
        record_toggle.toggled.connect(lambda checked, node_id=node.id: self._set_transform_recording(node_id, checked))
        self.attr_layout.addWidget(self._row("Recording Mode", record_toggle))
        for key, value in node.attrs.items():
            if key in {"recording", "record_start_transform"}:
                continue
            if key == "match_source":
                widget = self._choice_combo(self._scene_transforms(), str(value or ""))
                widget.currentTextChanged.connect(lambda value, node_id=node.id: self._set_attr(node_id, "match_source", value))
                self.attr_layout.addWidget(self._row("Match Source", widget))
                continue
            widget = self._attr_widget(node, key, value)
            self.attr_layout.addWidget(self._row(_label(key), widget))

    def _populate_material_attrs(self, node) -> None:
        material_combo = self._choice_combo(self._materials(), str(node.attrs.get("material_name") or ""))
        material_combo.currentTextChanged.connect(lambda value, node_id=node.id: self._set_attr(node_id, "material_name", value))
        self.attr_layout.addWidget(self._row("Material", material_combo))
        for key, value in node.attrs.items():
            if key == "material_name":
                continue
            widget = self._attr_widget(node, key, value)
            self.attr_layout.addWidget(self._row(_label(key), widget))

    def _populate_cast_attrs(self, node) -> None:
        reference_combo = self._choice_combo(self._scene_references(), str(node.attrs.get("reference_node") or ""))
        reference_combo.currentTextChanged.connect(lambda value, node_id=node.id: self._set_cast_reference_node(node_id, value))
        self.attr_layout.addWidget(self._row("Scene Reference", reference_combo))
        namespace_label = QtWidgets.QLabel(str(node.attrs.get("namespace") or ""))
        self.attr_layout.addWidget(self._row("Namespace", namespace_label))

    def _populate_object_attrs(self, node) -> None:
        mode_combo = QtWidgets.QComboBox()
        for label, value in (
            ("Current Selection", "selection"),
            ("Objects", "objects"),
            ("Display Layer", "display_layer"),
            ("Sets", "set"),
        ):
            mode_combo.addItem(label, value)
        mode_index = mode_combo.findData(str(node.attrs.get("mode") or "selection"))
        mode_combo.setCurrentIndex(max(0, mode_index))
        mode_combo.currentIndexChanged.connect(
            lambda _index, combo=mode_combo, node_id=node.id: self._set_object_mode(node_id, str(combo.currentData()))
        )
        self.attr_layout.addWidget(self._row("Mode", mode_combo))

        objects_edit = QtWidgets.QPlainTextEdit("\n".join(_object_refs_from_value(node.attrs.get("objects"))))
        objects_edit.setPlaceholderText("pSphere1\ndesk_GRP")
        objects_edit.setMinimumHeight(76)
        objects_edit.textChanged.connect(lambda edit=objects_edit, node_id=node.id: self._set_objects_text(node_id, edit.toPlainText()))
        self.attr_layout.addWidget(self._row("Objects", objects_edit))

        selection_buttons = QtWidgets.QWidget()
        selection_layout = QtWidgets.QHBoxLayout(selection_buttons)
        selection_layout.setContentsMargins(0, 0, 0, 0)
        selection_layout.setSpacing(4)
        add_btn = QtWidgets.QPushButton("Add Selected Objects")
        remove_btn = QtWidgets.QPushButton("Remove Selected Objects")
        add_btn.clicked.connect(lambda _checked=False, node_id=node.id: self._add_selected_objects(node_id))
        remove_btn.clicked.connect(lambda _checked=False, node_id=node.id: self._remove_selected_objects(node_id))
        selection_layout.addWidget(add_btn)
        selection_layout.addWidget(remove_btn)
        self.attr_layout.addWidget(selection_buttons)

        layer_combo = self._choice_combo(self._display_layers(), str(node.attrs.get("display_layer") or ""))
        layer_combo.currentTextChanged.connect(lambda value, node_id=node.id: self._set_attr(node_id, "display_layer", value))
        layer_combo.setEnabled(str(node.attrs.get("mode")) == "display_layer")
        self.attr_layout.addWidget(self._row("Display Layer", layer_combo))

        set_combo = self._choice_combo(self._sets(), str(node.attrs.get("set") or ""))
        set_combo.currentTextChanged.connect(lambda value, node_id=node.id: self._set_attr(node_id, "set", value))
        set_combo.setEnabled(str(node.attrs.get("mode")) == "set")
        self.attr_layout.addWidget(self._row("Sets", set_combo))

    def _attr_widget(self, node, key: str, value: Any):
        if isinstance(value, bool):
            widget = QtWidgets.QCheckBox()
            widget.setChecked(value)
            widget.toggled.connect(lambda checked, node_id=node.id, attr=key: self._set_attr(node_id, attr, checked))
            return widget
        if isinstance(value, int):
            widget = QtWidgets.QSpinBox()
            widget.setRange(-1000000, 1000000)
            widget.setValue(value)
            widget.valueChanged.connect(lambda val, node_id=node.id, attr=key: self._set_attr(node_id, attr, int(val)))
            return widget
        if isinstance(value, float):
            widget = QtWidgets.QDoubleSpinBox()
            widget.setRange(-1000000.0, 1000000.0)
            widget.setDecimals(3)
            widget.setValue(value)
            widget.valueChanged.connect(lambda val, node_id=node.id, attr=key: self._set_attr(node_id, attr, float(val)))
            return widget
        if isinstance(value, list) and len(value) == 3:
            row = QtWidgets.QWidget()
            layout = QtWidgets.QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(3)
            for index in range(3):
                spin = QtWidgets.QDoubleSpinBox()
                spin.setRange(-1000000.0, 1000000.0)
                spin.setDecimals(3)
                spin.setValue(float(value[index]))
                spin.valueChanged.connect(lambda val, i=index, node_id=node.id, attr=key: self._set_list_attr(node_id, attr, i, float(val)))
                layout.addWidget(spin)
            return row
        widget = QtWidgets.QLineEdit(str(value))
        widget.editingFinished.connect(lambda edit=widget, node_id=node.id, attr=key: self._set_attr(node_id, attr, edit.text()))
        if key in {"objects"}:
            widget.setPlaceholderText("pSphere1, desk_GRP")
        return widget

    def _row(self, label: str, widget) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(QtWidgets.QLabel(label))
        layout.addWidget(widget)
        return row

    def _set_node_name(self, node_id: str, value: str) -> None:
        node = self.graph.node(node_id)
        if node:
            node.name = value.strip() or NODE_TYPES[node.type]["label"]
            if node.type == "output" and _is_default_output_node_name(node.name):
                node.name = "CHA"
            ng_node = self.node_graph.get_node_by_id(node_id)
            if ng_node:
                self._update_node_graph_view(ng_node, node)
            self._sync_from_node_graph(preserve_existing_edges=True)
            self.populate_attrs()
            self._save_graph_to_scene()

    def _set_attr(self, node_id: str, key: str, value: Any) -> None:
        node = self.graph.node(node_id)
        if node:
            node.attrs[key] = value
            if node.type == "render_settings" and key == "start_frame" and node.attrs.get("frame_mode") == FRAME_MODE_SINGLE:
                node.attrs["end_frame"] = value
            ng_node = self.node_graph.get_node_by_id(node_id)
            if ng_node:
                self._update_node_graph_view(ng_node, node)
            self._save_graph_to_scene()

    def _set_render_frame_mode(self, node_id: str, value: str) -> None:
        node = self.graph.node(node_id)
        if not node:
            return
        node.attrs["frame_mode"] = value
        frame_range = _ui_frame_range_for_mode(value, node.attrs, self.project_config)
        if frame_range:
            node.attrs["start_frame"], node.attrs["end_frame"] = frame_range
        self.populate_attrs()
        self._save_graph_to_scene()

    def _set_object_mode(self, node_id: str, value: str) -> None:
        self._set_attr(node_id, "mode", value)
        self.populate_attrs()

    def _set_cast_reference_node(self, node_id: str, value: str) -> None:
        node = self.graph.node(node_id)
        if not node:
            return
        node.attrs["reference_node"] = value
        namespace = self._reference_namespace(value)
        if namespace:
            node.attrs["namespace"] = namespace
        ng_node = self.node_graph.get_node_by_id(node_id)
        if ng_node:
            self._update_node_graph_view(ng_node, node)
        self._save_graph_to_scene()
        self.populate_attrs()

    def _set_objects_text(self, node_id: str, value: str) -> None:
        self._set_attr(node_id, "objects", _object_refs_from_value(value))

    def _add_selected_objects(self, node_id: str) -> None:
        selected = self._selected_maya_objects()
        if not selected:
            self.status_label.setText("No Maya objects selected.")
            return
        node = self.graph.node(node_id)
        if not node:
            return
        objects = _dedupe([*_object_refs_from_value(node.attrs.get("objects")), *selected])
        node.attrs["objects"] = objects
        node.attrs["mode"] = "objects"
        self.status_label.setText(f"Added {len(selected)} selected objects.")
        self.populate_attrs()
        self._save_graph_to_scene()

    def _remove_selected_objects(self, node_id: str) -> None:
        selected = set(self._selected_maya_objects())
        if not selected:
            self.status_label.setText("No Maya objects selected.")
            return
        node = self.graph.node(node_id)
        if not node:
            return
        objects = [item for item in _object_refs_from_value(node.attrs.get("objects")) if item not in selected]
        node.attrs["objects"] = objects
        node.attrs["mode"] = "objects"
        self.status_label.setText(f"Removed selected objects from Objects.")
        self.populate_attrs()
        self._save_graph_to_scene()

    def _set_list_attr(self, node_id: str, key: str, index: int, value: float) -> None:
        node = self.graph.node(node_id)
        if node and isinstance(node.attrs.get(key), list) and index < len(node.attrs[key]):
            node.attrs[key][index] = value
            self._save_graph_to_scene()

    def _set_ae_slot_count(self, node_id: str, count: int) -> None:
        self._sync_from_node_graph()
        node = self.graph.node(node_id)
        if not node or node.type != "ae_slots":
            return
        count = max(AE_SLOT_MIN_COUNT, min(AE_SLOT_MAX_COUNT, int(count)))
        node.attrs["slot_count"] = count
        valid_ports = set(self.graph.input_port_types(node_id))
        self.graph.edges = [edge for edge in self.graph.edges if edge.target != node_id or edge.target_port in valid_ports]
        self.graph.update_ae_slot_orders()
        self.selected_node_id = node_id
        self._refresh_ae_slots_node(node_id)
        self._save_graph_to_scene()
        self.status_label.setText(f"AE Slots inputs: {count}")

    def _refresh_ae_slots_node(self, node_id: str) -> None:
        self._restore_ae_slot_edges_from_attrs()
        node = self.graph.node(node_id)
        ng_node = self.node_graph.get_node_by_id(node_id)
        if not node or node.type != "ae_slots" or not ng_node:
            self.refresh_graph()
            return
        try:
            x, y = ng_node.pos()
        except Exception:
            x, y = node.x, node.y
        node.x = float(x)
        node.y = float(y)
        self._syncing_node_graph = True
        try:
            self._configure_node_graph_ports(ng_node, node)
            self._update_node_graph_view(ng_node, node)
            for edge in self.graph.edges:
                if edge.target != node_id:
                    continue
                source = self.node_graph.get_node_by_id(edge.source)
                if not source:
                    continue
                source_port = source.outputs().get(edge.source_port)
                target_port = ng_node.inputs().get(edge.target_port)
                if source_port and target_port:
                    source_port.connect_to(target_port, push_undo=False)
            self._draw_graph_edges(target_node_id=node_id)
            try:
                ng_node.set_pos(x, y)
            except Exception:
                pass
            self._layout_ae_slot_ports(ng_node)
        finally:
            self._syncing_node_graph = False
        self.populate_attrs()

    def _restore_ae_slot_edges_from_attrs(self) -> None:
        _restore_ae_slot_edges_from_attrs(self.graph)

    def _draw_graph_edges(self, target_node_id: str | None = None) -> None:
        for edge in self.graph.edges:
            if target_node_id and edge.target != target_node_id:
                continue
            source = self.node_graph.get_node_by_id(edge.source)
            target = self.node_graph.get_node_by_id(edge.target)
            if not source or not target:
                continue
            source_port = source.outputs().get(edge.source_port)
            target_port = target.inputs().get(edge.target_port)
            if not source_port or not target_port:
                continue
            try:
                for connected in list(target_port.connected_ports()):
                    target_port.disconnect_from(connected, push_undo=False)
                source_port.connect_to(target_port, push_undo=False)
                source.view.reset_pipes()
                target.view.reset_pipes()
            except Exception:
                pass

    def _redraw_graph_edges_safely(self) -> None:
        if not hasattr(self, "node_graph"):
            return
        was_syncing = self._syncing_node_graph
        self._syncing_node_graph = True
        try:
            self._clear_node_graph_connections()
            self._draw_graph_edges()
        finally:
            self._syncing_node_graph = was_syncing

    def _clear_node_graph_connections(self) -> None:
        for node in self.node_graph.all_nodes():
            for input_port in list(node.input_ports()):
                for connected in list(input_port.connected_ports()):
                    try:
                        input_port.disconnect_from(connected, push_undo=False)
                    except Exception:
                        pass

    def _ae_slot_source(self, node_id: str, index: int) -> RenderNode | None:
        port = ae_slot_port_name(index)
        edge = next((item for item in self.graph.edges if item.target == node_id and item.target_port == port), None)
        return self.graph.node(edge.source) if edge else None

    def _set_transform_recording(self, node_id: str, checked: bool) -> None:
        cmds = _maya_cmds_or_none()
        node = self.graph.node(node_id)
        if not node or node.type != "transform_override":
            return
        if cmds is None:
            node.attrs["recording"] = False
            self.status_label.setText("Transform recording requires Maya.")
            self.populate_attrs()
            return
        try:
            from smartlib.dcc.maya.render_graph import finish_transform_recording, start_transform_recording

            self._sync_from_node_graph()
            node = self.graph.node(node_id)
            if not node:
                return
            objects = self._transform_record_objects(node_id)
            if checked:
                recorded, target = start_transform_recording(cmds, objects, node.attrs)
                if recorded:
                    self.status_label.setText(f"Transform recording started: {target}")
                else:
                    self.status_label.setText("Transform recording target not found.")
            else:
                recorded, target = finish_transform_recording(cmds, objects, node.attrs)
                if recorded:
                    self.status_label.setText(f"Recorded transform and restored: {target}")
                else:
                    self.status_label.setText("Transform recording target not found.")
        except Exception as exc:
            node = self.graph.node(node_id)
            if node:
                node.attrs["recording"] = False
                node.attrs["record_start_transform"] = {}
            self.status_label.setText(f"Transform recording failed: {exc}")
            self.populate_attrs()
            return
        self._save_graph_to_scene()
        self.populate_attrs()

    def _transform_record_objects(self, node_id: str) -> list[str]:
        cmds = _maya_cmds_or_none()
        if cmds is None:
            return []
        try:
            from smartlib.dcc.maya.render_graph import _ApplyState

            objects = _ApplyState(cmds=cmds, graph=self.graph).collect_objects(node_id)
        except Exception:
            objects = []
        return objects or self._selected_maya_objects()

    def connect_selected_nodes(self) -> None:
        self._sync_from_node_graph(preserve_existing_edges=True)
        selected = [node for node in self.node_graph.selected_nodes() if _node_type_from_ng(node)]
        if len(selected) != 2:
            self.status_label.setText("Select source node and target node.")
            return
        source, target = sorted(selected, key=lambda node: node.pos()[0])
        source_type = _node_type_from_ng(source)
        target_type = _node_type_from_ng(target)
        source_ports = list(self.graph.output_port_types(source.id).keys())
        target_ports = list(self.graph.input_port_types(target.id).keys())
        if target_type == "ae_slots":
            occupied_ports = {edge.target_port for edge in self.graph.input_edges(target.id)}
            target_ports = [port for port in target_ports if port not in occupied_ports]
            if not target_ports:
                self.status_label.setText("No empty AE Slot inputs.")
                return
        for source_port in source_ports:
            for target_port in target_ports:
                if self.graph.can_connect(source.id, source_port, target.id, target_port):
                    self._syncing_node_graph = True
                    try:
                        source.outputs()[source_port].connect_to(target.inputs()[target_port])
                    finally:
                        self._syncing_node_graph = False
                    self.graph.connect(source.id, source_port, target.id, target_port)
                    self.status_label.setText(f"Connected {source.name()} to {target.name()}")
                    self.populate_attrs()
                    self._save_graph_to_scene()
                    return
        self.status_label.setText("No compatible ports between selected nodes.")

    def delete_selected(self) -> None:
        selected = [node for node in self.node_graph.selected_nodes() if _node_type_from_ng(node)]
        for node in selected:
            if node.id in self.output_master_states:
                self._restore_output_master(node.id)
        if selected:
            self.node_graph.delete_nodes(selected)
        self._sync_from_node_graph()
        self.selected_node_id = ""
        self.populate_attrs()
        self._save_graph_to_scene()

    def playblast_selected_output(self) -> None:
        output_id = self._selected_output_id()
        if not output_id:
            self.status_label.setText("Select an Output node.")
            return
        self._playblast_output(output_id)

    def open_selected_output_in_rv(self) -> None:
        output_id = self._selected_output_id()
        if not output_id:
            self.status_label.setText("Select an Output node.")
            return
        try:
            from smartlib.dcc.maya.render_graph import evaluate_output_render_settings
            from smartlib.review.rv import open_output_in_rv

            self._sync_from_node_graph()
            self._save_graph_to_scene()
            settings = evaluate_output_render_settings(self.graph, output_id, self.project_config)
            opened, message = open_output_in_rv(settings, self.project_config)
        except Exception as exc:
            self.status_label.setText(f"OpenRV failed: {exc}")
            return
        self.status_label.setText(f"Opened in RV: {message}" if opened else message)

    def _selected_output_id(self) -> str:
        self._sync_from_node_graph()
        node = self.graph.node(self.selected_node_id)
        if node and node.type == "output":
            return node.id
        for ng_node in self.node_graph.selected_nodes():
            node_type = _node_type_from_ng(ng_node)
            if node_type == "output":
                return ng_node.id
        return ""

    def _show_output_in_explorer(self, output_id: str) -> None:
        try:
            folder = self._resolved_output_folder(output_id)
            if not folder:
                self.status_label.setText("Output path could not be resolved.")
                return
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))
        except Exception as exc:
            self.status_label.setText(f"Explorer failed: {exc}")
            return
        self.status_label.setText(f"Opened output folder: {folder}")

    def _resolved_output_folder(self, output_id: str) -> Path | None:
        try:
            from smartlib.dcc.maya.render_graph import evaluate_output_render_settings

            self._sync_from_node_graph()
            settings = evaluate_output_render_settings(self.graph, output_id, self.project_config)
        except Exception:
            settings = {}
        package_root = str(settings.get("package_root") or "").strip()
        if package_root:
            return Path(package_root)
        output_path = str(settings.get("output_path") or "").strip() or self._resolved_output_path(output_id)
        if not output_path:
            return None
        path = Path(output_path.replace("\\", "/"))
        return path if path.suffix else path.parent

    def apply_graph(self, playblast: bool) -> None:
        from smartlib.dcc.maya.render_graph import apply_graph, capture_master_state, load_master_state_from_scene, restore_master_state

        self._sync_from_node_graph()
        self._save_graph_to_scene()
        output_id = self.selected_node_id if self.graph.node(self.selected_node_id) and self.graph.node(self.selected_node_id).type == "output" else None
        master_state = None
        progress = self._begin_progress("Playblast") if playblast else None
        if playblast:
            try:
                self._update_progress(progress, "Capturing Master state...", 2)
                target_output = output_id or (self.graph.output_nodes()[0].id if self.graph.output_nodes() else "")
                master_state = (
                    self.output_master_states.get(target_output)
                    or load_master_state_from_scene(target_output)
                    or capture_master_state(self.graph, target_output)
                )
            except Exception as exc:
                self.status_label.setText(str(exc))
                self._finish_progress(progress)
                return
        try:
            result = apply_graph(
                self.graph,
                output_id,
                playblast=playblast,
                restore_after_playblast=False,
                project_config=self.project_config,
                progress_callback=self._progress_callback(progress) if progress else None,
            )
        except Exception as exc:
            self.status_label.setText(str(exc))
            return
        finally:
            if playblast and master_state is not None:
                try:
                    self._update_progress(progress, "Restoring Master state...", 96)
                    restore_master_state(master_state)
                except Exception as exc:
                    self.status_label.setText(f"Playblast restore failed: {exc}")
                    self._finish_progress(progress)
                    return
            self._finish_progress(progress)
        if playblast:
            self._set_status_with_warnings(f"Playblast: {result.get('playblast')}", result.get("warnings") or [])
        else:
            self._set_status_with_warnings("Applied render graph.", result.get("warnings") or [])

    def _toggle_output_apply(self, output_id: str, checked: bool) -> None:
        if checked:
            self._apply_output_setting(output_id)
        else:
            self._restore_output_master(output_id)
        self.populate_attrs()

    def _apply_output_setting(self, output_id: str) -> None:
        from smartlib.dcc.maya.render_graph import apply_graph, capture_master_state, restore_master_state, save_master_state_to_scene

        self._sync_from_node_graph()
        self._save_graph_to_scene()
        try:
            master_state = capture_master_state(self.graph, output_id)
            save_master_state_to_scene(output_id, master_state)
            result = apply_graph(self.graph, output_id, playblast=False, restore_after_playblast=False, project_config=self.project_config)
        except Exception as exc:
            if "master_state" in locals():
                try:
                    restore_master_state(master_state)
                except Exception:
                    pass
            self.output_master_states.pop(output_id, None)
            self.status_label.setText(str(exc))
            return
        self.output_master_states[output_id] = master_state
        self._set_status_with_warnings("Applied output settings.", result.get("warnings") or [])

    def _restore_output_master(self, output_id: str) -> None:
        from smartlib.dcc.maya.render_graph import delete_master_state_from_scene, load_master_state_from_scene, restore_master_state

        master_state = self.output_master_states.pop(output_id, None) or load_master_state_from_scene(output_id)
        if not master_state:
            self.status_label.setText("No Master state captured.")
            return
        try:
            restore_master_state(master_state)
            delete_master_state_from_scene(output_id)
        except Exception as exc:
            self.status_label.setText(str(exc))
            return
        self.status_label.setText("Restored Master state.")

    def _playblast_output(self, output_id: str) -> None:
        from smartlib.dcc.maya.render_graph import apply_graph, capture_master_state, load_master_state_from_scene, restore_master_state

        self._sync_from_node_graph()
        self._save_graph_to_scene()
        progress = self._begin_progress("Playblast")
        try:
            self._update_progress(progress, "Capturing Master state...", 2)
            master_state = self.output_master_states.get(output_id) or load_master_state_from_scene(output_id) or capture_master_state(self.graph, output_id)
            result = apply_graph(
                self.graph,
                output_id,
                playblast=True,
                restore_after_playblast=False,
                project_config=self.project_config,
                progress_callback=self._progress_callback(progress),
            )
        except Exception as exc:
            self.status_label.setText(str(exc))
            return
        finally:
            if "master_state" in locals():
                try:
                    self._update_progress(progress, "Restoring Master state...", 96)
                    restore_master_state(master_state)
                except Exception as exc:
                    self.status_label.setText(f"Playblast restore failed: {exc}")
                    self._finish_progress(progress)
                    return
            self._finish_progress(progress)
        self._set_status_with_warnings(f"Playblast: {result.get('playblast')}", result.get("warnings") or [])

    def _publish_ae_slots(self, node_id: str) -> None:
        try:
            from smartlib.dcc.maya.render_graph import publish_ae_slots

            self._sync_from_node_graph()
            self._save_graph_to_scene()
            paths = publish_ae_slots(self.graph, node_id, self.project_config)
        except Exception as exc:
            self.status_label.setText(f"Publish failed: {exc}")
            return
        self.status_label.setText(f"Published AE data: {paths[-1]}" if paths else "No AE Slot outputs to publish.")

    def _export_ae_slots_build_data(self, node_id: str) -> None:
        try:
            from smartlib.dcc.maya.render_graph import export_ae_slots_build_data

            self._sync_from_node_graph()
            self._save_graph_to_scene()
            paths = export_ae_slots_build_data(self.graph, node_id, self.project_config, area="output")
        except Exception as exc:
            self.status_label.setText(f"Review build data failed: {exc}")
            return
        self.status_label.setText(f"Review build data: {paths[-1]}" if paths else "No AE Slot outputs to export.")

    def _build_ae_slots(self, node_id: str) -> None:
        try:
            from smartlib.dcc.maya.render_graph import build_ae_slots

            self._sync_from_node_graph()
            self._save_graph_to_scene()
            results = build_ae_slots(self.graph, node_id, self.project_config)
        except Exception as exc:
            self.status_label.setText(f"AE build failed: {exc}")
            return
        if not results:
            self.status_label.setText("No AE Slot outputs to build.")
            return
        launched = [row for row in results if row.get("launched")]
        row = launched[-1] if launched else results[-1]
        log = row.get("log") or ""
        if launched:
            self.status_label.setText(f"After Effects launched: {row.get('project')} | Log: {log}")
        else:
            self.status_label.setText(f"AE data written. Launch skipped: {row.get('message')} | Log: {log}")

    def _scene_has_master_state(self, output_id: str) -> bool:
        try:
            from smartlib.dcc.maya.render_graph import scene_has_master_state

            return scene_has_master_state(output_id)
        except Exception:
            return False

    def _begin_progress(self, title: str) -> QtWidgets.QProgressDialog:
        progress = QtWidgets.QProgressDialog("Preparing...", "", 0, 100, self)
        progress.setWindowTitle(f"Smart Render - {title}")
        progress.setCancelButton(None)
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        progress.show()
        QtWidgets.QApplication.processEvents()
        return progress

    def _progress_callback(self, progress: QtWidgets.QProgressDialog):
        return lambda message, value: self._update_progress(progress, message, value)

    def _update_progress(self, progress: QtWidgets.QProgressDialog | None, message: str, value: int) -> None:
        if progress is None:
            return
        progress.setLabelText(str(message))
        progress.setValue(max(0, min(100, int(value))))
        self.status_label.setText(str(message))
        QtWidgets.QApplication.processEvents()

    def _finish_progress(self, progress: QtWidgets.QProgressDialog | None) -> None:
        if progress is None:
            return
        progress.setValue(100)
        QtWidgets.QApplication.processEvents()
        progress.close()
        progress.deleteLater()

    def _set_status_with_warnings(self, message: str, warnings: list[str]) -> None:
        if not warnings:
            self.status_label.setText(message)
            return
        suffix = f" (+{len(warnings) - 1} more)" if len(warnings) > 1 else ""
        self.status_label.setText(f"{message} Warning: {warnings[0]}{suffix}")

    def export_graph(self) -> None:
        self._sync_from_node_graph()
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(self, "Export Render Settings Data", "", "Smart Render Graph (*.json)")
        if not path:
            return
        self.current_path = save_graph(path, self.graph)
        self.status_label.setText(f"Exported {self.current_path}")

    def import_graph(self) -> None:
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(self, "Import Render Settings Data", "", "Smart Render Graph (*.json)")
        if not path:
            return
        try:
            self.graph = load_graph(path)
        except Exception as exc:
            self.status_label.setText(str(exc))
            return
        self.current_path = Path(path)
        self.output_master_states = {}
        self.selected_node_id = ""
        self.refresh_graph()
        self._save_graph_to_scene()
        self.status_label.setText(f"Imported {path}")

    def _disconnect(self, edge_index: int) -> None:
        self._sync_from_node_graph(preserve_existing_edges=True)
        if edge_index < 0 or edge_index >= len(self.graph.edges):
            return
        edge = self.graph.edges[edge_index]
        source = self.node_graph.get_node_by_id(edge.source)
        target = self.node_graph.get_node_by_id(edge.target)
        if source and target:
            source_port = source.outputs().get(edge.source_port)
            target_port = target.inputs().get(edge.target_port)
            if source_port and target_port:
                self._syncing_node_graph = True
                try:
                    source_port.disconnect_from(target_port, push_undo=False)
                finally:
                    self._syncing_node_graph = False
        self.graph.disconnect_edge(edge_index)
        self.populate_attrs()
        self._save_graph_to_scene()

    def _choice_combo(self, values: list[str], current: str) -> QtWidgets.QComboBox:
        combo = QtWidgets.QComboBox()
        combo.setEditable(False)
        combo.addItem("")
        for value in values:
            combo.addItem(value)
        if current and combo.findText(current) < 0:
            combo.addItem(current)
        index = combo.findText(current)
        combo.setCurrentIndex(max(0, index))
        return combo

    def _selected_maya_objects(self) -> list[str]:
        cmds = _maya_cmds_or_none()
        if cmds is None:
            return []
        try:
            return [str(item) for item in (cmds.ls(selection=True, long=True) or [])]
        except Exception:
            return []

    def _display_layers(self) -> list[str]:
        cmds = _maya_cmds_or_none()
        if cmds is None:
            return []
        try:
            return sorted(str(item) for item in (cmds.ls(type="displayLayer") or []) if str(item) != "defaultLayer")
        except Exception:
            return []

    def _sets(self) -> list[str]:
        cmds = _maya_cmds_or_none()
        if cmds is None:
            return []
        hidden = {"defaultLightSet", "defaultObjectSet", "initialParticleSE", "initialShadingGroup"}
        try:
            sets = []
            for item in cmds.ls(type="objectSet") or []:
                name = str(item)
                if name in hidden or name.endswith("SG"):
                    continue
                sets.append(name)
            return sorted(sets)
        except Exception:
            return []

    def _scene_transforms(self) -> list[str]:
        cmds = _maya_cmds_or_none()
        if cmds is None:
            return []
        try:
            return sorted(str(item) for item in (cmds.ls(type="transform", long=True) or []))
        except Exception:
            return []

    def _materials(self) -> list[str]:
        cmds = _maya_cmds_or_none()
        if cmds is None:
            return []
        materials = []
        try:
            materials.extend(str(item) for item in (cmds.ls(materials=True) or []))
        except Exception:
            pass
        if not materials:
            for node_type in ("lambert", "blinn", "phong", "phongE", "standardSurface", "aiStandardSurface", "surfaceShader"):
                try:
                    materials.extend(str(item) for item in (cmds.ls(type=node_type) or []))
                except Exception:
                    pass
        return sorted(_dedupe(materials))

    def _scene_references(self) -> list[str]:
        cmds = _maya_cmds_or_none()
        if cmds is None:
            return []
        try:
            references = [str(item) for item in (cmds.ls(type="reference") or [])]
        except Exception:
            return []
        hidden = {"sharedReferenceNode", "_UNKNOWN_REF_NODE_"}
        return sorted(reference for reference in references if reference not in hidden and not reference.startswith("_UNKNOWN"))

    def _reference_namespace(self, reference_node: str) -> str:
        cmds = _maya_cmds_or_none()
        if cmds is None or not reference_node:
            return ""
        try:
            return str(cmds.referenceQuery(reference_node, namespace=True) or "").strip(": ")
        except Exception:
            return ""

    def _cameras(self) -> list[str]:
        cmds = _maya_cmds_or_none()
        if cmds is None:
            return []
        try:
            cameras = []
            for shape in cmds.ls(type="camera") or []:
                parents = cmds.listRelatives(shape, parent=True, fullPath=False) or []
                cameras.append(str(parents[0] if parents else shape))
            return sorted(_dedupe(cameras))
        except Exception:
            return []

    def _current_view_camera(self) -> str:
        cmds = _maya_cmds_or_none()
        if cmds is None:
            return ""
        try:
            panel = cmds.getPanel(withFocus=True)
            if not panel or cmds.getPanel(typeOf=panel) != "modelPanel":
                panels = cmds.getPanel(visiblePanels=True) or cmds.getPanel(type="modelPanel") or []
                panel = next((item for item in panels if cmds.getPanel(typeOf=item) == "modelPanel"), "")
            camera = cmds.modelPanel(panel, query=True, camera=True) if panel else ""
        except Exception:
            return ""
        return _camera_transform_name(cmds, str(camera or ""))

    def _quality_preset_combo(self, current: str) -> QtWidgets.QComboBox:
        combo = QtWidgets.QComboBox()
        try:
            from smartlib.dcc.maya.playblast_preset import preset_label, preset_names

            for name in preset_names(self.project_config):
                combo.addItem(preset_label(self.project_config, name), name)
        except Exception:
            pass
        if combo.count() == 0:
            combo.addItem(current or "default", current or "")
        index = combo.findData(current)
        combo.setCurrentIndex(max(0, index))
        return combo

    def _frame_mode_combo(self, current: str) -> QtWidgets.QComboBox:
        combo = QtWidgets.QComboBox()
        for mode in FRAME_MODES:
            combo.addItem(mode)
        index = combo.findText(current)
        combo.setCurrentIndex(max(0, index))
        return combo

    def _take_spinbox(self, value: Any) -> QtWidgets.QSpinBox:
        spin = _TwoDigitSpinBox()
        spin.setRange(1, 99)
        try:
            number = int(str(value or "1").lower().replace("take", ""))
        except ValueError:
            number = 1
        spin.setValue(max(1, min(99, number)))
        return spin

    def _resolved_output_path(self, node_id: str) -> str:
        settings = self._output_render_settings(node_id)
        return str(settings.get("output_path") or "")

    def _output_render_settings(self, node_id: str) -> dict[str, Any]:
        try:
            from smartlib.dcc.maya.render_graph import evaluate_output_render_settings

            self._sync_from_node_graph()
            return evaluate_output_render_settings(self.graph, node_id, self.project_config)
        except Exception:
            return {}

    def _output_layer_name(self, node_id: str) -> str:
        node = self.graph.node(node_id)
        settings = self._output_render_settings(node_id)
        return str(settings.get("layer") or (node.name if node else "") or "-")

    def _ae_slot_display_text(self, source: RenderNode | None) -> str:
        if not source:
            return "-"
        status = self._output_take_status(source.id)
        if status["state"] == "replace":
            return f"{self._output_layer_name(source.id)}  Current {status['current']} -> Latest {status['latest']}  Replace"
        if status["latest"]:
            return f"{self._output_layer_name(source.id)}  Latest {status['latest']}"
        return self._output_layer_name(source.id)

    def _output_take_status(self, node_id: str) -> dict[str, str]:
        node = self.graph.node(node_id)
        settings = self._output_render_settings(node_id)
        current = str(settings.get("take") or ((node.attrs or {}).get("take") if node else "") or "").strip()
        latest = _latest_take_for_package(settings.get("package_root"), current)
        current_num = _take_number(current)
        latest_num = _take_number(latest)
        if latest_num and current_num and latest_num > current_num:
            return {
                "state": "replace",
                "current": current,
                "latest": latest,
                "text": f"Current {current}  ->  Latest {latest}  Replace",
                "style": "color: rgb(255, 190, 80); font-weight: 700;",
            }
        if latest:
            return {
                "state": "latest",
                "current": current,
                "latest": latest,
                "text": f"Latest {latest} (current)",
                "style": "color: rgb(170, 225, 170); font-weight: 700;",
            }
        return {
            "state": "unknown",
            "current": current,
            "latest": "",
            "text": "Latest -",
            "style": "color: rgb(190, 190, 190);",
        }

    def _apply_project_camera_defaults(self, node: RenderNode) -> None:
        if node.type != "camera":
            return
        node.attrs = normalized_attrs(node.type, node.attrs)
        camera = self._current_view_camera()
        if camera:
            node.attrs["camera"] = camera

    def _apply_project_render_settings_defaults(self, node: RenderNode) -> None:
        if node.type != "render_settings":
            return
        node.attrs = normalized_attrs(node.type, node.attrs)
        stock = default_attrs("render_settings")
        frame_range = _ui_frame_range_for_mode(str(node.attrs.get("frame_mode") or FRAME_MODE_EDITORIAL), node.attrs, self.project_config)
        if frame_range:
            start, end = frame_range
            if int(node.attrs.get("start_frame") or 0) == int(stock.get("start_frame") or 0):
                node.attrs["start_frame"] = start
            if int(node.attrs.get("end_frame") or 0) == int(stock.get("end_frame") or 0):
                node.attrs["end_frame"] = end

    def _apply_project_output_defaults(self, node: RenderNode) -> None:
        if node.type != "output":
            return
        node.attrs = normalized_attrs(node.type, node.attrs)
        dept = _scene_department(self.project_config)
        if dept:
            node.attrs["dept"] = dept
        node.attrs["output_path"] = ""
        self._sync_output_node_name(node)

    def _apply_scene_output_context(self) -> None:
        dept = _scene_department(self.project_config)
        default_dept = str(default_attrs("output").get("dept") or "layout")
        for node in self.graph.nodes:
            if node.type != "output":
                continue
            node.attrs = normalized_attrs(node.type, node.attrs)
            current_dept = str(node.attrs.get("dept") or "")
            if dept and (not current_dept or current_dept == default_dept):
                node.attrs["dept"] = dept
            self._sync_output_node_name(node)

    def _sync_output_node_name(self, node: RenderNode) -> None:
        if node.type != "output":
            return
        legacy_layer = str(node.attrs.pop("layer", "") or "").strip()
        if legacy_layer and _is_default_output_node_name(node.name):
            node.name = legacy_layer
        elif _is_default_output_node_name(node.name):
            node.name = "CHA"

    def _configure_node_graph_ports(self, ng_node, node: RenderNode) -> None:
        if node.type != "ae_slots":
            return
        ports = sorted(self.graph.input_port_types(node.id), key=_slot_index)
        try:
            ng_node.set_port_deletion_allowed(True)
        except Exception:
            pass
        try:
            self._ensure_ae_slot_ports(ng_node, ports)
        except Exception:
            pass
        self._layout_ae_slot_ports(ng_node)

    def _ensure_ae_slot_ports(self, ng_node, ports: list[str]) -> None:
        existing = ng_node.inputs()
        for name in ports:
            if name not in existing:
                ng_node.add_input(name, multi_input=False, display_name=True)
        for port in list(ng_node.input_ports()):
            if port.name() not in ports:
                ng_node.delete_input(port)
        for port in list(ng_node.output_ports()):
            ng_node.delete_output(port)
        try:
            ng_node.view.draw_node()
        except Exception:
            pass

    def _layout_ae_slot_ports(self, ng_node) -> None:
        _layout_ae_slots_view(ng_node.view)

    def _update_node_graph_view(self, ng_node, node: RenderNode) -> None:
        try:
            ng_node.set_name(node.name)
        except Exception:
            ng_node.NODE_NAME = node.name
            ng_node.model.name = node.name
        self._update_node_graph_info(ng_node, node)

    def _update_node_graph_info(self, ng_node, node: RenderNode) -> None:
        get_widget = getattr(ng_node, "get_widget", None)
        widget = get_widget("smart_render_info") if callable(get_widget) else None
        if widget:
            widget.set_value(_node_info_text(node))
        ae_widget = get_widget("smart_render_ae_slots") if callable(get_widget) else None
        if ae_widget and node.type == "ae_slots":
            ae_widget.set_value(self._ae_slots_node_info(node))

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _ae_slots_node_info(self, node: RenderNode) -> str:
        lines = []
        slot_count = int(node.attrs.get("slot_count") or 3)
        for index in range(1, slot_count + 1):
            source = self._ae_slot_source(node.id, index)
            if source:
                lines.append(f"{index:02d}: {source.name}")
        return "\n".join(lines) if lines else "01: -"


def _label(key: str) -> str:
    overrides = {
        "castsShadows": "castsShadows",
        "receiveShadows": "receiveShadows",
        "motionBlur": "motionBlur",
    }
    if key in overrides:
        return overrides[key]
    return key.replace("_", " ").title()


def _legacy_graph_node_display_name(node: RenderNode) -> str:
    detail = ""
    if node.type == "camera":
        detail = str(node.attrs.get("camera") or "").strip()
    elif node.type == "object":
        detail = _object_mode_display_name(str(node.attrs.get("mode") or "selection"))
    return f"{node.name} [{detail}]" if detail else node.name


def _is_default_output_node_name(name: str) -> bool:
    text = str(name or "").strip()
    return text in {"", "Node", "Output"} or (text.startswith("Output ") and text[7:].isdigit())


def _node_info_text(node: RenderNode) -> str:
    if node.type == "camera":
        return str(node.attrs.get("camera") or "")
    if node.type == "cast":
        return str(node.attrs.get("reference_node") or node.attrs.get("namespace") or "")
    if node.type == "object":
        return _object_mode_display_name(str(node.attrs.get("mode") or "selection"))
    if node.type == "ae_slots":
        return str(node.attrs.get("slot_count") or 3)
    return ""


def _object_mode_display_name(mode: str) -> str:
    return {
        "selection": "Current Selection",
        "objects": "Objects",
        "display_layer": "Display Layer",
        "set": "Sets",
    }.get(mode, mode or "Current Selection")


def _object_refs_from_value(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    raw = str(value or "")
    return [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _merge_preserved_edges(graph: RenderGraph, current_edges: list[RenderEdge], existing_edges: list[RenderEdge]) -> list[RenderEdge]:
    merged: list[RenderEdge] = []
    keys: set[tuple[str, str, str, str]] = set()

    def add(edge: RenderEdge) -> None:
        key = _edge_key(edge)
        if key in keys or not graph.can_connect(edge.source, edge.source_port, edge.target, edge.target_port):
            return
        if _single_input_edge(graph, edge) and any(item.target == edge.target and item.target_port == edge.target_port for item in merged):
            return
        merged.append(edge)
        keys.add(key)

    for edge in current_edges:
        add(edge)
    for edge in existing_edges:
        add(edge)
    return merged


def _restore_ae_slot_edges_from_attrs(graph: RenderGraph) -> None:
    for node in graph.nodes:
        if node.type != "ae_slots":
            continue
        node.attrs = normalized_attrs(node.type, node.attrs)
        restored: list[RenderEdge] = []
        slots = node.attrs.get("slots") if isinstance(node.attrs.get("slots"), dict) else {}
        for port, source_id in slots.items():
            edge = RenderEdge(str(source_id), "out", node.id, str(port))
            if graph.can_connect(edge.source, edge.source_port, edge.target, edge.target_port):
                restored.append(edge)
        if not slots:
            for index, source_id in enumerate(node.attrs.get("order") or [], start=1):
                port = ae_slot_port_name(index)
                if any(edge.target == node.id and edge.target_port == port for edge in restored):
                    continue
                edge = RenderEdge(str(source_id), "out", node.id, port)
                if graph.can_connect(edge.source, edge.source_port, edge.target, edge.target_port):
                    restored.append(edge)
        if restored:
            restored_ports = {edge.target_port for edge in restored}
            graph.edges = [edge for edge in graph.edges if not (edge.target == node.id and edge.target_port in restored_ports)]
            graph.edges.extend(restored)
            max_slot = max((_slot_index(edge.target_port) for edge in restored), default=AE_SLOT_MIN_COUNT)
            node.attrs["slot_count"] = max(int(node.attrs.get("slot_count") or AE_SLOT_MIN_COUNT), max_slot)


def _edge_key(edge: RenderEdge) -> tuple[str, str, str, str]:
    return edge.source, edge.source_port, edge.target, edge.target_port


def _single_input_edge(graph: RenderGraph, edge: RenderEdge) -> bool:
    target = graph.node(edge.target)
    return bool(target and (target.type == "ae_slots" or edge.target_port in {"camera", "render_settings"}))


def _slot_index(port_name: Any) -> int:
    text = str(port_name or "").strip().lower()
    if text.startswith("slot") and text[4:].isdigit():
        return int(text[4:])
    return AE_SLOT_MIN_COUNT


def _take_number(value: Any) -> int:
    text = str(value or "").strip().lower()
    if text.startswith("take"):
        text = text[4:]
    elif text.startswith("t"):
        text = text[1:]
    try:
        return int(text)
    except ValueError:
        return 0


def _take_label(value: Any) -> str:
    number = _take_number(value)
    if number:
        return f"t{number:03d}"
    return str(value or "").strip()


def _latest_take_for_package(package_root: Any, current: Any) -> str:
    try:
        from smartlib.review.playblast_package import latest_take_for_package

        return latest_take_for_package(package_root, current)
    except Exception:
        return _take_label(current)


def _default_output_image_prefix(project_config: ProjectConfig) -> str:
    maya_prefix = _maya_project_output_image_prefix()
    if maya_prefix:
        return maya_prefix
    configured_prefix = _configured_output_image_prefix(project_config)
    if configured_prefix:
        return configured_prefix
    project_root = project_config.project_root or _repo_root()
    return _path_as_posix(project_root / "images" / _scene_stem())


def _maya_project_output_image_prefix() -> str:
    cmds = _maya_cmds_or_none()
    if cmds is None:
        return ""
    root = _maya_workspace_root(cmds)
    image_rule = _maya_workspace_file_rule(cmds, "images") or "images"
    if not root:
        return ""
    image_dir = Path(image_rule)
    if not image_dir.is_absolute():
        image_dir = Path(root) / image_dir
    return _path_as_posix(image_dir / _scene_stem())


def _maya_playback_range() -> tuple[int, int] | None:
    cmds = _maya_cmds_or_none()
    if cmds is None:
        return None
    try:
        start = cmds.playbackOptions(query=True, minTime=True)
        end = cmds.playbackOptions(query=True, maxTime=True)
    except Exception:
        return None
    try:
        return int(round(float(start))), int(round(float(end)))
    except Exception:
        return None


def _ui_frame_range_for_mode(mode: str, attrs: dict[str, Any], project_config: ProjectConfig | None = None) -> tuple[int, int] | None:
    if mode == FRAME_MODE_CUSTOM:
        return None
    if mode == FRAME_MODE_EDITORIAL:
        frame_range = _editorial_frame_range(project_config)
        if frame_range:
            return frame_range
        frame_range = _maya_playback_range()
        if frame_range:
            return frame_range
    if mode == FRAME_MODE_SINGLE:
        frame = _maya_current_frame()
        if frame is not None:
            return frame, frame
    if mode == FRAME_MODE_TIME_RANGE:
        frame_range = _maya_playback_range()
        if frame_range:
            return frame_range
    if mode == FRAME_MODE_RENDER_GLOBAL:
        frame_range = _maya_render_global_range()
        if frame_range:
            return frame_range
    start = _int_value(attrs.get("start_frame"), 1)
    end = _int_value(attrs.get("end_frame"), start)
    return start, end


def _editorial_frame_range(project_config: ProjectConfig | None) -> tuple[int, int] | None:
    if project_config is None:
        return None
    scene_path = _maya_scene_path(short_name=False)
    if not scene_path:
        return None
    project_root = project_config.project_root or _repo_root()
    scene = Path(scene_path.replace("\\", "/"))
    episode = ""
    sequence = ""
    shot = ""
    try:
        relative = scene.resolve().relative_to((project_root / "shots").resolve())
        if len(relative.parts) >= 3:
            episode, sequence, shot = relative.parts[0], relative.parts[1], relative.parts[2]
    except Exception:
        return None
    if not (episode and sequence and shot):
        return None
    try:
        data = read_json(project_root / "shots" / episode / sequence / shot / "shot.json", default={}) or {}
    except Exception:
        return None
    editorial = data.get("editorial") if isinstance(data, dict) else {}
    if not isinstance(editorial, dict):
        return None
    cut_in = editorial.get("cut_in")
    cut_out = editorial.get("cut_out")
    if cut_in is None or cut_out is None:
        return None
    start = _int_value(cut_in, 1)
    end = _int_value(cut_out, start)
    return start, end


def _maya_current_frame() -> int | None:
    cmds = _maya_cmds_or_none()
    if cmds is None:
        return None
    try:
        return _int_value(cmds.currentTime(query=True), 1)
    except Exception:
        return None


def _camera_transform_name(cmds: Any, camera: str) -> str:
    if not camera:
        return ""
    try:
        if cmds.nodeType(camera) == "camera":
            parents = cmds.listRelatives(camera, parent=True, fullPath=False) or []
            return str(parents[0] if parents else camera)
    except Exception:
        pass
    try:
        shapes = cmds.listRelatives(camera, shapes=True, type="camera", fullPath=False) or []
    except Exception:
        shapes = []
    return str(camera if shapes else camera)


def _maya_render_global_range() -> tuple[int, int] | None:
    cmds = _maya_cmds_or_none()
    if cmds is None:
        return None
    try:
        start = cmds.getAttr("defaultRenderGlobals.startFrame")
        end = cmds.getAttr("defaultRenderGlobals.endFrame")
    except Exception:
        return None
    return _int_value(start, 1), _int_value(end, _int_value(start, 1))


def _int_value(value: Any, fallback: int) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return int(fallback)


def _maya_workspace_root(cmds) -> str:
    for flag in ("rootDirectory", "rd"):
        try:
            root = cmds.workspace(query=True, **{flag: True})
        except Exception:
            continue
        if root:
            return str(root)
    return ""


def _maya_workspace_file_rule(cmds, rule_name: str) -> str:
    try:
        value = cmds.workspace(query=True, fileRuleEntry=rule_name)
    except Exception:
        return ""
    return str(value or "").strip()


def _configured_output_image_prefix(project_config: ProjectConfig) -> str:
    keys = {
        "output_image",
        "output_images",
        "output_image_path",
        "image_output",
        "image_output_path",
        "render_output_image",
        "render_output_images",
        "render_image_output",
        "render_image_path",
        "output_path",
    }
    for filename in ("render_settings.yml", "render_profiles.yml", "project_settings.yml"):
        value = _find_configured_path(project_config.load(filename), keys)
        if value:
            return _format_project_path(value, project_config)
    template_value = _find_configured_path(project_config.templates, keys | {"shot_render", "dept_render"})
    if template_value:
        return _format_project_path(template_value, project_config)
    return ""


def _find_configured_path(value: Any, keys: set[str]) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("default", "beauty", "final", "path", "template", "output_path"):
            if key in value:
                found = _find_configured_path(value[key], keys)
                if found:
                    return found
        for key, child in value.items():
            if str(key) in keys:
                found = _find_configured_path(child, keys)
                if found:
                    return found
        for child in value.values():
            if isinstance(child, dict):
                found = _find_configured_path(child, keys)
                if found:
                    return found
            elif isinstance(child, list):
                for item in child:
                    if isinstance(item, dict):
                        found = _find_configured_path(item, keys)
                        if found:
                            return found
    return ""


def _format_project_path(template: str, project_config: ProjectConfig) -> str:
    values = {
        "project_root": _path_as_posix(project_config.project_root) if project_config.project_root else "",
        "project_name": project_config.project_name,
        "scene_name": _scene_stem(),
        "output_name": "beauty",
        "layer": "beauty",
        "render_layer": "beauty",
        "version": "v001",
        "frame": "####",
    }
    for key, value in project_config.templates.items():
        if key not in values:
            values[key] = _format_template(str(value), values)
    formatted = _format_template(template, values)
    if "{" in formatted or "}" in formatted:
        return ""
    return formatted.replace("\\", "/")


def _format_template(template: str, values: dict[str, Any]) -> str:
    needed = {field for _, field, _, _ in Formatter().parse(template) if field}
    if any(field not in values for field in needed):
        return template
    try:
        return template.format(**values)
    except Exception:
        return template


def _scene_stem() -> str:
    scene_name = _maya_scene_path(short_name=True)
    if scene_name:
        return Path(scene_name).stem
    return "smart_render"


def _scene_department(project_config: ProjectConfig) -> str:
    env_dept = str(os.environ.get("DEPT") or os.environ.get("DEPARTMENT") or "").strip()
    scene_path = _maya_scene_path(short_name=False)
    if not scene_path:
        return env_dept
    parts = [str(part) for part in Path(scene_path.replace("\\", "/")).parts]
    lowered = [part.lower() for part in parts]
    for marker in ("work", "output", "publish"):
        if marker not in lowered:
            continue
        index = lowered.index(marker)
        if marker == "work" and index + 1 < len(parts):
            return parts[index + 1]
        if marker in {"output", "publish"} and index + 2 < len(parts) and lowered[index + 1] == "review":
            return parts[index + 2]
    depts = _configured_departments(project_config)
    for part in reversed(parts):
        if part in depts:
            return part
    return env_dept


def _configured_departments(project_config: ProjectConfig) -> set[str]:
    depts = set()
    for value in (project_config.base.get("shot_depts"), (project_config.base.get("anchors") or {}).get("shot_depts")):
        if isinstance(value, list):
            depts.update(str(item) for item in value)
        elif isinstance(value, str):
            depts.update(item.strip(" '\"") for item in value.strip("[]").split(",") if item.strip(" '\""))
    return depts


def _maya_scene_path(short_name: bool = False) -> str:
    cmds = _maya_cmds_or_none()
    if cmds is None:
        return ""
    try:
        return str(cmds.file(query=True, sceneName=True, shortName=short_name) or "")
    except Exception:
        return ""


def _path_as_posix(path: str | os.PathLike[str] | None) -> str:
    return Path(path).as_posix() if path else ""


def _node_type_from_ng(node) -> str:
    return _NG_TYPE_TO_NODE_TYPE.get(getattr(node, "type_", ""), "")


def _maya_cmds_or_none():
    try:
        import maya.cmds as cmds
    except ImportError:
        return None
    return cmds


_WINDOW = None


def show(config_dir: str | os.PathLike[str] | None = None, parent=None):
    global _WINDOW
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    from smartlib.core.qt import parent_for_maya

    window_parent = parent_for_maya(QtWidgets, parent)
    if _WINDOW is None:
        _WINDOW = SmartRenderWindow(config_dir=config_dir, parent=window_parent)
    else:
        if window_parent is not None and _WINDOW.parent() is not window_parent:
            _WINDOW.setParent(window_parent)
        _WINDOW.project_config = ProjectConfig(config_dir or _default_config_dir())
    if window_parent is not None:
        _WINDOW.setWindowFlags(_WINDOW.windowFlags() | QtCore.Qt.Window)
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    return _WINDOW

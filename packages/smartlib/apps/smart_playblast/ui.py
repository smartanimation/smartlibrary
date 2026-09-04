from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess


def _qt():
    try:
        from PySide6 import QtCore, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtWidgets
    return QtCore, QtWidgets


QtCore, QtWidgets = _qt()
_WINDOW = None
WINDOW_OBJECT_NAME = "SmartPlayblastWindow"
UI_VERSION = 26
TOOL_VERSION = "1.0.0"
ALL_LAYER_LABEL = "ALL"


class _ReorderTable(QtWidgets.QTableWidget):
    rowsReordered = QtCore.Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Do not use QAbstractItemView drag/drop: Maya's Qt build can clear
        # QTableWidgetItems before dropEvent runs.
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self.setDragDropMode(QtWidgets.QAbstractItemView.NoDragDrop)
        self._manual_drag_source = -1
        self._manual_drag_start = QtCore.QPoint()
        self._manual_dragging = False
        self._manual_snapshot = []

    def _snapshot_rows(self):
        rows = []
        for row in range(self.rowCount()):
            use_item = self.item(row, 0)
            rows.append({
                "enabled": bool(
                    use_item is not None
                    and use_item.checkState() == QtCore.Qt.Checked
                ),
                "payload": dict(
                    use_item.data(QtCore.Qt.UserRole) or {}
                ) if use_item is not None else {},
                "values": [
                    self.item(row, column).text()
                    if self.item(row, column) is not None else ""
                    for column in range(1, self.columnCount())
                ],
            })
        return rows

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._manual_drag_source = self.rowAt(event.pos().y())
            self._manual_drag_start = event.pos()
            self._manual_dragging = False
            self._manual_snapshot = self._snapshot_rows()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._manual_drag_source >= 0
            and event.buttons() & QtCore.Qt.LeftButton
            and (event.pos() - self._manual_drag_start).manhattanLength()
            >= QtWidgets.QApplication.startDragDistance()
        ):
            self._manual_dragging = True
            self.viewport().setCursor(QtCore.Qt.ClosedHandCursor)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        source_row = self._manual_drag_source
        dragging = self._manual_dragging
        rows = list(self._manual_snapshot)
        self._manual_drag_source = -1
        self._manual_dragging = False
        self._manual_snapshot = []
        self.viewport().unsetCursor()
        if event.button() != QtCore.Qt.LeftButton or not dragging or source_row < 0:
            super().mouseReleaseEvent(event)
            return
        target_row = self.rowAt(event.pos().y())
        if target_row < 0:
            target_row = max(0, self.rowCount() - 1)
        if target_row == source_row:
            event.accept()
            return
        moved_row = rows.pop(source_row)
        rows.insert(target_row, moved_row)
        event.accept()
        self._restore_drag_snapshot(rows, target_row)

    def _restore_drag_snapshot(self, rows, selected_row):
        self.blockSignals(True)
        try:
            self.clearContents()
            self.setRowCount(len(rows))
            for row, row_data in enumerate(rows):
                use_item = QtWidgets.QTableWidgetItem()
                use_item.setFlags(
                    use_item.flags() | QtCore.Qt.ItemIsUserCheckable
                )
                use_item.setCheckState(
                    QtCore.Qt.Checked
                    if row_data["enabled"] else QtCore.Qt.Unchecked
                )
                use_item.setData(
                    QtCore.Qt.UserRole,
                    dict(row_data.get("payload") or {}),
                )
                self.setItem(row, 0, use_item)
                for column, value in enumerate(row_data["values"], 1):
                    item = QtWidgets.QTableWidgetItem(str(value))
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                    self.setItem(row, column, item)
            if rows:
                self.setCurrentCell(min(selected_row, len(rows) - 1), 1)
        finally:
            self.blockSignals(False)
        self.rowsReordered.emit()


class SmartPlayblastWindow(QtWidgets.QDialog):
    COLUMNS = ("Use", "Camera", "Review Layer", "Frame Range", "Render Size", "Version", "Take")

    def __init__(self, config_dir=None, parent=None):
        super().__init__(parent)
        from smartlib.apps.shot_manager import ShotManagerService
        from smartlib.core.config_loader import ProjectConfig

        self.project_config = ProjectConfig(config_dir or _default_config_dir())
        self.service = ShotManagerService(self.project_config)
        self.identity = None
        self._loading = True
        self._last_results = {}
        self._last_preview_render_plan = {}
        self._last_output_dir = ""
        self._excluded_layers = set()
        self._layer_order = []
        self._suppress_scene_save = False
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle(f"Smart Playblast v{TOOL_VERSION}")
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Tool)
        self.resize(720, 610)
        self._build_ui()
        self._load_shots()
        self.refresh_scene()
        self._loading = False
        self._restore_scene_settings()

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(5)

        context = QtWidgets.QHBoxLayout()
        self.department = QtWidgets.QComboBox()
        self.department.addItems(self.service.shot_departments)
        anim_index = self.department.findText("anim")
        if anim_index >= 0:
            self.department.setCurrentIndex(anim_index)
        self.shot_combo = QtWidgets.QComboBox()
        # Keep the combo boxes as internal context stores for compatibility
        # with the existing scene-settings code, but do not expose context
        # switching from a shot-scoped tool.
        self.department.setVisible(False)
        self.shot_combo.setVisible(False)
        self.context_label = QtWidgets.QLabel()
        self.context_label.setTextInteractionFlags(
            QtCore.Qt.TextSelectableByMouse
        )
        context.addWidget(self.context_label, 1)
        root.addLayout(context)

        toolbar = QtWidgets.QHBoxLayout()
        self.refresh_button = QtWidgets.QPushButton("")
        self.refresh_button.setToolTip("Refresh Review Layers")
        self.refresh_button.setFixedWidth(34)
        self.option_button = QtWidgets.QPushButton("Option")
        option_menu = QtWidgets.QMenu(self.option_button)
        self.generate_review_layers_action = option_menu.addAction(
            "Review Layersから生成"
        )
        self.generate_sequencer_action = option_menu.addAction(
            "Camera Sequencerから生成"
        )
        self.option_button.setMenu(option_menu)
        self.add_button = QtWidgets.QPushButton("Add")
        self.all_button = QtWidgets.QPushButton("Select All")
        self.none_button = QtWidgets.QPushButton("Clear Selection")
        self.invert_button = QtWidgets.QPushButton("Invert Selection")
        self.match_latest_button = QtWidgets.QPushButton("Match Latest Outputs")
        self.match_latest_button.setToolTip(
            "Set every row to its latest output Version and Take"
        )
        self.delete_button = QtWidgets.QPushButton("Remove")
        toolbar.addWidget(self.refresh_button)
        toolbar.addWidget(self.option_button)
        toolbar.addWidget(self.add_button)
        toolbar.addWidget(self.all_button)
        toolbar.addWidget(self.none_button)
        toolbar.addWidget(self.invert_button)
        toolbar.addWidget(self.match_latest_button)
        toolbar.addStretch(1)
        toolbar.addWidget(self.delete_button)
        root.addLayout(toolbar)

        self.table = _ReorderTable(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for column in range(len(self.COLUMNS)):
            header.setSectionResizeMode(
                column, QtWidgets.QHeaderView.ResizeToContents
            )
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        root.addWidget(self.table, 1)

        properties = QtWidgets.QGroupBox("Properties")
        form = QtWidgets.QFormLayout(properties)
        form.setContentsMargins(4, 4, 4, 4)
        form.setHorizontalSpacing(5)
        form.setVerticalSpacing(3)
        self.camera_combo = QtWidgets.QComboBox()
        self.preset_combo = QtWidgets.QComboBox()
        self._populate_playblast_presets()
        self.preset_preview_button = QtWidgets.QPushButton("Apply Preset Preview")
        preset_row = QtWidgets.QHBoxLayout()
        preset_row.addWidget(self.preset_combo, 1)
        preset_row.addWidget(self.preset_preview_button)
        self.layer_combo = QtWidgets.QComboBox()
        self.solo_button = QtWidgets.QPushButton("Preview SOLO Layer")
        layer_row = QtWidgets.QHBoxLayout()
        layer_row.addWidget(self.layer_combo, 1)
        layer_row.addWidget(self.solo_button)
        self.range_combo = QtWidgets.QComboBox()
        self.range_combo.addItems(["Animation", "Current Frame", "Custom"])
        self.start_spin = _frame_spin()
        self.end_spin = _frame_spin()
        range_row = QtWidgets.QHBoxLayout()
        range_row.addWidget(self.start_spin)
        range_row.addWidget(QtWidgets.QLabel("to"))
        range_row.addWidget(self.end_spin)
        self.width_spin = QtWidgets.QSpinBox()
        self.width_spin.setRange(1, 16384)
        self.width_spin.setValue(1280)
        self.height_spin = QtWidgets.QSpinBox()
        self.height_spin.setRange(1, 16384)
        self.height_spin.setValue(720)
        size_row = QtWidgets.QHBoxLayout()
        size_row.addWidget(self.width_spin)
        size_row.addWidget(QtWidgets.QLabel("x"))
        size_row.addWidget(self.height_spin)
        self.version_spin = QtWidgets.QSpinBox()
        self.version_spin.setRange(1, 9999)
        self.version_spin.setValue(1)
        self.take_spin = QtWidgets.QSpinBox()
        self.take_spin.setRange(1, 999)
        self.take_spin.setValue(1)
        self.output_override = QtWidgets.QLineEdit()
        self.output_override.setPlaceholderText(
            "Project Config default (leave blank)"
        )
        form.addRow("Camera", self.camera_combo)
        form.addRow("Playblast Preset", preset_row)
        form.addRow("Review Layer", layer_row)
        form.addRow("Frame Range", self.range_combo)
        form.addRow("Start / End", range_row)
        form.addRow("Width / Height", size_row)
        form.addRow("Version", self.version_spin)
        form.addRow("Take", self.take_spin)
        form.addRow("Output Override", self.output_override)
        root.addWidget(properties)

        self.filename_label = QtWidgets.QLineEdit()
        self.filename_label.setReadOnly(True)
        self.playblast_button = QtWidgets.QPushButton("Playblast Image Sequence")
        self.playblast_button.setMinimumHeight(64)
        playblast_font = self.playblast_button.font()
        playblast_font.setPointSize(max(16, playblast_font.pointSize() + 6))
        playblast_font.setBold(True)
        self.playblast_button.setFont(playblast_font)
        root.addWidget(QtWidgets.QLabel("Output"))
        root.addWidget(self.filename_label)
        self.status = QtWidgets.QLabel("")
        root.addWidget(self.status)
        root.addWidget(self.playblast_button)

        self._apply_button_icons()

        self.shot_combo.currentIndexChanged.connect(self._shot_changed)
        self.department.currentTextChanged.connect(self._context_changed)
        self.refresh_button.clicked.connect(self.refresh_scene)
        self.add_button.clicked.connect(self.add_layer_row)
        self.all_button.clicked.connect(lambda: self._check_all(True))
        self.none_button.clicked.connect(lambda: self._check_all(False))
        self.invert_button.clicked.connect(self._invert_selection)
        self.generate_review_layers_action.triggered.connect(
            self.generate_from_review_layers
        )
        self.generate_sequencer_action.triggered.connect(
            self.generate_from_camera_sequencer
        )
        self.match_latest_button.clicked.connect(
            lambda *_: self.match_latest_outputs()
        )
        self.delete_button.clicked.connect(self._remove_row)
        self.table.currentCellChanged.connect(lambda *_: self._load_properties())
        self.camera_combo.currentTextChanged.connect(lambda *_: self._apply_properties())
        self.preset_combo.currentIndexChanged.connect(lambda *_: self._apply_properties())
        self.preset_preview_button.clicked.connect(self.preview_preset)
        self.layer_combo.currentTextChanged.connect(lambda *_: self._apply_properties())
        self.range_combo.currentTextChanged.connect(self._range_mode_changed)
        for spin in (self.start_spin, self.end_spin, self.width_spin, self.height_spin, self.version_spin, self.take_spin):
            spin.valueChanged.connect(lambda *_: self._apply_properties())
        self.output_override.editingFinished.connect(self._apply_properties)
        self.solo_button.clicked.connect(self.preview_solo)
        self.playblast_button.clicked.connect(self.playblast)
        self.table.itemChanged.connect(lambda *_: self._save_scene_settings())
        self.table.rowsReordered.connect(self._rows_reordered)
        self.table.customContextMenuRequested.connect(self._show_table_context_menu)

    def _load_shots(self):
        self.shot_combo.blockSignals(True)
        self.shot_combo.clear()
        for identity in self.service.list_shots():
            self.shot_combo.addItem(identity.code, identity)
        self.shot_combo.blockSignals(False)
        scene_identity = None
        try:
            import maya.cmds as cmds

            scene_identity = self.service.shot_identity_from_path(
                cmds.file(query=True, sceneName=True) or ""
            )
        except Exception:
            pass
        if scene_identity:
            index = self.shot_combo.findText(scene_identity.code)
            if index >= 0:
                self.shot_combo.setCurrentIndex(index)
                self.identity = self.shot_combo.currentData()
        elif self.shot_combo.count():
            self.identity = self.shot_combo.currentData()
        self._update_context_label()

    def _update_context_label(self):
        shot = self.identity.code if self.identity else "Unknown Shot"
        department = self.department.currentText() or "anim"
        self.context_label.setText(
            f"{self.project_config.project_name}  /  {shot}  /  {department}"
        )
        self.context_label.setToolTip(
            "Current Maya scene context (read only)"
        )

    def _shot_changed(self):
        if self._loading:
            return
        self.identity = self.shot_combo.currentData()
        self._update_context_label()
        self.refresh_scene()
        self._apply_suggested_version_take()
        self._update_output_preview()
        self._save_scene_settings()

    def _context_changed(self):
        if self._loading:
            return
        self._update_context_label()
        self.refresh_scene()
        self._apply_suggested_version_take()
        self._update_output_preview()
        self._save_scene_settings()

    def refresh_scene(self):
        import maya.cmds as cmds

        current = {self._text(row, 2): self._row(row) for row in range(self.table.rowCount())}
        layers = (
            self.service.review_layers(
                self.identity,
                self.department.currentText() or "anim",
            )
            if self.identity
            else {}
        )
        cameras = _scene_cameras(cmds)
        self.camera_combo.clear()
        self.camera_combo.addItems(cameras)
        self.layer_combo.clear()
        ordered_layers = sorted(
            layers,
            key=lambda name: int((layers.get(name) or {}).get("order", 0)),
        )
        try:
            from smartlib.dcc.maya.review_playblast import load_scene_playblast_settings

            saved = load_scene_playblast_settings(cmds)
            saved_order = [
                str(name)
                for name in (saved.get("layer_order") or [])
                if str(name) in layers
            ]
            if (
                saved_order
                and str(saved.get("shot") or "") == self.identity.code
                and str(saved.get("department") or "")
                == (self.department.currentText() or "anim")
            ):
                order_index = {
                    name: index for index, name in enumerate(saved_order)
                }
                original_index = {
                    name: index for index, name in enumerate(ordered_layers)
                }
                ordered_layers.sort(
                    key=lambda name: order_index.get(
                        name, len(order_index) + original_index[name]
                    )
                )
        except Exception:
            pass
        self.layer_combo.addItems(ordered_layers)
        self.table.setRowCount(0)
        start = int(cmds.playbackOptions(query=True, minTime=True))
        end = int(cmds.playbackOptions(query=True, maxTime=True))
        version, take = self._suggested_version_take()
        latest_outputs = self._latest_preview_outputs()
        active_camera = _active_camera(cmds) or (cameras[0] if cameras else "")
        for layer_name in ordered_layers:
            contract = layers.get(layer_name) or {}
            try:
                from smartlib.dcc.maya.review_playblast import (
                    load_display_layer_row_settings,
                )

                stored_row = load_display_layer_row_settings(layer_name, cmds)
            except Exception:
                stored_row = {}
            previous = current.get(layer_name) or stored_row or {}
            latest_output = latest_outputs.get(layer_name) or {}
            latest_version = str(latest_output.get("version") or "")
            latest_take = str(latest_output.get("take") or "")
            output_version = (
                int(latest_version[1:])
                if re.fullmatch(r"v\d+", latest_version)
                else version
            )
            output_take = (
                int(latest_take[1:])
                if re.fullmatch(r"t\d+", latest_take)
                else take
            )
            frame_range = contract.get("export_frame_range") or contract.get("frame_range")
            if not isinstance(frame_range, (list, tuple)) or len(frame_range) < 2:
                frame_range = (start, end)
            resolution = contract.get("resolution") or {}
            if isinstance(resolution, dict):
                width = int(resolution.get("width") or 1280)
                height = int(resolution.get("height") or 720)
            elif isinstance(resolution, (list, tuple)) and len(resolution) >= 2:
                width, height = int(resolution[0]), int(resolution[1])
            else:
                width, height = 1280, 720
            camera = str((contract.get("camera") or {}).get("name") or active_camera)
            member_count = len(contract.get("members") or []) + len(contract.get("objects") or [])
            self._append_row(
                enabled=bool(member_count) and previous.get("enabled", True),
                camera=_dag_leaf(previous.get("camera", camera)),
                layer=layer_name,
                start=previous.get("start", frame_range[0]),
                end=previous.get("end", frame_range[1]),
                width=previous.get("width", width),
                height=previous.get("height", height),
                version=previous.get("version", output_version),
                take=previous.get("take", output_take),
                mode=previous.get("mode", "Animation"),
                preset=previous.get(
                    "preset",
                    contract.get("playblast_preset") or self.preset_combo.itemData(0) or "",
                ),
                output_override=previous.get("output_override", ""),
            )
            use_item = self.table.item(self.table.rowCount() - 1, 0)
            use_item.setData(QtCore.Qt.UserRole + 1, member_count)
            use_item.setToolTip(f"{member_count} member(s)")
            if not member_count:
                use_item.setFlags(use_item.flags() & ~QtCore.Qt.ItemIsEnabled)
        if self.table.rowCount():
            self.table.setCurrentCell(0, 1)
        self.table.resizeColumnsToContents()
        self.status.setText(f"{len(layers)} Review Layer(s) loaded from review_spec.json")
        self._update_output_preview()
        self._refresh_publish_availability()
        self._save_scene_settings()

    def add_layer_row(self):
        import maya.cmds as cmds
        from smartlib.dcc.maya.review_playblast import set_display_layer_excluded

        layer = self.layer_combo.currentText()
        if not layer or layer == ALL_LAYER_LABEL:
            self.status.setText("Select a Display Layer in Properties, then press Add")
            return
        for row in range(self.table.rowCount()):
            if self._text(row, 2) == layer:
                self.table.setCurrentCell(row, 1)
                return
        self._excluded_layers.discard(layer)
        set_display_layer_excluded(layer, False, cmds)
        version, take = self._suggested_version_take()
        self._append_row(
            enabled=True,
            camera=self.camera_combo.currentText() or _active_camera(cmds),
            layer=layer,
            start=self.start_spin.value(),
            end=self.end_spin.value(),
            width=self.width_spin.value(),
            height=self.height_spin.value(),
            version=version,
            take=take,
            mode=self.range_combo.currentText(),
            preset=self.preset_combo.currentData() or "",
        )
        self.table.setCurrentCell(self.table.rowCount() - 1, 1)
        self._layer_order = [
            self._text(row, 2) for row in range(self.table.rowCount())
        ]
        self._save_scene_settings()
        self.status.setText(f"Added: {layer}")

    def generate_from_review_layers(self):
        self.refresh_scene()
        self.status.setText(
            f"Generated {self.table.rowCount()} row(s) from Review Layers"
        )

    def generate_from_camera_sequencer(self):
        from smartlib.dcc.maya.smart_shot import list_sequencer_shots

        shots = list_sequencer_shots()
        if not shots:
            QtWidgets.QMessageBox.warning(
                self,
                "Smart Playblast",
                "Camera Sequencer shot was not found.",
            )
            return
        resolution = list(
            (self.project_config.base.get("anchors") or {}).get("resolution")
            or [1280, 720]
        )
        width = int(resolution[0]) if len(resolution) > 0 else 1280
        height = int(resolution[1]) if len(resolution) > 1 else 720
        latest_outputs = self._latest_preview_outputs()
        default_version, default_take = self._suggested_version_take()
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(0)
            for shot in shots:
                latest = latest_outputs.get(shot.shot) or {}
                version_label = str(latest.get("version") or "")
                take_label = str(latest.get("take") or "")
                version = (
                    int(version_label[1:])
                    if re.fullmatch(r"v\d+", version_label)
                    else default_version
                )
                take = (
                    int(take_label[1:])
                    if re.fullmatch(r"t\d+", take_label)
                    else default_take
                )
                self._append_row(
                    enabled=True,
                    camera=_dag_leaf(shot.camera),
                    layer=shot.shot,
                    display_layer=ALL_LAYER_LABEL,
                    source_type="camera_sequencer",
                    start=shot.start,
                    end=shot.end,
                    width=width,
                    height=height,
                    version=version,
                    take=take,
                    mode="Custom",
                    preset=self.preset_combo.itemData(0) or "",
                )
        finally:
            self.table.blockSignals(False)
        if self.table.rowCount():
            self.table.setCurrentCell(0, 1)
        self._layer_order = [
            self._text(row, 2) for row in range(self.table.rowCount())
        ]
        self._load_properties()
        self._save_scene_settings()
        self.status.setText(
            f"Generated {len(shots)} row(s) from Camera Sequencer"
        )

    def add_all_row(self):
        for row in range(self.table.rowCount()):
            if self._text(row, 2) == ALL_LAYER_LABEL:
                self.table.setCurrentCell(row, 1)
                return
        import maya.cmds as cmds
        self._excluded_layers.discard(ALL_LAYER_LABEL)
        cameras = _scene_cameras(cmds)
        start = int(cmds.playbackOptions(query=True, minTime=True))
        end = int(cmds.playbackOptions(query=True, maxTime=True))
        version, take = self._suggested_version_take()
        self._append_row(
            enabled=True,
            camera=_active_camera(cmds) or (cameras[0] if cameras else ""),
            layer=ALL_LAYER_LABEL,
            start=start,
            end=end,
            width=1280,
            height=720,
            version=version,
            take=take,
            mode="Animation",
            preset=self.preset_combo.itemData(0) or "",
        )
        self.table.setCurrentCell(self.table.rowCount() - 1, 1)
        self._layer_order = [
            self._text(row, 2) for row in range(self.table.rowCount())
        ]
        self._save_scene_settings()

    def _rows_reordered(self):
        self._layer_order = [
            self._text(row, 2) for row in range(self.table.rowCount())
        ]
        self._save_scene_settings()
        self.status.setText("Smart Playblast row order saved in the Maya scene")

    def _show_table_context_menu(self, point):
        row = self.table.rowAt(point.y())
        if row >= 0:
            self.table.setCurrentCell(row, 1)
        menu = QtWidgets.QMenu(self.table)
        open_action = menu.addAction(
            self.style().standardIcon(QtWidgets.QStyle.SP_DirOpenIcon),
            "Open Output Folder",
        )
        new_take_action = menu.addAction(
            self.style().standardIcon(QtWidgets.QStyle.SP_FileDialogNewFolder),
            "New Take",
        )
        version_up_action = menu.addAction(
            self.style().standardIcon(QtWidgets.QStyle.SP_ArrowUp),
            "Version UP",
        )
        match_latest_action = menu.addAction(
            self.style().standardIcon(QtWidgets.QStyle.SP_BrowserReload),
            "Match Latest Output",
        )
        match_latest_action.setEnabled(
            row >= 0 and bool(self._latest_output_for_row(row))
        )
        open_action.setEnabled(bool(self._last_output_dir))
        selected = menu.exec_(self.table.viewport().mapToGlobal(point))
        if selected == open_action:
            self.open_output_folder()
        elif selected == new_take_action:
            self.new_take()
        elif selected == version_up_action:
            self.version_up()
        elif selected == match_latest_action:
            self.match_latest_outputs(row)

    def _latest_preview_outputs(self):
        if not self.identity:
            return {}
        return self.service.latest_preview_render_outputs(
            self.identity,
            department=self.department.currentText() or "anim",
        )

    def _latest_output_for_row(self, row):
        if row < 0 or row >= self.table.rowCount():
            return {}
        return self._latest_preview_outputs().get(self._text(row, 2), {})

    def _refresh_publish_availability(self):
        outputs = self._latest_preview_outputs()
        return outputs

    def match_latest_outputs(self, row=None):
        outputs = self._latest_preview_outputs()
        target_rows = (
            [row]
            if (
                isinstance(row, int)
                and not isinstance(row, bool)
                and 0 <= row < self.table.rowCount()
            )
            else list(range(self.table.rowCount()))
        )
        matched = 0
        matched_labels = []
        self.table.blockSignals(True)
        try:
            for table_row in target_rows:
                layer_name = self._text(table_row, 2)
                latest = outputs.get(layer_name)
                if not latest:
                    continue
                version = int(str(latest["version"])[1:])
                take = int(str(latest["take"])[1:])
                self.table.item(table_row, 5).setText(str(version))
                self.table.item(table_row, 6).setText(str(take))
                matched += 1
                matched_labels.append(
                    f"{layer_name} v{version:03d}/t{take:02d}"
                )
        finally:
            self.table.blockSignals(False)
        if self.table.currentRow() >= 0:
            self._load_properties()
        self._save_scene_settings()
        self._refresh_publish_availability()
        details = ", ".join(matched_labels) if matched_labels else "none found"
        self.status.setText(f"Latest Preview Render: {details}")

    def new_take(self):
        row = self.table.currentRow()
        if row < 0:
            return
        data = self._row(row)
        take = data["take"] + 1
        self.table.item(row, 6).setText(str(take))
        self._load_properties()
        self._save_scene_settings()
        self.status.setText(f"New Take: t{take:02d}")

    def version_up(self):
        row = self.table.currentRow()
        if row < 0:
            return
        data = self._row(row)
        version = data["version"] + 1
        self.table.item(row, 5).setText(str(version))
        self.table.item(row, 6).setText("1")
        self._load_properties()
        self._save_scene_settings()
        self.status.setText(f"Version UP: v{version:03d} / t01")

    def _set_all_version_take(self, version, take):
        self._loading = True
        self.version_spin.setValue(int(version))
        self.take_spin.setValue(int(take))
        for row in range(self.table.rowCount()):
            self.table.item(row, 5).setText(str(int(version)))
            self.table.item(row, 6).setText(str(int(take)))
        self._loading = False
        self._update_output_preview()
        self._save_scene_settings()

    def _apply_button_icons(self):
        style = self.style()
        icon_map = (
            (self.refresh_button, QtWidgets.QStyle.SP_BrowserReload),
            (self.option_button, QtWidgets.QStyle.SP_FileDialogDetailedView),
            (self.add_button, QtWidgets.QStyle.SP_FileDialogNewFolder),
            (self.all_button, QtWidgets.QStyle.SP_DialogApplyButton),
            (self.none_button, QtWidgets.QStyle.SP_DialogCancelButton),
            (self.invert_button, QtWidgets.QStyle.SP_BrowserReload),
            (self.match_latest_button, QtWidgets.QStyle.SP_BrowserReload),
            (self.delete_button, QtWidgets.QStyle.SP_TrashIcon),
            (self.preset_preview_button, QtWidgets.QStyle.SP_BrowserReload),
            (self.solo_button, QtWidgets.QStyle.SP_ComputerIcon),
            (self.playblast_button, QtWidgets.QStyle.SP_MediaPlay),
        )
        for button, standard_pixmap in icon_map:
            button.setIcon(style.standardIcon(standard_pixmap))
            button.setIconSize(QtCore.QSize(18, 18))
        self.playblast_button.setIconSize(QtCore.QSize(26, 26))

    def _move_table_row(self, source_row, target_row):
        if source_row == target_row:
            return
        items = [self.table.takeItem(source_row, column) for column in range(self.table.columnCount())]
        self.table.removeRow(source_row)
        self.table.insertRow(target_row)
        for column, item in enumerate(items):
            self.table.setItem(target_row, column, item)

    def _suggested_version_take(self):
        if not self.identity:
            return 1, 1
        from smartlib.review.package import latest_review_version, next_review_take
        department = self.department.currentText() or "anim"
        shot_root = self.service.shot_root(self.identity)
        publish_root = self.service.shot_publish_root(self.identity)
        version = latest_review_version(
            shot_root, department, publish_root=publish_root
        ) or 1
        version_dir = publish_root / "review" / department / f"v{version:03d}"
        return version, next_review_take(version_dir)

    def _apply_suggested_version_take(self):
        version, take = self._suggested_version_take()
        was_loading = self._loading
        self._loading = True
        self.version_spin.setValue(version)
        self.take_spin.setValue(take)
        self._loading = was_loading
        for row in range(self.table.rowCount()):
            self.table.item(row, 5).setText(str(version))
            self.table.item(row, 6).setText(str(take))

    def _append_row(self, *, enabled, camera, layer, start, end, width, height, version, take, mode, preset="", output_override="", display_layer="", source_type="review_layers"):
        row = self.table.rowCount()
        self.table.insertRow(row)
        use = QtWidgets.QTableWidgetItem()
        use.setFlags(use.flags() | QtCore.Qt.ItemIsUserCheckable)
        use.setCheckState(QtCore.Qt.Checked if enabled else QtCore.Qt.Unchecked)
        self.table.setItem(row, 0, use)
        values = (camera, layer, f"{start}–{end}", f"{width}×{height}", version, take)
        for column, value in enumerate(values, 1):
            item = QtWidgets.QTableWidgetItem(str(value))
            item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
            self.table.setItem(row, column, item)
        use.setData(QtCore.Qt.UserRole, {
            "start": int(start), "end": int(end), "width": int(width), "height": int(height),
            "mode": mode, "preset": str(preset or ""),
            "output_override": str(output_override or ""),
            "display_layer": str(display_layer or layer),
            "source_type": str(source_type or "review_layers"),
        })

    def _row(self, row):
        item = self.table.item(row, 0)
        data = dict(item.data(QtCore.Qt.UserRole) or {})
        data.update({
            "enabled": item.checkState() == QtCore.Qt.Checked,
            "camera": self._text(row, 1),
            "layer": self._text(row, 2),
            "display_layer": str(data.get("display_layer") or self._text(row, 2)),
            "version": int(self._text(row, 5) or 1),
            "take": int(self._text(row, 6) or 1),
        })
        return data

    def _load_properties(self):
        row = self.table.currentRow()
        if row < 0:
            return
        self._loading = True
        data = self._row(row)
        _select_text(self.camera_combo, data["camera"])
        _select_data(self.preset_combo, data.get("preset", ""))
        _select_text(self.layer_combo, data.get("display_layer") or data["layer"])
        self.layer_combo.setEnabled(
            data.get("source_type") != "camera_sequencer"
        )
        _select_text(self.range_combo, data.get("mode", "Animation"))
        self.start_spin.setValue(data["start"])
        self.end_spin.setValue(data["end"])
        self.width_spin.setValue(data["width"])
        self.height_spin.setValue(data["height"])
        self.version_spin.setValue(data["version"])
        self.take_spin.setValue(data["take"])
        self.output_override.setText(data.get("output_override", ""))
        self._loading = False
        custom = self.range_combo.currentText() == "Custom"
        self.start_spin.setEnabled(custom)
        self.end_spin.setEnabled(custom)
        self._update_output_preview()

    def _apply_properties(self):
        if self._loading:
            return
        row = self.table.currentRow()
        if row < 0:
            return
        data = dict(self.table.item(row, 0).data(QtCore.Qt.UserRole) or {})
        data.update({
            "start": self.start_spin.value(), "end": self.end_spin.value(),
            "width": self.width_spin.value(), "height": self.height_spin.value(),
            "mode": self.range_combo.currentText(),
            "preset": self.preset_combo.currentData() or "",
            "output_override": self.output_override.text().strip(),
        })
        self.table.item(row, 0).setData(QtCore.Qt.UserRole, data)
        layer_value = (
            self._text(row, 2)
            if data.get("source_type") == "camera_sequencer"
            else self.layer_combo.currentText()
        )
        values = (
            self.camera_combo.currentText(), layer_value,
            f"{data['start']}–{data['end']}", f"{data['width']}×{data['height']}",
            self.version_spin.value(), self.take_spin.value(),
        )
        for column, value in enumerate(values, 1):
            self.table.item(row, column).setText(str(value))
        self._update_output_preview()

    def _range_mode_changed(self, mode):
        if self._loading:
            return
        import maya.cmds as cmds
        if mode == "Animation":
            self.start_spin.setValue(int(cmds.playbackOptions(query=True, minTime=True)))
            self.end_spin.setValue(int(cmds.playbackOptions(query=True, maxTime=True)))
        elif mode == "Current Frame":
            frame = int(cmds.currentTime(query=True))
            self.start_spin.setValue(frame)
            self.end_spin.setValue(frame)
        custom = mode == "Custom"
        self.start_spin.setEnabled(custom)
        self.end_spin.setEnabled(custom)
        self._apply_properties()

    def preview_solo(self):
        import maya.cmds as cmds
        from smartlib.dcc.maya.review_playblast import display_layers
        self._sync_review_display_layers()
        target = self.layer_combo.currentText()
        target_node = target
        for layer in display_layers(cmds):
            if cmds.objExists(f"{layer}.visibility"):
                cmds.setAttr(f"{layer}.visibility", layer == target_node)
        self.status.setText(f"SOLO preview: {target}")

    def preview_preset(self):
        try:
            from smartlib.dcc.maya.playblast_preset import apply_playblast_preset
            preset = str(self.preset_combo.currentData() or "")
            apply_playblast_preset(self.project_config, preset)
            self.status.setText(f"Preset preview applied: {self.preset_combo.currentText()}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Preset Preview Failed", str(exc))

    def playblast(self):
        if not self.identity:
            QtWidgets.QMessageBox.warning(self, "Smart Playblast", "Select a shot.")
            return
        try:
            import maya.cmds as cmds
            from smartlib.dcc.maya.review_playblast import export_preview_render_groups

            rows = [
                self._row(row)
                for row in range(self.table.rowCount())
                if self._row(row)["enabled"]
            ]
            if not rows:
                raise RuntimeError("Enable at least one Review Layer.")

            created = self._sync_review_display_layers()
            rows = [
                row
                for row in rows
                if (
                    str(row.get("display_layer") or "") == ALL_LAYER_LABEL
                    or int(created.get(str(row.get("display_layer") or ""), 0)) > 0
                )
            ]
            if not rows:
                raise RuntimeError("Enabled Review Layers contain no resolvable members.")

            # At this stage Smart Playblast only emits the image sequence.
            # Package publishing, movie encoding, AE setup and RV launch are
            # intentionally left out.
            self.status.setText("Playblast running…")
            QtWidgets.QApplication.processEvents()
            settings = self._scene_settings()
            settings["rows"] = rows
            plan = self.service.plan_preview_render_publish(
                self.identity,
                settings,
                department=self.department.currentText() or "anim",
            )
            self.hide()
            QtWidgets.QApplication.processEvents()
            try:
                exported = export_preview_render_groups(
                    plan,
                    cmds=cmds,
                    project_config=self.project_config,
                )
            finally:
                self.show()
                self.raise_()
                self.activateWindow()
            output_dirs = [Path(group["output_dir"]) for group in plan.get("groups") or []]
            summary = ", ".join(f"{name}: {data['file_count']}" for name, data in exported.items())
            self.service.record_preview_render_outputs(
                plan,
                exported,
                source_scene=cmds.file(query=True, sceneName=True) or "",
            )
            self.status.setText(f"Preview Render output recorded: {summary}")
            self._last_results = exported
            self._last_preview_render_plan = plan
            self._last_output_dir = str(output_dirs[-1])
            self._save_scene_settings()
            output_text = "\n".join(str(path) for path in output_dirs)
            QtWidgets.QMessageBox.information(
                self,
                "Smart Playblast",
                f"Image sequence exported:\n{output_text}\n\n{summary}\n\n"
                "Export Playblast Settings Data from Shot Manager next.",
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Smart Playblast Failed", str(exc))
            self.status.setText(str(exc))

    def _review_layers_from_table(self):
        existing = self.service.review_layers(
            self.identity,
            self.department.currentText() or "anim",
        )
        layers = {}
        has_review_layer_rows = False
        for order in range(self.table.rowCount()):
            row = self._row(order)
            if row.get("source_type") == "camera_sequencer":
                continue
            has_review_layer_rows = True
            name = str(row.get("layer") or "")
            if not name or name == ALL_LAYER_LABEL:
                continue
            layer = dict(existing.get(name) or {})
            camera = dict(layer.get("camera") or {})
            camera.update(
                {
                    "publish_type": "camera",
                    "version": "latest",
                    "name": row["camera"],
                }
            )
            ae = dict(layer.get("ae") or {})
            ae.setdefault("comp_name", name)
            ae.setdefault("template_slot", name)
            ae.setdefault("blend_mode", "normal")
            layer.update(
                {
                    "order": order * 10,
                    "camera": camera,
                    "frame_range": row["mode"],
                    "export_frame_range": [row["start"], row["end"]],
                    "resolution": {
                        "width": row["width"],
                        "height": row["height"],
                        "scale": 1.0,
                    },
                    "playblast_preset": row.get("preset", ""),
                    "output_override": row.get("output_override", ""),
                    "ae": ae,
                }
            )
            layers[name] = layer
        return layers if has_review_layer_rows else existing

    def save_review_spec(self):
        if not self.identity:
            return None
        try:
            path = self.service.write_review_layers(
                self.identity,
                self._review_layers_from_table(),
                department=self.department.currentText() or "anim",
                comment="Saved from Smart Playblast",
            )
            self._layer_order = [
                self._text(row, 2) for row in range(self.table.rowCount())
            ]
            self._save_scene_settings()
            self.status.setText(f"Review Spec saved: {path}")
            return path
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, "Save Review Spec Failed", str(exc)
            )
            self.status.setText(str(exc))
            return None

    def _sync_review_display_layers(self):
        from smartlib.dcc.maya.shot_builder import create_review_display_layers

        if not self.identity:
            return {}
        contract = self.service.load_cast(self.identity)
        contract["review_layers"] = self.service.review_layers(
            self.identity,
            self.department.currentText() or "anim",
        )
        return create_review_display_layers(contract)

    def open_output_folder(self):
        folder = self._selected_output_dir()
        if not folder:
            folder = Path(self._last_output_dir)
        if not folder.is_dir():
            QtWidgets.QMessageBox.warning(self, "Open Output Folder", f"Folder was not found:\n{folder}")
            return
        files = sorted(
            path
            for path in folder.iterdir()
            if path.is_file()
            and path.suffix.lower() in (".png", ".jpg", ".jpeg")
        )
        if files:
            subprocess.Popen(
                ["explorer.exe", "/select,", str(files[0])],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            os.startfile(str(folder))

    def _selected_output_dir(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        layer = self._text(row, 2)
        for group in self._last_preview_render_plan.get("groups") or []:
            if str(group.get("source_layer") or "") == layer:
                value = str(group.get("output_dir") or "")
                return Path(value) if value else None
        return None

    def _build_plan(self, rows):
        import maya.cmds as cmds
        from smartlib.dcc.maya.review_playblast import (
            ALL_DISPLAY_LAYERS,
            display_layer_members,
            display_layers,
        )
        from smartlib.review.package import build_review_package_plan

        if not rows:
            raise RuntimeError("Enable at least one display layer.")
        versions = {(row["version"], row["take"]) for row in rows}
        if len(versions) != 1:
            raise RuntimeError("All enabled rows must use the same Version and Take.")
        review_layers = {}
        node_map = {}
        for order, row in enumerate(rows):
            key = _layer_key(row["layer"], review_layers)
            if row["layer"] == ALL_LAYER_LABEL:
                members = []
                for scene_layer in display_layers(cmds):
                    members.extend(display_layer_members(scene_layer, cmds))
            else:
                members = display_layer_members(row["layer"], cmds)
            if not members:
                raise RuntimeError(f"Display layer has no members: {row['layer']}")
            review_layers[key] = {
                "members": members,
                "order": order * 10,
                "outputs": ["beauty"],
                "frame_range": row["mode"],
                "export_frame_range": [row["start"], row["end"]],
                "camera": {"publish_type": "camera", "version": "scene", "name": row["camera"]},
                "resolution": {"width": row["width"], "height": row["height"], "scale": 1.0},
                "ae": {"comp_name": key, "template_slot": key, "blend_mode": "normal"},
                "playblast_preset": row.get("preset", ""),
            }
            node_map[key] = (
                ALL_DISPLAY_LAYERS if row["layer"] == ALL_LAYER_LABEL else row["layer"]
            )
        version, take = next(iter(versions))
        cast_data = self.service.load_cast(self.identity)
        cast_data["review_layers"] = review_layers
        plan = build_review_package_plan(
            self.service.shot_root(self.identity),
            self.service.load_shot(self.identity),
            cast_data,
            self.department.currentText() or "anim",
            version=version,
            take=take,
            source_workfile=cmds.file(query=True, sceneName=True) or "",
            comment="Smart Playblast from Maya display layers",
            project_root=self.service.paths.project_root,
            pipeline_root=_pipeline_root(),
            publish_root=self.service.shot_publish_root(self.identity),
        )
        plan.review_data["project"] = self.project_config.project_name
        for key, row in zip(review_layers, rows):
            filename = self._output_filename(row)
            layer_data = (plan.review_data.get("layers") or {}).get(key)
            if isinstance(layer_data, dict):
                outputs = dict(layer_data.get("outputs") or {})
                outputs["beauty"] = filename
                layer_data["outputs"] = outputs
        return plan, node_map

    def _output_filename(self, row):
        override = str(row.get("output_override") or "").strip()
        legacy_template = str(
                (
                    self.project_config.load("naming.yml").get(
                        "smart_playblast"
                    ) or {}
                ).get("filename")
                or ""
            )
        template = override or legacy_template or (
                "{project}_{episode}_{sequence}_{shot}_{dept}_"
                "{preview}_v{version}_t{take}_####.{ext}"
            )
        values = {
            "episode": self.identity.episode,
            "sequence": self.identity.sequence,
            "shot": self.identity.shot,
            "department": self.department.currentText() or "anim",
            "preview": _layer_key(row.get("layer", ""), {}),
            "cam": _dag_leaf(row.get("camera", "")),
            "version": f"{int(row.get('version', 1)):03d}",
            "take": f"{int(row.get('take', 1)):02d}",
            "frame": "####",
            "ext": "png",
            "shot_root": self.service.shot_root(self.identity).as_posix(),
        }
        try:
            from smartlib.core.output_resolver import OutputPathResolver
            output = OutputPathResolver(self.project_config).resolve(
                "__smart_playblast_override__" if override else "smart_playblast",
                values,
                default_directory="{shot_root}",
                default_filename=template.replace("*", "_"),
            )
            return re.sub(r"\.[^./\\]+$", ".png", output.filename)
        except (KeyError, ValueError) as exc:
            raise RuntimeError(
                f"Invalid Smart Playblast output rule: {exc}"
            ) from exc

    def _scene_settings(self):
        current_order = [
            self._text(row, 2) for row in range(self.table.rowCount())
        ]
        return {
            "shot": self.shot_combo.currentText(),
            "department": self.department.currentText(),
            "rows": [self._row(row) for row in range(self.table.rowCount())],
            # Always serialize the live table.  A cached order can be stale
            # after refresh/restore and would undo the user's latest drag.
            "layer_order": current_order,
            "last_output_dir": self._last_output_dir,
            "last_results": self._last_results,
            "last_preview_render_plan": self._last_preview_render_plan,
            "excluded_layers": sorted(self._excluded_layers),
        }

    def _save_scene_settings(self):
        if self._loading:
            return
        try:
            import maya.cmds as cmds
            from smartlib.dcc.maya.review_playblast import (
                save_display_layer_row_settings,
                save_scene_playblast_settings,
            )
            settings = self._scene_settings()
            save_scene_playblast_settings(settings, cmds)
            for row in settings["rows"]:
                layer = str(row.get("layer") or "")
                if layer:
                    save_display_layer_row_settings(layer, row, cmds)
        except Exception as exc:
            self.status.setText(f"Scene settings save failed: {exc}")

    def _restore_scene_settings(self):
        try:
            import maya.cmds as cmds
            from smartlib.dcc.maya.review_playblast import load_scene_playblast_settings
            settings = load_scene_playblast_settings(cmds)
        except Exception:
            return
        if not settings:
            return
        scene_identity = None
        try:
            scene_identity = self.service.shot_identity_from_path(
                cmds.file(query=True, sceneName=True) or ""
            )
        except Exception:
            pass
        saved_shot = str(settings.get("shot") or "")
        if (
            scene_identity
            and saved_shot
            and saved_shot != scene_identity.code
        ):
            # A scene copied from another shot carries its network node and
            # fileInfo. Never let that copied metadata switch this tool back
            # to the source shot.
            self.identity = scene_identity
            shot_index = self.shot_combo.findText(scene_identity.code)
            if shot_index >= 0:
                self.shot_combo.setCurrentIndex(shot_index)
            self._update_context_label()
            self._save_scene_settings()
            self.status.setText(
                f"Ignored copied Smart Playblast settings from {saved_shot}"
            )
            return
        self._loading = True
        self.shot_combo.blockSignals(True)
        self.department.blockSignals(True)
        self.table.blockSignals(True)
        shot_index = self.shot_combo.findText(str(settings.get("shot") or ""))
        if shot_index >= 0:
            self.shot_combo.setCurrentIndex(shot_index)
            self.identity = self.shot_combo.currentData()
        _select_text(self.department, str(settings.get("department") or ""))
        self._update_context_label()
        # The window is initially populated before the saved shot context is
        # known. Rebuild now so rows come from the correct Review Spec.
        self.refresh_scene()
        raw_saved_rows = [
            row for row in (settings.get("rows") or []) if isinstance(row, dict)
        ]
        saved_order = [
            str(layer)
            for layer in (settings.get("layer_order") or [])
            if str(layer).strip()
        ]
        if saved_order:
            order_index = {
                layer: index for index, layer in enumerate(saved_order)
            }
            raw_saved_rows.sort(
                key=lambda row: order_index.get(
                    str(row.get("layer") or ""),
                    len(order_index),
                )
            )
        self._layer_order = saved_order or [
            str(row.get("layer") or "")
            for row in raw_saved_rows
            if str(row.get("layer") or "").strip()
        ]
        saved_rows = {
            str(row.get("layer") or ""): row
            for row in raw_saved_rows
            if str(row.get("layer") or "").strip()
        }
        # review_spec.json is authoritative for row existence. Scene settings
        # only restore per-row output choices.
        saved_rows_are_valid = False
        if ALL_LAYER_LABEL in saved_rows and all(
            self._text(row, 2) != ALL_LAYER_LABEL for row in range(self.table.rowCount())
        ):
            data = saved_rows[ALL_LAYER_LABEL]
            self._append_row(
                enabled=bool(data.get("enabled", True)),
                camera=_dag_leaf(data.get("camera", "")),
                layer=ALL_LAYER_LABEL,
                start=int(data.get("start", 1)),
                end=int(data.get("end", 1)),
                width=int(data.get("width", 1280)),
                height=int(data.get("height", 720)),
                version=int(data.get("version", 1)),
                take=int(data.get("take", 1)),
                mode=str(data.get("mode") or "Animation"),
                preset=str(data.get("preset") or ""),
                output_override=str(data.get("output_override") or ""),
            )
        if "excluded_layers" in settings:
            self._excluded_layers = set()
        else:
            # Migrate scene settings written before excluded_layers existed:
            # layers absent from the saved table were removed by the user.
            self._excluded_layers = (
                {
                    self._text(row, 2)
                    for row in range(self.table.rowCount())
                    if self._text(row, 2) not in saved_rows
                }
                if saved_rows_are_valid else set()
            )
        for table_row in reversed(range(self.table.rowCount())):
            layer = self._text(table_row, 2)
            # The saved row list is authoritative. This prevents a removed
            # scene DisplayLayer from being recreated on every UI launch.
            if layer in self._excluded_layers or (
                saved_rows_are_valid and layer not in saved_rows
            ):
                self.table.removeRow(table_row)
        for table_row in range(self.table.rowCount()):
            layer = self._text(table_row, 2)
            data = saved_rows.get(layer)
            if not data:
                continue
            self.table.item(table_row, 0).setCheckState(
                QtCore.Qt.Checked if data.get("enabled", True) else QtCore.Qt.Unchecked
            )
            for column, key in ((1, "camera"), (5, "version"), (6, "take")):
                value = data.get(key, self._text(table_row, column))
                if key == "camera":
                    value = _dag_leaf(value)
                self.table.item(table_row, column).setText(str(value))
            payload = {
                "start": int(data.get("start", 1)),
                "end": int(data.get("end", 1)),
                "width": int(data.get("width", 1280)),
                "height": int(data.get("height", 720)),
                "mode": str(data.get("mode") or "Animation"),
                "preset": str(data.get("preset") or ""),
                "output_override": str(data.get("output_override") or ""),
                "display_layer": str(data.get("display_layer") or layer),
                "source_type": str(data.get("source_type") or "review_layers"),
            }
            self.table.item(table_row, 0).setData(QtCore.Qt.UserRole, payload)
            self.table.item(table_row, 3).setText(f"{payload['start']}–{payload['end']}")
            self.table.item(table_row, 4).setText(f"{payload['width']}×{payload['height']}")
        # Rebuild from the serialized row list instead of trying to transform
        # the scene's default DisplayLayer order. The saved list is the
        # authoritative AE/playblast order.
        if any(
            str(row.get("source_type") or "") == "camera_sequencer"
            for row in raw_saved_rows
        ):
            self.table.clearContents()
            self.table.setRowCount(0)
            for data in raw_saved_rows:
                layer = str(data.get("layer") or "")
                if not layer or layer in self._excluded_layers:
                    continue
                self._append_row(
                    enabled=bool(data.get("enabled", True)),
                    camera=_dag_leaf(data.get("camera", "")),
                    layer=layer,
                    start=int(data.get("start", 1)),
                    end=int(data.get("end", 1)),
                    width=int(data.get("width", 1280)),
                    height=int(data.get("height", 720)),
                    version=int(data.get("version", 1)),
                    take=int(data.get("take", 1)),
                    mode=str(data.get("mode") or "Animation"),
                    preset=str(data.get("preset") or ""),
                    output_override=str(data.get("output_override") or ""),
                    display_layer=str(data.get("display_layer") or layer),
                    source_type=str(data.get("source_type") or "review_layers"),
                )
        desired_order = [
            str(row.get("layer") or "")
            for row in raw_saved_rows
            if str(row.get("layer") or "").strip()
        ]
        for target_row, layer in enumerate(desired_order):
            source_row = next(
                (
                    row for row in range(self.table.rowCount())
                    if self._text(row, 2) == layer
                ),
                -1,
            )
            if source_row >= 0 and source_row != target_row:
                self._move_table_row(source_row, target_row)
        self._last_output_dir = str(settings.get("last_output_dir") or "")
        self._last_results = dict(settings.get("last_results") or {})
        self._last_preview_render_plan = dict(settings.get("last_preview_render_plan") or {})
        self._refresh_publish_availability()
        self.shot_combo.blockSignals(False)
        self.department.blockSignals(False)
        self.table.blockSignals(False)
        self._loading = False
        if self.table.rowCount():
            self.table.setCurrentCell(0, 1)
        self._load_properties()
        self._save_scene_settings()

    def closeEvent(self, event):
        if not self._suppress_scene_save:
            self._save_scene_settings()
        super().closeEvent(event)

    def _check_all(self, checked):
        state = QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(state)
        self._save_scene_settings()

    def _invert_selection(self):
        self.table.blockSignals(True)
        try:
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                item.setCheckState(
                    QtCore.Qt.Unchecked
                    if item.checkState() == QtCore.Qt.Checked
                    else QtCore.Qt.Checked
                )
        finally:
            self.table.blockSignals(False)
        self._save_scene_settings()

    def _remove_row(self):
        row = self.table.currentRow()
        if row >= 0:
            layer = self._text(row, 2)
            if layer:
                self._excluded_layers.add(layer)
                try:
                    import maya.cmds as cmds
                    from smartlib.dcc.maya.review_playblast import set_display_layer_excluded
                    if layer != ALL_LAYER_LABEL:
                        set_display_layer_excluded(layer, True, cmds)
                except Exception as exc:
                    self.status.setText(f"DisplayLayer exclusion save failed: {exc}")
            self.table.removeRow(row)
            self._layer_order = [
                self._text(index, 2)
                for index in range(self.table.rowCount())
            ]
            self._save_scene_settings()
            try:
                import maya.cmds as cmds
                from smartlib.dcc.maya.review_playblast import load_scene_playblast_settings
                saved = load_scene_playblast_settings(cmds)
                saved_layers = {
                    str(item.get("layer") or "")
                    for item in (saved.get("rows") or [])
                    if isinstance(item, dict)
                }
                if layer in saved_layers:
                    raise RuntimeError(f"Removed layer was still present after save: {layer}")
                self.status.setText(f"Removed and saved: {layer}")
            except Exception as exc:
                self.status.setText(f"Remove Row save verification failed: {exc}")

    def _text(self, row, column):
        item = self.table.item(row, column)
        return item.text().strip() if item else ""

    def _update_output_preview(self):
        if not self.identity:
            self.filename_label.setText("")
            return
        row = self.table.currentRow()
        if row < 0:
            self.filename_label.setText("")
            return
        self.filename_label.setText(self._output_filename(self._row(row)))

    def _populate_playblast_presets(self):
        from smartlib.dcc.maya.playblast_preset import preset_label, preset_names
        self.preset_combo.clear()
        for name in preset_names(self.project_config):
            self.preset_combo.addItem(preset_label(self.project_config, name), name)


def show(config_dir=None, parent=None):
    global _WINDOW
    # smart_menu reloads this module on every launch, so the module-global
    # _WINDOW reference alone cannot find windows created by an older module.
    # Remove every previous instance without allowing stale windows to
    # overwrite the settings that the active UI already auto-saved.
    for widget in list(QtWidgets.QApplication.topLevelWidgets()):
        if widget.objectName() != WINDOW_OBJECT_NAME:
            continue
        try:
            widget._suppress_scene_save = True
            widget.close()
            widget.deleteLater()
        except RuntimeError:
            pass
    if _WINDOW is not None:
        try:
            _WINDOW._suppress_scene_save = True
            _WINDOW.close()
            _WINDOW.deleteLater()
        except RuntimeError:
            pass
    _WINDOW = SmartPlayblastWindow(
        config_dir=config_dir,
        parent=parent or _maya_main_window(),
    )
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    return _WINDOW


def _maya_main_window():
    try:
        import maya.OpenMayaUI as omui
        pointer = omui.MQtUtil.mainWindow()
        if not pointer:
            return None
        try:
            from shiboken6 import wrapInstance
        except ImportError:
            from shiboken2 import wrapInstance
        return wrapInstance(int(pointer), QtWidgets.QWidget)
    except (ImportError, RuntimeError):
        return None


def _default_config_dir():
    root = Path(os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT") or Path(__file__).resolve().parents[4])
    return Path(os.environ.get("PROJECT_CONFIG_DIR") or root / "config" / "STKB")


def _pipeline_root():
    return Path(
        os.environ.get("SMARTPIPELINE_ROOT")
        or os.environ.get("SMARTLIBRARY_ROOT")
        or Path(__file__).resolve().parents[4]
    )


def _frame_spin():
    spin = QtWidgets.QSpinBox()
    spin.setRange(-1000000, 1000000)
    return spin


def _select_text(combo, text):
    index = combo.findText(text)
    if index >= 0:
        combo.setCurrentIndex(index)


def _select_data(combo, value):
    for index in range(combo.count()):
        if str(combo.itemData(index) or "") == str(value or ""):
            combo.setCurrentIndex(index)
            return


def _layer_key(name, existing):
    base = re.sub(r"[^A-Za-z0-9_]+", "_", str(name)).strip("_").upper() or "LAYER"
    key = base
    suffix = 2
    while key in existing:
        key = f"{base}_{suffix}"
        suffix += 1
    return key


def _scene_cameras(cmds):
    result = []
    for shape in (cmds.ls(type="camera", long=True) or []):
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        camera = _dag_leaf(parents[0] if parents else shape)
        if camera not in result:
            result.append(camera)
    return result


def _active_camera(cmds):
    panel = cmds.getPanel(withFocus=True)
    if panel and cmds.getPanel(typeOf=panel) == "modelPanel":
        try:
            return _dag_leaf(cmds.modelPanel(panel, query=True, camera=True))
        except Exception:
            return ""
    return ""


def _dag_leaf(name):
    return str(name or "").replace("\\", "|").rsplit("|", 1)[-1]

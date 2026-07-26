from __future__ import annotations

import os
from pathlib import Path
import re


def _qt():
    try:
        from PySide6 import QtCore, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtWidgets
    return QtCore, QtWidgets


QtCore, QtWidgets = _qt()
_WINDOW = None


class SmartPlayblastWindow(QtWidgets.QDialog):
    COLUMNS = ("Use", "Camera", "Display Layer", "Frame Range", "Render Size", "Version", "Take")

    def __init__(self, config_dir=None, parent=None):
        super().__init__(parent)
        from smartlib.apps.shot_manager import ShotManagerService
        from smartlib.core.config_loader import ProjectConfig

        self.project_config = ProjectConfig(config_dir or _default_config_dir())
        self.service = ShotManagerService(self.project_config)
        self.identity = None
        self._loading = False
        self.setWindowTitle("Smart Playblast")
        self.resize(720, 610)
        self._build_ui()
        self._load_shots()
        self.refresh_scene()

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
        context.addWidget(QtWidgets.QLabel("PROJ"))
        context.addWidget(QtWidgets.QLabel(self.project_config.project_name))
        context.addWidget(QtWidgets.QLabel("DEPT"))
        context.addWidget(self.department)
        context.addWidget(QtWidgets.QLabel("SHOT"))
        context.addWidget(self.shot_combo, 1)
        root.addLayout(context)

        toolbar = QtWidgets.QHBoxLayout()
        self.refresh_button = QtWidgets.QPushButton("Refresh Display Layers")
        self.all_button = QtWidgets.QPushButton("All")
        self.none_button = QtWidgets.QPushButton("None")
        self.delete_button = QtWidgets.QPushButton("Remove Row")
        toolbar.addWidget(self.refresh_button)
        toolbar.addWidget(self.all_button)
        toolbar.addWidget(self.none_button)
        toolbar.addStretch(1)
        toolbar.addWidget(self.delete_button)
        root.addLayout(toolbar)

        self.table = QtWidgets.QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table, 1)

        properties = QtWidgets.QGroupBox("Properties")
        form = QtWidgets.QFormLayout(properties)
        self.camera_combo = QtWidgets.QComboBox()
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
        form.addRow("Camera", self.camera_combo)
        form.addRow("Display Layer", layer_row)
        form.addRow("Frame Range", self.range_combo)
        form.addRow("Start / End", range_row)
        form.addRow("Width / Height", size_row)
        form.addRow("Version", self.version_spin)
        form.addRow("Take", self.take_spin)
        root.addWidget(properties)

        self.filename_label = QtWidgets.QLineEdit()
        self.filename_label.setReadOnly(True)
        self.playblast_button = QtWidgets.QPushButton("🎞  Playblast + Open in Smart Review")
        self.playblast_button.setMinimumHeight(42)
        root.addWidget(QtWidgets.QLabel("Output"))
        root.addWidget(self.filename_label)
        root.addWidget(self.playblast_button)
        self.status = QtWidgets.QLabel("")
        root.addWidget(self.status)

        self.shot_combo.currentIndexChanged.connect(self._shot_changed)
        self.department.currentTextChanged.connect(self._context_changed)
        self.refresh_button.clicked.connect(self.refresh_scene)
        self.all_button.clicked.connect(lambda: self._check_all(True))
        self.none_button.clicked.connect(lambda: self._check_all(False))
        self.delete_button.clicked.connect(self._remove_row)
        self.table.currentCellChanged.connect(lambda *_: self._load_properties())
        self.camera_combo.currentTextChanged.connect(lambda *_: self._apply_properties())
        self.layer_combo.currentTextChanged.connect(lambda *_: self._apply_properties())
        self.range_combo.currentTextChanged.connect(self._range_mode_changed)
        for spin in (self.start_spin, self.end_spin, self.width_spin, self.height_spin, self.version_spin, self.take_spin):
            spin.valueChanged.connect(lambda *_: self._apply_properties())
        self.solo_button.clicked.connect(self.preview_solo)
        self.playblast_button.clicked.connect(self.playblast)

    def _load_shots(self):
        self.shot_combo.blockSignals(True)
        self.shot_combo.clear()
        for identity in self.service.list_shots():
            self.shot_combo.addItem(identity.code, identity)
        self.shot_combo.blockSignals(False)
        if self.shot_combo.count():
            self.identity = self.shot_combo.currentData()

    def _shot_changed(self):
        self.identity = self.shot_combo.currentData()
        self._apply_suggested_version_take()
        self._update_output_preview()

    def _context_changed(self):
        self._apply_suggested_version_take()
        self._update_output_preview()

    def refresh_scene(self):
        from smartlib.dcc.maya.review_playblast import display_layers
        import maya.cmds as cmds

        current = {self._text(row, 2): self._row(row) for row in range(self.table.rowCount())}
        layers = display_layers(cmds)
        cameras = _scene_cameras(cmds)
        self.camera_combo.clear()
        self.camera_combo.addItems(cameras)
        self.layer_combo.clear()
        self.layer_combo.addItems(layers)
        self.table.setRowCount(0)
        start = int(cmds.playbackOptions(query=True, minTime=True))
        end = int(cmds.playbackOptions(query=True, maxTime=True))
        version, take = self._suggested_version_take()
        active_camera = _active_camera(cmds) or (cameras[0] if cameras else "")
        for layer in layers:
            previous = current.get(layer, {})
            self._append_row(
                enabled=previous.get("enabled", True),
                camera=previous.get("camera", active_camera),
                layer=layer,
                start=previous.get("start", start),
                end=previous.get("end", end),
                width=previous.get("width", 1280),
                height=previous.get("height", 720),
                version=previous.get("version", version),
                take=previous.get("take", take),
                mode=previous.get("mode", "Animation"),
            )
        if self.table.rowCount():
            self.table.setCurrentCell(0, 1)
        self.table.resizeColumnsToContents()
        self.status.setText(f"{len(layers)} display layer(s) referenced from the Maya scene")
        self._update_output_preview()

    def _suggested_version_take(self):
        if not self.identity:
            return 1, 1
        from smartlib.review.package import latest_review_version, next_review_take
        department = self.department.currentText() or "anim"
        shot_root = self.service.shot_root(self.identity)
        version = latest_review_version(shot_root, department) or 1
        version_dir = shot_root / "publish" / "review" / department / f"v{version:03d}"
        return version, next_review_take(version_dir)

    def _apply_suggested_version_take(self):
        version, take = self._suggested_version_take()
        self._loading = True
        self.version_spin.setValue(version)
        self.take_spin.setValue(take)
        self._loading = False
        for row in range(self.table.rowCount()):
            self.table.item(row, 5).setText(str(version))
            self.table.item(row, 6).setText(str(take))

    def _append_row(self, *, enabled, camera, layer, start, end, width, height, version, take, mode):
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
            "start": int(start), "end": int(end), "width": int(width), "height": int(height), "mode": mode,
        })

    def _row(self, row):
        item = self.table.item(row, 0)
        data = dict(item.data(QtCore.Qt.UserRole) or {})
        data.update({
            "enabled": item.checkState() == QtCore.Qt.Checked,
            "camera": self._text(row, 1),
            "layer": self._text(row, 2),
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
        _select_text(self.layer_combo, data["layer"])
        _select_text(self.range_combo, data.get("mode", "Animation"))
        self.start_spin.setValue(data["start"])
        self.end_spin.setValue(data["end"])
        self.width_spin.setValue(data["width"])
        self.height_spin.setValue(data["height"])
        self.version_spin.setValue(data["version"])
        self.take_spin.setValue(data["take"])
        self._loading = False
        self._range_mode_changed(self.range_combo.currentText())
        self._update_output_preview()

    def _apply_properties(self):
        if self._loading:
            return
        row = self.table.currentRow()
        if row < 0:
            return
        data = {
            "start": self.start_spin.value(), "end": self.end_spin.value(),
            "width": self.width_spin.value(), "height": self.height_spin.value(),
            "mode": self.range_combo.currentText(),
        }
        self.table.item(row, 0).setData(QtCore.Qt.UserRole, data)
        values = (
            self.camera_combo.currentText(), self.layer_combo.currentText(),
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
        target = self.layer_combo.currentText()
        for layer in display_layers(cmds):
            if cmds.objExists(f"{layer}.visibility"):
                cmds.setAttr(f"{layer}.visibility", layer == target)
        self.status.setText(f"SOLO preview: {target}")

    def playblast(self):
        if not self.identity:
            QtWidgets.QMessageBox.warning(self, "Smart Playblast", "Select a shot.")
            return
        rows = [self._row(row) for row in range(self.table.rowCount()) if self._row(row)["enabled"]]
        if not rows:
            QtWidgets.QMessageBox.warning(self, "Smart Playblast", "Enable at least one display layer.")
            return
        versions = {(row["version"], row["take"]) for row in rows}
        if len(versions) != 1:
            QtWidgets.QMessageBox.warning(self, "Smart Playblast", "All enabled rows must use the same Version and Take.")
            return
        try:
            import maya.cmds as cmds
            from smartlib.dcc.maya.review_playblast import display_layer_members, export_display_layer_sequences
            from smartlib.review.rv import open_review_json_in_smart_review

            review_layers = {}
            node_map = {}
            for order, row in enumerate(rows):
                key = _layer_key(row["layer"], review_layers)
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
                }
                node_map[key] = row["layer"]
            self.service.write_review_layers(self.identity, review_layers)
            version, take = next(iter(versions))
            plan = self.service.plan_review_publish(
                self.identity,
                self.department.currentText() or "anim",
                version=version,
                take=take,
                source_workfile=cmds.file(query=True, sceneName=True) or "",
                comment="Smart Playblast from Maya display layers",
                write=True,
            )
            self.status.setText("Playblast running…")
            QtWidgets.QApplication.processEvents()
            exported = export_display_layer_sequences(plan, node_map, cmds=cmds)
            opened, message = open_review_json_in_smart_review(plan.review_json, self.project_config)
            summary = ", ".join(f"{name}: {data['file_count']}" for name, data in exported.items())
            rv_status = "RV opened" if opened else message
            self.status.setText(f"Done — {summary} — {rv_status}")
            QtWidgets.QMessageBox.information(self, "Smart Playblast", f"{plan.version_dir}\n{summary}\n{rv_status}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Smart Playblast Failed", str(exc))
            self.status.setText(str(exc))

    def _check_all(self, checked):
        state = QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(state)

    def _remove_row(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def _text(self, row, column):
        item = self.table.item(row, column)
        return item.text().strip() if item else ""

    def _update_output_preview(self):
        if not self.identity:
            self.filename_label.setText("")
            return
        row = self.table.currentRow()
        layer = _layer_key(self._text(row, 2), {}) if row >= 0 else "LAYER"
        dept = self.department.currentText() or "anim"
        version = self.version_spin.value()
        take = self.take_spin.value()
        self.filename_label.setText(
            f"{self.identity.code}_{dept}_{layer}_v{version:03d}_take{take:03d}_####.jpg"
        )


def show(config_dir=None, parent=None):
    global _WINDOW
    if _WINDOW is not None:
        try:
            _WINDOW.close()
            _WINDOW.deleteLater()
        except RuntimeError:
            pass
    _WINDOW = SmartPlayblastWindow(config_dir=config_dir, parent=parent)
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    return _WINDOW


def _default_config_dir():
    root = Path(os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT") or Path(__file__).resolve().parents[4])
    return Path(os.environ.get("PROJECT_CONFIG_DIR") or root / "config" / "STKB")


def _frame_spin():
    spin = QtWidgets.QSpinBox()
    spin.setRange(-1000000, 1000000)
    return spin


def _select_text(combo, text):
    index = combo.findText(text)
    if index >= 0:
        combo.setCurrentIndex(index)


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
        camera = str(parents[0] if parents else shape)
        if camera not in result:
            result.append(camera)
    return result


def _active_camera(cmds):
    panel = cmds.getPanel(withFocus=True)
    if panel and cmds.getPanel(typeOf=panel) == "modelPanel":
        try:
            return str(cmds.modelPanel(panel, query=True, camera=True))
        except Exception:
            return ""
    return ""

from __future__ import annotations

import json
import os
from pathlib import Path

from smartlib.apps.smart_playblast.ui import (
    SmartPlayblastWindow, QtCore, QtWidgets, _maya_main_window,
)
from smartlib.dcc.maya import camera_output, camera_live
from .layer_list import LayerListView

WINDOW_OBJECT_NAME = "SmartCameraPlayblastWindow"
SETTINGS_NODE = ":smartCameraPlayblastInfo"
_WINDOW = None
_PORTABLE_CAMERA_PROCESSES = []


class SmartCameraPlayblastWindow(SmartPlayblastWindow):
    """Keep the production Playblast implementation; add camera authoring only."""

    def __init__(self, config_dir=None, parent=None):
        import maya.cmds as cmds
        self._camera_loading = True
        self._camera_prefs = {}
        if cmds.objExists(f"{SETTINGS_NODE}.settingsJson"):
            try:
                self._camera_prefs = json.loads(cmds.getAttr(f"{SETTINGS_NODE}.settingsJson"))
                if not isinstance(self._camera_prefs, dict):
                    self._camera_prefs = {}
            except (ValueError, TypeError):
                pass
        super().__init__(config_dir=config_dir, parent=parent)
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle("Smart Camera Playblast — Experimental v0.8 · Live")
        self.resize(680, 620)
        self.camera_splitter.setSizes([250, 410])
        self._camera_loading = False
        self._refresh_primary_cameras()
        self._load_camera_policy()

    def _build_ui(self):
        super()._build_ui()
        import maya.cmds as cmds
        root = self.layout()
        toolbar = root.itemAt(1).layout()
        root.removeItem(toolbar)
        root.removeWidget(self.table)
        properties = next(w for w in self.findChildren(QtWidgets.QGroupBox) if w.title() == "Properties")
        root.removeWidget(properties)
        properties.setTitle("OUTPUT CAMERA")
        self.output_properties = properties
        form = properties.layout()
        version_row, _ = form.getWidgetPosition(self.version_spin)
        # Maya's PySide2 does not expose QFormLayout.takeRow(). Detach only
        # the widgets; keep their signal connections and ownership intact.
        take_label = form.labelForField(self.take_spin)
        form.removeWidget(self.version_spin)
        form.removeWidget(self.take_spin)
        form.removeWidget(take_label)
        version_take = QtWidgets.QHBoxLayout()
        version_take.setSpacing(10)
        version_take.addWidget(self.version_spin)
        version_take.addWidget(take_label)
        version_take.addWidget(self.take_spin)
        version_take.addStretch(1)
        form.setLayout(version_row, QtWidgets.QFormLayout.FieldRole, version_take)
        # Resolution is part of the selected camera rule. Move the existing
        # controls beside that rule instead of leaving them in a distant form.
        size_row = None
        size_label = None
        for row in range(form.rowCount()):
            field = form.itemAt(row, QtWidgets.QFormLayout.FieldRole)
            layout = field.layout() if field else None
            if layout and any(
                layout.itemAt(index).widget() in (self.width_spin, self.height_spin)
                for index in range(layout.count())
            ):
                size_row = layout
                label = form.itemAt(row, QtWidgets.QFormLayout.LabelRole)
                size_label = label.widget() if label else None
                break
        if size_row is None:
            raise RuntimeError("Width / Height controls were not found in OUTPUT CAMERA.")
        form.removeItem(size_row)
        if size_label is not None:
            form.removeWidget(size_label)
            size_label.hide()
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.camera_splitter = splitter
        splitter.setChildrenCollapsible(False)
        left = QtWidgets.QWidget()
        left.setMinimumWidth(200)
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        heading = QtWidgets.QLabel("Review Layers")
        heading.setObjectName("layerHeading")
        left_layout.addWidget(heading)
        while toolbar.count():
            item = toolbar.takeAt(0)
            if item.widget():
                item.widget().hide()
        # Keep only the existing row model/controller, not its table UI.
        self.table.hide()
        self.layer_list = LayerListView(self.table, left)
        self.layer_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.layer_list.customContextMenuRequested.connect(self._layer_context_menu)
        self.layer_list.selectionModel().currentChanged.connect(
            lambda index, previous: self.table.setCurrentCell(index.row(), 1) if index.isValid() else None)
        self.table.currentCellChanged.connect(lambda row, *_: self._select_layer_card(row))
        self.table.rowsReordered.connect(lambda: self._select_layer_card(self.table.currentRow()))
        # Card metadata is read from sibling columns of the shared model.
        self.table.model().dataChanged.connect(lambda *_: self.layer_list.viewport().update())
        left_layout.addWidget(self.layer_list, 1)
        shared_note = QtWidgets.QLabel("Existing Playblast rows and output settings are shared. Uncheck special-camera layers before generating.")
        shared_note.setWordWrap(True)
        left_layout.addWidget(shared_note)
        splitter.addWidget(left)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 0, 0)
        primary_box = QtWidgets.QGroupBox("PRIMARY CAMERA")
        primary_box.setToolTip("Creative Source / Burn-in")
        form = QtWidgets.QFormLayout(primary_box)
        self.primary_combo = QtWidgets.QComboBox()
        self.primary_combo.setMinimumContentsLength(8)
        self.primary_combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.primary_refresh = QtWidgets.QPushButton("Refresh")
        camera_row = QtWidgets.QHBoxLayout()
        camera_row.addWidget(self.primary_combo, 1)
        camera_row.addWidget(self.primary_refresh)
        form.addRow("Primary", camera_row)
        self.primary_info = QtWidgets.QLabel()
        self.primary_info.setWordWrap(True)
        form.addRow(self.primary_info)
        self.reference_width = QtWidgets.QSpinBox()
        self.reference_height = QtWidgets.QSpinBox()
        reference = self._camera_prefs.get("reference_resolution") or [
            int(cmds.getAttr("defaultResolution.width")),
            int(cmds.getAttr("defaultResolution.height")),
        ]
        reference_row = QtWidgets.QHBoxLayout()
        for spin, value in zip((self.reference_width, self.reference_height), reference):
            spin.setRange(1, 16384)
            spin.setValue(int(value))
            reference_row.addWidget(spin)
        form.addRow("Final output size", reference_row)
        note = QtWidgets.QLabel("Burn-in source · Primary remains unchanged")
        note.setStyleSheet("font-size: 13px; color: #59b3fa;")
        note.setToolTip("Square pixels. Reference size defines the Primary camera gate.")
        note.setWordWrap(True)
        form.addRow(note)
        right_layout.addWidget(primary_box)
        right_layout.addWidget(properties)

        generation_box = QtWidgets.QGroupBox("CAMERA GENERATION")
        generation_layout = QtWidgets.QVBoxLayout(generation_box)
        rule_form = QtWidgets.QFormLayout()
        rule_form.setContentsMargins(0, 0, 0, 0)
        rule_form.setVerticalSpacing(6)
        self.fit_combo = QtWidgets.QComboBox()
        for label, policy in (("Use Primary — shared", "shared"), ("Expand by scale", "scale"),
                              ("Expand by material resolution", "resolution")):
            self.fit_combo.addItem(label, policy)
        rule_form.addRow("Output Rule", self.fit_combo)
        self.expansion_spin = QtWidgets.QDoubleSpinBox()
        self.expansion_spin.setRange(1., 10.)
        self.expansion_spin.setDecimals(3)
        self.expansion_spin.setSingleStep(.05)
        self.expansion_spin.setValue(1.1)
        self.expansion_spin.setSuffix(" ×")
        rule_form.addRow("Expansion", self.expansion_spin)
        rule_form.addRow("Width / Height", size_row)
        generation_layout.addLayout(rule_form)
        self.camera_note = QtWidgets.QLabel()
        self.camera_note.setWordWrap(True)
        generation_layout.addWidget(self.camera_note)
        self.generate_camera_button = QtWidgets.QPushButton("Apply Live Camera Rules")
        self.generate_camera_button.setObjectName("generateCameras")
        self.generate_camera_button.setMinimumHeight(30)
        generation_layout.addWidget(self.generate_camera_button)
        self.auto_update_cameras = QtWidgets.QCheckBox("Live cameras — no Bake before Playblast")
        self.auto_update_cameras.setChecked(True)
        self.auto_update_cameras.setEnabled(False)
        generation_layout.addWidget(self.auto_update_cameras)
        note = QtWidgets.QLabel("Primary is shared by default. Expanded cameras follow it live.\nPublish preserves Primary dependencies + rules; no Bake.\nFinal output framing stays unchanged; expansion adds margins.")
        note.setWordWrap(True)
        generation_layout.addWidget(note)
        right_layout.addWidget(generation_box)
        self.publish_camera_button = QtWidgets.QPushButton("Publish Camera Package…")
        self.publish_camera_button.setObjectName("publishCameraPackage")
        self.publish_camera_button.setMinimumHeight(46)
        self.publish_camera_button.setToolTip("Publish native Primary dependencies and checked layer rules. No Bake or frame scan.")
        self.publish_camera_button.clicked.connect(self.publish_camera_package)
        right_layout.addStretch()
        scroll = QtWidgets.QScrollArea()
        scroll.setMinimumWidth(340)
        scroll.setWidgetResizable(True)
        scroll.setWidget(right)
        # Publishing is the durable pipeline action, so keep it visible and
        # visually primary outside the scrolling editor.
        right_container = QtWidgets.QWidget()
        container_layout = QtWidgets.QVBoxLayout(right_container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.addWidget(scroll, 1)
        container_layout.addWidget(self.publish_camera_button)
        splitter.addWidget(right_container)
        splitter.setSizes([580, 540])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root.insertWidget(1, splitter, 1)
        self.primary_refresh.clicked.connect(self._refresh_primary_cameras)
        self.primary_combo.currentIndexChanged.connect(self._camera_controls_changed)
        self.reference_width.valueChanged.connect(self._camera_controls_changed)
        self.reference_height.valueChanged.connect(self._camera_controls_changed)
        self.fit_combo.currentIndexChanged.connect(self._camera_controls_changed)
        self.expansion_spin.valueChanged.connect(self._camera_controls_changed)
        self.table.currentCellChanged.connect(lambda *_: self._load_camera_policy())
        self.generate_camera_button.clicked.connect(lambda *_: self.generate_cameras())
        self.auto_update_cameras.toggled.connect(self._camera_controls_changed)
        self.status.setWordWrap(True)
        for spin in (self.width_spin, self.height_spin, self.start_spin, self.end_spin):
            spin.valueChanged.connect(lambda *_: self._update_camera_info())
        self._apply_reference_style()

    def _apply_reference_style(self):
        style = '''
            QDialog { background: #202020; color: #dddddd; }
            QWidget { font-family: "Segoe UI"; font-size: 12px; color: #dddddd; }
            QLabel { background: transparent; }
            QLabel#layerHeading { font-size: 15px; padding: 12px 8px; color: #eeeeee; }
            QGroupBox { background: #252525; border: 1px solid #3d3d3d;
                margin-top: 15px; padding: 16px 12px 10px; font-size: 16px; color: #59b3fa; }
            QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 4px; }
            QLineEdit, QComboBox, QSpinBox { background: #303030; border: 1px solid #494949;
                border-radius: 3px; min-height: 20px; padding: 1px 5px; font-size: 12px; selection-background-color: #386c96; }
            QComboBox::drop-down { width: 24px; border: none; }
            QComboBox::down-arrow { image: url("DOWN_ICON"); width: 10px; height: 7px; }
            QSpinBox::up-arrow { image: url("UP_ICON"); width: 9px; height: 6px; }
            QSpinBox::down-arrow { image: url("DOWN_ICON"); width: 9px; height: 6px; }
            QPushButton { background: #343434; border: 1px solid #505050; border-radius: 3px;
                min-height: 22px; padding: 3px 8px; font-size: 12px; }
            QPushButton:hover { background: #414141; border-color: #6c6c6c; }
            QPushButton:pressed { background: #252525; }
            QPushButton#generateCameras { background: #303942; border-color: #526576; }
            QPushButton#generateCameras:hover { background: #394754; border-color: #66829a; }
            QPushButton#publishCameraPackage { background: #287543; border-color: #41955e;
                font-size: 14px; font-weight: 600; }
            QPushButton#publishCameraPackage:hover { background: #318c50; }
            QListView { background: #232323; border: 1px solid #393939; outline: none; }
            QScrollArea { border: none; background: #202020; }
            QScrollArea > QWidget > QWidget { background: #202020; }
            QSplitter::handle { background: #171717; width: 5px; }
            QScrollBar:vertical { background: #222222; width: 12px; margin: 0; }
            QScrollBar::handle:vertical { background: #555555; min-height: 30px; border-radius: 4px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: #222222; }
            QCheckBox { spacing: 8px; }
            QCheckBox::indicator { width: 18px; height: 18px; }
            QMenu { background: #2b2b2b; border: 1px solid #505050; }
            QMenu::item { padding: 7px 20px; }
            QMenu::item:selected { background: #365774; }
        '''
        icon_root = Path(__file__).resolve().parent
        self.setStyleSheet(style.replace("DOWN_ICON", (icon_root / "down.svg").as_posix()).replace("UP_ICON", (icon_root / "up.svg").as_posix()))
        self.layout().setContentsMargins(10, 10, 10, 10)
        self.layout().setSpacing(8)
        for form in self.findChildren(QtWidgets.QFormLayout):
            form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
            form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
            form.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            form.setVerticalSpacing(6)
        for spin in self.findChildren(QtWidgets.QSpinBox):
            spin.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            spin.setMaximumWidth(150)
            spin.setMaximumHeight(26)
        self.version_spin.setMaximumWidth(100)
        self.take_spin.setMaximumWidth(100)
        for combo in self.findChildren(QtWidgets.QComboBox):
            combo.setMinimumContentsLength(4)
            combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            combo.setMinimumWidth(0)
        for label in (self.context_label, self.status, self.camera_note):
            label.setStyleSheet("font-size: 11px; color: #a6a6a6;")
        self.playblast_button.setStyleSheet("QPushButton { font-size: 17px; font-weight: 600; background: #1764b7; border-color: #3889d5; min-height: 62px; } QPushButton:hover { background: #2278cd; }")
        self.playblast_button.setMinimumHeight(74)

    def _select_layer_card(self, row):
        if row >= 0:
            self.layer_list.setCurrentIndex(self.table.model().index(row, 0))

    def _scene_settings(self):
        settings = super()._scene_settings()
        if self._camera_prefs.get("publish_source"):
            settings["camera_package_source"] = self._camera_prefs["publish_source"]
        return settings

    def _row(self, row):
        data = super()._row(row)
        data['camera_rule'] = dict((self._camera_prefs.get('layer_rules') or {}).get(data['layer'], {'mode': 'shared'}))
        return data

    def _restore_scene_settings(self):
        from smartlib.dcc.maya.review_playblast import load_scene_playblast_settings
        settings = load_scene_playblast_settings()
        super()._restore_scene_settings()
        if not settings.get("camera_package_source"):
            return
        # Restore the explicit package rows even before material/display layers
        # exist. This creates UI settings, never scene material memberships.
        self._loading = True
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(0)
            for row in settings.get("rows", []):
                self._append_row(**{key: row[key] for key in (
                    "enabled", "camera", "layer", "start", "end", "width", "height", "version", "take", "mode")},
                    preset=row.get("preset", ""), display_layer=row.get("display_layer", row["layer"]),
                    source_type=row.get("source_type", "review_layers"))
        finally:
            self.table.blockSignals(False)
            self._loading = False
        if self.table.rowCount():
            self.table.setCurrentCell(0, 1)
            self._load_properties()

    def _layer_context_menu(self, point):
        index = self.layer_list.indexAt(point)
        if index.isValid():
            self.table.setCurrentCell(index.row(), 1)
        menu = QtWidgets.QMenu(self.layer_list)
        for label, callback in (
            ("Refresh Review Layers", self.refresh_scene),
            ("Import from Review Layers", self.generate_from_review_layers),
            ("Import from Camera Sequencer", self.generate_from_camera_sequencer),
            ("Add Layer", self.add_layer_row),
            ("Select All", lambda: self._check_all(True)),
            ("Clear Selection", lambda: self._check_all(False)),
            ("Invert Selection", self._invert_selection),
            ("Match Latest Outputs", self.match_latest_outputs),
            ("Remove Layer", self._remove_row),
            ("New Take", self.new_take),
            ("Version UP", self.version_up),
            ("Open Output Folder", self.open_output_folder),
        ):
            menu.addAction(label, lambda checked=False, fn=callback: fn())
        menu.exec_(self.layer_list.viewport().mapToGlobal(point))

    def _refresh_primary_cameras(self):
        import maya.cmds as cmds
        saved = self.primary_combo.currentData() or self._camera_prefs.get("primary") or self.camera_combo.currentText()
        saved_uuid = self._camera_prefs.get("primary_uuid")
        resolved = cmds.ls(saved_uuid, long=True) if saved_uuid else []
        if resolved and not self.primary_combo.currentData():
            saved = resolved[0]
        self.primary_combo.blockSignals(True)
        self.primary_combo.clear()
        for shape in cmds.ls(type="camera", long=True) or []:
            node, _ = camera_output.camera_nodes(shape, cmds)
            if cmds.objExists(f"{node}.{camera_output.OWNER_ATTR}"):
                continue
            self.primary_combo.addItem(node, node)
        candidates = cmds.ls(saved, long=True) if saved else []
        if saved:
            self.primary_combo.setCurrentIndex(-1)
        if len(candidates or []) == 1:
            candidates = [camera_output.primary_camera(candidates[0], cmds)]
            index = self.primary_combo.findData(candidates[0])
            if index >= 0:
                self.primary_combo.setCurrentIndex(index)
        self.primary_combo.blockSignals(False)
        self._update_camera_info()

    def _load_camera_policy(self):
        if not hasattr(self, "fit_combo"):
            return
        row = self.table.currentRow()
        layer = self._text(row, 2) if row >= 0 else ""
        self.output_properties.setTitle(f"OUTPUT CAMERA — {layer}" if layer else "OUTPUT CAMERA")
        rule = (self._camera_prefs.get("layer_rules") or {}).get(layer, {'mode': 'shared'})
        policy = rule.get('mode', 'shared')
        self.fit_combo.blockSignals(True)
        self.fit_combo.setCurrentIndex(max(0, self.fit_combo.findData(policy)))
        self.fit_combo.blockSignals(False)
        self.expansion_spin.blockSignals(True)
        self.expansion_spin.setValue(float(rule.get('scale', 1.1)))
        self.expansion_spin.blockSignals(False)
        self._update_camera_info()

    def _camera_controls_changed(self, *_):
        if self._camera_loading or self._loading:
            return
        import maya.cmds as cmds
        source = self.primary_combo.currentData() or ""
        self._camera_prefs.update(primary=source, primary_uuid=(cmds.ls(source, uuid=True) or [""])[0],
                                  auto_update=self.auto_update_cameras.isChecked(),
                                  reference_resolution=[self.reference_width.value(), self.reference_height.value()])
        row = self.table.currentRow()
        if row >= 0:
            mode = self.fit_combo.currentData()
            rule = {'mode': mode}
            if mode == 'scale':
                rule['scale'] = self.expansion_spin.value()
            elif mode == 'resolution':
                rule.update(width=self.width_spin.value(), height=self.height_spin.value())
            self._camera_prefs.setdefault("layer_rules", {})[self._text(row, 2)] = rule
        self._save_camera_preferences()
        self._update_camera_info()

    def _save_camera_preferences(self):
        import maya.cmds as cmds
        if not cmds.objExists(SETTINGS_NODE):
            cmds.createNode("network", name=SETTINGS_NODE, skipSelect=True)
        if cmds.nodeType(SETTINGS_NODE) != "network":
            raise RuntimeError(f"Settings node name is occupied: {SETTINGS_NODE}")
        camera_output._string_attr(cmds, SETTINGS_NODE, "settingsJson", json.dumps(self._camera_prefs))

    def _update_camera_info(self):
        import maya.cmds as cmds
        source = self.primary_combo.currentData()
        try:
            _, shape = camera_output.camera_nodes(source, cmds)
            lens = cmds.getAttr(f"{shape}.focalLength")
            h = cmds.getAttr(f"{shape}.horizontalFilmAperture") * 25.4
            v = cmds.getAttr(f"{shape}.verticalFilmAperture") * 25.4
            self.primary_info.setText(f"Focal Length       {lens:.2f} mm\nFilm Aperture      {h:.2f} × {v:.2f} mm")
        except (ValueError, RuntimeError, TypeError):
            self.primary_info.setText("Select a valid Primary camera.")
        row = self.table.currentRow()
        if row >= 0:
            data = self._row(row)
            mode = self.fit_combo.currentData()
            self.expansion_spin.setEnabled(mode == 'scale')
            self.width_spin.setEnabled(mode == 'resolution')
            self.height_spin.setEnabled(mode == 'resolution')
            rule = {'mode': mode, 'scale': self.expansion_spin.value(),
                    'width': self.width_spin.value(), 'height': self.height_spin.value()}
            try:
                width, height = camera_live.output_size([self.reference_width.value(), self.reference_height.value()], rule)
                self.camera_note.setText(f"{data['layer']}: material {width} × {height} / {data['start']}–{data['end']}\nFinal framing unchanged. No Bake.")
            except ValueError as exc:
                self.camera_note.setText(str(exc))

    def generate_cameras(self, rows=None):
        import maya.cmds as cmds
        if rows is None:
            rows = [self._row(i) for i in range(self.table.rowCount()) if self._row(i)["enabled"]]
        progress = QtWidgets.QProgressDialog("Configuring live cameras — no Bake…", "", 0, 100, self)
        progress.setCancelButton(None)
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.setMinimumDuration(0)
        try:
            self._camera_controls_changed()
            rules = self._camera_prefs.get("layer_rules") or {}
            rows = [{**r, "camera_rule": rules.get(r["layer"], {'mode': 'shared'})} for r in rows]
            def update(done, total):
                progress.setValue(int(done * 100 / total))
                QtWidgets.QApplication.processEvents()
                return not progress.wasCanceled()
            results = camera_live.configure(
                self.primary_combo.currentData(), rows,
                [self.reference_width.value(), self.reference_height.value()], cmds=cmds,
            )
            was_loading = self._loading
            self._loading = True
            self.table.blockSignals(True)
            try:
                for result in results:
                    name = result["camera"].rsplit("|", 1)[-1]
                    if self.camera_combo.findText(name) < 0:
                        self.camera_combo.addItem(name)
                    for index in range(self.table.rowCount()):
                        if self._text(index, 2) == result["layer"]:
                            self.table.item(index, 1).setText(name)
                            item = self.table.item(index, 0)
                            data = dict(item.data(QtCore.Qt.UserRole) or {})
                            data.update(width=result['width'], height=result['height'])
                            item.setData(QtCore.Qt.UserRole, data)
                            self.table.item(index, 4).setText(f"{result['width']}×{result['height']}")
            finally:
                self.table.blockSignals(False)
                self._loading = was_loading
            self._load_properties()
            self._save_scene_settings()
            self.status.setText(f"Applied {len(results)} live camera rule(s). No Bake; Primary unchanged.")
            return True
        except Exception as exc:
            self.status.setText(str(exc))
            QtWidgets.QMessageBox.critical(self, "Camera Generation Failed", str(exc))
            return False
        finally:
            progress.close()

    def playblast(self):
        if not self.generate_cameras():
            return
        super().playblast()

    def publish_camera_package(self):
        import maya.cmds as cmds
        from smartlib.dcc.maya import camera_native
        if not self.identity:
            QtWidgets.QMessageBox.warning(self, "Publish Camera Package", "Select a shot first.")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Publish Camera Package")
        form = QtWidgets.QFormLayout(dialog)
        note = QtWidgets.QLabel(
            "Publish native Primary + upstream dependencies + layer rules.\n"
            "Maya Build stays live; primary_cam FBX/USD is baked in background.\n"
            "External caches / dynamic script dependencies must be resolved first.\n"
            f"Shot: {self.identity.episode} / {self.identity.sequence} / {self.identity.shot}")
        note.setWordWrap(True)
        form.addRow(note)
        target = QtWidgets.QLineEdit("main")
        subset = QtWidgets.QLineEdit("main")
        comment = QtWidgets.QLineEdit()
        form.addRow("Target", target)
        form.addRow("Subset", subset)
        form.addRow("Comment", comment)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Validate / Publish")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        progress = QtWidgets.QProgressDialog("Exporting Primary dependencies — no Bake…", "", 0, 100, self)
        progress.setCancelButton(None)
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.setMinimumDuration(0)
        try:
            import re
            if any(not re.fullmatch(r"[A-Za-z0-9_]+", value.text().strip()) for value in (target, subset)):
                raise ValueError("Target / Subset: use letters, digits and underscores.")
            self._camera_controls_changed()
            rows = [self._row(i) for i in range(self.table.rowCount()) if self._row(i)['enabled']]
            self.publish_camera_button.setEnabled(False)
            def update(done, total):
                progress.setValue(int(done * 100 / total))
                QtWidgets.QApplication.processEvents()
                return not progress.wasCanceled()
            payload = camera_native.collect(
                self.primary_combo.currentData(), rows,
                [self.reference_width.value(), self.reference_height.value()], cmds)
            progress.close()
            payload["department"] = self.department.currentText()
            payload["portable_export"] = {
                "status": "pending",
                "camera_name": "primary_cam",
                "formats": ["usd", "fbx"],
            }
            from smartlib.core.maya_runtime import resolve_mayapy, validate_worker_version
            validate_worker_version(
                resolve_mayapy(self.project_config), cmds.about(version=True)
            )
            published = self.service.publish_shot_scene_snapshot(
                self.identity, payload, data_type="camera", target=target.text().strip(),
                subset=subset.text().strip(), source_workfile=cmds.file(query=True, sceneName=True) or "",
                comment=comment.text().strip(),
                native_exporter=lambda directory: camera_native.export_native(payload, directory, cmds))
            self._start_portable_camera_export(published, cmds)
            self.status.setText(f"Camera Package published; FBX/USD bake running: {published}")
            QtWidgets.QMessageBox.information(self, "Camera Package Published",
                f"{published}\n\nSelect this Camera Publish in Build.\n"
                "Primary, output cameras and layer settings will be restored.\n"
                "World-baked primary_cam FBX/USD export is running in the background.")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Camera Publish Failed", str(exc))
            self.status.setText(str(exc))
        finally:
            progress.close()
            self.publish_camera_button.setEnabled(True)

    def _start_portable_camera_export(self, published, cmds):
        from smartlib.dcc.maya import camera_portable
        from smartlib.core.maya_runtime import (
            process_environment, resolve_mayapy, validate_worker_version,
        )

        mayapy = resolve_mayapy(self.project_config)
        validate_worker_version(mayapy, cmds.about(version=True))
        worker = Path(__file__).resolve().parents[4] / "tools" / "maya" / "camera_portable_worker.py"
        if not mayapy.is_file() or not worker.is_file():
            message = f"Camera exchange worker was not found: {mayapy} / {worker}"
            camera_portable.update_publish(published, status="failed", error=message)
            raise FileNotFoundError(message)
        process = QtCore.QProcess(QtWidgets.QApplication.instance())
        environment = QtCore.QProcessEnvironment.systemEnvironment()
        env_vars, path_vars = process_environment(self.project_config)
        for key, value in env_vars.items():
            environment.insert(key, str(value))
        for key, values in path_vars.items():
            current = environment.value(key)
            combined = list(values) + ([current] if current else [])
            environment.insert(key, os.pathsep.join(combined))
        package_root = str(Path(__file__).resolve().parents[3])
        current_pythonpath = environment.value("PYTHONPATH")
        environment.insert(
            "PYTHONPATH",
            package_root + (os.pathsep + current_pythonpath if current_pythonpath else ""),
        )
        process.setProcessEnvironment(environment)
        process.setProgram(str(mayapy))
        process.setArguments([str(worker), str(published)])
        process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        process.finished.connect(
            lambda exit_code, _status, process=process, published=Path(published):
            self._portable_camera_export_finished(process, published, exit_code)
        )
        _PORTABLE_CAMERA_PROCESSES.append(process)
        process.start()
        if not process.waitForStarted(5000):
            message = process.errorString() or "Camera exchange worker failed to start."
            camera_portable.update_publish(published, status="failed", error=message)
            _PORTABLE_CAMERA_PROCESSES.remove(process)
            process.deleteLater()
            raise RuntimeError(message)

    def _portable_camera_export_finished(self, process, published, exit_code):
        from smartlib.dcc.maya import camera_portable

        output = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace").strip()
        if process in _PORTABLE_CAMERA_PROCESSES:
            _PORTABLE_CAMERA_PROCESSES.remove(process)
        process.deleteLater()
        try:
            if exit_code == 0:
                self.status.setText(f"Camera Package ready — primary_cam FBX/USD complete: {published}")
            else:
                message = output or f"Camera exchange worker exited with code {exit_code}."
                camera_portable.update_publish(published, status="failed", error=message)
                self.status.setText(f"Camera Package FBX/USD failed: {message}")
        except RuntimeError:
            # The tool window may have been closed while the application-level
            # background process continued. Metadata has still been finalized.
            pass


def show(config_dir=None, parent=None):
    global _WINDOW
    for widget in list(QtWidgets.QApplication.topLevelWidgets()):
        if widget.objectName() == WINDOW_OBJECT_NAME:
            widget._suppress_scene_save = True
            widget.close()
            widget.deleteLater()
    _WINDOW = SmartCameraPlayblastWindow(config_dir=config_dir, parent=parent or _maya_main_window())
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    return _WINDOW

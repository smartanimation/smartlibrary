from __future__ import annotations

from pathlib import Path

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets

from smartlib.apps.shot_manager import ShotIdentity
from smartlib.review.playblast_package import find_ffmpeg
from smartlib.core.asset_categories import canonical_asset_category
from smartlib.core.icons import tool_ico_path

from .service import SmartDeliveryService, expand_sequence


class SmartDeliveryWindow(QtWidgets.QMainWindow):
    def __init__(self, config_dir: str | Path, parent=None):
        super().__init__(parent)
        icon_path = tool_ico_path("smart_delivery")
        if icon_path:
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))
        self.service = SmartDeliveryService(config_dir)
        self._plan = None
        self.setWindowTitle("Smart Delivery")
        self.resize(1100, 720)
        self._build_ui()
        self._apply_delivery_preferences()
        self._populate_shots()
        self._apply_style()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        form = QtWidgets.QGridLayout()
        self.episode_combo = QtWidgets.QComboBox()
        self.sequence_combo = QtWidgets.QComboBox()
        self.shot_list = QtWidgets.QListWidget()
        self.shot_list.setMaximumHeight(110)
        self.profile_label = QtWidgets.QLabel("DandeLione_v003")
        self.package_profile_combo = QtWidgets.QComboBox()
        self.package_profile_combo.addItems(self.service.package_profile_names())
        self.delivery_type_combo = QtWidgets.QComboBox()
        self.delivery_type_combo.addItems(["Asset", "Shot", "Editorial"])
        self.workflow_combo = QtWidgets.QComboBox()
        self.task_edit = QtWidgets.QLineEdit("preComp")
        self.version_spin = QtWidgets.QSpinBox(); self.version_spin.setRange(1, 9999); self.version_spin.setValue(1)
        self.output_edit = QtWidgets.QLineEdit()
        self.browse_output_button = QtWidgets.QPushButton("Browse…")
        self.browse_output_button.clicked.connect(self._browse_output)
        self.manifest_edit = QtWidgets.QLineEdit()
        self.manifest_edit.setPlaceholderText("Asset manifest or editorial_mapping.json")
        self.browse_manifest_button = QtWidgets.QPushButton("Browse Manifest…")
        self.browse_manifest_button.clicked.connect(self._browse_manifest)
        self.editorial_mapping_label = QtWidgets.QLabel("Editorial Mapping")
        self.editorial_mapping_combo = QtWidgets.QComboBox()
        self.editorial_mapping_combo.setMinimumContentsLength(24)
        self.refresh_editorial_button = QtWidgets.QPushButton("Refresh")
        self.refresh_editorial_button.clicked.connect(self._populate_editorial_mappings)
        self.output_override_check = QtWidgets.QCheckBox("Override Output")
        self.output_override_check.toggled.connect(self._output_override_changed)
        self.asset_scene_edit = QtWidgets.QLineEdit()
        self.asset_texture_edit = QtWidgets.QLineEdit()
        self.asset_identity_edit = QtWidgets.QLineEdit("character/main/YOU/default")
        browse_scene = QtWidgets.QPushButton("Browse Scene…"); browse_scene.clicked.connect(self._browse_asset_scene)
        browse_texture = QtWidgets.QPushButton("Browse Texture…"); browse_texture.clicked.connect(self._browse_asset_texture)
        self.add_shot_files_button = QtWidgets.QPushButton("Add Shot Files…")
        self.add_shot_files_button.clicked.connect(self._add_shot_files)
        self.episode_label = QtWidgets.QLabel("Episode"); self.sequence_label = QtWidgets.QLabel("Sequence")
        self.client_profile_label = QtWidgets.QLabel("Client Profile"); self.package_profile_label = QtWidgets.QLabel("Package Profile")
        self.task_label = QtWidgets.QLabel("Department"); self.version_label = QtWidgets.QLabel("Version")
        form.addWidget(self.episode_label, 0, 0); form.addWidget(self.episode_combo, 0, 1)
        form.addWidget(self.sequence_label, 0, 2); form.addWidget(self.sequence_combo, 0, 3)
        form.addWidget(self.client_profile_label, 1, 0); form.addWidget(self.profile_label, 1, 1)
        form.addWidget(self.package_profile_label, 1, 4); form.addWidget(self.package_profile_combo, 1, 5)
        form.addWidget(self.task_label, 1, 2); form.addWidget(self.task_edit, 1, 3)
        form.addWidget(self.version_label, 1, 6); form.addWidget(self.version_spin, 1, 7)
        form.addWidget(QtWidgets.QLabel("Package Output"), 2, 0); form.addWidget(self.output_edit, 2, 1, 1, 5); form.addWidget(self.output_override_check, 2, 6); form.addWidget(self.browse_output_button, 2, 7)
        form.addWidget(QtWidgets.QLabel("Delivery Type"), 0, 4); form.addWidget(self.delivery_type_combo, 0, 5)
        form.addWidget(QtWidgets.QLabel("Workflow"), 0, 6); form.addWidget(self.workflow_combo, 0, 7)
        self.manifest_label = QtWidgets.QLabel("Asset Manifest")
        form.addWidget(self.manifest_label, 3, 0); form.addWidget(self.manifest_edit, 3, 1, 1, 6); form.addWidget(self.browse_manifest_button, 3, 7)
        form.addWidget(self.editorial_mapping_label, 3, 0); form.addWidget(self.editorial_mapping_combo, 3, 1, 1, 6); form.addWidget(self.refresh_editorial_button, 3, 7)
        self.asset_scene_label = QtWidgets.QLabel("Asset Scene")
        self.asset_texture_label = QtWidgets.QLabel("Texture Root (optional)")
        self.asset_identity_label = QtWidgets.QLabel("Asset Target")
        form.addWidget(self.asset_scene_label, 4, 0); form.addWidget(self.asset_scene_edit, 4, 1, 1, 6); form.addWidget(browse_scene, 4, 7)
        form.addWidget(self.asset_texture_label, 5, 0); form.addWidget(self.asset_texture_edit, 5, 1, 1, 6); form.addWidget(browse_texture, 5, 7)
        form.addWidget(self.asset_identity_label, 6, 0); form.addWidget(self.asset_identity_edit, 6, 1, 1, 4)
        form.addWidget(self.add_shot_files_button, 6, 7)
        self.asset_widgets = (self.asset_scene_label, self.asset_scene_edit, browse_scene,
                              self.asset_texture_label, self.asset_texture_edit, browse_texture,
                              self.asset_identity_label, self.asset_identity_edit)
        layout.addLayout(form)
        self.shot_list_label = QtWidgets.QLabel("Shots (Internal Review version / state)")
        layout.addWidget(self.shot_list_label)
        layout.addWidget(self.shot_list)

        self.editorial_selection_bar = QtWidgets.QWidget()
        editorial_selection_layout = QtWidgets.QHBoxLayout(self.editorial_selection_bar)
        editorial_selection_layout.setContentsMargins(0, 0, 0, 0)
        self.editorial_selection_label = QtWidgets.QLabel("Delivery Shots")
        self.editorial_select_all = QtWidgets.QPushButton("Select All")
        self.editorial_clear_all = QtWidgets.QPushButton("Clear All")
        self.editorial_invert = QtWidgets.QPushButton("Invert")
        self.editorial_select_all.clicked.connect(lambda: self._set_editorial_selection("all"))
        self.editorial_clear_all.clicked.connect(lambda: self._set_editorial_selection("none"))
        self.editorial_invert.clicked.connect(lambda: self._set_editorial_selection("invert"))
        editorial_selection_layout.addWidget(self.editorial_selection_label)
        editorial_selection_layout.addStretch(1)
        editorial_selection_layout.addWidget(self.editorial_select_all)
        editorial_selection_layout.addWidget(self.editorial_clear_all)
        editorial_selection_layout.addWidget(self.editorial_invert)
        layout.addWidget(self.editorial_selection_bar)

        self.inputs = QtWidgets.QTableWidget(0, 4)
        self.inputs.setHorizontalHeaderLabels(["Enabled", "Type / Review Layer", "Source", "Status"])
        self.inputs.horizontalHeader().setStretchLastSection(False)
        self.inputs.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.inputs, 2)
        buttons = QtWidgets.QHBoxLayout()
        context_manifest = QtWidgets.QPushButton("Load Context Manifest…")
        context_manifest.clicked.connect(self._load_context_manifest)
        refresh = QtWidgets.QPushButton("Auto Resolve")
        dry = QtWidgets.QPushButton("Dry Run")
        build = QtWidgets.QPushButton("Build Delivery"); build.setProperty("primary", True)
        refresh.clicked.connect(self._resolve); dry.clicked.connect(self._dry_run); build.clicked.connect(self._build)
        buttons.addWidget(context_manifest); buttons.addWidget(refresh); buttons.addStretch(1); buttons.addWidget(dry); buttons.addWidget(build)
        layout.addLayout(buttons)
        self.report = QtWidgets.QPlainTextEdit(); self.report.setReadOnly(True)
        layout.addWidget(self.report, 1)
        self.episode_combo.currentIndexChanged.connect(self._populate_sequences)
        self.sequence_combo.currentIndexChanged.connect(self._populate_shot_list)
        self.shot_list.currentItemChanged.connect(self._resolve)
        self.delivery_type_combo.currentTextChanged.connect(self._delivery_type_changed)
        self.workflow_combo.currentTextChanged.connect(self._workflow_changed)
        self.manifest_edit.editingFinished.connect(self._resolve)
        self.package_profile_combo.currentTextChanged.connect(self._resolve)
        self.editorial_mapping_combo.currentIndexChanged.connect(self._editorial_mapping_changed)
        self._delivery_type_changed()

    def _apply_delivery_preferences(self):
        preferences = self.service.delivery_preferences()
        profile = preferences.get("package_profile")
        if profile and self.package_profile_combo.findText(profile) >= 0:
            self.package_profile_combo.setCurrentText(profile)
        workflow = preferences.get("asset_workflow")
        if workflow and self.workflow_combo.findText(workflow) >= 0:
            self.workflow_combo.setCurrentText(workflow)

    def _load_context_manifest(self):
        path, _selected = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load Smart Preflight / Build Manifest", "",
            "JSON Manifest (*.json);;All Files (*)")
        if not path: return
        self.load_context_manifest(path)

    def load_context_manifest(self, path: str | Path):
        """Apply a Smart Preflight or Build manifest without a file dialog."""
        try:
            defaults = self.service.manifest_delivery_defaults(path)
            self.delivery_type_combo.setCurrentText(defaults["delivery_type"])
            preferences = self.service.delivery_preferences()
            profile = preferences.get("package_profile")
            if profile and self.package_profile_combo.findText(profile) >= 0:
                self.package_profile_combo.setCurrentText(profile)
            if defaults["delivery_type"] == "Editorial":
                self.package_profile_combo.setCurrentText("editorial")
                mapping_value = str(Path(path))
                index = self.editorial_mapping_combo.findData(mapping_value)
                if index < 0:
                    self.editorial_mapping_combo.addItem(
                        f"{defaults.get('episode') or '-'} / {defaults.get('revision') or '-'}",
                        mapping_value,
                    )
                    index = self.editorial_mapping_combo.count() - 1
                self.editorial_mapping_combo.setCurrentIndex(index)
                self.output_edit.setText(str(self.service.suggested_editorial_output(path)))
            elif defaults["delivery_type"] == "Asset":
                workflow = preferences.get("asset_workflow") or "Package ZIP"
                if self.workflow_combo.findText(workflow) >= 0: self.workflow_combo.setCurrentText(workflow)
                self.asset_identity_edit.setText("/".join(defaults[key] for key in ("category", "group", "asset", "variant")))
                self.asset_scene_edit.setText(defaults.get("scene") or "")
                scene = Path(defaults.get("scene") or "")
                candidate = Path(str(scene).replace("/rig/ANM/", "/texture/ANM/").replace("\\rig\\ANM\\", "\\texture\\ANM\\"))
                if candidate.is_file(): candidate = candidate.parent
                self.asset_texture_edit.setText(str(candidate) if candidate.is_dir() else "")
                self.output_edit.setText(str(self.service.suggested_package_output(defaults["asset"], profile=profile)))
            else:
                workflow = preferences.get("shot_workflow") or "Package ZIP"
                if self.workflow_combo.findText(workflow) >= 0: self.workflow_combo.setCurrentText(workflow)
                self.episode_combo.setCurrentText(defaults.get("episode") or "")
                self.sequence_combo.setCurrentText(defaults.get("sequence") or "")
                for index in range(self.shot_list.count()):
                    identity = self.shot_list.item(index).data(QtCore.Qt.UserRole)
                    if identity and identity.shot == defaults.get("shot"):
                        self.shot_list.setCurrentRow(index); break
                if defaults.get("scene"): self._add_row("shot:maya", defaults["scene"])
                self.output_edit.setText(str(self.service.suggested_package_output(defaults.get("shot") or "shot", profile=profile)))
            self._resolve()
            self.report.appendPlainText(f"\nContext loaded: {path}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Context Manifest", str(exc))

    def _populate_shots(self):
        self._shots = self.service.list_shots()
        self.episode_combo.blockSignals(True)
        self.episode_combo.clear()
        self.episode_combo.addItems(sorted({row.episode for row in self._shots}, key=str.lower))
        self.episode_combo.blockSignals(False)
        self._populate_sequences()

    def _populate_sequences(self):
        episode = self.episode_combo.currentText()
        current = self.sequence_combo.currentText()
        values = sorted({row.sequence for row in self._shots if row.episode == episode}, key=str.lower)
        self.sequence_combo.blockSignals(True)
        self.sequence_combo.clear(); self.sequence_combo.addItems(values)
        if current in values:
            self.sequence_combo.setCurrentText(current)
        self.sequence_combo.blockSignals(False)
        self._populate_shot_list()

    def _populate_shot_list(self):
        episode = self.episode_combo.currentText(); sequence = self.sequence_combo.currentText()
        self.shot_list.blockSignals(True)
        self.shot_list.clear()
        for identity in self._shots:
            if identity.episode != episode or identity.sequence != sequence:
                continue
            status = self.service.review_status(identity)
            item = QtWidgets.QListWidgetItem(f"{identity.shot}    {status}")
            item.setData(QtCore.Qt.UserRole, identity)
            self.shot_list.addItem(item)
        if self.shot_list.count():
            self.shot_list.setCurrentRow(0)
        self.shot_list.blockSignals(False)
        self._resolve()

    def _identity(self) -> ShotIdentity | None:
        item = self.shot_list.currentItem()
        return item.data(QtCore.Qt.UserRole) if item else None

    def _resolve(self):
        mode = self._mode()
        if mode != "Editorial ZIP":
            self.inputs.setColumnCount(4)
            self.inputs.setHorizontalHeaderLabels(["Enabled", "Type / Review Layer", "Source", "Status"])
        if mode in {"Asset ZIP", "Asset Assembly ZIP"}:
            self._resolve_asset_zip()
            return
        if mode == "Shot ZIP":
            self._resolve_shot_zip()
            return
        if mode == "Editorial ZIP":
            self._resolve_editorial_zip()
            return
        if mode == "Client Manifest":
            self._resolve_asset_package()
            return
        identity = self._identity()
        if not identity:
            return
        sources = self.service.suggested_sources(identity)
        sequences = self.service.suggested_image_sequences(identity)
        review_source = self.service.review_source(identity)
        review_version = str(review_source.get("version") or "")
        if review_version.lower().startswith("v") and review_version[1:].isdigit():
            self.version_spin.setValue(int(review_version[1:]))
        layers = self.service.review_layers(identity)
        self.inputs.setRowCount(0)
        for kind in ("maya", "aep", "review"):
            self._add_row(kind, sources.get(kind, ""))
        for layer in layers:
            self._add_row(f"image_sequence:{layer}", sequences.get(layer, ""))
        project_root = self.service.config.project_root or Path.cwd()
        self.output_edit.setText(str(Path(project_root)))
        manifest = str(review_source.get("manifest") or "not found")
        if review_source.get("approved"):
            approval = review_source.get("approval") or {}
            self.report.setPlainText(
                f"APPROVED {review_version} by {approval.get('author') or '-'}\n"
                f"Resolved from source manifest:\n{manifest}\n\nConfirm every source, then run Dry Run."
            )
        else:
            self.report.setPlainText(
                f"BLOCKED: No approved Internal Review. Latest submission is "
                f"{review_source.get('latest_version') or '-'}。\nApprove a version in Smart Review first."
            )

    def _resolve_asset_package(self):
        self.inputs.setRowCount(0)
        project_root = self.service.config.project_root or Path.cwd()
        if not self.output_edit.text().strip():
            self.output_edit.setText(str(Path(project_root)))
        manifest = self.manifest_edit.text().strip()
        if not manifest:
            self.report.setPlainText("Select a smart_ingest.asset_package.v1 manifest.")
            return
        try:
            summary = self.service.asset_package_summary(manifest)
            target = summary["target"]; data = summary["data"]; root = summary["path"].parent
            for row in data.get("files") or []:
                source = root / str(row.get("path") or "")
                self._add_row(f"asset:{row.get('role') or 'file'}", str(source))
            self.report.setPlainText(
                "Asset Package\n"
                f"Target: {target['category']}/{target['group']}/{target['asset']}/{target.get('variant') or 'default'}\n"
                f"Ingest: {summary['path']}\n"
                "Dry Run shows the reconstructed Client Asset Tree."
            )
        except Exception as exc:
            self.report.setPlainText(f"INVALID ASSET MANIFEST: {exc}")

    def _resolve_asset_zip(self):
        self.inputs.setRowCount(0)
        scene = self.asset_scene_edit.text().strip()
        textures = self.asset_texture_edit.text().strip()
        self._add_row("asset:scene", scene)
        if textures:
            self._add_row("asset:texture_root", textures)
        if not self.output_edit.text().strip() and scene:
            self.output_edit.setText(str(Path(scene).with_suffix(".zip")))
        title = "Asset Assembly ZIP" if self._mode() == "Asset Assembly ZIP" else "Asset ZIP"
        self.report.setPlainText(
            f"{title} for Smart Ingest\n"
            f"Profile: {self.package_profile_combo.currentText()}\n"
            f"Target: {self.asset_identity_edit.text().strip()} / assembly\n"
            "Texture Root is optional. Dry Run validates the package inputs.\n"
            + ("References remain in the .ma; placements are recorded in manifest metadata."
               if self._mode() == "Asset Assembly ZIP" else "")
        )

    def _resolve_shot_zip(self):
        if not self.output_edit.text().strip() and self._identity():
            self.output_edit.setText(str(Path.cwd() / f"{self._identity().code}.zip"))
        self.report.setPlainText(
            "Shot Package ZIP for Smart Ingest\n"
            f"Profile: {self.package_profile_combo.currentText()}\n"
            "Add Maya, After Effects, image sequence, cache, or USD files."
        )

    def _populate_editorial_mappings(self):
        current = self._editorial_mapping_path()
        options = self.service.editorial_mapping_options()
        self.editorial_mapping_combo.blockSignals(True)
        self.editorial_mapping_combo.clear()
        for row in options:
            self.editorial_mapping_combo.addItem(row["label"], str(row["path"]))
        if current:
            index = self.editorial_mapping_combo.findData(current)
            if index >= 0:
                self.editorial_mapping_combo.setCurrentIndex(index)
        self.editorial_mapping_combo.blockSignals(False)
        self._editorial_mapping_changed()

    def _editorial_mapping_path(self) -> str:
        if hasattr(self, "editorial_mapping_combo"):
            return str(self.editorial_mapping_combo.currentData() or "")
        return ""

    def _editorial_mapping_changed(self, *_args):
        if self._mode() == "Editorial ZIP":
            self._resolve_editorial_zip()

    def _output_override_changed(self, enabled: bool):
        is_editorial = self._mode() == "Editorial ZIP"
        self.output_edit.setReadOnly(is_editorial and not enabled)
        self.browse_output_button.setEnabled(not is_editorial or enabled)
        if is_editorial and not enabled:
            self._resolve_editorial_zip()
    def _resolve_editorial_zip(self):
        self.inputs.setRowCount(0)
        mapping = self._editorial_mapping_path()
        if not mapping:
            self.report.setPlainText(
                "Select revisions/metadata/v###/editorial_mapping.json.\n"
                "The ZIP contains the mapping, Shot Registry, and non-OMIT HUD masters."
            )
            return
        try:
            summary = self.service.editorial_delivery_context(mapping)
            self.inputs.setColumnCount(5)
            self.inputs.setHorizontalHeaderLabels([
                "Deliver", "Shot / IDs", "Latest Media", "Delivery Status", "Last Delivered"
            ])
            self.inputs.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
            for state in summary["delivery_shots"]:
                self._add_editorial_row(state)
            if not self.output_override_check.isChecked():
                self.output_edit.setText(str(summary["output"]))
            profile = self.service.package_profile("editorial")
            self.report.setPlainText(
                "Editorial Delivery ZIP\n"
                f"Recipient: {profile.delivery_recipient}\n"
                f"Process: {profile.delivery_process}\n"
                f"Episode: {summary['episode']}\n"
                f"Timeline Revision: {summary['timeline_revision']}\n"
                f"Delivery Revision: {summary['delivery_revision']}\n"
                f"Delivery Batch: {summary['delivery_batch']}\n"
                f"Available HUD movies: {sum(shot.available for shot in summary['shots'])}\n"
                f"Need delivery: {sum(row['needs_delivery'] for row in summary['delivery_shots'])}\n"
                "Undelivered and updated shots are selected automatically."
            )
        except Exception as exc:
            self.report.setPlainText(f"INVALID EDITORIAL MAPPING: {exc}")
    def _delivery_type_changed(self):
        delivery_type = self.delivery_type_combo.currentText()
        if delivery_type == "Asset":
            workflows = ["Package ZIP", "Assembly ZIP", "Client Manifest"]
        elif delivery_type == "Editorial":
            workflows = ["Editorial ZIP"]
            if self.package_profile_combo.findText("editorial") >= 0:
                self.package_profile_combo.setCurrentText("editorial")
        else:
            workflows = ["Package ZIP", "Reviewed Delivery"]
        current = self.workflow_combo.currentText()
        self.workflow_combo.blockSignals(True)
        self.workflow_combo.clear(); self.workflow_combo.addItems(workflows)
        if current in workflows: self.workflow_combo.setCurrentText(current)
        self.workflow_combo.blockSignals(False)
        self._workflow_changed()

    def _workflow_changed(self):
        mode = self._mode()
        self.inputs.setRowCount(0)
        is_shot = self.delivery_type_combo.currentText() == "Shot"
        for widget in (self.episode_label, self.episode_combo, self.sequence_label, self.sequence_combo,
                       self.shot_list_label, self.shot_list, self.task_label, self.task_edit):
            widget.setVisible(is_shot)
        is_editorial = mode == "Editorial ZIP"
        is_manifest = mode == "Client Manifest"
        self.manifest_label.setText("Asset Manifest")
        for widget in (self.manifest_label, self.manifest_edit, self.browse_manifest_button):
            widget.setVisible(is_manifest)
        for widget in (self.editorial_mapping_label, self.editorial_mapping_combo, self.refresh_editorial_button):
            widget.setVisible(is_editorial)
        self.output_override_check.setVisible(is_editorial)
        self.editorial_selection_bar.setVisible(is_editorial)
        self.output_edit.setReadOnly(is_editorial and not self.output_override_check.isChecked())
        self.browse_output_button.setEnabled(not is_editorial or self.output_override_check.isChecked())
        is_asset = mode in {"Asset ZIP", "Asset Assembly ZIP"}
        for widget in self.asset_widgets: widget.setVisible(is_asset)
        self.add_shot_files_button.setVisible(mode == "Shot ZIP")
        uses_package_profile = mode in {"Asset ZIP", "Asset Assembly ZIP", "Shot ZIP", "Editorial ZIP"}
        self.package_profile_label.setVisible(uses_package_profile); self.package_profile_combo.setVisible(uses_package_profile)
        uses_client_profile = mode in {"Reviewed Shot", "Client Manifest"}
        self.client_profile_label.setVisible(uses_client_profile); self.profile_label.setVisible(uses_client_profile)
        uses_version = mode in {"Reviewed Shot", "Client Manifest"}
        self.version_label.setVisible(uses_version); self.version_spin.setVisible(uses_version)
        if is_editorial:
            self._populate_editorial_mappings()
        else:
            self._resolve()

    def _mode(self):
        workflow = self.workflow_combo.currentText()
        delivery_type = self.delivery_type_combo.currentText()
        if delivery_type == "Asset":
            if workflow == "Client Manifest": return "Client Manifest"
            return "Asset Assembly ZIP" if workflow == "Assembly ZIP" else "Asset ZIP"
        if delivery_type == "Editorial":
            return "Editorial ZIP"
        return "Reviewed Shot" if workflow == "Reviewed Delivery" else "Shot ZIP"

    def _add_editorial_row(self, state):
        shot = state["shot"]
        row = self.inputs.rowCount()
        self.inputs.insertRow(row)
        enabled = QtWidgets.QTableWidgetItem()
        enabled.setFlags(enabled.flags() | QtCore.Qt.ItemIsUserCheckable)
        enabled.setCheckState(
            QtCore.Qt.Checked if state["needs_delivery"] else QtCore.Qt.Unchecked
        )
        enabled.setData(QtCore.Qt.UserRole, shot.key)
        if not shot.available:
            enabled.setFlags(enabled.flags() & ~QtCore.Qt.ItemIsEnabled)
        identity = " / ".join(value for value in (
            shot.shot,
            f"CGID-{shot.cg_shot_id[:8]}" if shot.cg_shot_id else "",
            shot.editorial_event_id,
            f"EVID-{shot.editorial_event_uid.replace('-', '')[:8]}" if shot.editorial_event_uid else "",
        ) if value)
        media_item = QtWidgets.QTableWidgetItem(
            state["latest_media_version"] or "-"
        )
        media_item.setToolTip(str(shot.source or "HUD source not found"))
        delivered_at = state["last_delivered_at"]
        if delivered_at:
            delivered_at = delivered_at.replace("T", " ")[:16]
            revision = state["last_delivery_revision"]
            if revision:
                delivered_at = f"{delivered_at}  ({revision})"
        self.inputs.setItem(row, 0, enabled)
        self.inputs.setItem(row, 1, QtWidgets.QTableWidgetItem(identity or shot.key))
        self.inputs.setItem(row, 2, media_item)
        self.inputs.setItem(row, 3, QtWidgets.QTableWidgetItem(state["status"]))
        self.inputs.setItem(row, 4, QtWidgets.QTableWidgetItem(delivered_at or "-"))

    def _selected_editorial_shot_keys(self) -> set[str]:
        selected = set()
        for row in range(self.inputs.rowCount()):
            item = self.inputs.item(row, 0)
            if item and item.checkState() == QtCore.Qt.Checked:
                key = str(item.data(QtCore.Qt.UserRole) or "")
                if key:
                    selected.add(key)
        return selected

    def _set_editorial_selection(self, mode: str):
        for row in range(self.inputs.rowCount()):
            item = self.inputs.item(row, 0)
            if not item or not (item.flags() & QtCore.Qt.ItemIsEnabled):
                continue
            if mode == "all":
                item.setCheckState(QtCore.Qt.Checked)
            elif mode == "none":
                item.setCheckState(QtCore.Qt.Unchecked)
            else:
                item.setCheckState(
                    QtCore.Qt.Unchecked if item.checkState() == QtCore.Qt.Checked
                    else QtCore.Qt.Checked
                )

    def _add_row(self, label: str, source: str):
        row = self.inputs.rowCount(); self.inputs.insertRow(row)
        enabled = QtWidgets.QTableWidgetItem(); enabled.setFlags(enabled.flags() | QtCore.Qt.ItemIsUserCheckable); enabled.setCheckState(QtCore.Qt.Checked if source else QtCore.Qt.Unchecked)
        found = bool(source) and (
            Path(source).exists() or any(path.exists() for path in expand_sequence(source))
        )
        self.inputs.setItem(row, 0, enabled); self.inputs.setItem(row, 1, QtWidgets.QTableWidgetItem(label)); self.inputs.setItem(row, 2, QtWidgets.QTableWidgetItem(source)); self.inputs.setItem(row, 3, QtWidgets.QTableWidgetItem("FOUND" if found else "MISSING"))

    def _browse_output(self):
        if self._mode() in {"Asset ZIP", "Asset Assembly ZIP", "Shot ZIP", "Editorial ZIP"}:
            path, _selected = QtWidgets.QFileDialog.getSaveFileName(
                self, "Smart Delivery ZIP", self.output_edit.text(), "ZIP Archive (*.zip)")
            if path:
                self.output_edit.setText(str(Path(path).with_suffix(".zip")))
            return
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Package parent directory", self.output_edit.text())
        if path:
            self.output_edit.setText(str(Path(path)))

    def _browse_manifest(self):
        path, _selected = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select Manifest", self.manifest_edit.text(),
            "JSON Manifest (*.json);;All Files (*)",
        )
        if path:
            self.manifest_edit.setText(str(Path(path)))
            self._resolve()

    def _browse_asset_scene(self):
        path, _selected = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select Asset Scene", self.asset_scene_edit.text(), "Maya Scene (*.ma *.mb);;All Files (*)")
        if path:
            self.asset_scene_edit.setText(str(Path(path))); self._resolve()

    def _browse_asset_texture(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Texture Root", self.asset_texture_edit.text())
        if path:
            self.asset_texture_edit.setText(str(Path(path))); self._resolve()

    def _add_shot_files(self):
        paths, _selected = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Add Shot Package Files", "",
            "Delivery Files (*.ma *.mb *.aep *.abc *.fbx *.usd *.usda *.usdc *.exr *.png *.jpg *.tif *.tiff);;All Files (*)")
        for path in paths:
            self._add_row("shot:file", str(Path(path)))

    def _values(self):
        sources, sequences = {}, {}
        for row in range(self.inputs.rowCount()):
            if self.inputs.item(row, 0).checkState() != QtCore.Qt.Checked:
                continue
            label = self.inputs.item(row, 1).text(); value = self.inputs.item(row, 2).text().strip()
            if label.startswith("image_sequence:"):
                sequences[label.split(":", 1)[1]] = value
            else:
                sources[label] = value
        return sources, sequences

    def _make_plan(self):
        if self._mode() == "Client Manifest":
            return self.service.build_asset_package_plan(
                self.manifest_edit.text().strip(),
                package_root=self.output_edit.text().strip(),
                version=self.version_spin.value(),
            )
        identity = self._identity()
        if not identity:
            raise RuntimeError("Select a shot.")
        sources, sequences = self._values()
        return self.service.build_plan(identity, task=self.task_edit.text().strip(), version=self.version_spin.value(), package_root=self.output_edit.text().strip(), sources=sources, layer_sequences=sequences)

    def _dry_run(self):
        try:
            mode = self._mode()
            if mode in {"Asset ZIP", "Asset Assembly ZIP", "Shot ZIP", "Editorial ZIP"}:
                lines = self._package_preview(mode)
                self.report.setPlainText("\n".join(lines))
                return
            self._plan = self._make_plan()
            lines = [f"Job: {self._plan.job_id}", f"Package: {self._plan.package_root}", f"Files: {len(self._plan.items)}", ""]
            lines.extend(self._plan_lines(self._plan))
            self.report.setPlainText("\n".join(lines))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Dry Run", str(exc))

    def _build(self):
        try:
            mode = self._mode()
            if mode in {"Asset ZIP", "Asset Assembly ZIP", "Shot ZIP", "Editorial ZIP"}:
                QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
                result = self._build_package_zip(mode)
                delivery = result.manifest.get("delivery") or {}
                lines = ["Delivery complete", f"Profile: {result.manifest.get('profile')}",
                         f"ZIP: {result.archive}", f"Members: {len(result.files)}",
                         f"Delivery Revision: {delivery.get('delivery_revision') or '-'}",
                         f"Delivery Batch: {delivery.get('delivery_batch') or '-'}",
                         f"Target: {(result.manifest.get('ingest') or {}).get('expected_target_root') or '-'}"]
                self.report.setPlainText("\n".join(lines))
                QtWidgets.QMessageBox.information(self, "Delivery complete", "\n".join(lines))
                if mode == "Editorial ZIP":
                    self._resolve_editorial_zip()
                return
            plan = self._make_plan()
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            result = self.service.execute(plan, ffmpeg=find_ffmpeg(self.service.config))
            lines = [f"Blocked: {result.blocked}", f"Package: {result.package_root}", f"Manifest: {result.manifest}", f"ZIP: {result.archive or '-'}", f"Contact Sheet: {result.contact_sheet or '-'}", ""]
            sequence_results = [row for row in result.results if row.item_id.startswith("image_sequence.")]
            regular_results = [row for row in result.results if not row.item_id.startswith("image_sequence.")]
            lines.extend(f"[{row.severity}] {row.code}: {row.message}" for row in regular_results)
            if sequence_results:
                counts = {state: sum(row.severity == state for row in sequence_results) for state in ("PASS", "WARNING", "ERROR")}
                lines.append(
                    f"[IMAGE SEQUENCES] {len(sequence_results)} checks: "
                    f"{counts['PASS']} pass, {counts['WARNING']} warning, {counts['ERROR']} error"
                )
            self.report.setPlainText("\n".join(lines))
            title = "Delivery blocked" if result.blocked else "Delivery complete"
            QtWidgets.QMessageBox.information(self, title, "\n".join(lines[:5]))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Smart Delivery", str(exc))
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def _package_preview(self, mode: str):
        output = Path(self.output_edit.text().strip())
        if output.suffix.lower() != ".zip": raise ValueError("Package Output must be a .zip file.")
        if output.exists(): raise FileExistsError(f"Output ZIP already exists: {output}")
        profile = self.service.package_profile(self.package_profile_combo.currentText())
        if mode == "Editorial ZIP":
            summary = self.service.editorial_delivery_context(self._editorial_mapping_path())
            return [
                "Editorial ZIP Dry Run", f"Profile: {profile.id}", f"Output: {output}",
                f"Episode: {summary['episode']}",
                f"Timeline Revision: {summary['timeline_revision']}",
                f"Delivery Revision: {summary['delivery_revision']}",
                f"Delivery Batch: {summary['delivery_batch']}",
                f"Selected HUD movies: {len(self._selected_editorial_shot_keys())}",
                f"Shot Registry: {summary['registry'] or '(not found)'}",
            ]
        if mode in {"Asset ZIP", "Asset Assembly ZIP"}:
            scene = Path(self.asset_scene_edit.text().strip())
            if not scene.is_file(): raise FileNotFoundError(f"Asset scene was not found: {scene}")
            textures = self.asset_texture_edit.text().strip()
            if textures and not Path(textures).is_dir(): raise FileNotFoundError(f"Texture root was not found: {textures}")
            category, group, asset, variant = self._asset_target()
            target = profile.asset_root.format(category=category, group=group, asset=asset, variant=variant,
                                                subset=profile.asset_subset, department="assembly")
            count = 1 + (sum(path.is_file() for path in Path(textures).rglob("*")) if textures else 0)
            if mode == "Asset Assembly ZIP" and scene.suffix.lower() != ".ma":
                raise ValueError("Asset Assembly requires a reference-preserving .ma scene.")
            label = "Asset Assembly ZIP Dry Run" if mode == "Asset Assembly ZIP" else "Asset ZIP Dry Run"
            return [label, f"Profile: {profile.id}", f"Output: {output}",
                    f"Scene: {scene}", f"Texture Root: {textures or '(none)'}", f"Source files: {count}", f"Target: {target}"]
        identity = self._identity()
        if not identity: raise RuntimeError("Select a shot.")
        sources = self._shot_package_sources()
        if not sources: raise RuntimeError("Add at least one Shot file.")
        missing = [str(path) for path in sources if not path.is_file()]
        if missing: raise FileNotFoundError(f"Shot source was not found: {missing[0]}")
        target = profile.shot_root.format(episode=identity.episode, sequence=identity.sequence,
                                          shot=identity.shot, department=self.task_edit.text().strip(), subset=profile.asset_subset)
        return ["Shot Package ZIP Dry Run", f"Profile: {profile.id}", f"Output: {output}",
                f"Files: {len(sources)}", f"Target: {target}"]

    def _build_package_zip(self, mode: str):
        self._package_preview(mode)
        if mode == "Editorial ZIP":
            return self.service.build_editorial_package(
                mapping_path=self._editorial_mapping_path(),
                output=self.output_edit.text().strip(),
                selected_shot_keys=self._selected_editorial_shot_keys(),
            )
        if mode in {"Asset ZIP", "Asset Assembly ZIP"}:
            category, group, asset, variant = self._asset_target()
            return self.service.build_exchange_asset(
                profile=self.package_profile_combo.currentText(), scene=self.asset_scene_edit.text().strip(),
                texture_root=self.asset_texture_edit.text().strip() or None, output=self.output_edit.text().strip(),
                category=category, group=group, asset=asset, variant=variant,
                comment="Created by Smart Delivery GUI", assembly=mode == "Asset Assembly ZIP")
        return self.service.build_exchange_shot(
            profile=self.package_profile_combo.currentText(), sources=self._shot_package_sources(),
            output=self.output_edit.text().strip(), identity=self._identity(),
            department=self.task_edit.text().strip(), comment="Created by Smart Delivery GUI")

    def _asset_target(self):
        parts = [value.strip() for value in self.asset_identity_edit.text().replace("\\", "/").split("/") if value.strip()]
        if len(parts) != 4: raise ValueError("Asset Target must be category/group/asset/variant.")
        parts[0] = canonical_asset_category(parts[0], strict=True)
        return tuple(parts)

    def _shot_package_sources(self):
        return [Path(self.inputs.item(row, 2).text().strip()) for row in range(self.inputs.rowCount())
                if self.inputs.item(row, 0).checkState() == QtCore.Qt.Checked and self.inputs.item(row, 2).text().strip()]

    @staticmethod
    def _plan_lines(plan):
        lines = []
        sequences = {}
        for item in plan.items:
            if item.kind != "image_sequence":
                lines.append(f"{item.kind}: {item.source} -> {item.destination.as_posix()}")
                continue
            layer = str(item.metadata.get("review_layer") or "sequence")
            sequences.setdefault(layer, []).append(item)
        for layer, items in sequences.items():
            frames = sorted(str(item.metadata.get("frame") or "") for item in items if item.metadata.get("frame"))
            source_pattern = str(items[0].source)
            destination_pattern = items[0].destination.as_posix()
            if frames:
                source_pattern = source_pattern.replace(frames[0], "####")
                destination_pattern = destination_pattern.replace(frames[0].zfill(4), "####")
            frame_range = f"{frames[0]}-{frames[-1]}" if frames else "-"
            lines.append(
                f"image_sequence:{layer}: {source_pattern} -> {destination_pattern} "
                f"({len(items)} frames, {frame_range})"
            )
        return lines

    def _apply_style(self):
        self.setStyleSheet("""
        QMainWindow,QWidget { background:#252728; color:#e5e5e5; }
        QLineEdit,QComboBox,QSpinBox,QTableWidget,QPlainTextEdit { background:#202223; border:1px solid #3a3d3f; }
        QPushButton { min-height:28px; padding:3px 12px; background:#3a3d3f; border:1px solid #4a4d4f; border-radius:4px; }
        QPushButton[primary="true"] { background:#296eaa; }
        QHeaderView::section { background:#333638; padding:6px; }
        """)

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _qt_modules():
    try:
        from PySide6 import QtCore, QtWidgets

        return QtCore, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtWidgets

        return QtCore, QtWidgets


QtCore, QtWidgets = _qt_modules()


def _ensure_smartlib_on_path() -> None:
    root = (
        os.environ.get("SMARTPIPELINE_ROOT")
        or os.environ.get("SMARTLIBRARY_ROOT")
        or str(Path(__file__).resolve().parents[1])
    )
    package_dir = str(Path(root) / "packages")
    if package_dir not in sys.path:
        sys.path.insert(0, package_dir)


def _default_config_dir() -> Path:
    env_path = os.environ.get("PROJECT_CONFIG_DIR")
    if env_path:
        return Path(env_path)
    root = Path(os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT") or Path(__file__).resolve().parents[1])
    return root / "config" / "STKB"


def _service(config_dir: str | os.PathLike[str] | None = None):
    _ensure_smartlib_on_path()
    from smartlib.apps.shot_manager import ShotCreateRequest, ShotIdentity, ShotManagerService
    from smartlib.core.config_loader import ProjectConfig

    return ShotManagerService(ProjectConfig(config_dir or _default_config_dir())), ShotCreateRequest, ShotIdentity


def _is_maya_session() -> bool:
    try:
        import maya.cmds  # noqa: F401

        return True
    except ImportError:
        return False


class ShotCreateDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, *, fps: int = 24):
        super().__init__(parent)
        self.setWindowTitle("Create Shot")
        layout = QtWidgets.QFormLayout(self)
        self.episode_edit = QtWidgets.QLineEdit("ep001")
        self.sequence_edit = QtWidgets.QLineEdit("sq010")
        self.shot_edit = QtWidgets.QLineEdit("sh0010")
        self.fps_spin = QtWidgets.QSpinBox()
        self.fps_spin.setRange(1, 240)
        self.fps_spin.setValue(fps)
        self.fps_spin.setEnabled(False)
        self.cut_in_spin = QtWidgets.QSpinBox()
        self.cut_in_spin.setRange(-100000, 1000000)
        self.cut_in_spin.setValue(1001)
        self.cut_out_spin = QtWidgets.QSpinBox()
        self.cut_out_spin.setRange(-100000, 1000000)
        self.cut_out_spin.setValue(1080)
        layout.addRow("Episode", self.episode_edit)
        layout.addRow("Sequence", self.sequence_edit)
        layout.addRow("Shot", self.shot_edit)
        layout.addRow("FPS", self.fps_spin)
        layout.addRow("Cut In", self.cut_in_spin)
        layout.addRow("Cut Out", self.cut_out_spin)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self) -> dict:
        return {
            "episode": self.episode_edit.text().strip(),
            "sequence": self.sequence_edit.text().strip(),
            "shot": self.shot_edit.text().strip(),
            "fps": self.fps_spin.value(),
            "cut_in": self.cut_in_spin.value(),
            "cut_out": self.cut_out_spin.value(),
        }

    def accept(self) -> None:
        values = self.values()
        if not values["episode"] or not values["sequence"] or not values["shot"]:
            QtWidgets.QMessageBox.warning(self, "Create Shot", "Episode, Sequence, and Shot are required.")
            return
        if values["cut_out"] < values["cut_in"]:
            QtWidgets.QMessageBox.warning(self, "Create Shot", "Cut Out must be greater than or equal to Cut In.")
            return
        super().accept()


class ShotManagerWindow(QtWidgets.QDialog):
    def __init__(self, config_dir: str | os.PathLike[str] | None = None, parent=None):
        super().__init__(parent)
        self.service, self.request_cls, self.identity_cls = _service(config_dir)
        self.shots = []
        self.is_maya_session = _is_maya_session()
        self.setWindowTitle(f"Shot Manager - {self.service.project_config.project_name}")
        self.resize(900, 560)
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(4, 4, 4, 4)
        root_layout.setSpacing(4)

        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setSpacing(4)
        self.create_btn = QtWidgets.QPushButton("Create Shot")
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.add_cast_btn = QtWidgets.QPushButton("Add Cast")
        self.add_selected_asset_btn = QtWidgets.QPushButton("Add Selected Asset")
        self.remove_cast_btn = QtWidgets.QPushButton("Remove Cast")
        self.save_cast_btn = QtWidgets.QPushButton("Save Cast")
        self.import_cast_btn = QtWidgets.QPushButton("Import Cast CSV")
        self.export_cast_btn = QtWidgets.QPushButton("Export Cast CSV")
        self.import_cast_cache_btn = QtWidgets.QPushButton("Import Cast Cache")
        self.sync_cast_sheet_btn = QtWidgets.QPushButton("Sync Cast Spreadsheet")
        self.import_cast_sheet_btn = QtWidgets.QPushButton("Import Cast Spreadsheet")
        if self.is_maya_session:
            self.sync_cast_sheet_btn.setEnabled(False)
            self.sync_cast_sheet_btn.setToolTip("Use standalone Shot Manager for Spreadsheet sync.")
            self.import_cast_sheet_btn.setEnabled(False)
            self.import_cast_sheet_btn.setToolTip("Use standalone Shot Manager for Spreadsheet import.")
        self.validate_btn = QtWidgets.QPushButton("Validate Cast")
        self.build_preview_btn = QtWidgets.QPushButton("Build Preview")
        self.build_shot_btn = QtWidgets.QPushButton("Build Shot From Cast")
        self.save_work_btn = QtWidgets.QPushButton("Save Work Scene")
        self.review_layers_btn = QtWidgets.QPushButton("Create Review Layers")
        if not self.is_maya_session:
            self.save_work_btn.setEnabled(False)
            self.save_work_btn.setToolTip("Available inside Maya.")
            self.review_layers_btn.setEnabled(False)
            self.review_layers_btn.setToolTip("Available inside Maya.")
        toolbar.addWidget(self.create_btn)
        toolbar.addWidget(self.refresh_btn)
        toolbar.addWidget(self.add_cast_btn)
        toolbar.addWidget(self.add_selected_asset_btn)
        toolbar.addWidget(self.remove_cast_btn)
        toolbar.addWidget(self.save_cast_btn)
        toolbar.addWidget(self.import_cast_btn)
        toolbar.addWidget(self.export_cast_btn)
        toolbar.addWidget(self.import_cast_cache_btn)
        toolbar.addWidget(self.sync_cast_sheet_btn)
        toolbar.addWidget(self.import_cast_sheet_btn)
        toolbar.addWidget(self.validate_btn)
        toolbar.addWidget(self.build_preview_btn)
        toolbar.addWidget(self.build_shot_btn)
        toolbar.addWidget(self.save_work_btn)
        toolbar.addWidget(self.review_layers_btn)
        toolbar.addStretch(1)
        root_layout.addLayout(toolbar)

        splitter = QtWidgets.QSplitter()
        root_layout.addWidget(splitter, 1)

        self.shot_list = QtWidgets.QTreeWidget()
        self.shot_list.setHeaderLabels(["Episode", "Sequence", "Shot", "Status", "Frames"])
        self.shot_list.header().setStretchLastSection(True)
        self.shot_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        splitter.addWidget(self.shot_list)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(4, 4, 4, 4)
        right_layout.setSpacing(4)
        self.tabs = QtWidgets.QTabWidget()
        self.shot_json_view = QtWidgets.QPlainTextEdit()
        self.cast_table = QtWidgets.QTableWidget(0, 8)
        self.cast_table.setHorizontalHeaderLabels([
            "cast_key",
            "asset",
            "variant",
            "role",
            "namespace",
            "asset_publish",
            "required",
            "note",
        ])
        self.cast_table.horizontalHeader().setStretchLastSection(True)
        self.cast_table.verticalHeader().setVisible(False)
        self.cast_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.cast_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.cast_json_view = QtWidgets.QPlainTextEdit()
        self.validation_view = QtWidgets.QPlainTextEdit()
        self.build_preview_table = QtWidgets.QTableWidget(0, 9)
        self.build_preview_table.setHorizontalHeaderLabels([
            "cast_key",
            "asset",
            "variant",
            "namespace",
            "layer",
            "asset_publish",
            "required",
            "status",
            "publish_path",
        ])
        self.build_preview_table.horizontalHeader().setStretchLastSection(True)
        self.build_preview_table.verticalHeader().setVisible(False)
        self.build_preview_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)

        self.work_tab = QtWidgets.QWidget()
        work_layout = QtWidgets.QVBoxLayout(self.work_tab)
        work_layout.setContentsMargins(4, 4, 4, 4)
        work_layout.setSpacing(4)
        work_header = QtWidgets.QHBoxLayout()
        work_header.setSpacing(4)
        self.work_dept_combo = QtWidgets.QComboBox()
        self.work_dept_combo.addItems(self.service.shot_departments)
        self.open_work_btn = QtWidgets.QPushButton("Open Work Scene")
        self.refresh_work_btn = QtWidgets.QPushButton("Refresh Work")
        work_header.addWidget(QtWidgets.QLabel("Dept"))
        work_header.addWidget(self.work_dept_combo)
        work_header.addWidget(self.open_work_btn)
        work_header.addWidget(self.refresh_work_btn)
        work_header.addStretch(1)
        self.work_table = QtWidgets.QTableWidget(0, 5)
        self.work_table.setHorizontalHeaderLabels(["File", "Dept", "Updated", "Comment", "Path"])
        self.work_table.horizontalHeader().setStretchLastSection(True)
        self.work_table.verticalHeader().setVisible(False)
        self.work_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.work_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.work_table.setColumnHidden(4, True)
        work_layout.addLayout(work_header)
        work_layout.addWidget(self.work_table, 1)

        for widget in (self.shot_json_view, self.cast_json_view, self.validation_view):
            widget.setReadOnly(True)
        self.tabs.addTab(self.shot_json_view, "shot.json")
        self.tabs.addTab(self.work_tab, "Work Scene")
        self.tabs.addTab(self.cast_table, "Cast")
        self.tabs.addTab(self.cast_json_view, "cast.json")
        self.tabs.addTab(self.validation_view, "Validation")
        self.tabs.addTab(self.build_preview_table, "Build Preview")
        right_layout.addWidget(self.tabs)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)

        self.status_label = QtWidgets.QLabel("")
        root_layout.addWidget(self.status_label)

        self.create_btn.clicked.connect(self.create_shot)
        self.refresh_btn.clicked.connect(self.refresh)
        self.add_cast_btn.clicked.connect(self.add_cast_row)
        self.add_selected_asset_btn.clicked.connect(self.add_selected_asset_to_cast)
        self.remove_cast_btn.clicked.connect(self.remove_cast_row)
        self.save_cast_btn.clicked.connect(self.save_cast)
        self.import_cast_btn.clicked.connect(self.import_cast_csv)
        self.export_cast_btn.clicked.connect(self.export_cast_csv)
        self.import_cast_cache_btn.clicked.connect(self.import_cast_cache)
        self.sync_cast_sheet_btn.clicked.connect(self.sync_cast_spreadsheet)
        self.import_cast_sheet_btn.clicked.connect(self.import_cast_spreadsheet)
        self.validate_btn.clicked.connect(self.validate_current_cast)
        self.build_preview_btn.clicked.connect(self.show_build_preview)
        self.build_shot_btn.clicked.connect(self.build_shot_from_cast)
        self.save_work_btn.clicked.connect(self.save_work_scene)
        self.review_layers_btn.clicked.connect(self.create_review_layers)
        self.open_work_btn.clicked.connect(self.open_work_scene)
        self.refresh_work_btn.clicked.connect(self.refresh_work_files)
        self.work_dept_combo.currentTextChanged.connect(lambda _text: self.refresh_work_files())
        self.work_table.itemDoubleClicked.connect(lambda _item: self.open_work_scene())
        self.shot_list.currentItemChanged.connect(lambda _current, _previous: self.show_current_shot())

    def refresh(self) -> None:
        selected = self.current_identity()
        selected_code = selected.code if selected else ""
        self.shots = self.service.list_shots()
        self.shot_list.clear()
        row_to_select = None
        for identity in self.shots:
            data = self.service.load_shot(identity)
            editorial = data.get("editorial") or {}
            frames = ""
            if editorial:
                frames = f"{editorial.get('cut_in', '')}-{editorial.get('cut_out', '')}"
            item = QtWidgets.QTreeWidgetItem([
                identity.episode,
                identity.sequence,
                identity.shot,
                str(data.get("status", "")),
                frames,
            ])
            item.setData(0, QtCore.Qt.UserRole, identity)
            self.shot_list.addTopLevelItem(item)
            if identity.code == selected_code:
                row_to_select = item
        if row_to_select:
            self.shot_list.setCurrentItem(row_to_select)
        elif self.shot_list.topLevelItemCount():
            self.shot_list.setCurrentItem(self.shot_list.topLevelItem(0))
        self.status_label.setText(f"{len(self.shots)} shots")

    def current_identity(self):
        item = self.shot_list.currentItem()
        if not item:
            return None
        return item.data(0, QtCore.Qt.UserRole)

    def show_current_shot(self) -> None:
        identity = self.current_identity()
        if not identity:
            self.shot_json_view.clear()
            self.cast_table.setRowCount(0)
            self.cast_json_view.clear()
            self.validation_view.clear()
            self.build_preview_table.setRowCount(0)
            self.work_table.setRowCount(0)
            return
        shot_data = self.service.load_shot(identity)
        cast_data = self.service.load_cast(identity)
        self.shot_json_view.setPlainText(json.dumps(shot_data, indent=2, ensure_ascii=False))
        self.cast_json_view.setPlainText(json.dumps(cast_data, indent=2, ensure_ascii=False))
        self.populate_cast_table(cast_data)
        self.refresh_work_files()
        self.validate_current_cast(update_tab=False)
        self.populate_build_preview(switch_tab=False)

    def create_shot(self) -> None:
        dialog = ShotCreateDialog(self, fps=self.service.project_fps)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        try:
            request = self.request_cls(**dialog.values())
            shot_root = self.service.create_shot(request)
            self.status_label.setText(f"Created shot: {shot_root}")
            self.refresh()
            self._select_identity(request.identity)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Create Shot Failed", str(exc))

    def validate_current_cast(self, update_tab: bool = True) -> None:
        identity = self.current_identity()
        if not identity:
            return
        issues = self.service.validate_cast(identity)
        if issues:
            text = "\n".join(f"[{issue.severity}] {issue.code}: {issue.message}" for issue in issues)
        else:
            text = "Cast validation OK"
        self.validation_view.setPlainText(text)
        if update_tab:
            self.tabs.setCurrentWidget(self.validation_view)
            self.status_label.setText(text.splitlines()[0])

    def show_build_preview(self) -> None:
        self.populate_build_preview(switch_tab=True)

    def build_shot_from_cast(self) -> None:
        identity = self.current_identity()
        if not identity:
            return
        preview = self.service.build_preview(identity)
        missing_required = [item for item in preview if item.required and item.status != "resolved"]
        if missing_required:
            message = "\n".join(f"{item.cast_key}: {item.message or item.status}" for item in missing_required)
            QtWidgets.QMessageBox.warning(self, "Build Shot From Cast", f"Required cast is not resolved:\n{message}")
            return
        resolved = [item for item in preview if item.status == "resolved"]
        if not resolved:
            self.status_label.setText("No resolved cast to build")
            return
        try:
            _ensure_smartlib_on_path()
            from smartlib.dcc.maya.shot_builder import build_shot_from_preview

            referenced = build_shot_from_preview(resolved, self.service.load_shot(identity))
            self.status_label.setText(f"Referenced {len(referenced)} assets")
            self.populate_build_preview(switch_tab=True)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Build Shot From Cast Failed", str(exc))

    def populate_build_preview(self, switch_tab: bool = False) -> None:
        identity = self.current_identity()
        if not identity:
            return
        preview = self.service.build_preview(identity)
        self.build_preview_table.setRowCount(0)
        for item in preview:
            row = self.build_preview_table.rowCount()
            self.build_preview_table.insertRow(row)
            values = [
                item.cast_key,
                item.asset,
                item.variant,
                item.namespace,
                item.review_layer,
                item.asset_publish,
                "yes" if item.required else "no",
                item.status,
                item.publish_path or item.message,
            ]
            for column, value in enumerate(values):
                table_item = QtWidgets.QTableWidgetItem(str(value))
                if item.status != "resolved":
                    table_item.setToolTip(item.message)
                self.build_preview_table.setItem(row, column, table_item)
        self.build_preview_table.resizeColumnsToContents()
        self.build_preview_table.setColumnWidth(7, 120)
        resolved = len([item for item in preview if item.status == "resolved"])
        self.status_label.setText(f"Build preview: {resolved}/{len(preview)} resolved")
        if switch_tab:
            self.tabs.setCurrentWidget(self.build_preview_table)

    def populate_cast_table(self, cast_data: dict) -> None:
        self.cast_table.setRowCount(0)
        cast = cast_data.get("cast") or {}
        for cast_key, entry in sorted(cast.items()):
            self._append_cast_row(
                cast_key=cast_key,
                asset=entry.get("asset", ""),
                variant=entry.get("variant", "default"),
                role=entry.get("role", "CHA"),
                namespace=entry.get("namespace", cast_key),
                asset_publish=entry.get("asset_publish", "approved"),
                required=bool(entry.get("required", True)),
                note=entry.get("note", ""),
            )
        self.cast_table.resizeColumnsToContents()

    def add_cast_row(self) -> None:
        row_number = self.cast_table.rowCount() + 1
        self._append_cast_row(
            cast_key=f"cast_{row_number:03d}",
            asset="",
            variant="default",
            role="CHA",
            namespace=f"cast{row_number:03d}",
            asset_publish="approved",
            required=True,
            note="",
        )
        self.cast_table.setCurrentCell(self.cast_table.rowCount() - 1, 0)

    def add_selected_asset_to_cast(self) -> None:
        identity = self.current_identity()
        if not identity:
            return
        existing = self.service.load_cast(identity).get("cast", {})
        for row in self.cast_table_rows():
            if row.get("cast_key"):
                existing[row["cast_key"]] = row
        entry = self.service.selected_asset_for_cast(existing_cast=existing)
        if not entry:
            QtWidgets.QMessageBox.information(
                self,
                "Add Selected Asset",
                "No selected asset cache was found. Use Asset Manager > right click asset > Send to Shot Cast.",
            )
            return
        self._append_cast_row(
            cast_key=entry["cast_key"],
            asset=entry["asset"],
            variant=entry["variant"],
            role=entry["role"],
            namespace=entry["namespace"],
            asset_publish=entry["asset_publish"],
            required=entry["required"],
            note=entry["note"],
        )
        self.cast_table.setCurrentCell(self.cast_table.rowCount() - 1, 0)
        self.status_label.setText(f"Added selected asset to cast: {entry['asset']}")

    def remove_cast_row(self) -> None:
        row = self.cast_table.currentRow()
        if row >= 0:
            self.cast_table.removeRow(row)

    def save_cast(self) -> None:
        identity = self.current_identity()
        if not identity:
            return
        existing = self.service.load_cast(identity)
        try:
            cast_data = self.service.build_cast_data(self.cast_table_rows(), existing=existing)
            self.service.write_cast(identity, cast_data)
            self.cast_json_view.setPlainText(json.dumps(cast_data, indent=2, ensure_ascii=False))
            self.status_label.setText("Saved cast.json")
            self.validate_current_cast(update_tab=False)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save Cast Failed", str(exc))

    def import_cast_csv(self) -> None:
        identity = self.current_identity()
        if not identity:
            return
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Import Cast CSV",
            "",
            "CSV Files (*.csv);;All Files (*.*)",
        )
        if not path:
            return
        try:
            self.service.import_cast_csv(identity, path)
            cast_data = self.service.load_cast(identity)
            self.populate_cast_table(cast_data)
            self.cast_json_view.setPlainText(json.dumps(cast_data, indent=2, ensure_ascii=False))
            self.validate_current_cast(update_tab=False)
            self.populate_build_preview(switch_tab=False)
            self.status_label.setText(f"Imported cast CSV: {Path(path).name}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Import Cast CSV Failed", str(exc))

    def import_cast_spreadsheet(self) -> None:
        identity = self.current_identity()
        if not identity:
            return
        if self.is_maya_session:
            QtWidgets.QMessageBox.information(
                self,
                "Import Cast Spreadsheet",
                "Spreadsheet import is disabled inside Maya. Use standalone Shot Manager.",
            )
            return
        try:
            self.service.import_cast_spreadsheet(identity)
            cast_data = self.service.load_cast(identity)
            self.populate_cast_table(cast_data)
            self.cast_json_view.setPlainText(json.dumps(cast_data, indent=2, ensure_ascii=False))
            self.validate_current_cast(update_tab=False)
            self.populate_build_preview(switch_tab=True)
            self.status_label.setText("Imported cast spreadsheet")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Import Cast Spreadsheet Failed", str(exc))

    def sync_cast_spreadsheet(self) -> None:
        if self.is_maya_session:
            QtWidgets.QMessageBox.information(
                self,
                "Sync Cast Spreadsheet",
                "Spreadsheet sync is disabled inside Maya. Use standalone Shot Manager.",
            )
            return
        try:
            path = self.service.sync_cast_spreadsheet_cache()
            self.status_label.setText(f"Synced cast spreadsheet: {path}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Sync Cast Spreadsheet Failed", str(exc))

    def import_cast_cache(self) -> None:
        identity = self.current_identity()
        if not identity:
            return
        try:
            self.service.import_cast_cache(identity)
            cast_data = self.service.load_cast(identity)
            self.populate_cast_table(cast_data)
            self.cast_json_view.setPlainText(json.dumps(cast_data, indent=2, ensure_ascii=False))
            self.validate_current_cast(update_tab=False)
            self.populate_build_preview(switch_tab=True)
            self.status_label.setText(f"Imported cast cache: {self.service.cast_cache_path}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Import Cast Cache Failed", str(exc))

    def export_cast_csv(self) -> None:
        identity = self.current_identity()
        if not identity:
            return
        default_name = f"{identity.episode}_{identity.sequence}_{identity.shot}_cast.csv"
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export Cast CSV",
            default_name,
            "CSV Files (*.csv);;All Files (*.*)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            self.save_cast()
            output = self.service.export_cast_csv(identity, path)
            self.status_label.setText(f"Exported cast CSV: {output}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Export Cast CSV Failed", str(exc))

    def refresh_work_files(self) -> None:
        identity = self.current_identity()
        self.work_table.setRowCount(0)
        if not identity:
            return
        department = self.work_dept_combo.currentText().strip()
        for item in self.service.list_shot_work_files(identity, department=department):
            row = self.work_table.rowCount()
            self.work_table.insertRow(row)
            values = [item.file, item.department, item.updated, item.comment, item.path]
            for column, value in enumerate(values):
                table_item = QtWidgets.QTableWidgetItem(str(value))
                if column == 4:
                    table_item.setToolTip(str(value))
                self.work_table.setItem(row, column, table_item)
        self.work_table.resizeColumnsToContents()
        self.work_table.setColumnHidden(4, True)
        self.work_table.horizontalHeader().setStretchLastSection(True)

    def selected_work_scene_path(self) -> Path | None:
        row = self.work_table.currentRow()
        if row < 0:
            return None
        item = self.work_table.item(row, 4)
        if not item or not item.text().strip():
            return None
        return Path(item.text().strip())

    def open_work_scene(self) -> None:
        identity = self.current_identity()
        if not identity:
            return
        if not self.is_maya_session:
            QtWidgets.QMessageBox.information(self, "Open Work Scene", "Open Work Scene is available inside Maya.")
            return
        path = self.selected_work_scene_path()
        if not path:
            self.status_label.setText("Select a work scene first")
            return
        try:
            _ensure_smartlib_on_path()
            from smartlib.dcc.maya.shot_builder import open_work_scene

            open_work_scene(path, self.service.load_shot(identity))
            self.status_label.setText(f"Opened work scene: {path.name}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Open Work Scene Failed", str(exc))

    def save_work_scene(self) -> None:
        identity = self.current_identity()
        if not identity:
            return
        if not self.is_maya_session:
            QtWidgets.QMessageBox.information(self, "Save Work Scene", "Shot work save is available inside Maya.")
            return
        department = self.work_dept_combo.currentText().strip() or self.service.shot_departments[0]
        comment, accepted = QtWidgets.QInputDialog.getText(self, "Save Work Scene", "Comment")
        if not accepted:
            return
        try:
            _ensure_smartlib_on_path()
            import maya.cmds as cmds
            from smartlib.dcc.maya.shot_builder import save_current_scene

            current_path = cmds.file(query=True, sceneName=True) or None
            target_path = self.service.next_shot_work_path(identity, department, current_path=current_path)
            scene_info = save_current_scene(target_path, self.service.load_shot(identity))
            self.service.write_shot_work_metadata(
                target_path,
                identity,
                department,
                scene_info=scene_info,
                comment=comment,
            )
            self.status_label.setText(f"Saved work scene: {target_path}")
            self.refresh_work_files()
            self.tabs.setCurrentWidget(self.work_tab)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save Work Scene Failed", str(exc))

    def create_review_layers(self) -> None:
        identity = self.current_identity()
        if not identity:
            return
        if not self.is_maya_session:
            QtWidgets.QMessageBox.information(self, "Create Review Layers", "Review layer creation is available inside Maya.")
            return
        try:
            existing = self.service.load_cast(identity)
            cast_data = self.service.build_cast_data(self.cast_table_rows(), existing=existing)
            self.service.write_cast(identity, cast_data)
            self.cast_json_view.setPlainText(json.dumps(cast_data, indent=2, ensure_ascii=False))
            _ensure_smartlib_on_path()
            from smartlib.dcc.maya.shot_builder import create_review_display_layers

            result = create_review_display_layers(cast_data)
            summary = ", ".join(f"{name}: {count}" for name, count in sorted(result.items()))
            self.status_label.setText(f"Created review layers: {summary}")
            self.validate_current_cast(update_tab=True)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Create Review Layers Failed", str(exc))

    def cast_table_rows(self) -> list[dict]:
        rows = []
        for row in range(self.cast_table.rowCount()):
            values = {
                "cast_key": self._table_text(row, 0),
                "asset": self._table_text(row, 1),
                "variant": self._table_text(row, 2) or "default",
                "role": self._table_text(row, 3) or "CHA",
                "namespace": self._table_text(row, 4),
                "asset_publish": self._table_text(row, 5) or "approved",
                "required": self._table_required(row),
                "note": self._table_text(row, 7),
            }
            if any(str(value).strip() for key, value in values.items() if key != "required"):
                rows.append(values)
        return rows

    def _append_cast_row(
        self,
        *,
        cast_key: str,
        asset: str,
        variant: str,
        role: str,
        namespace: str,
        asset_publish: str,
        required: bool,
        note: str,
    ) -> None:
        row = self.cast_table.rowCount()
        self.cast_table.insertRow(row)
        for column, value in enumerate([cast_key, asset, variant, role, namespace, asset_publish]):
            self.cast_table.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))
        required_item = QtWidgets.QTableWidgetItem("")
        required_item.setFlags(required_item.flags() | QtCore.Qt.ItemIsUserCheckable)
        required_item.setCheckState(QtCore.Qt.Checked if required else QtCore.Qt.Unchecked)
        self.cast_table.setItem(row, 6, required_item)
        self.cast_table.setItem(row, 7, QtWidgets.QTableWidgetItem(str(note)))

    def _table_text(self, row: int, column: int) -> str:
        item = self.cast_table.item(row, column)
        return item.text().strip() if item else ""

    def _table_required(self, row: int) -> bool:
        item = self.cast_table.item(row, 6)
        return item.checkState() == QtCore.Qt.Checked if item else True

    def _select_identity(self, identity) -> None:
        for index in range(self.shot_list.topLevelItemCount()):
            item = self.shot_list.topLevelItem(index)
            item_identity = item.data(0, QtCore.Qt.UserRole)
            if item_identity and item_identity.code == identity.code:
                self.shot_list.setCurrentItem(item)
                return


_window = None


def show(config_dir: str | os.PathLike[str] | None = None):
    global _window
    try:
        _window.close()
    except Exception:
        pass
    _window = ShotManagerWindow(config_dir=config_dir)
    _window.show()
    return _window


if __name__ == "__main__":
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    show()
    sys.exit(app.exec())

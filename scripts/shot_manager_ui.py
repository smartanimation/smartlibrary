from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _qt_modules():
    try:
        from PySide6 import QtCore, QtGui, QtUiTools, QtWidgets

        return QtCore, QtGui, QtUiTools, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtUiTools, QtWidgets

        return QtCore, QtGui, QtUiTools, QtWidgets


QtCore, QtGui, QtUiTools, QtWidgets = _qt_modules()


def _exec_menu(menu, pos):
    """Execute a context menu with either the Qt 6 or Qt 5 API."""
    exec_method = getattr(menu, "exec", None)
    if exec_method is None:
        exec_method = menu.exec_
    return exec_method(pos)


CONSTRUCT_TYPES = ("rig", "camera", "animation", "fx", "light", "audio", "cast", "placement", "layout_overlay", "playblast_settings")
CONSTRUCT_MODES = ("reference", "import", "apply", "reference_cache", "file")
FX_CACHE_FILTER = "FX Cache Files (*.abc *.usd *.usda *.usdc);;All Files (*.*)"


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


def _resource_icon_path(*parts: str) -> Path:
    root = Path(os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT") or Path(__file__).resolve().parents[1])
    return root / "resources" / "icons" / Path(*parts)


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


class ShotManagerWindow(QtWidgets.QMainWindow):
    SETTINGS_ORGANIZATION = "smartpipeline"
    SETTINGS_APPLICATION = "ShotManager"
    SETTINGS_GEOMETRY_KEY = "window/geometry"
    SETTINGS_STATE_GROUP = "window/state"

    def __init__(self, config_dir: str | os.PathLike[str] | None = None, parent=None):
        super().__init__(parent)
        self.service, self.request_cls, self.identity_cls = _service(config_dir)
        self.shots = []
        self.active_sequence_identity = None
        self.active_shot_identity = None
        self.context_target_shot_identity = None
        self._opened_construct_scene_path = ""
        self._opened_construct_record = {}
        self._restore_state_pending = True
        self.is_maya_session = _is_maya_session()
        self.setWindowTitle(f"Shot Manager - {self.service.project_config.project_name}")
        self.resize(900, 560)
        self.setMinimumSize(760, 480)
        self._build_ui()
        self._restore_window_geometry()
        self.refresh()
        self._restore_window_state()

    def _window_settings(self):
        return QtCore.QSettings(self.SETTINGS_ORGANIZATION, self.SETTINGS_APPLICATION)

    def _restore_window_geometry(self) -> None:
        geometry = self._window_settings().value(self.SETTINGS_GEOMETRY_KEY)
        if geometry:
            self.restoreGeometry(geometry)

    def closeEvent(self, event) -> None:
        self._window_settings().setValue(self.SETTINGS_GEOMETRY_KEY, self.saveGeometry())
        self._save_window_state()
        super().closeEvent(event)

    def _save_window_state(self) -> None:
        settings = self._window_settings()
        settings.beginGroup(self.SETTINGS_STATE_GROUP)
        episode = self._current_episode()
        identity = self.current_identity()
        sequence_identity = self.current_sequence_identity()
        selected_kind = "sequence" if sequence_identity else ("shot" if identity else "")
        selected_code = sequence_identity.code if sequence_identity else (identity.code if identity else "")
        settings.setValue("episode", episode)
        settings.setValue("selected_kind", selected_kind)
        settings.setValue("selected_code", selected_code)
        settings.setValue("detail_mode", self.main_stack.currentWidget() == self.shot_detail_page)
        settings.setValue("detail_tab", self.tabs.currentIndex())
        department = self.work_dept_combo.currentText().strip()
        task_item = self.work_task_list.currentItem()
        option_item = self.shot_variant_list.currentItem()
        settings.setValue("department", department)
        settings.setValue("task", task_item.text() if task_item else "")
        settings.setValue("option", option_item.text() if option_item else "")
        settings.endGroup()

    def _restore_window_state(self) -> None:
        settings = self._window_settings()
        settings.beginGroup(self.SETTINGS_STATE_GROUP)
        episode = str(settings.value("episode", "") or "")
        selected_kind = str(settings.value("selected_kind", "") or "")
        selected_code = str(settings.value("selected_code", "") or "")
        detail_mode = self._settings_bool(settings.value("detail_mode", False))
        detail_tab = self._settings_int(settings.value("detail_tab"), 0)
        department = str(settings.value("department", "") or "")
        task = str(settings.value("task", "") or "")
        option = str(settings.value("option", "") or "")
        settings.endGroup()

        self._select_episode(episode)
        if selected_kind == "sequence":
            self._select_sequence_code(selected_code)
        elif selected_kind == "shot":
            self._select_shot_code(selected_code)
        if department:
            index = self.work_dept_combo.findText(department)
            if index >= 0:
                self.work_dept_combo.setCurrentIndex(index)
        self._populate_shot_tasks(keep_task=task)
        if option:
            self._populate_shot_options(keep_option=option)
        self.tabs.setCurrentIndex(max(0, min(detail_tab, self.tabs.count() - 1)))
        if detail_mode and (self.current_identity() or self.current_sequence_identity()):
            self.show_detail_mode()
        else:
            self.show_shot_browser()
        self._restore_state_pending = False

    @staticmethod
    def _settings_int(value, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _settings_bool(value) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    def _build_ui(self) -> None:
        ui_path = Path(__file__).resolve().parent / "ui" / "shot_manager.ui"
        ui_file = QtCore.QFile(str(ui_path))
        if not ui_file.open(QtCore.QFile.ReadOnly):
            raise RuntimeError(f"Could not open UI file: {ui_path}")
        try:
            self.ui = QtUiTools.QUiLoader().load(ui_file)
        finally:
            ui_file.close()
        if self.ui is None:
            raise RuntimeError(f"Could not load UI file: {ui_path}")

        self.setCentralWidget(self.ui)
        self.ui_path = ui_path

        self.main_stack = self._ui_object("main_stack")
        self.shot_browser_page = self._ui_object("shot_browser_page")
        self.shot_detail_page = self._ui_object("shot_detail_page")
        self.back_to_shots_btn = self._ui_object("back_to_shots_btn")
        self.detail_title_label = self._ui_object("detail_title_label")
        self.episode_listview = self._ui_object("episode_listview")
        self.shot_filter_tree = self._ui_object("shot_filter_tree")
        self.shot_list = self._ui_object("shot_list")
        self.info_widget = self._ui_object("info_widget")
        self.shot_thumbnail_label = self._ui_object("shot_thumbnail_label")
        self.shot_info_table = self._ui_object("shot_info_table")
        self.set_edit_range_btn = QtWidgets.QPushButton("Set Edit Range")
        self.shot_dept_list = self._ui_object("shot_dept_list")
        self.shot_variant_list = self._ui_object("shot_variant_list")
        self.shot_dept_label = self._ui_object("shot_dept_label")
        self.shot_variant_label = self._ui_object("shot_variant_label")
        self.add_shot_variant_btn = self._ui_object("add_shot_variant_btn")
        self.stage_shot_btn = self._ui_object("stage_shot_btn")
        self.tabs = self._ui_object("tabs")
        self.shot_json_view = self._ui_object("shot_json_view")
        self.work_tab = self._ui_object("work_tab")
        self.cast_tab = self._ui_object("cast_tab")
        self.data_tab = self._ui_object("data_tab")
        self.shot_data_tree = self._ui_object("shot_data_tree")
        self.validation_view = self._ui_object("validation_view")
        self.build_preview_tab = self._ui_object("build_preview_tab")
        self.context_tab = self._ui_object("context_tab")
        self.shot_context_tree = self._ui_object("shot_context_tree")
        self.cast_json_view = self._ui_object("cast_json_view")
        self.cast_table = self._ui_object("cast_table")
        self.build_preview_table = self._ui_object("build_preview_table")
        self.work_dept_combo = self._ui_object("work_dept_combo")
        self.open_work_btn = self._ui_object("open_work_btn")
        self.refresh_work_btn = self._ui_object("refresh_work_btn")
        self.work_table = self._ui_object("work_table")
        self.add_cast_btn = self._ui_object("add_cast_btn")
        self.add_selected_asset_btn = self._ui_object("add_selected_asset_btn")
        self.remove_cast_btn = self._ui_object("remove_cast_btn")
        self.validate_btn = self._ui_object("validate_btn")
        self.save_cast_btn = self._ui_object("save_cast_btn")
        self.build_preview_btn = self._ui_object("build_preview_btn")
        self.open_review_layer_ui_btn = self._ui_object("open_review_layer_ui_btn")
        self.review_layers_btn = self._ui_object("review_layers_btn")
        self.plan_review_publish_btn = self._ui_object("plan_review_publish_btn")
        self.export_beauty_playblast_btn = self._ui_object("export_beauty_playblast_btn")
        self.build_shot_btn = self._ui_object("build_shot_btn")
        self.save_work_btn = self._ui_object("save_work_btn")
        self.archive_scene_btn = self._ui_object("archive_scene_btn")
        self.publish_animation_curves_btn = QtWidgets.QPushButton("Export Animation Curves")
        self.apply_animation_curves_btn = QtWidgets.QPushButton("Apply Animation Curves")
        self.export_scene_data_btn = QtWidgets.QPushButton("Export Data")
        self.apply_scene_data_btn = QtWidgets.QPushButton("Apply Data")
        self.publish_animation_btn = QtWidgets.QPushButton("Publish Animation Package")
        self.publish_animation_cache_btn = QtWidgets.QPushButton("Publish Animation USD")
        self.publish_animation_alembic_btn = QtWidgets.QPushButton("Publish Alembic Cache")
        self.build_animation_package_btn = QtWidgets.QPushButton("Build Package")
        self.build_animation_review_scene_btn = QtWidgets.QPushButton("Build Review Scene")
        self.export_camera_btn = QtWidgets.QPushButton("Export Camera")
        self.apply_camera_btn = QtWidgets.QPushButton("Apply Camera")
        self.publish_camera_btn = QtWidgets.QPushButton("Publish Camera")
        self.publish_preview_render_btn = QtWidgets.QPushButton("Publish Preview Render")
        self.apply_set_dress_btn = QtWidgets.QPushButton("Apply Set Dress")
        self.data_type_list = QtWidgets.QListWidget()
        self.data_cast_list = QtWidgets.QListWidget()
        self.publish_tab = QtWidgets.QWidget()
        self.publish_type_list = QtWidgets.QListWidget()
        self.publish_target_list = QtWidgets.QListWidget()
        self.publish_tree = QtWidgets.QTreeWidget()
        self.work_task_list = QtWidgets.QListWidget()
        self.construct_tab = QtWidgets.QWidget()
        self.construct_scene_list = QtWidgets.QListWidget()
        self.construct_table = QtWidgets.QTableWidget()
        self.construct_stage_btn = QtWidgets.QPushButton("Stage")
        self.dependencies_tab = QtWidgets.QWidget()
        self.dependencies_tree = QtWidgets.QTreeWidget()
        self.status_label = self._ui_object("status_label")

        self.create_action = self._ui_object("actionCreate_Shot")
        self.refresh_action = self._ui_object("actionRefresh")
        self.import_cast_action = self._ui_object("actionImport_Cast_CSV")
        self.export_cast_action = self._ui_object("actionExport_Cast_CSV")
        self.import_cast_cache_action = self._ui_object("actionImport_Cast_Cache")
        self.sync_cast_sheet_action = self._ui_object("actionSync_Cast_Spreadsheet")
        self.import_cast_sheet_action = self._ui_object("actionImport_Cast_Spreadsheet")

        self.work_dept_combo.addItems(self.service.shot_departments)
        self.shot_dept_list.addItems(self.service.shot_departments)
        if self.shot_dept_list.count():
            self.shot_dept_list.setCurrentRow(0)
        self.shot_variant_list.addItems(["all", "main"])
        self.shot_variant_list.setCurrentRow(1)
        self.add_shot_variant_btn.setToolTip("Add a shot work option such as acting_A.")
        self.stage_shot_btn.setToolTip("Build shot references from cast, then Save into the selected work option.")
        self.stage_shot_btn.hide()
        self.construct_stage_btn.setToolTip("Stage from checked Construct components.")
        self._apply_action_icons()
        if self.is_maya_session:
            self.sync_cast_sheet_action.setEnabled(False)
            self.sync_cast_sheet_action.setToolTip("Use standalone Shot Manager for Spreadsheet sync.")
            self.import_cast_sheet_action.setEnabled(False)
            self.import_cast_sheet_action.setToolTip("Use standalone Shot Manager for Spreadsheet import.")
        if not self.is_maya_session:
            self.save_work_btn.setEnabled(False)
            self.save_work_btn.setToolTip("Available inside Maya.")
            self.archive_scene_btn.setEnabled(False)
            self.archive_scene_btn.setToolTip("Available inside Maya.")
            self.review_layers_btn.setEnabled(False)
            self.review_layers_btn.setToolTip("Available inside Maya.")
            self.export_beauty_playblast_btn.setEnabled(False)
            self.export_beauty_playblast_btn.setToolTip("Available inside Maya.")
            self.publish_animation_curves_btn.setEnabled(False)
            self.publish_animation_curves_btn.setToolTip("Available inside Maya.")
            self.apply_animation_curves_btn.setEnabled(False)
            self.apply_animation_curves_btn.setToolTip("Available inside Maya.")
            self.export_scene_data_btn.setEnabled(False)
            self.export_scene_data_btn.setToolTip("Available inside Maya.")
            self.apply_scene_data_btn.setEnabled(False)
            self.apply_scene_data_btn.setToolTip("Available inside Maya.")
            self.publish_animation_cache_btn.setEnabled(False)
            self.publish_animation_cache_btn.setToolTip("Available inside Maya.")
            self.publish_animation_alembic_btn.setEnabled(False)
            self.publish_animation_alembic_btn.setToolTip("Available inside Maya.")
            self.build_animation_review_scene_btn.setEnabled(False)
            self.build_animation_review_scene_btn.setToolTip("Available inside Maya.")
            self.export_camera_btn.setEnabled(False)
            self.export_camera_btn.setToolTip("Available inside Maya.")
            self.apply_camera_btn.setEnabled(False)
            self.apply_camera_btn.setToolTip("Available inside Maya.")
            self.publish_camera_btn.setEnabled(False)
            self.publish_camera_btn.setToolTip("Available inside Maya.")
            self.apply_set_dress_btn.setEnabled(False)
            self.apply_set_dress_btn.setToolTip("Available inside Maya.")
        self.main_stack.setCurrentWidget(self.shot_browser_page)
        self.episode_listview.setMaximumHeight(120)
        self.episode_listview.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.shot_filter_tree.setIndentation(10)
        self.shot_filter_tree.setStyleSheet("QTreeWidget::item { height: 26px; }")
        self.shot_list.setColumnCount(6)
        self.shot_list.setHeaderLabels(["Thumbnail", "Episode", "Sequence", "Shot", "Status", "Frames"])
        self.shot_list.setRootIsDecorated(False)
        self.shot_list.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.shot_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.shot_list.setAllColumnsShowFocus(True)
        self.shot_list.header().setStretchLastSection(True)
        self.shot_list.setUniformRowHeights(True)
        self.shot_list.setIconSize(QtCore.QSize(96, 54))
        self.shot_list.setStyleSheet("QTreeWidget::item { height: 58px; }")
        self._install_detail_header()
        self._apply_detail_visual_style()
        self._setup_work_context_ui()
        self.info_widget.setMinimumWidth(260)
        self.shot_thumbnail_label.setStyleSheet("QLabel { background: #303030; border: 1px solid #454545; }")
        self.shot_info_table.horizontalHeader().setVisible(False)
        self.shot_info_table.verticalHeader().setVisible(False)
        self.shot_info_table.horizontalHeader().setStretchLastSection(True)
        self.shot_info_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.shot_info_table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.set_edit_range_btn.setToolTip("Set Maya playback range to the editorial cut range.")
        if not self.is_maya_session:
            self.set_edit_range_btn.setEnabled(False)
            self.set_edit_range_btn.setToolTip("Available inside Maya.")
        self._hide_detail_json_views()
        self.cast_table.horizontalHeader().setStretchLastSection(True)
        self.cast_table.verticalHeader().setVisible(False)
        self.cast_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.cast_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.cast_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.cast_table.setColumnCount(9)
        self.cast_table.setHorizontalHeaderLabels(
            ["", "Category", "Group", "Asset Name", "Variant", "FAST", "WORK", "FINAL", "Status"]
        )
        self.cast_table.setIconSize(QtCore.QSize(52, 52))
        self.cast_table.verticalHeader().setDefaultSectionSize(58)
        self.cast_table.setColumnWidth(0, 58)
        self.cast_table.setColumnWidth(1, 90)
        self.cast_table.setColumnWidth(2, 90)
        self.cast_table.setColumnWidth(3, 150)
        self.cast_table.setColumnWidth(4, 90)
        for column in (5, 6, 7):
            self.cast_table.setColumnWidth(column, 74)
        self.build_preview_table.horizontalHeader().setStretchLastSection(True)
        self.build_preview_table.verticalHeader().setVisible(False)
        self.build_preview_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._install_cast_build_widgets()
        self._install_preview_history_widgets()
        self._setup_data_tree()
        self._setup_publish_tab()
        self._setup_context_tab()
        self._setup_construct_tab()
        self._setup_dependencies_tab()
        self._hide_validation_tab()
        self.work_table.horizontalHeader().setStretchLastSection(True)
        self.work_table.verticalHeader().setVisible(False)
        self.work_table.setColumnCount(7)
        self.work_table.setHorizontalHeaderLabels(["Thumbnail", "File", "Task", "Option", "Updated", "Comment", "Path"])
        self.work_table.verticalHeader().setDefaultSectionSize(64)
        self.work_table.verticalHeader().setMinimumSectionSize(58)
        self.work_table.setIconSize(QtCore.QSize(88, 50))
        self.work_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.work_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.work_table.setShowGrid(False)
        self.work_table.setAlternatingRowColors(False)
        self.work_table.setColumnHidden(6, True)
        self.work_table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.open_work_btn.setText("Open")
        self.save_work_btn.setText("Save")
        self.archive_scene_btn.setText("Archive")
        self.open_work_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DialogOpenButton))
        self.save_work_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DialogSaveButton))
        self.archive_scene_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DriveHDIcon))
        for button in (self.open_work_btn, self.save_work_btn, self.archive_scene_btn):
            button.setIconSize(QtCore.QSize(18, 18))
        self.refresh_work_btn.setText("")
        self.refresh_work_btn.setToolTip("Refresh Work")
        self.refresh_work_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_BrowserReload))
        self.refresh_work_btn.setIconSize(QtCore.QSize(18, 18))
        self.refresh_work_btn.setFixedSize(28, 24)
        for button in (self.open_work_btn, self.save_work_btn, self.archive_scene_btn):
            button.setMinimumHeight(30)
        self.open_work_btn.setStyleSheet(
            "QPushButton { background-color: #2f6f4e; color: white; font-weight: bold; border-radius: 7px; padding: 5px 12px; }"
            "QPushButton:hover { background-color: #3d835f; }"
            "QPushButton:disabled { background-color: #4c5a52; color: #b8b8b8; }"
        )
        self.save_work_btn.setStyleSheet(
            "QPushButton { background-color: #2f5f9f; color: white; font-weight: bold; border-radius: 7px; padding: 5px 12px; }"
            "QPushButton:hover { background-color: #3b73b8; }"
            "QPushButton:disabled { background-color: #4b5665; color: #b8b8b8; }"
        )
        self.archive_scene_btn.setStyleSheet(
            "QPushButton { background-color: #635a36; color: white; font-weight: bold; border-radius: 7px; padding: 5px 12px; }"
            "QPushButton:hover { background-color: #756a40; }"
            "QPushButton:disabled { background-color: #5a5548; color: #b8b8b8; }"
        )
        for widget in (self.shot_json_view, self.cast_json_view, self.validation_view):
            widget.setReadOnly(True)

        self.create_action.triggered.connect(self.create_shot)
        self.refresh_action.triggered.connect(self.refresh)
        self.add_cast_btn.clicked.connect(self.open_smart_casting)
        self.add_cast_btn.setText("Edit in Smart Casting")
        self.add_cast_btn.setToolTip("Open this shot in Smart Casting, the sole cast editor.")
        for button in (self.add_selected_asset_btn, self.remove_cast_btn, self.save_cast_btn):
            button.hide()
        for action in (
            self.import_cast_action,
            self.export_cast_action,
            self.import_cast_cache_action,
            self.sync_cast_sheet_action,
            self.import_cast_sheet_action,
        ):
            action.setText("Open Smart Casting")
            action.setToolTip("Cast import, export, sync, and editing are managed in Smart Casting.")
            action.setEnabled(True)
            action.triggered.connect(self.open_smart_casting)
        self.validate_btn.clicked.connect(self.validate_current_cast)
        self.tabs.currentChanged.connect(lambda _index: self._on_detail_tab_changed())
        self.build_preview_btn.clicked.connect(self.show_build_preview)
        self.set_edit_range_btn.clicked.connect(self.set_edit_time_range)
        self.build_shot_btn.clicked.connect(self.build_shot_from_cast)
        self.save_work_btn.clicked.connect(self.save_work_scene)
        self.archive_scene_btn.clicked.connect(self.archive_scene_snapshot)
        self.open_review_layer_ui_btn.clicked.connect(self.open_review_layer_manager)
        self.review_layers_btn.clicked.connect(self.create_review_layers)
        self.plan_review_publish_btn.clicked.connect(self.plan_review_publish)
        self.export_beauty_playblast_btn.clicked.connect(self.export_beauty_playblast)
        self.publish_animation_curves_btn.clicked.connect(self.publish_animation_curves)
        self.apply_animation_curves_btn.clicked.connect(self.apply_animation_curves)
        self.export_scene_data_btn.clicked.connect(self.export_scene_component_data)
        self.apply_scene_data_btn.clicked.connect(self.apply_scene_component_data)
        self.publish_animation_btn.clicked.connect(self.publish_animation)
        self.publish_animation_cache_btn.clicked.connect(self.publish_animation_cache)
        self.publish_animation_alembic_btn.clicked.connect(self.publish_animation_alembic_cache)
        self.build_animation_package_btn.clicked.connect(self.build_animation_package)
        self.build_animation_review_scene_btn.clicked.connect(self.build_animation_review_scene)
        self.export_camera_btn.clicked.connect(self.export_camera_data)
        self.apply_camera_btn.clicked.connect(self.apply_camera_data)
        self.publish_camera_btn.clicked.connect(self.publish_camera_data)
        self.publish_preview_render_btn.clicked.connect(self.publish_preview_render)
        self.apply_set_dress_btn.clicked.connect(self.apply_set_dress)
        self.open_preview_rv_btn.clicked.connect(self.open_selected_preview_in_rv)
        self.generate_review_btn.clicked.connect(self.open_generate_review)
        self.refresh_preview_history_btn.clicked.connect(self.populate_preview_history)
        self.preview_history_table.itemDoubleClicked.connect(lambda _item: self.open_selected_preview_in_rv())
        self.open_work_btn.clicked.connect(self.open_work_scene)
        self.refresh_work_btn.clicked.connect(self.refresh_work_files)
        self.work_dept_combo.currentTextChanged.connect(lambda _text: self._on_work_department_changed())
        self.shot_dept_list.currentRowChanged.connect(self._on_shot_department_selected)
        self.work_task_list.currentRowChanged.connect(lambda _row: self._on_work_task_changed())
        self.shot_variant_list.currentRowChanged.connect(lambda _row: self.refresh_work_files())
        self.add_shot_variant_btn.clicked.connect(self.add_shot_option)
        self.stage_shot_btn.clicked.connect(self.stage_shot_option)
        self.work_table.itemChanged.connect(self._on_work_comment_changed)
        self.work_table.customContextMenuRequested.connect(self.show_work_context_menu)
        self.episode_listview.currentRowChanged.connect(lambda _row: self._on_episode_selected())
        self.shot_filter_tree.currentItemChanged.connect(lambda _current, _previous: self._apply_shot_filter())
        self.shot_filter_tree.itemClicked.connect(lambda _item, _column: self._apply_shot_filter())
        self.shot_list.currentItemChanged.connect(lambda _current, _previous: self.show_current_shot())
        self.shot_list.itemDoubleClicked.connect(self.open_selected_detail)
        self.back_to_shots_btn.clicked.connect(self.show_shot_browser)

    def _install_detail_header(self) -> None:
        layout = self.shot_detail_page.layout()
        if layout is None:
            return
        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(4, 4, 4, 2)
        header.setSpacing(8)
        self.back_to_shots_btn.setMinimumHeight(24)
        self.back_to_shots_btn.setMinimumWidth(118)
        self.detail_title_label.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.set_edit_range_btn.setMinimumHeight(24)
        self.set_edit_range_btn.setMinimumWidth(88)
        header.addWidget(self.back_to_shots_btn)
        header.addWidget(self.detail_title_label)
        header.addWidget(self.set_edit_range_btn)
        header.addStretch(1)
        self.department_header_label = QtWidgets.QLabel("Department")
        self.department_header_label.setStyleSheet("font-weight: 600;")
        self.work_dept_combo.setMinimumWidth(150)
        header.addWidget(self.department_header_label)
        header.addWidget(self.work_dept_combo)
        layout.insertLayout(0, header)

    def _setup_work_context_ui(self) -> None:
        work_layout = self.work_tab.layout()
        if work_layout is None:
            return

        content_widget = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(4)
        while work_layout.count():
            item = work_layout.takeAt(0)
            if item.widget():
                content_layout.addWidget(item.widget())
            elif item.layout():
                content_layout.addLayout(item.layout())
            elif item.spacerItem():
                content_layout.addItem(item.spacerItem())

        selector_widget = QtWidgets.QWidget()
        selector_widget.setMinimumWidth(145)
        selector_widget.setMaximumWidth(190)
        selector_layout = QtWidgets.QVBoxLayout(selector_widget)
        selector_layout.setContentsMargins(0, 0, 0, 0)
        selector_layout.setSpacing(4)
        task_label = QtWidgets.QLabel("Task")
        task_label.setObjectName("work_task_label")
        selector_layout.addWidget(task_label)
        self.work_task_list.setObjectName("work_task_list")
        selector_layout.addWidget(self.work_task_list, 1)

        old_selector_layout = self.shot_variant_list.parentWidget().layout()
        if old_selector_layout:
            old_selector_layout.removeWidget(self.shot_variant_label)
            old_selector_layout.removeWidget(self.shot_variant_list)
            old_selector_layout.removeWidget(self.add_shot_variant_btn)
        option_row = QtWidgets.QHBoxLayout()
        option_row.setContentsMargins(0, 0, 0, 0)
        option_row.addWidget(self.shot_variant_label)
        option_row.addStretch(1)
        option_row.addWidget(self.add_shot_variant_btn)
        selector_layout.addLayout(option_row)
        selector_layout.addWidget(self.shot_variant_list, 1)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(selector_widget)
        splitter.addWidget(content_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([155, 700])
        work_layout.addWidget(splitter)

        detail_selector = self.ui.findChild(QtWidgets.QWidget, "detail_selector_widget")
        if detail_selector:
            detail_selector.hide()
        self.shot_dept_label.hide()
        self.shot_dept_list.hide()
        self.stage_shot_btn.hide()
        self._populate_shot_tasks()

    def _apply_detail_visual_style(self) -> None:
        self.ui.setStyleSheet(
            self.ui.styleSheet()
            + """
            QWidget#shot_detail_page {
                background: #2b2b2b;
            }
            QLabel#detail_title_label {
                color: #d8e3ef;
                font-size: 20px;
                font-weight: 700;
            }
            QLabel#shot_dept_label,
            QLabel#shot_variant_label,
            QLabel#work_task_label {
                color: #d8e3ef;
                font-size: 20px;
                font-weight: 700;
            }
            QPushButton {
                min-height: 22px;
                border-radius: 7px;
                padding: 3px 10px;
                background: #545454;
                color: #f0f0f0;
                border: 1px solid #626262;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #626262;
            }
            QTabBar::tab {
                padding: 5px 14px;
                min-height: 20px;
            }
            QListWidget#shot_dept_list,
            QListWidget#shot_variant_list,
            QListWidget#work_task_list {
                background: #262626;
                border: 1px solid #3d3d3d;
                font-size: 13px;
                color: #ededed;
                outline: 0;
            }
            QListWidget#shot_dept_list::item,
            QListWidget#shot_variant_list::item,
            QListWidget#work_task_list::item {
                min-height: 24px;
                padding: 3px 6px;
            }
            QListWidget#shot_dept_list::item:selected,
            QListWidget#shot_variant_list::item:selected,
            QListWidget#work_task_list::item:selected {
                background: #4a8ab0;
                color: white;
            }
            QTableWidget {
                gridline-color: transparent;
                background: #282828;
                alternate-background-color: #2f2f2f;
                color: #ededed;
                selection-background-color: #4a8ab0;
                selection-color: white;
                border: 1px solid #3d3d3d;
            }
            QHeaderView::section {
                background: #4a4a4a;
                color: #eeeeee;
                border: 0;
                border-right: 1px solid #666666;
                padding: 5px 8px;
                font-weight: 600;
            }
            """
        )
        self.back_to_shots_btn.setStyleSheet(
            "QPushButton { background-color: #666666; color: #f5f5f5; font-weight: bold; }"
            "QPushButton:hover { background-color: #747474; }"
        )
        self.set_edit_range_btn.setStyleSheet(
            "QPushButton { background-color: #3478c7; color: white; font-weight: bold; }"
            "QPushButton:hover { background-color: #4188dc; }"
            "QPushButton:disabled { background-color: #4b5665; color: #b8b8b8; }"
        )
        self.add_shot_variant_btn.setText("+")
        self.add_shot_variant_btn.setFixedSize(30, 24)
        self.add_shot_variant_btn.setStyleSheet(
            "QPushButton { background-color: #5f5f5f; color: white; font-size: 16px; "
            "font-weight: bold; border-radius: 7px; padding: 0; }"
            "QPushButton:hover { background-color: #707070; }"
        )
        self._move_add_option_button()
        for table in (
            self.work_table,
            self.cast_table,
            self.build_preview_table,
            self.shot_info_table,
            self.shot_data_tree,
            self.shot_context_tree,
        ):
            if hasattr(table, "setShowGrid"):
                table.setShowGrid(False)
            if hasattr(table, "setAlternatingRowColors"):
                table.setAlternatingRowColors(False)

    def _move_add_option_button(self) -> None:
        option_label = self.shot_variant_label
        parent_layout = option_label.parentWidget().layout() if option_label.parentWidget() else None
        if parent_layout is None:
            return
        parent_layout.removeWidget(self.add_shot_variant_btn)
        option_index = parent_layout.indexOf(option_label)
        parent_layout.removeWidget(option_label)
        option_row = QtWidgets.QHBoxLayout()
        option_row.setContentsMargins(0, 0, 0, 0)
        option_row.setSpacing(6)
        option_row.addWidget(option_label)
        option_row.addStretch(1)
        option_row.addWidget(self.add_shot_variant_btn)
        parent_layout.insertLayout(max(0, option_index), option_row)

    def _install_cast_build_widgets(self) -> None:
        # Cast editing lives in Smart Casting, while review/build operations
        # are owned by their dedicated tools. Keep these widgets alive for
        # legacy signal paths, but do not expose them in the Cast tab.
        self.cast_validation_label = QtWidgets.QLabel(
            "Cast validation has not been run.", self.cast_tab
        )
        self.cast_validation_label.hide()

        for widget in (
            self.build_preview_btn,
            self.build_shot_btn,
            self.open_review_layer_ui_btn,
            self.review_layers_btn,
            self.plan_review_publish_btn,
            self.export_beauty_playblast_btn,
            self.build_preview_table,
        ):
            widget.hide()

    def _install_preview_history_widgets(self) -> None:
        layout = self.build_preview_tab.layout()
        if layout is None:
            return
        self._clear_layout(layout)
        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        history_label = QtWidgets.QLabel("Preview History")
        history_label.setStyleSheet("font-weight: bold;")
        self.refresh_preview_history_btn = QtWidgets.QPushButton("Refresh")
        header.addWidget(history_label)
        header.addStretch(1)
        header.addWidget(self.refresh_preview_history_btn)
        self.preview_history_table = QtWidgets.QTableWidget(0, 6)
        self.preview_history_table.setHorizontalHeaderLabels(["Dept", "Package", "Version", "Updated", "Frames", "review.json"])
        self.preview_history_table.horizontalHeader().setStretchLastSection(True)
        self.preview_history_table.verticalHeader().setVisible(False)
        self.preview_history_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.preview_history_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.preview_history_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.preview_history_table.setColumnHidden(5, True)
        self.open_preview_rv_btn = QtWidgets.QPushButton("Open Package in RV")
        self.generate_review_btn = QtWidgets.QPushButton("Generate Review")
        self.generate_review_btn.setStyleSheet(
            "QPushButton { background-color: #316da8; color: white; font-weight: bold; }"
            "QPushButton:hover { background-color: #3d7fbe; }"
        )
        self.open_preview_rv_btn.setStyleSheet(
            "QPushButton { background-color: #2f6f4e; color: white; font-weight: bold; }"
            "QPushButton:hover { background-color: #3d835f; }"
        )
        layout.addLayout(header)
        layout.addWidget(self.preview_history_table, 1)
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addStretch(1)
        button_layout.addWidget(self.generate_review_btn)
        button_layout.addWidget(self.open_preview_rv_btn)
        layout.addLayout(button_layout)

    def _setup_dependencies_tab(self) -> None:
        layout = QtWidgets.QVBoxLayout(self.dependencies_tab)
        layout.setContentsMargins(8, 8, 8, 8)
        self.dependencies_target_label = QtWidgets.QLabel("Sequence Input Assignments")
        self.dependencies_target_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.dependencies_target_label)
        self.dependencies_help_label = QtWidgets.QLabel(
            "Assign source material by shot and target. Loading behavior is managed in Construct."
        )
        self.dependencies_help_label.setStyleSheet("color: #999;")
        layout.addWidget(self.dependencies_help_label)
        self.dependencies_tree.setColumnCount(6)
        self.dependencies_tree.setHeaderLabels(["Shot / Target", "Type", "Role", "Assigned Input", "Rep.", "Status"])
        self.dependencies_tree.setRootIsDecorated(True)
        self.dependencies_tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.dependencies_tree.setAlternatingRowColors(False)
        self.dependencies_tree.header().setStretchLastSection(True)
        layout.addWidget(self.dependencies_tree, 1)
        buttons = QtWidgets.QHBoxLayout()
        self.select_dependency_btn = QtWidgets.QPushButton("Assign Input")
        self.remove_dependency_btn = QtWidgets.QPushButton("Clear")
        self.preview_dependency_btn = QtWidgets.QPushButton("Preview")
        self.add_assignment_btn = QtWidgets.QPushButton("+ Add Assignment")
        for button in (self.select_dependency_btn, self.remove_dependency_btn, self.preview_dependency_btn, self.add_assignment_btn):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        lower = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        candidates_widget = QtWidgets.QWidget()
        candidates_layout = QtWidgets.QVBoxLayout(candidates_widget)
        candidates_layout.setContentsMargins(0, 4, 0, 0)
        self.dependency_context_label = QtWidgets.QLabel("Available Inputs")
        self.dependency_context_label.setStyleSheet("font-weight: bold;")
        candidates_layout.addWidget(self.dependency_context_label)
        self.dependency_candidates = QtWidgets.QTableWidget(0, 6)
        self.dependency_candidates.setHorizontalHeaderLabels(["", "Name", "Target", "Type", "Representation", "Source"])
        self.dependency_candidates.verticalHeader().setVisible(False)
        self.dependency_candidates.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.dependency_candidates.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.dependency_candidates.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.dependency_candidates.horizontalHeader().setStretchLastSection(True)
        candidates_layout.addWidget(self.dependency_candidates)
        lower.addWidget(candidates_widget)

        inspector = QtWidgets.QWidget()
        inspector_layout = QtWidgets.QFormLayout(inspector)
        self.dependency_target_value = QtWidgets.QLabel("—")
        self.dependency_type_value = QtWidgets.QLabel("—")
        self.dependency_role_value = QtWidgets.QLabel("—")
        self.dependency_source_value = QtWidgets.QLabel("—")
        self.dependency_source_value.setWordWrap(True)
        inspector_layout.addRow("Target", self.dependency_target_value)
        inspector_layout.addRow("Type", self.dependency_type_value)
        inspector_layout.addRow("Role", self.dependency_role_value)
        inspector_layout.addRow("Source", self.dependency_source_value)
        self.assign_candidate_btn = QtWidgets.QPushButton("Assign")
        inspector_layout.addRow(self.assign_candidate_btn)
        lower.addWidget(inspector)
        lower.setStretchFactor(0, 3)
        lower.setStretchFactor(1, 1)
        layout.addWidget(lower, 1)
        self.tabs.addTab(self.dependencies_tab, "Dependencies / Inputs")
        self.select_dependency_btn.clicked.connect(self.assign_selected_candidate)
        self.remove_dependency_btn.clicked.connect(self.remove_dependency)
        self.preview_dependency_btn.clicked.connect(self.preview_dependency)
        self.add_assignment_btn.clicked.connect(self.show_add_assignment_menu)
        self.assign_candidate_btn.clicked.connect(self.assign_selected_candidate)
        self.dependencies_tree.currentItemChanged.connect(lambda _current, _previous: self.populate_dependency_candidates())
        self.dependency_candidates.itemDoubleClicked.connect(lambda _item, _column: self.assign_selected_candidate())
        self.dependency_candidates.currentItemChanged.connect(lambda _current, _previous: self._update_dependency_inspector())

    def _setup_data_tree(self) -> None:
        layout = self.data_tab.layout()
        if layout is not None:
            self._clear_layout(layout)
            data_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
            data_splitter.setChildrenCollapsible(False)

            type_widget = QtWidgets.QWidget()
            type_layout = QtWidgets.QVBoxLayout(type_widget)
            type_layout.setContentsMargins(0, 0, 0, 0)
            type_layout.setSpacing(4)
            type_layout.addWidget(QtWidgets.QLabel("DataType :"))
            animation_item = QtWidgets.QListWidgetItem("Animation Curves")
            animation_item.setData(QtCore.Qt.UserRole, "animation_curve")
            self.data_type_list.addItem(animation_item)
            camera_item = QtWidgets.QListWidgetItem("Camera")
            camera_item.setData(QtCore.Qt.UserRole, "camera")
            self.data_type_list.addItem(camera_item)
            light_item = QtWidgets.QListWidgetItem("Light")
            light_item.setData(QtCore.Qt.UserRole, "light")
            self.data_type_list.addItem(light_item)
            playblast_item = QtWidgets.QListWidgetItem("Playblast Settings")
            playblast_item.setData(QtCore.Qt.UserRole, "playblast_settings")
            self.data_type_list.addItem(playblast_item)
            preview_render_item = QtWidgets.QListWidgetItem("Review Spec")
            preview_render_item.setData(QtCore.Qt.UserRole, "review_spec")
            self.data_type_list.addItem(preview_render_item)
            set_dress_item = QtWidgets.QListWidgetItem("Set Dress Work Data")
            set_dress_item.setData(QtCore.Qt.UserRole, "set_dress_data")
            self.data_type_list.addItem(set_dress_item)
            self.data_type_list.setMinimumWidth(130)
            self.data_type_list.setStyleSheet("QListWidget::item { height: 34px; }")
            type_layout.addWidget(self.data_type_list, 1)

            target_widget = QtWidgets.QWidget()
            target_layout = QtWidgets.QVBoxLayout(target_widget)
            target_layout.setContentsMargins(0, 0, 0, 0)
            target_layout.setSpacing(4)
            self.data_target_label = QtWidgets.QLabel("Name :")
            target_layout.addWidget(self.data_target_label)
            self.data_cast_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.data_cast_list.setAlternatingRowColors(True)
            self.data_cast_list.setMinimumWidth(190)
            target_layout.addWidget(self.data_cast_list, 1)

            version_widget = QtWidgets.QWidget()
            version_layout = QtWidgets.QVBoxLayout(version_widget)
            version_layout.setContentsMargins(0, 0, 0, 0)
            version_layout.setSpacing(4)
            version_layout.addWidget(self.shot_data_tree, 1)
            action_layout = QtWidgets.QHBoxLayout()
            action_layout.setContentsMargins(0, 0, 0, 0)
            action_layout.setSpacing(4)
            action_layout.addStretch(1)
            action_layout.addWidget(self.publish_animation_curves_btn)
            action_layout.addWidget(self.apply_animation_curves_btn)
            action_layout.addWidget(self.export_scene_data_btn)
            action_layout.addWidget(self.apply_scene_data_btn)
            version_layout.addLayout(action_layout)

            data_splitter.addWidget(type_widget)
            data_splitter.addWidget(target_widget)
            data_splitter.addWidget(version_widget)
            data_splitter.setStretchFactor(0, 0)
            data_splitter.setStretchFactor(1, 0)
            data_splitter.setStretchFactor(2, 1)
            layout.addWidget(data_splitter, 1)
        self.shot_data_tree.setColumnCount(4)
        self.shot_data_tree.setHeaderLabels(["Name", "Version", "Updated", "Comment"])
        self.shot_data_tree.setRootIsDecorated(True)
        self.shot_data_tree.setIndentation(10)
        self.shot_data_tree.setAlternatingRowColors(True)
        self.shot_data_tree.header().setStretchLastSection(True)
        self.shot_data_tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.shot_data_tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.data_type_list.currentRowChanged.connect(lambda _row: self._on_data_type_changed())
        self.data_cast_list.currentRowChanged.connect(lambda _row: self.populate_data_tree())
        if self.data_type_list.count():
            self.data_type_list.setCurrentRow(0)
        self._update_data_action_visibility()

    def _setup_publish_tab(self) -> None:
        insert_index = self.tabs.indexOf(self.build_preview_tab)
        if insert_index < 0:
            insert_index = self.tabs.count()
        self.tabs.insertTab(insert_index, self.publish_tab, "Publish")
        layout = QtWidgets.QVBoxLayout(self.publish_tab)
        layout.setContentsMargins(4, 4, 4, 4)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        type_widget = QtWidgets.QWidget()
        type_layout = QtWidgets.QVBoxLayout(type_widget)
        type_layout.setContentsMargins(0, 0, 0, 0)
        type_layout.setSpacing(4)
        type_layout.addWidget(QtWidgets.QLabel("Publish Type :"))
        for label, key in (
            ("Camera", "camera"),
            ("Animation Cache", "animation_cache"),
            ("Alembic Cache", "animation_alembic"),
            ("Animation Package", "animation_package"),
            ("Placements", "placements"),
            ("Set Dress", "set_dress"),
            ("Preview Render", "preview_render"),
        ):
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, key)
            self.publish_type_list.addItem(item)
        self.publish_type_list.setMinimumWidth(150)
        self.publish_type_list.setStyleSheet("QListWidget::item { height: 34px; }")
        type_layout.addWidget(self.publish_type_list, 1)

        target_widget = QtWidgets.QWidget()
        target_layout = QtWidgets.QVBoxLayout(target_widget)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.setSpacing(4)
        self.publish_target_label = QtWidgets.QLabel("Target :")
        target_layout.addWidget(self.publish_target_label)
        self.publish_target_list.setMinimumWidth(180)
        self.publish_target_list.setAlternatingRowColors(True)
        target_layout.addWidget(self.publish_target_list, 1)

        version_widget = QtWidgets.QWidget()
        version_layout = QtWidgets.QVBoxLayout(version_widget)
        version_layout.setContentsMargins(0, 0, 0, 0)
        version_layout.setSpacing(4)
        self.publish_tree.setColumnCount(5)
        self.publish_tree.setHeaderLabels(["Name", "State", "Range", "Update", "Comment"])
        self.publish_tree.setIndentation(10)
        self.publish_tree.setAlternatingRowColors(True)
        self.publish_tree.header().setStretchLastSection(True)
        self.publish_tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        version_layout.addWidget(self.publish_tree, 1)
        action_layout = QtWidgets.QHBoxLayout()
        action_layout.addStretch(1)
        for button in (
            self.apply_camera_btn,
            self.publish_camera_btn,
            self.publish_animation_cache_btn,
            self.publish_animation_alembic_btn,
            self.publish_animation_btn,
            self.build_animation_review_scene_btn,
            self.apply_set_dress_btn,
            self.publish_preview_render_btn,
        ):
            action_layout.addWidget(button)
        version_layout.addLayout(action_layout)

        splitter.addWidget(type_widget)
        splitter.addWidget(target_widget)
        splitter.addWidget(version_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 1)
        layout.addWidget(splitter)

        self.publish_type_list.currentRowChanged.connect(
            lambda _row: self._on_publish_type_changed()
        )
        self.publish_target_list.currentRowChanged.connect(
            lambda _row: self.populate_publish_tree()
        )
        if self.publish_type_list.count():
            self.publish_type_list.setCurrentRow(0)
        self._update_publish_action_visibility()

    def _setup_context_tab(self) -> None:
        layout = self.context_tab.layout()
        self.shot_context_profile_combo = QtWidgets.QComboBox()
        self.shot_context_profile_combo.addItems(["FAST", "WORK", "FINAL"])
        self.shot_context_profile_combo.setCurrentText("WORK")
        self.refresh_shot_context_btn = QtWidgets.QPushButton("Refresh Context")
        self.assemble_shot_context_btn = QtWidgets.QPushButton("Assemble Context")
        self.assemble_shot_context_btn.setStyleSheet(
            "QPushButton { background-color: #2f5f9f; color: white; font-weight: bold; }"
            "QPushButton:hover { background-color: #3b73b8; }"
        )
        self.shot_context_component_tree = QtWidgets.QTreeWidget()
        self.shot_context_component_tree.setColumnCount(7)
        self.shot_context_component_tree.setHeaderLabels(
            ["Use", "Type", "Name", "Subset", "Version", "Load Policy", "State"]
        )
        self.shot_context_component_tree.setRootIsDecorated(False)
        self.shot_context_component_tree.setAlternatingRowColors(True)
        self.shot_context_component_tree.header().setStretchLastSection(True)
        self.shot_context_component_tree.header().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        self.shot_context_component_tree.setMinimumHeight(130)
        self.shot_context_versions_tree = QtWidgets.QTreeWidget()
        self.shot_context_versions_tree.setColumnCount(3)
        self.shot_context_versions_tree.setHeaderLabels(["Version", "State", "Comment"])
        self.shot_context_versions_tree.setRootIsDecorated(False)
        self.shot_context_versions_tree.header().setStretchLastSection(True)
        self.shot_context_versions_tree.setMaximumHeight(105)
        self.shot_context_tree.setColumnCount(5)
        self.shot_context_tree.setHeaderLabels(["Item", "State", "Version", "Message", "Path"])
        self.shot_context_tree.setIndentation(10)
        self.shot_context_tree.setAlternatingRowColors(True)
        self.shot_context_tree.header().setStretchLastSection(True)
        self.shot_context_tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.shot_context_tree.header().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        self.shot_context_tree.setColumnHidden(4, True)
        self.refresh_layout_status_btn = QtWidgets.QPushButton("Refresh")
        self.build_anim_input_btn = QtWidgets.QPushButton("Build Anim Input Package")
        self.context_shot_detail_label = QtWidgets.QLabel("Selected Shot Status")
        self.context_shot_detail_label.setStyleSheet("font-weight: bold;")
        self.context_shot_detail_tree = QtWidgets.QTreeWidget()
        self.context_shot_detail_tree.setColumnCount(5)
        self.context_shot_detail_tree.setHeaderLabels(["Item", "State", "Version", "Message", "Path"])
        self.context_shot_detail_tree.setIndentation(10)
        self.context_shot_detail_tree.setAlternatingRowColors(True)
        self.context_shot_detail_tree.header().setStretchLastSection(True)
        self.context_shot_detail_tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.context_shot_detail_tree.header().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        self.context_shot_detail_tree.setColumnHidden(4, True)
        self.context_shot_detail_tree.setMaximumHeight(170)
        self.build_anim_input_btn.setStyleSheet(
            "QPushButton { background-color: #2f5f9f; color: white; font-weight: bold; }"
            "QPushButton:hover { background-color: #3b73b8; }"
        )
        if layout is not None:
            context_group = QtWidgets.QGroupBox("Shot Context USD Proxy")
            context_layout = QtWidgets.QVBoxLayout(context_group)
            context_layout.setContentsMargins(6, 6, 6, 6)
            context_header = QtWidgets.QHBoxLayout()
            context_header.addWidget(QtWidgets.QLabel("Profile"))
            context_header.addWidget(self.shot_context_profile_combo)
            context_header.addStretch(1)
            context_header.addWidget(self.refresh_shot_context_btn)
            context_header.addWidget(self.assemble_shot_context_btn)
            context_layout.addLayout(context_header)
            context_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
            context_splitter.addWidget(self.shot_context_component_tree)
            context_splitter.addWidget(self.shot_context_versions_tree)
            context_splitter.setStretchFactor(0, 1)
            context_splitter.setStretchFactor(1, 0)
            context_layout.addWidget(context_splitter)
            layout.insertWidget(0, context_group)
            header = QtWidgets.QHBoxLayout()
            self.layout_status_label = QtWidgets.QLabel("Layout Publish Status")
            header.addWidget(self.layout_status_label)
            header.addStretch(1)
            header.addWidget(self.refresh_layout_status_btn)
            header.addWidget(self.build_anim_input_btn)
            layout.insertLayout(1, header)
            layout.addWidget(self.context_shot_detail_label)
            layout.addWidget(self.context_shot_detail_tree)
            self.context_shot_detail_label.hide()
            self.context_shot_detail_tree.hide()
        self.refresh_layout_status_btn.clicked.connect(self.populate_layout_publish_status)
        self.build_anim_input_btn.clicked.connect(self.build_anim_input_package)
        self.shot_context_tree.itemClicked.connect(self._on_context_tree_item_clicked)
        self.refresh_shot_context_btn.clicked.connect(self.populate_shot_context_builder)
        self.assemble_shot_context_btn.clicked.connect(self.assemble_shot_context)
        self.shot_context_profile_combo.currentTextChanged.connect(
            lambda _text: self.populate_shot_context_builder()
        )

    def _setup_construct_tab(self) -> None:
        self.construct_tab.setObjectName("construct_tab")
        layout = QtWidgets.QVBoxLayout(self.construct_tab)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.construct_status_label = QtWidgets.QLabel("Construct")
        self.construct_status_label.setStyleSheet("font-weight: bold;")
        self.refresh_construct_btn = QtWidgets.QToolButton()
        self.refresh_construct_btn.setText("Refresh")
        self.refresh_construct_btn.setToolTip("Refresh Construct scene files")
        self.construct_from_cast_btn = QtWidgets.QPushButton("From Stage Inputs")
        self.add_construct_btn = QtWidgets.QPushButton("Add")
        self.add_fx_cache_btn = QtWidgets.QPushButton("Add FX Cache")
        self.remove_construct_btn = QtWidgets.QPushButton("Remove")
        self.save_construct_btn = QtWidgets.QPushButton("Save")
        self.open_construct_btn = QtWidgets.QPushButton("Open Construct")
        header.addWidget(self.construct_status_label)
        header.addStretch(1)
        header.addWidget(self.refresh_construct_btn)
        self.construct_stage_btn.setText("Save / Build / Open")
        self.construct_stage_btn.setToolTip("Save Construct settings, build the scene, and open it in Maya")
        header.addWidget(self.construct_stage_btn)
        header.addWidget(self.construct_from_cast_btn)
        header.addWidget(self.add_construct_btn)
        header.addWidget(self.remove_construct_btn)
        layout.addLayout(header)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        left_panel = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        left_layout.addWidget(QtWidgets.QLabel("Scene Files"))
        self.construct_scene_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.construct_scene_list.setAlternatingRowColors(True)
        left_layout.addWidget(self.construct_scene_list, 1)
        splitter.addWidget(left_panel)

        self.construct_table.setColumnCount(11)
        self.construct_table.setHorizontalHeaderLabels(
            ["Use", "Type", "Name", "Version", "Latest", "State", "Mode", "Namespace", "Path", "Required", "Note"]
        )
        self.construct_table.verticalHeader().setVisible(False)
        self.construct_table.setAlternatingRowColors(True)
        self.construct_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.construct_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.construct_table.horizontalHeader().setStretchLastSection(True)
        self.construct_table.setColumnHidden(8, True)
        splitter.addWidget(self.construct_table)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        layout.addWidget(splitter, 1)

        insert_index = self.tabs.indexOf(self.cast_tab) + 1
        self.tabs.insertTab(insert_index, self.construct_tab, "Construct")

        self.construct_stage_btn.clicked.connect(self.stage_shot_option)
        self.refresh_construct_btn.clicked.connect(
            lambda: self.populate_construct_table(force=True)
        )
        self.construct_from_cast_btn.clicked.connect(self.populate_construct_from_stage_inputs)
        self.add_construct_btn.clicked.connect(self.add_construct_row)
        self.add_fx_cache_btn.clicked.connect(self.add_fx_cache_row)
        self.remove_construct_btn.clicked.connect(self.remove_construct_row)
        self.save_construct_btn.clicked.connect(self.save_construct)
        self.open_construct_btn.clicked.connect(self.open_construct_scene)
        self.construct_scene_list.itemSelectionChanged.connect(self.populate_construct_table)
        self.construct_scene_list.itemDoubleClicked.connect(self.open_construct_scene)

    def _hide_validation_tab(self) -> None:
        index = self.tabs.indexOf(self.validation_view)
        if index >= 0:
            self.tabs.removeTab(index)

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget:
                widget.setParent(None)
            elif child_layout:
                self._clear_layout(child_layout)

    def _ui_object(self, name: str):
        obj = self.ui.findChild(QtCore.QObject, name)
        if obj is None:
            raise RuntimeError(f"UI object was not found: {name}")
        return obj

    def _apply_action_icons(self) -> None:
        icon_size = QtCore.QSize(20, 20)
        for button, icon_path in (
            (self.back_to_shots_btn, _resource_icon_path("actions", "back.svg")),
            (self.stage_shot_btn, _resource_icon_path("actions", "stage.svg")),
            (self.construct_stage_btn, _resource_icon_path("actions", "stage.svg")),
        ):
            if icon_path.exists():
                button.setIcon(QtGui.QIcon(str(icon_path)))
                button.setIconSize(icon_size)

    def _hide_detail_json_views(self) -> None:
        for name in ("shot_json_label", "shot_json_view", "cast_json_label", "cast_json_view"):
            widget = self.ui.findChild(QtWidgets.QWidget, name)
            if widget:
                widget.setVisible(False)
        work_dept_label = self.ui.findChild(QtWidgets.QWidget, "work_dept_label")
        if work_dept_label:
            work_dept_label.setVisible(False)
        self.work_dept_combo.setVisible(True)

    def _on_shot_department_selected(self, _row: int) -> None:
        item = self.shot_dept_list.currentItem()
        if not item:
            return
        index = self.work_dept_combo.findText(item.text())
        if index >= 0 and index != self.work_dept_combo.currentIndex():
            self.work_dept_combo.setCurrentIndex(index)
        else:
            self.refresh_work_files()

    def _on_detail_tab_changed(self) -> None:
        current = self.tabs.currentWidget()
        if current == self.build_preview_tab:
            self.populate_preview_history()
        elif current == self.data_tab:
            self.populate_data_tree()
        elif current == self.publish_tab:
            self.populate_publish_tree()
        elif current == self.context_tab:
            self.populate_layout_publish_status()
        elif current == self.construct_tab:
            self.populate_construct_table()

    def _on_work_department_changed(self) -> None:
        self._populate_shot_tasks()
        self._populate_shot_options()
        self.refresh_work_files()
        if self.tabs.currentWidget() == self.data_tab:
            self.populate_data_tree()
        elif self.tabs.currentWidget() == self.publish_tab:
            self.populate_publish_tree()

    def _current_shot_task(self) -> str:
        item = self.work_task_list.currentItem()
        return item.text().strip() if item and item.text().strip() else "main"

    def _populate_shot_tasks(self, keep_task: str | None = None) -> None:
        department = self.work_dept_combo.currentText().strip()
        current = keep_task or self._current_shot_task()
        tasks = self.service.shot_tasks(department) if department else ["main"]
        self.work_task_list.blockSignals(True)
        self.work_task_list.clear()
        self.work_task_list.addItems(tasks)
        row = tasks.index(current) if current in tasks else 0
        self.work_task_list.setCurrentRow(row)
        self.work_task_list.blockSignals(False)

    def _on_work_task_changed(self) -> None:
        self._populate_shot_options()
        self.refresh_work_files()

    def _current_shot_option(self, for_save: bool = False) -> str:
        item = self.shot_variant_list.currentItem()
        option = item.text().strip() if item else "main"
        if for_save and option == "all":
            return "main"
        return option or "main"

    def _populate_shot_options(self, keep_option: str | None = None) -> None:
        identity = self.current_identity()
        department = self.work_dept_combo.currentText().strip() or self.service.shot_departments[0]
        task = self._current_shot_task()
        current = keep_option or self._current_shot_option()
        options = ["all", "main"]
        if identity:
            options = ["all"] + self.service.list_shot_work_options(
                identity,
                department,
                task=task,
            )
        self.shot_variant_list.blockSignals(True)
        self.shot_variant_list.clear()
        self.shot_variant_list.addItems(options)
        selected = options.index(current) if current in options else (options.index("main") if "main" in options else 0)
        self.shot_variant_list.setCurrentRow(selected)
        self.shot_variant_list.blockSignals(False)

    def add_shot_option(self) -> None:
        identity = self.current_identity()
        if not identity:
            return
        option, accepted = QtWidgets.QInputDialog.getText(self, "Add Shot Option", "Option")
        option = option.strip()
        if not accepted or not option:
            return
        try:
            self.service.create_shot_work_option(
                identity,
                option,
                department=self.work_dept_combo.currentText().strip(),
                task=self._current_shot_task(),
            )
            self._populate_shot_options(keep_option=option)
            self.refresh_work_files()
            self.status_label.setText(f"Added shot option: {option}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Add Shot Option Failed", str(exc))

    def stage_shot_option(self) -> None:
        sequence_identity = self.current_sequence_identity()
        if sequence_identity:
            self.stage_sequence_layout(sequence_identity)
            return
        if self._current_shot_option() == "all":
            QtWidgets.QMessageBox.information(self, "Stage Shot", "Select a work option before staging.")
            return
        if not self.is_maya_session:
            QtWidgets.QMessageBox.information(self, "Stage Shot", "Shot staging is available inside Maya.")
            return
        identity = self.current_identity()
        if identity:
            try:
                self._save_construct_table_for_stage(identity)
                self.ensure_stage_construct(identity)
            except Exception:
                pass
        if (self.work_dept_combo.currentText().strip() or "").lower() == "anim":
            self.stage_anim_from_input()
            return
        self.build_shot_from_cast(stage=True)

    def stage_sequence_layout(self, sequence_identity) -> None:
        if not self.is_maya_session:
            QtWidgets.QMessageBox.information(self, "Stage Sequence", "Sequence layout staging is available inside Maya.")
            return
        try:
            import importlib
            from smartlib.dcc.maya import shot_builder

            importlib.invalidate_caches()
            shot_builder = importlib.reload(shot_builder)

            preview = self.service.build_sequence_preview(sequence_identity)
            missing_required = [item for item in preview if item.required and item.status != "resolved"]
            if missing_required:
                message = "\n".join(f"{item.cast_key}: {item.message or item.status}" for item in missing_required)
                QtWidgets.QMessageBox.warning(self, "Stage Sequence", f"Required sequence cast is not resolved:\n{message}")
                return
            sequence_data = self.service.load_sequence(sequence_identity)
            referenced = shot_builder.stage_sequence_layout_from_preview(
                [item for item in preview if item.status == "resolved"],
                sequence_data,
                project_root=self.service.paths.project_root,
            )
            self.status_label.setText(f"Staged sequence layout: {len(referenced)} references")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Stage Sequence Failed", str(exc))

    def stage_anim_from_input(self) -> None:
        identity = self.current_identity()
        if not identity:
            return
        try:
            _ensure_smartlib_on_path()
            from smartlib.dcc.maya.shot_builder import stage_anim_from_input

            anim_input = self.service.latest_anim_input(identity)
            if not anim_input:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Stage Anim",
                    "Anim input package was not found. Build it from the sequence Context tab first.",
                )
                return
            preview = self.service.build_preview_from_anim_input(identity)
            preview = self.service.filter_preview_items_for_construct(identity, preview)
            missing_required = [item for item in preview if item.required and item.status != "resolved"]
            if missing_required:
                message = "\n".join(f"{item.cast_key}: {item.message or item.status}" for item in missing_required)
                QtWidgets.QMessageBox.warning(self, "Stage Anim", f"Required cast is not resolved:\n{message}")
                return
            construct_data = self.service.load_construct(identity)
            referenced = stage_anim_from_input(
                [item for item in preview if item.status == "resolved"],
                anim_input,
                self.service.load_shot(identity),
                project_root=self.service.paths.project_root,
                construct_data=construct_data,
            )
            self.status_label.setText(f"Staged anim scene: {identity.code}, {len(referenced)} references")
            self.populate_build_preview(switch_tab=True)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Stage Anim Failed", str(exc))

    def show_detail_mode(self) -> None:
        if self.current_identity() or self.current_sequence_identity():
            self.show_current_shot()
            self.main_stack.setCurrentWidget(self.shot_detail_page)

    def open_selected_detail(self, item=None, _column=0) -> None:
        if item is not None:
            self.shot_list.setCurrentItem(item)
        self.show_detail_mode()

    def show_shot_browser(self) -> None:
        self.main_stack.setCurrentWidget(self.shot_browser_page)

    def refresh(self) -> None:
        selected = self.current_identity()
        selected_code = selected.code if selected else ""
        self.shots = self.service.list_shots()
        self.sequences = self.service.list_sequences()
        self._populate_episode_list()
        self._populate_shot_filter_tree()
        if self._current_episode():
            self._populate_sequence_table()
        else:
            self._populate_shot_table(selected_code=selected_code)
        self.status_label.setText(f"{len(self.shots)} shots")

    def _populate_episode_list(self) -> None:
        current = self._current_episode()
        episodes = sorted({sequence.episode for sequence in getattr(self, "sequences", [])} | {shot.episode for shot in self.shots})
        self.episode_listview.blockSignals(True)
        self.episode_listview.clear()
        all_item = QtWidgets.QListWidgetItem("ALL")
        all_item.setData(QtCore.Qt.UserRole, "")
        self.episode_listview.addItem(all_item)
        selected_row = 0
        for episode in episodes:
            item = QtWidgets.QListWidgetItem(episode)
            item.setData(QtCore.Qt.UserRole, episode)
            self.episode_listview.addItem(item)
            if episode == current:
                selected_row = self.episode_listview.count() - 1
        self.episode_listview.setCurrentRow(selected_row)
        self.episode_listview.blockSignals(False)

    def _current_episode(self) -> str:
        item = self.episode_listview.currentItem() if getattr(self, "episode_listview", None) else None
        return str(item.data(QtCore.Qt.UserRole) or "") if item else ""

    def _select_episode(self, episode: str) -> None:
        for row in range(self.episode_listview.count()):
            item = self.episode_listview.item(row)
            if str(item.data(QtCore.Qt.UserRole) or "") == episode:
                self.episode_listview.setCurrentRow(row)
                return
        if self.episode_listview.count():
            self.episode_listview.setCurrentRow(0)

    def _select_shot_code(self, code: str) -> None:
        if not code:
            return
        if self._current_episode():
            self._populate_shot_table(selected_code=code)
        else:
            self._populate_shot_table(selected_code=code)

    def _select_sequence_code(self, code: str) -> None:
        if not code:
            return
        parts = code.split("_", 1)
        if parts:
            self._select_episode(parts[0])
        self._populate_sequence_table(selected_code=code)

    def _on_episode_selected(self) -> None:
        episode = self._current_episode()
        self._populate_shot_filter_tree()
        if episode:
            self._populate_sequence_table()
        else:
            self._populate_shot_table()

    def _populate_shot_filter_tree(self) -> None:
        selected_filter = self._selected_shot_filter()
        self.shot_filter_tree.blockSignals(True)
        self.shot_filter_tree.clear()
        selected_item = None
        current_episode = self._current_episode()
        for identity in getattr(self, "sequences", []):
            if current_episode and identity.episode != current_episode:
                continue
            label = identity.sequence if current_episode else f"{identity.episode}/{identity.sequence}"
            sequence_item = QtWidgets.QTreeWidgetItem([label])
            sequence_item.setData(0, QtCore.Qt.UserRole, (identity.episode, identity.sequence, ""))
            self.shot_filter_tree.addTopLevelItem(sequence_item)
            if selected_filter == (identity.episode, identity.sequence, ""):
                selected_item = sequence_item
        if selected_item is not None:
            self.shot_filter_tree.setCurrentItem(selected_item)
        else:
            self.shot_filter_tree.clearSelection()
        self.shot_filter_tree.blockSignals(False)

    def _apply_shot_filter(self) -> None:
        selected = self.current_identity()
        episode, _sequence, _shot = self._selected_shot_filter()
        if episode and not _sequence and not _shot:
            self._populate_sequence_table()
            self.status_label.setText(f"Shot filter: {episode}")
            return
        self._populate_shot_table(selected_code=selected.code if selected else "")
        if episode:
            self.status_label.setText(f"Shot filter: {episode}")

    def _populate_shot_table(self, selected_code: str = "") -> None:
        self.shot_list.blockSignals(True)
        try:
            self.shot_list.clear()
            self.shot_list.setHeaderLabels(["Thumbnail", "Episode", "Sequence", "Shot", "Status", "Frames"])
            row_to_select = None
            for identity in self.shots:
                if not self._shot_matches_filter(identity):
                    continue
                data = self.service.load_shot(identity)
                editorial = data.get("editorial") or {}
                frames = ""
                if editorial:
                    frames = f"{editorial.get('cut_in', '')}-{editorial.get('cut_out', '')}"
                item = QtWidgets.QTreeWidgetItem([
                    "",
                    identity.episode,
                    identity.sequence,
                    identity.shot,
                    str(data.get("status", "")),
                    frames,
                ])
                item.setData(0, QtCore.Qt.UserRole, {"kind": "shot", "identity": identity})
                item.setToolTip(0, identity.code)
                thumbnail_path = self._shot_thumbnail_path(identity, data)
                if thumbnail_path:
                    item.setIcon(0, QtGui.QIcon(str(thumbnail_path)))
                self.shot_list.addTopLevelItem(item)
                if identity.code == selected_code:
                    row_to_select = item
            if row_to_select:
                self.shot_list.setCurrentItem(row_to_select)
            else:
                first_shot = self._first_shot_item()
                if first_shot:
                    self.shot_list.setCurrentItem(first_shot)
        finally:
            self.shot_list.blockSignals(False)
        self.shot_list.setColumnWidth(0, 110)
        self.shot_list.resizeColumnToContents(1)
        self.shot_list.resizeColumnToContents(2)

    def _populate_sequence_table(self, selected_code: str = "") -> None:
        episode = self._current_episode()
        self.shot_list.blockSignals(True)
        try:
            self.shot_list.clear()
            self.shot_list.setHeaderLabels(["Thumbnail", "Episode", "Sequence", "Shots", "Status", "Frames"])
            row_to_select = None
            for sequence in getattr(self, "sequences", []):
                if episode and sequence.episode != episode:
                    continue
                data = self.service.load_sequence(sequence)
                shots = data.get("shots") or []
                editorial = data.get("editorial") or {}
                frames = ""
                if editorial:
                    frames = f"{editorial.get('cut_in', '')}-{editorial.get('cut_out', '')}"
                item = QtWidgets.QTreeWidgetItem([
                    "",
                    sequence.episode,
                    sequence.sequence,
                    str(len(shots)),
                    str(data.get("status", "")),
                    frames,
                ])
                item.setData(0, QtCore.Qt.UserRole, {"kind": "sequence", "identity": sequence})
                item.setToolTip(0, sequence.code)
                self.shot_list.addTopLevelItem(item)
                if sequence.code == selected_code:
                    row_to_select = item
            if row_to_select:
                self.shot_list.setCurrentItem(row_to_select)
            elif self.shot_list.topLevelItemCount():
                self.shot_list.setCurrentItem(self.shot_list.topLevelItem(0))
        finally:
            self.shot_list.blockSignals(False)
        self.shot_list.setColumnWidth(0, 110)
        self.shot_list.resizeColumnToContents(1)
        self.shot_list.resizeColumnToContents(2)

    def _selected_shot_filter(self) -> tuple[str, str, str]:
        item = self.shot_filter_tree.currentItem()
        data = item.data(0, QtCore.Qt.UserRole) if item else None
        if isinstance(data, tuple) and len(data) == 3:
            return tuple(str(value) for value in data)
        return "", "", ""

    def _shot_matches_filter(self, identity) -> bool:
        episode, sequence, shot = self._selected_shot_filter()
        if episode and identity.episode != episode:
            return False
        if sequence and identity.sequence != sequence:
            return False
        return True

    def current_identity(self):
        item = self.shot_list.currentItem()
        if not item:
            return None
        data = item.data(0, QtCore.Qt.UserRole)
        if isinstance(data, dict):
            return data.get("identity") if data.get("kind") == "shot" else None
        return data

    def current_sequence_identity(self):
        item = self.shot_list.currentItem()
        if not item:
            return None
        data = item.data(0, QtCore.Qt.UserRole)
        if isinstance(data, dict) and data.get("kind") == "sequence":
            return data.get("identity")
        return None

    def current_token_context(self, **overrides):
        _ensure_smartlib_on_path()
        from smartlib.core.tokens import TokenContext

        identity = self.active_shot_identity or self.current_identity()
        sequence_identity = self.active_sequence_identity or self.current_sequence_identity()
        values = {
            "project_root": self.service.paths.project_root,
            "project_name": getattr(self.service.project_config, "project_name", "") or self.service.paths.project_name,
            "department": self.work_dept_combo.currentText().strip() or "",
            "task": self._current_shot_task(),
            "tool": "maya" if self.is_maya_session else "",
            "subset": self._current_shot_option(for_save=True),
        }
        if identity:
            values.update(
                {
                    "episode": identity.episode,
                    "sequence": identity.sequence,
                    "shot": identity.shot,
                }
            )
        elif sequence_identity:
            values.update(
                {
                    "episode": sequence_identity.episode,
                    "sequence": sequence_identity.sequence,
                }
            )
        values.update(overrides)
        return TokenContext.from_mapping(values)

    def show_current_shot(self) -> None:
        sequence_identity = self.current_sequence_identity()
        if sequence_identity:
            self._show_current_sequence(sequence_identity)
            return
        identity = self.current_identity()
        if not identity:
            self.active_shot_identity = None
            self.active_sequence_identity = None
            self.detail_title_label.setText("Shot Detail")
            self.shot_thumbnail_label.clear()
            self.shot_thumbnail_label.setText("Thumbnail")
            self.shot_info_table.setRowCount(0)
            self.shot_json_view.clear()
            self.cast_table.setRowCount(0)
            self.cast_json_view.clear()
            self.validation_view.clear()
            self.build_preview_table.setRowCount(0)
            self.preview_history_table.setRowCount(0)
            self.work_table.setRowCount(0)
            self.shot_data_tree.clear()
            self.data_cast_list.clear()
            self.publish_tree.clear()
            self.publish_target_list.clear()
            self.shot_context_tree.clear()
            self.construct_table.setRowCount(0)
            self.construct_status_label.setText("Construct")
            self.dependencies_tree.clear()
            self.dependencies_target_label.setText("Shot Dependencies")
            self._populate_shot_options(keep_option="main")
            return
        self._show_current_shot_identity(identity)

    def _show_current_shot_identity(self, identity) -> None:
        self.active_shot_identity = identity
        self.active_sequence_identity = None
        shot_data = self.service.load_shot(identity)
        cast_data = self.service.load_cast(identity)
        editorial = shot_data.get("editorial") or {}
        frames = ""
        if editorial.get("cut_in") is not None and editorial.get("cut_out") is not None:
            frames = f" | {editorial.get('cut_in')} - {editorial.get('cut_out')}"
        status = shot_data.get("status", "")
        status_text = f" | {status}" if status else ""
        self.detail_title_label.setText(f"{identity.episode} / {identity.sequence} / {identity.shot}{frames}{status_text}")
        self._populate_shot_thumbnail(identity, shot_data)
        self._populate_shot_info_table(identity, shot_data, cast_data)
        self.shot_json_view.setPlainText(json.dumps(shot_data, indent=2, ensure_ascii=False))
        self.cast_json_view.setPlainText(json.dumps(cast_data, indent=2, ensure_ascii=False))
        self.populate_cast_table(cast_data)
        self.populate_data_cast_list(cast_data)
        self._populate_shot_options()
        self.refresh_work_files()
        self.validate_current_cast(update_tab=False)
        self.populate_build_preview(switch_tab=False)
        self.populate_preview_history()
        self.populate_data_tree()
        self._populate_publish_targets()
        self.populate_publish_tree()
        self.populate_layout_publish_status()
        self.populate_construct_table()
        self.populate_dependencies()

    def _show_current_sequence(self, sequence_identity) -> None:
        self.active_sequence_identity = sequence_identity
        self.active_shot_identity = None
        sequence_data = self.service.load_sequence(sequence_identity)
        cast_data = self.service.load_sequence_cast(sequence_identity.episode, sequence_identity.sequence)
        editorial = sequence_data.get("editorial") or {}
        frames = ""
        if editorial.get("cut_in") is not None and editorial.get("cut_out") is not None:
            frames = f" | {editorial.get('cut_in')} - {editorial.get('cut_out')}"
        status = sequence_data.get("status", "")
        status_text = f" | {status}" if status else ""
        self.detail_title_label.setText(f"{sequence_identity.episode} / {sequence_identity.sequence} / Sequence{frames}{status_text}")
        self.shot_thumbnail_label.clear()
        self.shot_thumbnail_label.setText("Sequence")
        self._populate_sequence_info_table(sequence_identity, sequence_data, cast_data)
        self.shot_json_view.setPlainText(json.dumps(sequence_data, indent=2, ensure_ascii=False))
        self.cast_json_view.setPlainText(json.dumps(cast_data, indent=2, ensure_ascii=False))
        self.populate_cast_table(cast_data)
        self.populate_data_cast_list(cast_data)
        self.refresh_work_files()
        self.populate_sequence_build_preview(sequence_identity)
        self.populate_preview_history()
        self.populate_data_tree()
        self._populate_publish_targets()
        self.populate_publish_tree()
        self.populate_layout_publish_status()
        self.populate_construct_table()
        self.cast_validation_label.setText("Sequence cast")
        self.populate_dependencies()

    def _dependency_identity(self):
        return self.active_shot_identity or self.active_sequence_identity

    def populate_dependencies(self) -> None:
        self.dependencies_tree.clear()
        self.dependency_candidates.setRowCount(0)
        identity = self._dependency_identity()
        enabled = identity is not None
        for button in (self.select_dependency_btn, self.remove_dependency_btn, self.preview_dependency_btn, self.assign_candidate_btn, self.add_assignment_btn):
            button.setEnabled(enabled)
        if not identity:
            return
        try:
            if hasattr(identity, "shot"):
                shot_identities = [identity]
                sequence_identity = type(self.active_sequence_identity)(identity.episode, identity.sequence) if self.active_sequence_identity else None
                if sequence_identity is None:
                    _ensure_smartlib_on_path()
                    from smartlib.apps.shot_manager import SequenceIdentity
                    sequence_identity = SequenceIdentity(identity.episode, identity.sequence)
                self.dependencies_target_label.setText(f"Shot Input Assignments — {identity.shot}")
            else:
                sequence_identity = identity
                shot_identities = self.service.sequence_shot_identities(identity)
                self.dependencies_target_label.setText(f"Sequence Input Assignments — {identity.episode} / {identity.sequence}")
            cast_data = self.service.load_sequence_cast(sequence_identity.episode, sequence_identity.sequence)
            cast = cast_data.get("cast") or {}
            character_targets = sorted(
                {str(entry.get("asset") or key) for key, entry in cast.items() if str(entry.get("role") or "").upper() == "CHA"},
                key=str.lower,
            )
            for shot_identity in shot_identities:
                data = self.service.load_dependencies(shot_identity)
                entries = list(data.get("dependencies") or [])
                slots = [(target, "mocap", "body_motion") for target in character_targets]
                slots.extend((("Camera", "virtual_camera", "import_fbx"), ("Shot", "audio", "editorial_mix")))
                existing_slots = {
                    (str(item.get("target") or item.get("asset") or "Shot"), str(item.get("type") or ""), str(item.get("role") or ""))
                    for item in entries
                }
                slots.extend(sorted(existing_slots - set(slots)))
                parent = QtWidgets.QTreeWidgetItem([shot_identity.shot])
                parent.setData(0, QtCore.Qt.UserRole, {"kind": "shot", "identity": shot_identity})
                font = parent.font(0)
                font.setBold(True)
                parent.setFont(0, font)
                self.dependencies_tree.addTopLevelItem(parent)
                missing = 0
                for target, dependency_type, role in slots:
                    group_entries = [
                        item for item in entries
                        if str(item.get("target") or item.get("asset") or "Shot") == target
                        and item.get("type") == dependency_type
                        and (
                            item.get("role") == role
                            or (
                                dependency_type == "virtual_camera"
                                and item.get("role") == "camera_reference"
                            )
                        )
                    ]
                    dependency = next((item for item in group_entries if item.get("status") == "selected"), None)
                    if dependency is None:
                        missing += 1
                    item = QtWidgets.QTreeWidgetItem([
                        target, dependency_type.replace("_", " ").title(), self._dependency_role_label(role),
                        str((dependency or {}).get("name") or (dependency or {}).get("id") or "—"),
                        str((dependency or {}).get("representation") or ""),
                        str((dependency or {}).get("status") or "Missing"),
                    ])
                    item.setData(0, QtCore.Qt.UserRole, {
                        "kind": "assignment", "identity": shot_identity, "target": target,
                        "type": dependency_type, "role": role, "dependency": dict(dependency or {}),
                    })
                    if dependency is None:
                        item.setForeground(5, QtGui.QBrush(QtGui.QColor("#d6a84b")))
                    parent.addChild(item)
                parent.setText(5, f"{len(slots) - missing} inputs · " + ("Ready" if not missing else f"{missing} missing"))
                parent.setForeground(5, QtGui.QBrush(QtGui.QColor("#70bd75" if not missing else "#d6a84b")))
                parent.setExpanded(True)
        except Exception as exc:
            self.status_label.setText(f"Dependencies load failed: {exc}")
            return
        self.dependencies_tree.resizeColumnToContents(0)
        for column in range(1, 6):
            self.dependencies_tree.resizeColumnToContents(column)

    def _selected_dependency_assignment(self) -> dict | None:
        item = self.dependencies_tree.currentItem()
        data = item.data(0, QtCore.Qt.UserRole) if item else None
        return dict(data) if isinstance(data, dict) and data.get("kind") == "assignment" else None

    @staticmethod
    def _dependency_role_label(role) -> str:
        clean_role = str(role or "").strip().lower()
        if clean_role in {"import_fbx", "camera_reference"}:
            return "Import FBX"
        return clean_role.replace("_", " ").title() if clean_role else "—"

    def show_add_assignment_menu(self) -> None:
        current = self.dependencies_tree.currentItem()
        if current is None:
            return
        data = current.data(0, QtCore.Qt.UserRole)
        parent = current.parent() if isinstance(data, dict) and data.get("kind") == "assignment" else current
        parent_data = parent.data(0, QtCore.Qt.UserRole) if parent else None
        if not isinstance(parent_data, dict) or parent_data.get("kind") != "shot":
            return
        identity = parent_data["identity"]
        cast = self.service.load_sequence_cast(identity.episode, identity.sequence).get("cast") or {}
        characters = sorted(
            {str(entry.get("asset") or key) for key, entry in cast.items() if str(entry.get("role") or "").upper() == "CHA"},
            key=str.lower,
        )
        choices = [(target, "audio", "dialogue_iso", f"{target} / Audio / Dialogue ISO") for target in characters]
        choices.append(("Shot", "reference", "reference", "Shot / Reference"))
        menu = QtWidgets.QMenu(self)
        for target, dependency_type, role, label in choices:
            action = menu.addAction(label)
            action.setData((target, dependency_type, role))
        chosen = _exec_menu(menu, self.add_assignment_btn.mapToGlobal(QtCore.QPoint(0, self.add_assignment_btn.height())))
        if chosen is None:
            return
        target, dependency_type, role = chosen.data()
        for index in range(parent.childCount()):
            payload = parent.child(index).data(0, QtCore.Qt.UserRole)
            if isinstance(payload, dict) and (payload.get("target"), payload.get("type"), payload.get("role")) == (target, dependency_type, role):
                self.dependencies_tree.setCurrentItem(parent.child(index))
                return
        item = QtWidgets.QTreeWidgetItem([
            target, dependency_type.replace("_", " ").title(), role.replace("_", " ").title(), "—", "", "Missing",
        ])
        item.setData(0, QtCore.Qt.UserRole, {
            "kind": "assignment", "identity": identity, "target": target,
            "type": dependency_type, "role": role, "dependency": {},
        })
        item.setForeground(5, QtGui.QBrush(QtGui.QColor("#d6a84b")))
        parent.addChild(item)
        parent.setExpanded(True)
        self.dependencies_tree.setCurrentItem(item)

    def populate_dependency_candidates(self) -> None:
        self.dependency_candidates.setRowCount(0)
        assignment = self._selected_dependency_assignment()
        if not assignment:
            self.dependency_context_label.setText("Available Inputs")
            return
        self.dependency_context_label.setText(
            f"Available Inputs — {assignment['identity'].shot} / {assignment['target']} / {assignment['role'].replace('_', ' ').title()}"
        )
        try:
            _ensure_smartlib_on_path()
            from smartlib.apps.shot_manager import SequenceIdentity
            sequence_identity = SequenceIdentity(assignment["identity"].episode, assignment["identity"].sequence)
            candidates = self.service.sequence_input_candidates(sequence_identity)
            for candidate in candidates:
                if candidate.get("type") != assignment.get("type"):
                    continue
                if assignment.get("type") == "virtual_camera" and candidate.get("representation") != "fbx":
                    continue
                if assignment.get("type") == "mocap" and candidate.get("target") != assignment.get("target"):
                    continue
                row = self.dependency_candidates.rowCount()
                self.dependency_candidates.insertRow(row)
                selected = candidate.get("source") == (assignment.get("dependency") or {}).get("source")
                source = str(candidate.get("source") or "")
                source_name = Path(source).name or source
                values = ["●" if selected else "○", candidate.get("name"), candidate.get("target"), candidate.get("type"), candidate.get("representation"), source_name]
                for column, value in enumerate(values):
                    table_item = QtWidgets.QTableWidgetItem(str(value or ""))
                    table_item.setData(QtCore.Qt.UserRole, dict(candidate))
                    if column == 5:
                        table_item.setToolTip(source)
                    self.dependency_candidates.setItem(row, column, table_item)
                if selected:
                    self.dependency_candidates.selectRow(row)
            self.dependency_candidates.resizeColumnsToContents()
        except Exception as exc:
            self.status_label.setText(f"Input discovery failed: {exc}")
        self._update_dependency_inspector()

    def _selected_dependency_candidate(self) -> dict | None:
        row = self.dependency_candidates.currentRow()
        item = self.dependency_candidates.item(row, 0) if row >= 0 else None
        data = item.data(QtCore.Qt.UserRole) if item else None
        return dict(data) if isinstance(data, dict) else None

    def _update_dependency_inspector(self) -> None:
        assignment = self._selected_dependency_assignment() or {}
        candidate = self._selected_dependency_candidate() or assignment.get("dependency") or {}
        self.dependency_target_value.setText(str(assignment.get("target") or "—"))
        self.dependency_type_value.setText(str(assignment.get("type") or "—"))
        self.dependency_role_value.setText(self._dependency_role_label(assignment.get("role")))
        self.dependency_source_value.setText(str(candidate.get("source") or "—"))
        shot = getattr(assignment.get("identity"), "shot", "")
        self.assign_candidate_btn.setText(f"Assign to {assignment.get('target')} ({shot})" if shot else "Assign")

    def assign_selected_candidate(self) -> None:
        assignment = self._selected_dependency_assignment()
        candidate = self._selected_dependency_candidate()
        if not assignment or not candidate:
            return
        try:
            identity = assignment["identity"]
            data = self.service.load_dependencies(identity)
            entries = list(data.get("dependencies") or [])
            for entry in entries:
                entry_target = str(entry.get("target") or entry.get("asset") or "Shot")
                same_role = entry.get("role") == assignment["role"] or (
                    assignment["type"] == "virtual_camera"
                    and entry.get("role") == "camera_reference"
                )
                if entry_target == assignment["target"] and entry.get("type") == assignment["type"] and same_role:
                    entry["status"] = "alternate"
            dependency = dict(candidate)
            dependency.update({
                "target": assignment["target"], "asset": assignment["target"],
                "type": assignment["type"], "role": assignment["role"], "status": "selected",
            })
            if assignment["type"] == "virtual_camera":
                dependency["mode"] = "import"
            existing = next((item for item in entries if item.get("id") == dependency["id"]), None)
            if existing is None:
                entries.append(dependency)
            else:
                existing.update(dependency)
            path = self.service.write_dependencies(identity, {"dependencies": entries})
            self.status_label.setText(f"Assigned {candidate.get('name')} to {identity.shot}/{assignment['target']}: {path}")
            self.populate_dependencies()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Assign Input Failed", str(exc))

    def remove_dependency(self) -> None:
        assignment = self._selected_dependency_assignment()
        dependency = (assignment or {}).get("dependency") or {}
        if not assignment or not dependency:
            return
        answer = QtWidgets.QMessageBox.question(self, "Clear Input", f"Clear {assignment['identity'].shot} / {assignment['target']} / {assignment['role']}?")
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            identity = assignment["identity"]
            entries = [item for item in self.service.load_dependencies(identity).get("dependencies", []) if item.get("id") != dependency.get("id")]
            path = self.service.write_dependencies(identity, {"dependencies": entries})
            self.status_label.setText(f"Cleared input: {path}")
            self.populate_dependencies()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Clear Input Failed", str(exc))

    def preview_dependency(self) -> None:
        dependency = self._selected_dependency_candidate() or (self._selected_dependency_assignment() or {}).get("dependency")
        if not dependency:
            return
        source = str(dependency.get("source") or "").strip()
        local_path = Path(source)
        if local_path.exists():
            target = local_path if local_path.is_dir() else local_path.parent
            os.startfile(str(target))
            return
        if not QtGui.QDesktopServices.openUrl(QtCore.QUrl(source)):
            QtWidgets.QApplication.clipboard().setText(source)
            QtWidgets.QMessageBox.information(self, "Preview Dependency", f"No preview handler is registered. Source copied to clipboard:\n{source}")

    def set_edit_time_range(self) -> None:
        if not self.is_maya_session:
            QtWidgets.QMessageBox.information(self, "Set Edit Range", "Set Edit Range is available inside Maya.")
            return
        identity = self.active_shot_identity or self.current_identity()
        sequence_identity = None if identity else (self.active_sequence_identity or self.current_sequence_identity())
        if identity:
            data = self.service.load_shot(identity)
            label = identity.shot
        elif sequence_identity:
            data = self.service.load_sequence(sequence_identity)
            label = sequence_identity.sequence
        else:
            QtWidgets.QMessageBox.information(self, "Set Edit Range", "Select a shot or sequence first.")
            return
        editorial = data.get("editorial") or {}
        cut_in = editorial.get("cut_in")
        cut_out = editorial.get("cut_out")
        if cut_in is None or cut_out is None:
            QtWidgets.QMessageBox.warning(self, "Set Edit Range", "editorial.cut_in / cut_out is not set.")
            return
        try:
            start = float(cut_in)
            end = float(cut_out)
            if end < start:
                raise ValueError(f"Invalid edit range: {cut_in} - {cut_out}")
            import maya.cmds as cmds

            fps = editorial.get("fps")
            if fps:
                fps_map = {
                    24: "film",
                    25: "pal",
                    30: "ntsc",
                    48: "show",
                    50: "palf",
                    60: "ntscf",
                }
                fps_int = int(float(fps))
                cmds.currentUnit(time=fps_map.get(fps_int, f"{fps_int}fps"))
            cmds.playbackOptions(minTime=start, animationStartTime=start)
            cmds.playbackOptions(maxTime=end, animationEndTime=end)
            cmds.currentTime(start, edit=True)
            self.status_label.setText(f"Set edit range: {label} {int(start)}-{int(end)}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Set Edit Range Failed", str(exc))

    def populate_sequence_build_preview(self, sequence_identity) -> None:
        preview = self.service.build_sequence_preview(sequence_identity)
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
                    table_item.setBackground(QtGui.QColor(120, 90, 35) if not item.required else QtGui.QColor(120, 58, 45))
                self.build_preview_table.setItem(row, column, table_item)
        self.build_preview_table.resizeColumnsToContents()
        self.build_preview_table.setColumnWidth(7, 120)

    def _populate_sequence_info_table(self, sequence_identity, sequence_data: dict, cast_data: dict) -> None:
        editorial = sequence_data.get("editorial") or {}
        cast = cast_data.get("cast") or {}
        rows = [
            ("Sequence", f"{sequence_identity.episode}/{sequence_identity.sequence}"),
            ("Status", sequence_data.get("status", "")),
            ("FPS", editorial.get("fps", "")),
            ("Cut", f"{editorial.get('cut_in', '')} - {editorial.get('cut_out', '')}"),
            ("Shots", len(sequence_data.get("shots") or [])),
            ("Cast", len(cast)),
        ]
        self.shot_info_table.setRowCount(0)
        for label, value in rows:
            row = self.shot_info_table.rowCount()
            self.shot_info_table.insertRow(row)
            self.shot_info_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(label)))
            self.shot_info_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(value)))
        self.shot_info_table.resizeColumnsToContents()

    def _populate_shot_thumbnail(self, identity, shot_data: dict) -> None:
        path = self._shot_thumbnail_path(identity, shot_data)
        if not path:
            self.shot_thumbnail_label.clear()
            self.shot_thumbnail_label.setText("Thumbnail")
            return
        pixmap = QtGui.QPixmap(str(path))
        self.shot_thumbnail_label.setPixmap(
            pixmap.scaled(
                self.shot_thumbnail_label.size(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
        )

    def _shot_thumbnail_path(self, identity, shot_data: dict) -> Path | None:
        candidates = []
        thumbnail = str(shot_data.get("thumbnail") or "").strip()
        if thumbnail:
            candidates.append(Path(thumbnail))
            candidates.append(self.service.shot_root(identity) / thumbnail)
        candidates.extend(
            self.service.shot_root(identity) / name
            for name in ("thumbnail.jpg", "thumbnail.jpeg", "thumbnail.png")
        )
        return next((candidate for candidate in candidates if candidate.exists()), None)

    def _populate_shot_info_table(self, identity, shot_data: dict, cast_data: dict) -> None:
        editorial = shot_data.get("editorial") or {}
        cast = cast_data.get("cast") or {}
        rows = [
            ("Shot", identity.shot),
            ("Sequence", f"{identity.episode}/{identity.sequence}"),
            ("Status", shot_data.get("status", "")),
            ("FPS", editorial.get("fps", shot_data.get("fps", ""))),
            ("Cut", f"{editorial.get('cut_in', '')} - {editorial.get('cut_out', '')}"),
            ("Cast", len(cast)),
        ]
        self.shot_info_table.setRowCount(0)
        for label, value in rows:
            row = self.shot_info_table.rowCount()
            self.shot_info_table.insertRow(row)
            self.shot_info_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(label)))
            self.shot_info_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(value)))
        self.shot_info_table.resizeColumnsToContents()

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
        self.cast_validation_label.setText(text)
        self._apply_cast_validation_colors(issues)
        if update_tab:
            self.tabs.setCurrentWidget(self.cast_tab)
            self.status_label.setText(text.splitlines()[0])

    def show_build_preview(self) -> None:
        self.populate_build_preview(switch_tab=True)

    def build_shot_from_cast(self, stage: bool = False) -> None:
        identity = self.current_identity()
        if not identity:
            return
        preview = self.service.build_preview(
            identity,
            department=self.work_dept_combo.currentText().strip() or "default",
        )
        if stage:
            preview = self.service.filter_preview_items_for_construct(identity, preview)
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
            if stage:
                from smartlib.dcc.maya.shot_builder import stage_shot_from_preview

                referenced = stage_shot_from_preview(
                    resolved,
                    self.service.load_shot(identity),
                    department=self.work_dept_combo.currentText().strip() or "layout",
                    project_root=self.service.project_config.project_root,
                )
                self.status_label.setText(f"Staged {identity.code}: referenced {len(referenced)} assets")
            else:
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
        preview = self.service.build_preview(
            identity,
            department=self.work_dept_combo.currentText().strip() or "default",
        )
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
            self.tabs.setCurrentWidget(self.build_preview_tab)

    def populate_preview_history(self) -> None:
        identity = self.active_shot_identity or self.current_identity()
        sequence_identity = self.active_sequence_identity or self.current_sequence_identity()
        self.preview_history_table.setRowCount(0)
        if not identity and not sequence_identity:
            return
        rows = self._sequence_preview_history_rows(sequence_identity) if sequence_identity else self._preview_history_rows(identity)
        for row_data in rows:
            row = self.preview_history_table.rowCount()
            self.preview_history_table.insertRow(row)
            values = [
                row_data["department"],
                row_data["package"],
                row_data["version"],
                row_data["updated"],
                row_data["frames"],
                str(row_data["review_json"]),
            ]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setData(QtCore.Qt.UserRole, str(row_data["review_json"]))
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                self.preview_history_table.setItem(row, column, item)
        self.preview_history_table.resizeColumnsToContents()
        self.preview_history_table.horizontalHeader().setStretchLastSection(True)
        self.preview_history_table.setColumnHidden(5, True)
        if self.preview_history_table.rowCount():
            self.preview_history_table.setCurrentCell(0, 0)

    def populate_data_tree(self) -> None:
        identity = self.active_shot_identity or self.current_identity()
        sequence_identity = self.active_sequence_identity or self.current_sequence_identity()
        self.shot_data_tree.clear()
        if sequence_identity:
            department = self.work_dept_combo.currentText().strip() or "layout"
            rows = self.service.list_sequence_data_versions(
                sequence_identity,
                department=department,
            )
            published_animation_sources = set()
        elif identity:
            rows = self.service.list_shot_data_versions(identity)
            rows.extend(self.service.list_set_dress_data(identity))
            published_animation_sources = self.service.published_animation_source_paths(identity)
        else:
            return

        data_types = (
            ("animation", "Animation Curves"),
            ("camera", "Camera"),
            ("light", "Light"),
            ("playblast_settings", "Playblast Settings"),
            ("review_spec", "Review Spec"),
            ("set_dress_data", "Set Dress Work Data"),
        )
        published_icon = self.style().standardIcon(QtWidgets.QStyle.SP_DialogApplyButton)
        for data_prefix, label in data_types:
            section = QtWidgets.QTreeWidgetItem([label, "", "", ""])
            section.setFirstColumnSpanned(True)
            section_font = section.font(0)
            section_font.setBold(True)
            section.setFont(0, section_font)
            self.shot_data_tree.addTopLevelItem(section)

            matching_rows = [
                row for row in rows
                if str(row.name or "").split("/", 1)[0] == data_prefix
            ]
            name_items = {}
            for row_data in matching_rows:
                name_parts = str(row_data.name or "").split("/")
                display_parts = name_parts[1:]
                if len(display_parts) > 1 and display_parts[-1] in {"main", "curves"}:
                    display_parts = display_parts[:-1]
                display_name = "/".join(display_parts) or "main"
                name_item = name_items.get(display_name)
                if name_item is None:
                    name_item = QtWidgets.QTreeWidgetItem([display_name, "", "", ""])
                    section.addChild(name_item)
                    name_items[display_name] = name_item

                version_labels = self._data_version_labels(row_data)
                version_item = QtWidgets.QTreeWidgetItem([
                    row_data.version,
                    " / ".join(version_labels),
                    row_data.updated,
                    row_data.comment,
                ])
                version_item.setData(0, QtCore.Qt.UserRole, row_data.path)
                version_item.setData(0, QtCore.Qt.UserRole + 1, data_prefix)
                if self._data_version_is_published_animation(row_data.path, published_animation_sources):
                    version_item.setIcon(1, published_icon)
                    version_item.setToolTip(1, "Published animation source")
                name_item.addChild(version_item)

        self.shot_data_tree.expandAll()
        self.shot_data_tree.resizeColumnToContents(1)
        self.shot_data_tree.resizeColumnToContents(2)

    def _current_data_type(self) -> str:
        item = self.data_type_list.currentItem() if getattr(self, "data_type_list", None) else None
        if not item:
            return "animation_curve"
        key = item.data(QtCore.Qt.UserRole)
        return str(key or item.text()).strip().lower().replace(" ", "_")

    def _current_data_target(self) -> str:
        item = self.data_cast_list.currentItem() if getattr(self, "data_cast_list", None) else None
        if not item:
            return ""
        data = item.data(QtCore.Qt.UserRole)
        if isinstance(data, dict):
            return str(data.get("cast_key") or data.get("target") or item.text()).strip()
        return item.text().strip()

    def _on_data_type_changed(self) -> None:
        self._update_data_action_visibility()
        data_type = self._current_data_type()
        if data_type == "animation_curve":
            cast_data = self.service.load_cast(self.active_shot_identity) if self.active_shot_identity else (
                self.service.load_sequence_cast(self.active_sequence_identity.episode, self.active_sequence_identity.sequence)
                if self.active_sequence_identity else {"cast": {}}
            )
            self.populate_data_cast_list(cast_data)
        elif data_type in {"review_spec", "playblast_settings"}:
            self._populate_preview_render_targets()
        elif data_type in {"camera", "light"}:
            self._populate_scene_data_targets(data_type)
        else:
            self._populate_data_targets_from_existing_rows()
        self.populate_data_tree()

    def _update_data_action_visibility(self) -> None:
        data_type = self._current_data_type()
        is_animation = data_type == "animation_curve"
        is_scene_data = data_type in {"camera", "light", "playblast_settings"}
        for button in (
            self.publish_animation_curves_btn,
            self.apply_animation_curves_btn,
        ):
            button.setVisible(is_animation)
        self.export_scene_data_btn.setVisible(is_scene_data)
        self.apply_scene_data_btn.setVisible(is_scene_data)
        self.build_animation_package_btn.setVisible(False)
        if hasattr(self, "data_target_label"):
            labels = {
                "animation_curve": "Cast :",
                "review_spec": "Department :",
                "set_dress_data": "Package :",
                "camera": "Root :",
                "light": "Root :",
                "playblast_settings": "Department :",
            }
            self.data_target_label.setText(labels.get(data_type, "Name :"))

    def _current_publish_type(self) -> str:
        item = self.publish_type_list.currentItem()
        if not item:
            return "camera"
        return str(item.data(QtCore.Qt.UserRole) or item.text()).strip().lower()

    def _current_publish_target(self) -> str:
        item = self.publish_target_list.currentItem()
        if not item:
            return ""
        data = item.data(QtCore.Qt.UserRole)
        if isinstance(data, dict):
            return str(data.get("cast_key") or data.get("target") or item.text()).strip()
        return item.text().strip()

    def _on_publish_type_changed(self) -> None:
        self._populate_publish_targets()
        self._update_publish_action_visibility()
        self.populate_publish_tree()

    def _update_publish_action_visibility(self) -> None:
        publish_type = self._current_publish_type()
        self.apply_camera_btn.setVisible(publish_type == "camera")
        self.publish_camera_btn.setVisible(publish_type == "camera")
        self.publish_animation_cache_btn.setVisible(
            publish_type == "animation_cache"
        )
        self.publish_animation_alembic_btn.setVisible(
            publish_type == "animation_alembic"
        )
        self.publish_animation_btn.setVisible(
            publish_type == "animation_package"
        )
        self.build_animation_review_scene_btn.setVisible(
            publish_type == "animation_package"
        )
        self.apply_set_dress_btn.setVisible(publish_type == "set_dress")
        self.publish_preview_render_btn.setVisible(
            publish_type == "preview_render"
        )
        labels = {
            "camera": "Camera :",
            "animation_cache": "Cast :",
            "animation_alembic": "Cast :",
            "animation_package": "Package :",
            "placements": "Target :",
            "set_dress": "Package :",
            "preview_render": "Department :",
        }
        self.publish_target_label.setText(labels.get(publish_type, "Target :"))

    def _populate_publish_targets(self) -> None:
        identity = self.active_shot_identity or self.current_identity()
        sequence_identity = self.active_sequence_identity or self.current_sequence_identity()
        publish_type = self._current_publish_type()
        current = self._current_publish_target()
        targets: list[tuple[str, dict]] = []
        if publish_type in {"animation_cache", "animation_alembic"} and identity:
            cast_data = self.service.load_cast(identity)
            for cast_key, entry in sorted((cast_data.get("cast") or {}).items()):
                data = dict(entry)
                data["cast_key"] = cast_key
                targets.append((cast_key, data))
        elif publish_type == "camera" and identity:
            names = {}
            if self.is_maya_session:
                try:
                    from smartlib.dcc.maya.shot_scene_data import list_scene_cameras

                    for name in list_scene_cameras():
                        names[self._data_target_token(name)] = str(name)
                except Exception:
                    pass
            for row in self.service.list_shot_scene_publish_versions(identity, "camera"):
                parts = row.name.split("/")
                if len(parts) > 1:
                    names.setdefault(parts[1], parts[1])
            targets = [(name, {"target": value}) for name, value in sorted(names.items())]
        elif publish_type == "animation_package":
            targets = [("main", {"target": "main"})]
        elif publish_type == "placements":
            targets = [("main", {"target": "main"})]
        elif publish_type == "set_dress":
            rows = (
                self.service.list_sequence_set_dress_publish_versions(sequence_identity)
                if sequence_identity
                else self.service.list_set_dress_publish_versions(identity)
                if identity
                else []
            )
            names = sorted({row.name.split("/", 1)[-1] for row in rows})
            targets = [(name, {"target": name}) for name in names]
        elif publish_type == "preview_render" and identity:
            departments = {
                self.work_dept_combo.currentText().strip() or "default"
            }
            for row in self.service.list_review_spec_versions(identity):
                parts = row.name.split("/")
                if len(parts) > 1:
                    departments.add(parts[1])
            for row in self.service.list_preview_render_versions(identity):
                parts = row.name.split("/")
                if len(parts) > 1:
                    departments.add(parts[1])
            targets = [
                (department, {"target": department})
                for department in sorted(departments)
            ]
        self.publish_target_list.blockSignals(True)
        self.publish_target_list.clear()
        selected = None
        for label, data in targets:
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, data)
            self.publish_target_list.addItem(item)
            if label == current or str(data.get("target") or "") == current:
                selected = item
        if selected:
            self.publish_target_list.setCurrentItem(selected)
        elif self.publish_target_list.count():
            self.publish_target_list.setCurrentRow(0)
        self.publish_target_list.blockSignals(False)

    def _publish_rows(self):
        identity = self.active_shot_identity or self.current_identity()
        sequence_identity = self.active_sequence_identity or self.current_sequence_identity()
        if not identity and not sequence_identity:
            return []
        publish_type = self._current_publish_type()
        if publish_type == "camera" and identity:
            return self.service.list_shot_scene_publish_versions(identity, "camera")
        if publish_type == "animation_cache" and identity:
            return self.service.list_animation_cache_versions(
                identity,
                target="",
            )
        if publish_type == "animation_alembic" and identity:
            return self.service.list_animation_cache_versions(
                identity,
                target="",
                subset="alembic",
            )
        if publish_type == "animation_package" and identity:
            return self.service.list_animation_package_versions(identity)
        if publish_type == "placements" and identity:
            return self.service.list_placement_publish_versions(identity)
        if publish_type == "set_dress":
            return (
                self.service.list_sequence_set_dress_publish_versions(sequence_identity)
                if sequence_identity
                else self.service.list_set_dress_publish_versions(identity)
                if identity
                else []
            )
        if publish_type == "preview_render" and identity:
            return self.service.list_preview_render_versions(
                identity,
                department="",
            )
        return []

    def populate_publish_tree(self) -> None:
        self.publish_tree.clear()
        rows = self._publish_rows()
        parent_items = {}
        parent_state = self._publish_tree_parent_state()
        for row in rows:
            parent = parent_items.get(row.name)
            if parent is None:
                parent = QtWidgets.QTreeWidgetItem(
                    [
                        self._publish_tree_parent_label(row.name),
                        parent_state,
                        "",
                        "",
                        "",
                    ]
                )
                parent.setToolTip(0, row.name)
                parent_items[row.name] = parent
                self.publish_tree.addTopLevelItem(parent)
            state = "Latest" if row.latest else ""
            item = QtWidgets.QTreeWidgetItem(
                [
                    row.version,
                    state,
                    self._publish_tree_frame_range(Path(row.path)),
                    row.updated,
                    row.comment,
                ]
            )
            item.setData(0, QtCore.Qt.UserRole, row.path)
            parent.addChild(item)
        self.publish_tree.expandAll()
        for column in (0, 1, 2, 3):
            self.publish_tree.resizeColumnToContents(column)

    def _publish_tree_parent_label(self, name: str) -> str:
        parts = [part for part in str(name).replace("\\", "/").split("/") if part]
        publish_type = self._current_publish_type()
        if publish_type == "camera" and len(parts) >= 2:
            return parts[1]
        if publish_type == "animation_cache" and len(parts) >= 3:
            return parts[2]
        if publish_type == "preview_render" and len(parts) >= 2:
            return parts[1]
        if publish_type == "set_dress" and parts:
            return parts[-1]
        return parts[-1] if parts else str(name)

    def _publish_tree_parent_state(self) -> str:
        return {
            "camera": "CAMERA READY",
            "animation_cache": "CACHE READY",
            "animation_package": "PACKAGE READY",
            "placements": "PLACEMENT READY",
            "set_dress": "SET DRESS READY",
            "preview_render": "MANIFEST READY",
        }.get(self._current_publish_type(), "PUBLISHED")

    def _publish_tree_frame_range(self, path: Path) -> str:
        if not path.is_file() or path.suffix.lower() != ".json":
            return ""
        try:
            from smartlib.core.metadata import read_json

            data = read_json(path, {}) or {}
        except Exception:
            return ""
        frame_range = (
            data.get("frame_range")
            or data.get("render_frame_range")
            or data.get("cut_range")
            or []
        )
        if not isinstance(frame_range, (list, tuple)) or len(frame_range) < 2:
            return ""
        return f"Frames[{frame_range[0]}-{frame_range[1]}]"

    def _add_publish_contents(
        self,
        version_item: QtWidgets.QTreeWidgetItem,
        path: Path,
    ) -> None:
        if not path.is_file() or path.suffix.lower() != ".json":
            return
        try:
            from smartlib.core.metadata import read_json

            data = read_json(path, {}) or {}
        except Exception:
            return
        if path.name == "animation_manifest.json":
            for cast_key, entry in sorted((data.get("casts") or {}).items()):
                cache_version = str(entry.get("cache_version") or "")
                curve_version = str(
                    (entry.get("curve_dependency") or {}).get("version") or "-"
                )
                child = QtWidgets.QTreeWidgetItem(
                    [
                        cast_key,
                        cache_version,
                        "CACHE READY",
                        "",
                        f"Curve {curve_version}",
                    ]
                )
                version_item.addChild(child)
        elif path.name == "camera.json":
            frame_range = data.get("frame_range") or data.get("render_frame_range") or []
            resolution = data.get("resolution") or []
            layer = data.get("display_layer") or data.get("layer") or ""
            details = []
            if frame_range:
                details.append(f"Frames {frame_range}")
            if resolution:
                details.append(f"Resolution {resolution}")
            if layer:
                details.append(f"Layer {layer}")
            version_item.addChild(
                QtWidgets.QTreeWidgetItem(
                    [
                        str(data.get("camera") or data.get("name") or "Camera Settings"),
                        "",
                        "CAMERA READY",
                        "",
                        " / ".join(details),
                    ]
                )
            )
        elif path.name == "render_manifest.json":
            layers = data.get("layers") or data.get("groups") or {}
            for layer_name, entry in sorted(
                layers.items(),
                key=lambda pair: int(pair[1].get("order", 0)),
            ):
                pattern = str(entry.get("pattern") or "")
                child = QtWidgets.QTreeWidgetItem(
                    [
                        layer_name,
                        str(entry.get("version") or ""),
                        "FOOTAGE READY" if pattern else "FOOTAGE MISSING",
                        "",
                        f"{entry.get('take', '')}  {pattern}",
                    ]
                )
                version_item.addChild(child)

    def _populate_preview_render_targets(self) -> None:
        current = self._current_data_target()
        identity = self.active_shot_identity or self.current_identity()
        departments = {self.work_dept_combo.currentText().strip() or "default"}
        if identity:
            for row in self.service.list_review_spec_versions(identity):
                parts = str(row.name or "").split("/")
                if len(parts) > 1:
                    departments.add(parts[1])
        self.data_cast_list.blockSignals(True)
        self.data_cast_list.clear()
        for department in sorted(departments, key=str.lower):
            item = QtWidgets.QListWidgetItem(department)
            item.setData(QtCore.Qt.UserRole, {"target": department})
            self.data_cast_list.addItem(item)
            if department == current:
                self.data_cast_list.setCurrentItem(item)
        if self.data_cast_list.count() and self.data_cast_list.currentRow() < 0:
            self.data_cast_list.setCurrentRow(0)
        self.data_cast_list.blockSignals(False)

    def _populate_scene_data_targets(self, data_type: str) -> None:
        current = self._current_data_target()
        self.data_cast_list.blockSignals(True)
        self.data_cast_list.clear()
        targets = []
        try:
            from smartlib.dcc.maya.shot_scene_data import list_scene_component_roots

            targets = list_scene_component_roots(data_type)
        except Exception:
            targets = []
        identity = self.active_shot_identity or self.current_identity()
        sequence_identity = self.active_sequence_identity or self.current_sequence_identity()
        targets_by_token = {self._data_target_token(target): target for target in targets}
        if identity:
            rows = self.service.list_shot_data_versions(identity)
        elif sequence_identity:
            rows = self.service.list_sequence_data_versions(
                sequence_identity,
                department=self.work_dept_combo.currentText().strip() or "layout",
            )
        else:
            rows = []
        for row in rows:
            parts = str(row.name or "").split("/")
            if len(parts) > 1 and parts[0] == data_type:
                targets_by_token.setdefault(parts[1], parts[1])
        for target in sorted(targets_by_token.values(), key=lambda value: str(value).lower()):
            display_name = str(target).rsplit("|", 1)[-1]
            item = QtWidgets.QListWidgetItem(display_name)
            item.setData(QtCore.Qt.UserRole, {"target": str(target)})
            self.data_cast_list.addItem(item)
            if str(target) == current:
                self.data_cast_list.setCurrentItem(item)
        if self.data_cast_list.count() and self.data_cast_list.currentRow() < 0:
            self.data_cast_list.setCurrentRow(0)
        self.data_cast_list.blockSignals(False)

    def _filter_data_rows(self, rows, data_type: str, target: str):
        if data_type == "animation_curve":
            return rows
        target_token = self._data_target_token(target)
        filtered = []
        for row in rows:
            parts = str(row.name or "").split("/")
            if not parts or parts[0] != data_type:
                continue
            if target_token and len(parts) > 1 and parts[1] != target_token:
                continue
            filtered.append(row)
        return filtered

    @staticmethod
    def _data_target_token(value: str) -> str:
        import re

        cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").rsplit("|", 1)[-1]).strip("_")
        return cleaned or "main"

    def _populate_data_targets_from_existing_rows(self) -> None:
        identity = self.active_shot_identity or self.current_identity()
        sequence_identity = self.active_sequence_identity or self.current_sequence_identity()
        data_type = self._current_data_type()
        current = self._current_data_target()
        self.data_cast_list.blockSignals(True)
        self.data_cast_list.clear()
        rows = []
        if sequence_identity:
            department = self.work_dept_combo.currentText().strip() or "layout"
            rows = (
                self.service.list_sequence_set_dress_publish_versions(sequence_identity)
                if data_type == "set_dress"
                else self.service.list_sequence_data_versions(sequence_identity, department=department)
            )
        elif identity:
            rows = (
                self.service.list_set_dress_data(identity)
                if data_type == "set_dress_data"
                else self.service.list_shot_data_versions(identity)
            )
        targets = []
        for row in rows:
            parts = str(row.name or "").split("/")
            if parts and parts[0] == data_type:
                target = parts[1] if len(parts) > 1 else "main"
                if target not in targets:
                    targets.append(target)
        for target in sorted(targets, key=str.lower):
            item = QtWidgets.QListWidgetItem(target)
            item.setData(QtCore.Qt.UserRole, {"target": target})
            self.data_cast_list.addItem(item)
            if target == current:
                self.data_cast_list.setCurrentItem(item)
        if self.data_cast_list.count() and self.data_cast_list.currentRow() < 0:
            self.data_cast_list.setCurrentRow(0)
        self.data_cast_list.blockSignals(False)

    def _data_version_is_published_animation(self, path: str, published_sources: set[str]) -> bool:
        if not published_sources:
            return False
        version_path = Path(path)
        source_path = version_path / "animation_curve.json" if version_path.is_dir() else version_path
        return source_path.resolve().as_posix().lower() in published_sources

    @staticmethod
    def _data_version_labels(row_data) -> list[str]:
        labels = []
        if row_data.latest:
            labels.append("latest")

        path = Path(str(row_data.path))
        version_dir = path if path.is_dir() else path.parent
        metadata = {}
        for filename in ("data.json", "publish.json"):
            metadata_path = version_dir / filename
            if not metadata_path.is_file():
                continue
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8")) or {}
            except (OSError, ValueError, TypeError):
                metadata = {}
            if metadata:
                break

        status = str(metadata.get("status") or metadata.get("state") or "").strip().lower()
        official_value = metadata.get("official")
        is_official = (
            official_value is True
            or str(official_value or "").strip().lower() in {"1", "true", "yes"}
            or status in {
                "approved",
                "official",
                "released",
            }
        )
        official_path = version_dir.parent / "official.json"
        if not is_official and official_path.is_file():
            try:
                official = json.loads(official_path.read_text(encoding="utf-8")) or {}
                is_official = str(official.get("version") or "") == str(row_data.version)
            except (OSError, ValueError, TypeError):
                pass
        if is_official:
            labels.append("official")
        return labels

    def populate_shot_context_builder(self) -> None:
        if not hasattr(self, "shot_context_component_tree"):
            return
        self.shot_context_component_tree.clear()
        self.shot_context_versions_tree.clear()
        identity = self.active_shot_identity or self.current_identity()
        enabled = bool(identity and getattr(identity, "shot", ""))
        self.assemble_shot_context_btn.setEnabled(enabled)
        if not enabled:
            item = QtWidgets.QTreeWidgetItem(["", "", "Open a shot detail", "", "", "", "N/A"])
            self.shot_context_component_tree.addTopLevelItem(item)
            return
        department = self.work_dept_combo.currentText().strip() or "anim"
        profile = self.shot_context_profile_combo.currentText().strip() or "WORK"
        try:
            rows = self.service.shot_context_components(
                identity, department=department, profile=profile
            )
            for row in rows:
                item = QtWidgets.QTreeWidgetItem(
                    [
                        "",
                        str(row.get("type") or ""),
                        str(row.get("name") or ""),
                        str(row.get("subset") or ""),
                        str(row.get("version") or ""),
                        "",
                        str(row.get("state") or ""),
                    ]
                )
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(0, QtCore.Qt.Checked if row.get("use") else QtCore.Qt.Unchecked)
                item.setData(0, QtCore.Qt.UserRole, dict(row))
                policy = QtWidgets.QComboBox()
                policy.addItems(["payload", "reference"])
                policy.setCurrentText(str(row.get("load_policy") or "payload"))
                self.shot_context_component_tree.addTopLevelItem(item)
                self.shot_context_component_tree.setItemWidget(item, 5, policy)
            for row in self.service.list_shot_context_versions(
                identity, department=department, profile=profile
            ):
                item = QtWidgets.QTreeWidgetItem(
                    [str(row.get("version") or ""), str(row.get("state") or ""), str(row.get("comment") or "")]
                )
                item.setData(0, QtCore.Qt.UserRole, str(row.get("path") or ""))
                self.shot_context_versions_tree.addTopLevelItem(item)
            for column in (0, 1, 3, 4, 5, 6):
                self.shot_context_component_tree.resizeColumnToContents(column)
        except Exception as exc:
            self.status_label.setText(f"Shot Context refresh failed: {exc}")

    def assemble_shot_context(self) -> None:
        identity = self.active_shot_identity or self.current_identity()
        if not identity or not getattr(identity, "shot", ""):
            self.status_label.setText("Open a shot detail first")
            return
        rows = []
        for index in range(self.shot_context_component_tree.topLevelItemCount()):
            item = self.shot_context_component_tree.topLevelItem(index)
            row = item.data(0, QtCore.Qt.UserRole)
            if not isinstance(row, dict):
                continue
            row = dict(row)
            row["use"] = item.checkState(0) == QtCore.Qt.Checked
            policy = self.shot_context_component_tree.itemWidget(item, 5)
            row["load_policy"] = policy.currentText() if policy else "payload"
            rows.append(row)
        comment, accepted = QtWidgets.QInputDialog.getText(
            self, "Assemble Shot Context", "Version comment"
        )
        if not accepted:
            return
        department = self.work_dept_combo.currentText().strip() or "anim"
        profile = self.shot_context_profile_combo.currentText().strip() or "WORK"
        try:
            path = self.service.build_shot_context(
                identity,
                department=department,
                profile=profile,
                components=rows,
                comment=comment.strip(),
            )
            self.status_label.setText(f"Assembled Shot Context: {path.parent.name}")
            self.populate_shot_context_builder()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Assemble Shot Context Failed", str(exc))

    def populate_layout_publish_status(self) -> None:
        self.populate_shot_context_builder()
        shot_identity = self.active_shot_identity or self.current_identity()
        sequence_identity = self.active_sequence_identity or self.current_sequence_identity()
        self.shot_context_tree.clear()
        self._clear_context_shot_detail()
        if shot_identity:
            self.context_target_shot_identity = shot_identity
            if hasattr(self, "layout_status_label"):
                self.layout_status_label.setText("Shot Anim Input Status")
            self.build_anim_input_btn.setText("Build Anim Input Package")
            rows = self.service.shot_anim_input_status(shot_identity)
            parent = QtWidgets.QTreeWidgetItem([shot_identity.shot, "", "", "Shot anim input readiness", ""])
            self.shot_context_tree.addTopLevelItem(parent)
            for row_data in rows:
                parent.addChild(self._context_status_item(row_data))
            parent.setExpanded(True)
            self.shot_context_tree.resizeColumnToContents(0)
            self.shot_context_tree.resizeColumnToContents(1)
            ready = len([row for row in rows if row.state in {"READY", "OPTIONAL"}])
            self.status_label.setText(f"Shot anim input status: {ready}/{len(rows)} ready")
            return
        if not sequence_identity:
            self.context_target_shot_identity = None
            if hasattr(self, "layout_status_label"):
                self.layout_status_label.setText("Layout Publish Status")
            self.build_anim_input_btn.setText("Build Anim Input Package")
            item = QtWidgets.QTreeWidgetItem(["Layout Publish Status", "N/A", "", "Open a sequence detail to check anim input readiness.", ""])
            self.shot_context_tree.addTopLevelItem(item)
            return
        if hasattr(self, "layout_status_label"):
            self.layout_status_label.setText("Sequence Anim Input Status")
        self.context_target_shot_identity = None
        self._set_sequence_context_target_label(sequence_identity)
        rows = self.service.layout_publish_status(sequence_identity)
        blocking = [row for row in rows if row.state == "MISSING"]
        state = "READY" if not blocking else "MISSING"
        message = "" if not blocking else "Missing: " + ", ".join(row.name for row in blocking)
        sequence_parent = QtWidgets.QTreeWidgetItem([sequence_identity.sequence, state, "", message, ""])
        sequence_parent.setData(
            0,
            QtCore.Qt.UserRole,
            {"kind": "sequence_context", "identity": sequence_identity},
        )
        color = QtGui.QColor("#355f45") if state == "READY" else QtGui.QColor("#6d3939")
        for column in range(self.shot_context_tree.columnCount()):
            sequence_parent.setBackground(column, color)
        self.shot_context_tree.addTopLevelItem(sequence_parent)
        shot_summary_parent = QtWidgets.QTreeWidgetItem(["Shots", "", "", "Shot anim input readiness summary", ""])
        self.shot_context_tree.addTopLevelItem(shot_summary_parent)
        sequence_data = self.service.load_sequence(sequence_identity)
        shot_identities = self.service._sequence_shot_identities(sequence_identity, sequence_data)
        for shot in shot_identities:
            shot_rows = self.service.shot_anim_input_status(shot)
            blocking = [row for row in shot_rows if row.state == "MISSING" and row.name != "layout_overlay"]
            optional_missing = [row for row in shot_rows if row.state == "OPTIONAL"]
            state = "READY" if not blocking else "MISSING"
            message = ""
            if blocking:
                message = "Missing: " + ", ".join(row.name for row in blocking)
            elif optional_missing:
                message = "Optional missing: " + ", ".join(row.name for row in optional_missing)
            summary = QtWidgets.QTreeWidgetItem([shot.shot, state, "", message, ""])
            summary.setData(0, QtCore.Qt.UserRole, {"kind": "shot_context", "identity": shot})
            color = QtGui.QColor("#355f45") if state == "READY" else QtGui.QColor("#6d3939")
            for column in range(self.shot_context_tree.columnCount()):
                summary.setBackground(column, color)
            shot_summary_parent.addChild(summary)
        shot_summary_parent.setExpanded(True)
        self.shot_context_tree.resizeColumnToContents(0)
        self.shot_context_tree.resizeColumnToContents(1)
        ready = len([row for row in rows if row.state == "READY"])
        self.status_label.setText(f"Layout publish status: {ready}/{len(rows)} ready")

    def _on_context_tree_item_clicked(self, item, _column=0) -> None:
        data = item.data(0, QtCore.Qt.UserRole) if item else None
        if not isinstance(data, dict):
            return
        if data.get("kind") == "sequence_context":
            identity = data.get("identity")
            if not identity:
                return
            self._populate_context_sequence_detail(identity)
            self.context_target_shot_identity = None
            self._set_sequence_context_target_label(identity)
            return
        if data.get("kind") != "shot_context":
            return
        identity = data.get("identity")
        if not identity:
            return
        self._populate_context_shot_detail(identity)
        self.context_target_shot_identity = identity
        self.build_anim_input_btn.setText("Build Anim Input Package")

    def _populate_context_sequence_detail(self, identity) -> None:
        if not hasattr(self, "context_shot_detail_tree"):
            return
        self.context_shot_detail_tree.clear()
        if hasattr(self, "context_shot_detail_label"):
            self.context_shot_detail_label.setText(f"Selected Sequence Status: {identity.sequence}")
            self.context_shot_detail_label.show()
        rows = self.service.layout_publish_status(identity)
        parent = QtWidgets.QTreeWidgetItem([identity.sequence, "", "", "Sequence layout publish readiness", ""])
        self.context_shot_detail_tree.addTopLevelItem(parent)
        for row_data in rows:
            parent.addChild(self._context_status_item(row_data))
        parent.setExpanded(True)
        self.context_shot_detail_tree.resizeColumnToContents(0)
        self.context_shot_detail_tree.resizeColumnToContents(1)
        self.context_shot_detail_tree.show()

    def _set_sequence_context_target_label(self, sequence_identity) -> None:
        self.build_anim_input_btn.setText("Build Anim Input Packages")

    def _clear_context_shot_detail(self) -> None:
        if hasattr(self, "context_shot_detail_tree"):
            self.context_shot_detail_tree.clear()
            self.context_shot_detail_tree.hide()
        if hasattr(self, "context_shot_detail_label"):
            self.context_shot_detail_label.hide()

    def _populate_context_shot_detail(self, identity) -> None:
        if not hasattr(self, "context_shot_detail_tree"):
            return
        self.context_shot_detail_tree.clear()
        if hasattr(self, "context_shot_detail_label"):
            self.context_shot_detail_label.setText(f"Selected Shot Status: {identity.shot}")
            self.context_shot_detail_label.show()
        rows = self.service.shot_anim_input_status(identity)
        parent = QtWidgets.QTreeWidgetItem([identity.shot, "", "", "Shot anim input readiness", ""])
        self.context_shot_detail_tree.addTopLevelItem(parent)
        for row_data in rows:
            parent.addChild(self._context_status_item(row_data))
        parent.setExpanded(True)
        self.context_shot_detail_tree.resizeColumnToContents(0)
        self.context_shot_detail_tree.resizeColumnToContents(1)
        self.context_shot_detail_tree.show()

    def _context_status_item(self, row_data) -> QtWidgets.QTreeWidgetItem:
        item = QtWidgets.QTreeWidgetItem(
            [row_data.name, row_data.state, row_data.version, row_data.message, row_data.path]
        )
        item.setData(0, QtCore.Qt.UserRole, row_data.path)
        color = QtGui.QColor("#355f45") if row_data.state == "READY" else QtGui.QColor("#6a5631")
        if row_data.state == "MISSING":
            color = QtGui.QColor("#6d3939")
        elif row_data.state == "OPTIONAL":
            color = QtGui.QColor("#3d4f61")
        for column in range(self.shot_context_tree.columnCount()):
            item.setBackground(column, color)
        return item

    def build_anim_input_package(self) -> None:
        shot_identity = self.context_target_shot_identity or self.active_shot_identity or self.current_identity()
        sequence_identity = self.active_sequence_identity or self.current_sequence_identity()
        if shot_identity:
            comment, accepted = QtWidgets.QInputDialog.getText(self, "Build Anim Input Package", "Comment")
            if not accepted:
                return
            try:
                result = self.service.build_anim_input_package_for_shot(shot_identity, comment=comment.strip())
                self.service.ensure_stage_construct(shot_identity)
                self.status_label.setText(f"Built anim input package: {result.shot}")
                self.populate_layout_publish_status()
                self.populate_construct_table()
            except Exception as exc:
                QtWidgets.QMessageBox.critical(self, "Build Anim Input Package Failed", str(exc))
                self.populate_layout_publish_status()
            return
        if not sequence_identity:
            self.status_label.setText("Open a sequence detail first")
            return
        comment, accepted = QtWidgets.QInputDialog.getText(self, "Build Anim Input Packages", "Comment")
        if not accepted:
            return
        try:
            results = self.service.build_anim_input_package(sequence_identity, comment=comment.strip())
            for result in results:
                shot_identity = self.identity_cls(sequence_identity.episode, sequence_identity.sequence, result.shot)
                self.service.ensure_stage_construct(shot_identity)
            self.status_label.setText(f"Built anim input packages: {len(results)} shots")
            self.populate_layout_publish_status()
            self.populate_construct_table()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Build Anim Input Package Failed", str(exc))
            self.populate_layout_publish_status()

    def _preview_history_rows(self, identity) -> list[dict]:
        root = self.service.shot_root(identity) / "publish" / "review"
        return self._preview_history_rows_from_root(root)

    def _sequence_preview_history_rows(self, sequence_identity) -> list[dict]:
        root = self.service.sequence_workspace_root(sequence_identity.episode, sequence_identity.sequence) / "publish" / "review"
        return self._preview_history_rows_from_root(root)

    def _preview_history_rows_from_root(self, root: Path) -> list[dict]:
        rows = []
        if not root.exists():
            return rows
        for review_json in sorted(root.glob("**/review.json")):
            version_dir = review_json.parent.parent if review_json.parent.name == "metadata" else review_json.parent
            if not version_dir.name.lower().startswith("v"):
                continue
            data = _read_json_file(review_json)
            if not isinstance(data, dict):
                data = {}
            try:
                relative_parent = version_dir.parent.relative_to(root)
                parts = relative_parent.parts
            except ValueError:
                parts = ()
            department = parts[0] if parts else str(data.get("department") or data.get("subset") or "")
            package = "/".join(parts[1:]) if len(parts) > 1 else "main"
            frame_range = data.get("frame_range") or []
            frames = f"{frame_range[0]}-{frame_range[1]}" if len(frame_range) >= 2 else ""
            updated = ""
            try:
                updated = QtCore.QDateTime.fromSecsSinceEpoch(int(review_json.stat().st_mtime)).toString("yyyy-MM-dd HH:mm")
            except Exception:
                pass
            rows.append(
                {
                    "department": department,
                    "package": package,
                    "version": version_dir.name,
                    "updated": updated,
                    "frames": frames,
                    "review_json": review_json,
                }
            )
        return sorted(rows, key=lambda item: (item["department"], item["package"], item["version"]), reverse=True)

    def _selected_preview_review_json(self) -> Path | None:
        row = self.preview_history_table.currentRow()
        if row < 0:
            return None
        item = self.preview_history_table.item(row, 0)
        if not item:
            return None
        path = item.data(QtCore.Qt.UserRole)
        return Path(path) if path else None

    def open_selected_preview_in_rv(self) -> None:
        review_json = self._selected_preview_review_json()
        if not review_json:
            self.status_label.setText("Select a preview package first")
            return
        try:
            _ensure_smartlib_on_path()
            from smartlib.apps.viewer import ViewerService

            viewer = ViewerService(self.service.project_config)
            package = viewer.review_package_from_json(review_json)
            if not package:
                self.status_label.setText("Preview package could not be read")
                return
            args = viewer.rv_args_for_package(package)
            if not args:
                self.status_label.setText("No preview sequence files were found for RV")
                return
            rv = viewer.rv_executable()
            if not rv:
                QtWidgets.QMessageBox.warning(
                    self,
                    "OpenRV Not Found",
                    "Set tools.openrv.path in config/STKB/tools.yml or set OPENRV_PATH.",
                )
                return
            subprocess.Popen([str(rv), *args])
            self.status_label.setText(f"Opened preview in RV: {package.code}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Open Package in RV Failed", str(exc))

    def open_generate_review(self) -> None:
        identity = self.current_identity()
        if identity is None or getattr(identity, "shot", "") in ("", "all"):
            QtWidgets.QMessageBox.information(self, "Generate Review", "Select a shot first.")
            return
        try:
            _ensure_smartlib_on_path()
            from smartlib.apps.review_build_manager.window import show as show_review_build_manager

            window = show_review_build_manager(
                config_dir=self.service.project_config.config_dir,
                parent=self,
                initial_scope="Shot",
            )
            self._review_build_manager_window = window
            if hasattr(window, "mode_combo"):
                window.mode_combo.setCurrentText("REVIEW ONLY")

            def select_current_shot():
                restore = getattr(window, "_restore_shot_selection", None)
                if callable(restore):
                    restore(identity, ("shot", identity.episode, identity.sequence, identity.shot))

            QtCore.QTimer.singleShot(0, select_current_shot)
            self.status_label.setText(f"Generate Review opened: {identity.code}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Generate Review Failed", str(exc))

    def populate_cast_table(self, cast_data: dict) -> None:
        self.cast_table.setRowCount(0)
        asset_lookup = {}
        casting_service = None
        try:
            _ensure_smartlib_on_path()
            from smartlib.apps.smart_casting.service import SmartCastingService

            casting_service = SmartCastingService(self.service.project_config)
            asset_lookup = {
                (item.asset, item.variant): item
                for item in casting_service.list_assets()
            }
        except Exception:
            casting_service = None
        cast = cast_data.get("cast") or {}
        for cast_key, entry in sorted(cast.items()):
            asset = entry.get("asset", "")
            variant = entry.get("variant", "default")
            asset_info = asset_lookup.get((asset, variant)) or asset_lookup.get((asset, "default"))
            context_statuses = {}
            if casting_service is not None:
                try:
                    context_statuses = casting_service.cast_context_statuses(entry)
                except Exception:
                    context_statuses = {}
            self._append_cast_row(
                cast_key=cast_key,
                asset=asset,
                variant=variant,
                role=entry.get("role", "CHA"),
                namespace=entry.get("namespace", cast_key),
                asset_publish=entry.get("asset_publish", "approved"),
                required=bool(entry.get("required", True)),
                note=entry.get("note", ""),
                category=getattr(asset_info, "category", ""),
                group=getattr(asset_info, "group", ""),
                status=getattr(asset_info, "status", "") or "Wait",
                thumbnail=getattr(asset_info, "thumbnail", ""),
                context_statuses=context_statuses,
            )
        self.cast_table.resizeColumnsToContents()
        self._clear_cast_validation_colors()

    def populate_data_cast_list(self, cast_data: dict) -> None:
        if self._current_data_type() != "animation_curve":
            self._populate_data_targets_from_existing_rows()
            return
        current_key = ""
        current_item = self.data_cast_list.currentItem()
        if current_item:
            current_data = current_item.data(QtCore.Qt.UserRole) or {}
            current_key = str(current_data.get("cast_key") or "") if isinstance(current_data, dict) else ""
        self.data_cast_list.blockSignals(True)
        self.data_cast_list.clear()
        cast = cast_data.get("cast") or {}
        row_to_select = None
        for cast_key, entry in sorted(cast.items()):
            row_data = {
                "cast_key": cast_key,
                "asset": entry.get("asset", ""),
                "variant": entry.get("variant", "default"),
                "role": entry.get("role", "CHA"),
                "namespace": entry.get("namespace", cast_key),
                "asset_publish": entry.get("asset_publish", "approved"),
                "required": bool(entry.get("required", True)),
                "note": entry.get("note", ""),
            }
            item = QtWidgets.QListWidgetItem(cast_key)
            item.setData(QtCore.Qt.UserRole, row_data)
            self.data_cast_list.addItem(item)
            if cast_key == current_key:
                row_to_select = item
        if row_to_select:
            self.data_cast_list.setCurrentItem(row_to_select)
        elif self.data_cast_list.count():
            self.data_cast_list.setCurrentRow(0)
        self.data_cast_list.blockSignals(False)
        self.populate_data_tree()

    def _clear_cast_validation_colors(self) -> None:
        for row in range(self.cast_table.rowCount()):
            for column in range(self.cast_table.columnCount()):
                item = self.cast_table.item(row, column)
                if item:
                    # Clear the validation override so Maya's active Qt palette
                    # paints the normal table background. An invalid QColor is
                    # rendered as opaque black by some Maya Qt builds.
                    item.setData(QtCore.Qt.BackgroundRole, None)
                    item.setToolTip("")

    def _apply_cast_validation_colors(self, issues) -> None:
        self._clear_cast_validation_colors()
        if not issues:
            self.cast_validation_label.setStyleSheet(
                "QLabel { background: #244532; color: #dff5e7; border: 1px solid #3c7d55; padding: 4px; }"
            )
            return
        has_error = any(str(issue.severity).lower() == "error" for issue in issues)
        self.cast_validation_label.setStyleSheet(
            "QLabel { background: #5a3329; color: #ffe4d8; border: 1px solid #9b5a45; padding: 4px; }"
            if has_error
            else "QLabel { background: #5a4a24; color: #fff1c6; border: 1px solid #9b7a35; padding: 4px; }"
        )
        error_color = QtGui.QColor(120, 58, 45)
        warning_color = QtGui.QColor(120, 100, 45)
        for issue in issues:
            color = error_color if str(issue.severity).lower() == "error" else warning_color
            text = f"{issue.code}: {issue.message}"
            targets = self._cast_issue_rows(issue)
            if not targets:
                targets = range(self.cast_table.rowCount())
            for row in targets:
                for column in range(self.cast_table.columnCount()):
                    item = self.cast_table.item(row, column)
                    if item:
                        item.setBackground(color)
                        item.setToolTip(text)

    def _cast_issue_rows(self, issue) -> list[int]:
        message = str(issue.message)
        rows = []
        for row in range(self.cast_table.rowCount()):
            values = [self._table_text(row, column) for column in range(self.cast_table.columnCount())]
            values.extend(str(value) for value in self._cast_row_payload(row).values())
            if any(value and value in message for value in values):
                rows.append(row)
        return rows

    def open_smart_casting(self) -> None:
        identity = self.current_identity()
        sequence_identity = self.current_sequence_identity()
        episode = getattr(identity, "episode", "") or getattr(sequence_identity, "episode", "")
        sequence = getattr(identity, "sequence", "") or getattr(sequence_identity, "sequence", "")
        shot = getattr(identity, "shot", "")
        try:
            _ensure_smartlib_on_path()
            from smartlib.apps.smart_casting.ui import show

            show(
                config_dir=self.service.project_config.config_dir,
                parent=self,
                episode=episode,
                sequence=sequence,
                shot=shot,
            )
            self.status_label.setText("Opened Smart Casting. Cast changes are managed there.")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Open Smart Casting Failed", str(exc))

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
        self._sync_data_cast_list_from_table()

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
        self._sync_data_cast_list_from_table()
        self.status_label.setText(f"Added selected asset to cast: {entry['asset']}")

    def remove_cast_row(self) -> None:
        row = self.cast_table.currentRow()
        if row >= 0:
            self.cast_table.removeRow(row)
            self._sync_data_cast_list_from_table()

    def save_cast(self) -> None:
        identity = self.current_identity()
        if not identity:
            return
        existing = self.service.load_cast(identity)
        try:
            cast_data = self.service.build_cast_data(self.cast_table_rows(), existing=existing)
            self.service.write_cast(identity, cast_data)
            self.cast_json_view.setPlainText(json.dumps(cast_data, indent=2, ensure_ascii=False))
            self.populate_data_cast_list(cast_data)
            self.status_label.setText("Saved cast.json")
            self.validate_current_cast(update_tab=False)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save Cast Failed", str(exc))

    def populate_construct_table(self, force: bool = False) -> None:
        if self._restore_state_pending and not force:
            return
        if not force and self.tabs.currentWidget() != self.construct_tab:
            return
        sequence_identity = self.active_sequence_identity
        identity = None if sequence_identity else (
            self.active_shot_identity or self.current_identity()
        )
        if not identity and not sequence_identity:
            sequence_identity = self.current_sequence_identity()
        self.construct_table.setRowCount(0)
        if sequence_identity:
            self._populate_sequence_construct_table(sequence_identity)
            return
        if identity:
            self.populate_construct_scene_list(identity)
        record = self._selected_construct_record()
        is_current_record = record.get("mode") == "current"
        enabled = bool(identity) and is_current_record
        for button in (
            self.construct_stage_btn,
            self.construct_from_cast_btn,
            self.add_construct_btn,
            self.add_fx_cache_btn,
            self.remove_construct_btn,
            self.save_construct_btn,
        ):
            button.setEnabled(enabled)
        self.open_construct_btn.setEnabled(
            bool(identity) and record.get("mode") in {"build", "work"}
        )
        if self.construct_stage_btn.isEnabled() and not self.is_maya_session:
            self.construct_stage_btn.setEnabled(False)
        if not identity:
            self.construct_scene_list.clear()
            self.construct_status_label.setText("Construct: select a shot")
            return
        try:
            latest_data = self.service.resolved_construct(identity)
        except Exception:
            latest_data = {"components": []}
        latest_map = self._construct_latest_map(latest_data.get("components") or [])
        if record.get("mode") in {"work", "build"}:
            data = self._construct_data_from_work_record(record)
        elif is_current_record:
            data = latest_data
        else:
            data = self.service.load_construct(identity)
        if is_current_record and not data.get("components"):
            try:
                self.service.ensure_stage_construct(identity)
                data = self.service.load_construct(identity)
            except Exception:
                pass
        for component in data.get("components") or []:
            latest_version = latest_map.get(self._construct_component_key(component), "")
            diff_state = self._construct_diff_state(component, latest_version)
            self._append_construct_row(
                enabled=bool(component.get("enabled", True)),
                component_type=str(component.get("component_type") or "rig"),
                name=str(component.get("name") or ""),
                version=str(component.get("version") or "latest"),
                mode=str(component.get("mode") or "reference"),
                namespace=str(component.get("namespace") or ""),
                path=str(component.get("path") or ""),
                required=bool(component.get("required", True)),
                note=str(component.get("note") or ""),
                source=dict(component.get("source") or {}) if isinstance(component.get("source"), dict) else {},
                latest_version=latest_version,
                diff_state=diff_state,
            )
        self.construct_table.resizeColumnsToContents()
        self.construct_table.setColumnHidden(8, True)
        label = "Current" if is_current_record else Path(str(record.get("path") or "")).name
        self.construct_status_label.setText(f"Construct: {identity.shot} / {label} ({self.construct_table.rowCount()} components)")

    def _populate_sequence_construct_table(self, sequence_identity) -> None:
        """Show the sequence work scene and its Camera Sequencer shot contract."""
        self.populate_sequence_construct_scene_list(sequence_identity)
        record = self._selected_construct_record()
        self.construct_stage_btn.setEnabled(self.is_maya_session)
        self.construct_from_cast_btn.setEnabled(False)
        self.add_construct_btn.setEnabled(False)
        self.add_fx_cache_btn.setEnabled(False)
        self.remove_construct_btn.setEnabled(False)
        self.save_construct_btn.setEnabled(False)
        self.open_construct_btn.setEnabled(
            self.is_maya_session
            and record.get("mode") in {"build", "work"}
            and Path(str(record.get("path") or "")).exists()
        )

        sequence_data = self.service.load_sequence(sequence_identity) or {}
        shots = sequence_data.get("shots") or []
        for shot_data in shots:
            if not isinstance(shot_data, dict):
                continue
            shot_name = str(shot_data.get("shot") or "").strip()
            if not shot_name:
                continue
            cut_in = shot_data.get("cut_in")
            cut_out = shot_data.get("cut_out")
            duration = shot_data.get("duration")
            range_text = f"Frames [{cut_in}-{cut_out}]"
            if duration not in (None, ""):
                range_text += f" / {duration}f"
            self._append_construct_row(
                enabled=True,
                component_type="camera",
                name=shot_name,
                version="editorial",
                mode="sequence",
                namespace=shot_name,
                path="",
                required=True,
                note=range_text,
                source={"kind": "sequence_shot", "shot": shot_name},
                latest_version="editorial",
                diff_state="OK",
            )
        self.construct_table.resizeColumnsToContents()
        self.construct_table.setColumnHidden(8, True)
        self.construct_table.horizontalHeader().setStretchLastSection(True)
        label = "Current Stage" if record.get("mode") in {"current", "current_sequence"} else Path(str(record.get("path") or "")).name
        self.construct_status_label.setText(
            f"Construct: {sequence_identity.sequence} / {label} "
            f"({self.construct_table.rowCount()} sequencer shots)"
        )

    def populate_sequence_construct_scene_list(self, sequence_identity) -> None:
        current_data = self._selected_construct_record()
        current_key = str(current_data.get("path") or current_data.get("mode") or "current")
        prefer_latest_build = current_data.get("mode") in {
            None,
            "",
            "current",
            "current_sequence",
        }
        self.construct_scene_list.blockSignals(True)
        try:
            self.construct_scene_list.clear()
            current_item = QtWidgets.QListWidgetItem("Current Sequence Stage")
            current_item.setData(QtCore.Qt.UserRole, {"mode": "current_sequence"})
            self.construct_scene_list.addItem(current_item)
            department = self.work_dept_combo.currentText().strip() or "layout"
            task = self._current_shot_task()
            for build in self.service.list_sequence_construct_build_scenes(
                sequence_identity,
                department=department,
                task=task,
            ):
                state = str(build.get("validation_state") or "UNKNOWN").upper()
                version = str(build.get("version") or "")
                label = f"[{state}] {version} {Path(str(build.get('path') or '')).name}"
                item = QtWidgets.QListWidgetItem(label)
                item.setToolTip(str(build.get("path") or ""))
                item.setData(QtCore.Qt.UserRole, build)
                if state in {"PASSED", "READY", "WARNING"}:
                    item.setForeground(QtGui.QColor("#80c98d"))
                else:
                    item.setForeground(QtGui.QColor("#d47b70"))
                self.construct_scene_list.addItem(item)
            for work_file in self.service.list_sequence_work_files(
                sequence_identity,
                department=department,
                tool_name="maya",
            ):
                item = QtWidgets.QListWidgetItem(work_file.file)
                item.setToolTip(work_file.path)
                item.setData(
                    QtCore.Qt.UserRole,
                    {"mode": "work", "path": work_file.path, "sequence": True},
                )
                self.construct_scene_list.addItem(item)
            target_row = 1 if prefer_latest_build and self.construct_scene_list.count() > 1 else 0
            for row in range(self.construct_scene_list.count()):
                data = self.construct_scene_list.item(row).data(QtCore.Qt.UserRole) or {}
                key = str(data.get("path") or data.get("mode") or "current")
                if not prefer_latest_build and key == current_key:
                    target_row = row
                    break
            self.construct_scene_list.setCurrentRow(target_row)
        finally:
            self.construct_scene_list.blockSignals(False)

    def populate_construct_scene_list(self, identity) -> None:
        current_data = self._selected_construct_record()
        current_key = str(current_data.get("path") or current_data.get("mode") or "current")
        self.construct_scene_list.blockSignals(True)
        try:
            self.construct_scene_list.clear()
            current_item = QtWidgets.QListWidgetItem("Current Construct")
            current_item.setData(QtCore.Qt.UserRole, {"mode": "current"})
            self.construct_scene_list.addItem(current_item)
            department = self.work_dept_combo.currentText().strip() or None
            task = self._current_shot_task()
            for build in self.service.list_construct_build_scenes(
                identity,
                department=department or "",
                task=task,
            ):
                state = str(build.get("validation_state") or "UNKNOWN").upper()
                version = str(build.get("version") or "")
                label = f"[{state}] {version} {Path(str(build.get('path') or '')).name}"
                item = QtWidgets.QListWidgetItem(label)
                item.setToolTip(str(build.get("path") or ""))
                item.setData(QtCore.Qt.UserRole, build)
                if state in {"PASSED", "READY", "WARNING"}:
                    item.setForeground(QtGui.QColor("#80c98d"))
                else:
                    item.setForeground(QtGui.QColor("#d47b70"))
                self.construct_scene_list.addItem(item)
            option = self._current_shot_option()
            option_arg = None if option == "all" else option
            for work_file in self.service.list_shot_work_files(
                identity,
                department=department,
                option=option_arg,
                task=task,
            ):
                item = QtWidgets.QListWidgetItem(work_file.file)
                item.setToolTip(work_file.path)
                item.setData(QtCore.Qt.UserRole, {"mode": "work", "path": work_file.path})
                self.construct_scene_list.addItem(item)
            target_row = 0
            for row in range(self.construct_scene_list.count()):
                data = self.construct_scene_list.item(row).data(QtCore.Qt.UserRole) or {}
                key = str(data.get("path") or data.get("mode") or "current")
                if key == current_key:
                    target_row = row
                    break
            self.construct_scene_list.setCurrentRow(target_row)
        finally:
            self.construct_scene_list.blockSignals(False)

    def _selected_construct_record(self) -> dict:
        item = self.construct_scene_list.currentItem()
        data = item.data(QtCore.Qt.UserRole) if item else {"mode": "current"}
        return dict(data or {}) if isinstance(data, dict) else {"mode": "current"}

    def _construct_data_from_work_record(self, record: dict) -> dict:
        path = Path(str(record.get("path") or ""))
        if not path.exists():
            return {"components": []}
        if record.get("mode") == "build":
            manifest = _read_json_file(str(record.get("manifest") or ""), {}) or {}
            construct = manifest.get("construct") if isinstance(manifest.get("construct"), dict) else {}
            return construct if isinstance(construct, dict) else {"components": []}
        try:
            from smartlib.core.metadata import sidecar_path
        except Exception:
            return {"components": []}
        metadata = _read_json_file(sidecar_path(path), {}) or {}
        construct = metadata.get("construct") if isinstance(metadata.get("construct"), dict) else {}
        return construct if isinstance(construct, dict) else {"components": []}

    def open_construct_scene(self, item=None) -> None:
        record = self._selected_construct_record()
        if record.get("mode") not in {"build", "work"}:
            self.status_label.setText("Select a Construct build or Work scene")
            return
        if not self.is_maya_session:
            QtWidgets.QMessageBox.information(self, "Open Construct", "Construct scenes can be opened inside Maya.")
            return
        state = str(record.get("validation_state") or "PASSED").upper()
        if record.get("mode") == "build" and state not in {"PASSED", "READY", "WARNING"}:
            QtWidgets.QMessageBox.warning(
                self,
                "Construct Validation Failed",
                f"This Construct scene cannot be opened because validation is {state}.",
            )
            return
        path = Path(str(record.get("path") or ""))
        sequence_identity = self.active_sequence_identity
        identity = None if sequence_identity else (
            self.active_shot_identity or self.current_identity()
        )
        if not identity and not sequence_identity:
            sequence_identity = self.current_sequence_identity()
        if (not identity and not sequence_identity) or not path.exists():
            QtWidgets.QMessageBox.warning(self, "Open Construct", f"Scene was not found: {path}")
            return
        try:
            _ensure_smartlib_on_path()
            import importlib
            from smartlib.dcc.maya import shot_builder

            importlib.invalidate_caches()
            shot_builder = importlib.reload(shot_builder)

            scene_data = (
                self.service.load_shot(identity)
                if identity
                else self.service.load_sequence(sequence_identity)
            )
            shot_builder.open_work_scene(path, scene_data)
            self._opened_construct_scene_path = str(path.resolve())
            self._opened_construct_record = dict(record)
            self.status_label.setText(f"Opened validated Construct: {path.name}. Use Save to create a Work scene.")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Open Construct Failed", str(exc))

    def _construct_latest_map(self, components: list[dict]) -> dict[str, str]:
        result = {}
        for component in components:
            key = self._construct_component_key(component)
            if not key:
                continue
            result[key] = str(component.get("version") or "")
        return result

    def _construct_component_key(self, component: dict) -> str:
        source = component.get("source") if isinstance(component.get("source"), dict) else {}
        source_kind = str(source.get("kind") or "")
        source_field = str(source.get("field") or "")
        source_key = f"{source_kind}:{source_field}" if source_kind or source_field else ""
        return "|".join(
            [
                str(component.get("component_type") or component.get("type") or "").strip().lower(),
                str(component.get("name") or "").strip(),
                source_key,
            ]
        )

    def _construct_diff_state(self, component: dict, latest_version: str) -> str:
        if not bool(component.get("enabled", True)):
            return "DISABLED"
        version = str(component.get("version") or "")
        if not latest_version:
            return "MISSING"
        if version and version != latest_version:
            return "UPDATED"
        return "OK"

    def ensure_stage_construct(self, identity=None) -> None:
        identity = identity or self.active_shot_identity or self.current_identity()
        if not identity:
            return
        path = self.service.ensure_stage_construct(identity)
        if self.tabs.currentWidget() == self.construct_tab:
            self.populate_construct_table()
        self.status_label.setText(f"Registered construct inputs: {path.name}")

    def _save_construct_table_for_stage(self, identity) -> None:
        if self._selected_construct_record().get("mode") == "work":
            return
        if self.construct_table.rowCount() <= 0:
            return
        self.service.write_construct(identity, {"components": self.construct_table_rows()})

    def populate_construct_from_stage_inputs(self) -> None:
        identity = self.active_shot_identity or self.current_identity()
        if not identity:
            return
        try:
            data = self.service.resolved_construct(identity)
            self.construct_table.setRowCount(0)
            for component in data.get("components") or []:
                self._append_construct_row(
                    enabled=bool(component.get("enabled", True)),
                    component_type=str(component.get("component_type") or "rig"),
                    name=str(component.get("name") or ""),
                    version=str(component.get("version") or "approved"),
                    mode=str(component.get("mode") or "reference"),
                    namespace=str(component.get("namespace") or ""),
                    path=str(component.get("path") or ""),
                    required=bool(component.get("required", True)),
                    note=str(component.get("note") or ""),
                    source=dict(component.get("source") or {}) if isinstance(component.get("source"), dict) else {},
                )
            self.construct_table.resizeColumnsToContents()
            self.status_label.setText(f"Construct from stage inputs: {self.construct_table.rowCount()} components")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Construct From Stage Inputs Failed", str(exc))

    def populate_construct_from_cast(self) -> None:
        self.populate_construct_from_stage_inputs()

    def add_construct_row(self) -> None:
        self._append_construct_row(
            enabled=True,
            component_type="rig",
            name=f"component_{self.construct_table.rowCount() + 1:03d}",
            version="latest",
            mode="reference",
            namespace="",
            path="",
            required=True,
            note="",
            source={"kind": "manual"},
        )
        self.construct_table.setCurrentCell(self.construct_table.rowCount() - 1, 1)

    def add_fx_cache_row(self) -> None:
        identity = self.active_shot_identity or self.current_identity()
        if not identity:
            return
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Add FX Cache",
            str(self.service.shot_root(identity)),
            FX_CACHE_FILTER,
        )
        if not path:
            return
        cache_path = Path(path)
        name = cache_path.stem or f"fx_{self.construct_table.rowCount() + 1:03d}"
        self._append_construct_row(
            enabled=True,
            component_type="fx",
            name=name,
            version="latest",
            mode="reference_cache",
            namespace=name,
            path=str(cache_path),
            required=False,
            note="",
            source={"kind": "manual"},
        )
        self.construct_table.setCurrentCell(self.construct_table.rowCount() - 1, 8)

    def remove_construct_row(self) -> None:
        row = self.construct_table.currentRow()
        if row >= 0:
            self.construct_table.removeRow(row)

    def save_construct(self) -> None:
        identity = self.active_shot_identity or self.current_identity()
        if not identity:
            return
        try:
            path = self.service.write_construct(identity, {"components": self.construct_table_rows()})
            self.status_label.setText(f"Saved construct.json: {path.name}")
            self.populate_construct_table()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save Construct Failed", str(exc))

    def construct_table_rows(self) -> list[dict]:
        rows = []
        for row in range(self.construct_table.rowCount()):
            values = {
                "enabled": self._construct_table_checked(row, 0, default=True),
                "component_type": self._construct_table_text(row, 1).lower() or "rig",
                "name": self._construct_table_text(row, 2),
                "version": self._construct_table_text(row, 3) or "latest",
                "mode": self._construct_table_text(row, 6).lower() or "reference",
                "namespace": self._construct_table_text(row, 7),
                "path": self._construct_table_text(row, 8),
                "required": self._construct_table_checked(row, 9, default=True),
                "note": self._construct_table_text(row, 10),
                "source": self._construct_table_source(row),
            }
            if any(str(value).strip() for key, value in values.items() if key not in {"enabled", "required"}):
                rows.append(values)
        return rows

    def _append_construct_row(
        self,
        *,
        enabled: bool,
        component_type: str,
        name: str,
        version: str,
        mode: str,
        namespace: str,
        path: str,
        required: bool,
        note: str,
        source: dict | None = None,
        latest_version: str = "",
        diff_state: str = "",
    ) -> None:
        row = self.construct_table.rowCount()
        self.construct_table.insertRow(row)
        use_item = self._construct_check_item(enabled)
        use_item.setData(QtCore.Qt.UserRole, dict(source or {}))
        self.construct_table.setItem(row, 0, use_item)
        for column, value in enumerate([component_type, name, version], start=1):
            item = QtWidgets.QTableWidgetItem(str(value))
            if column == 1:
                item.setToolTip(", ".join(CONSTRUCT_TYPES))
            self.construct_table.setItem(row, column, item)
        self.construct_table.setItem(row, 4, QtWidgets.QTableWidgetItem(str(latest_version)))
        state_item = QtWidgets.QTableWidgetItem(str(diff_state))
        if diff_state == "UPDATED":
            state_item.setBackground(QtGui.QColor(120, 90, 35))
        elif diff_state == "MISSING":
            state_item.setBackground(QtGui.QColor(120, 58, 45))
        elif diff_state == "DISABLED":
            state_item.setForeground(QtGui.QColor(150, 150, 150))
        elif diff_state == "OK":
            state_item.setBackground(QtGui.QColor(45, 95, 60))
        self.construct_table.setItem(row, 5, state_item)
        mode_item = QtWidgets.QTableWidgetItem(str(mode))
        mode_item.setToolTip(", ".join(CONSTRUCT_MODES))
        self.construct_table.setItem(row, 6, mode_item)
        self.construct_table.setItem(row, 7, QtWidgets.QTableWidgetItem(str(namespace)))
        self.construct_table.setItem(row, 8, QtWidgets.QTableWidgetItem(str(path)))
        self.construct_table.setItem(row, 9, self._construct_check_item(required))
        self.construct_table.setItem(row, 10, QtWidgets.QTableWidgetItem(str(note)))

    def _construct_check_item(self, checked: bool) -> QtWidgets.QTableWidgetItem:
        item = QtWidgets.QTableWidgetItem("")
        item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
        item.setCheckState(QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)
        return item

    def _construct_table_text(self, row: int, column: int) -> str:
        item = self.construct_table.item(row, column)
        return item.text().strip() if item else ""

    def _construct_table_checked(self, row: int, column: int, *, default: bool) -> bool:
        item = self.construct_table.item(row, column)
        if not item:
            return default
        return item.checkState() == QtCore.Qt.Checked

    def _construct_table_source(self, row: int) -> dict:
        item = self.construct_table.item(row, 0)
        data = item.data(QtCore.Qt.UserRole) if item else {}
        return dict(data or {}) if isinstance(data, dict) else {}

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
            self.populate_data_cast_list(cast_data)
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
            self.populate_data_cast_list(cast_data)
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
        sequence_identity = self.current_sequence_identity()
        self.work_table.blockSignals(True)
        self.work_table.setRowCount(0)
        if not identity and not sequence_identity:
            self.work_table.blockSignals(False)
            return
        department = self.work_dept_combo.currentText().strip()
        task = self._current_shot_task()
        option = self._current_shot_option()
        if sequence_identity:
            items = self.service.list_sequence_work_files(sequence_identity, department=department, tool_name="maya")
        else:
            items = self.service.list_shot_work_files(
                identity,
                department=department,
                option=option,
                task=task,
            )
        for item in items:
            row = self.work_table.rowCount()
            self.work_table.insertRow(row)
            values = ["", item.file, item.task, item.option, item.updated, item.comment, item.path]
            for column, value in enumerate(values):
                table_item = QtWidgets.QTableWidgetItem(str(value))
                if column != 5:
                    table_item.setFlags(table_item.flags() & ~QtCore.Qt.ItemIsEditable)
                if column == 0 and item.thumbnail and Path(item.thumbnail).exists():
                    table_item.setIcon(QtGui.QIcon(item.thumbnail))
                if column == 6:
                    table_item.setToolTip(str(value))
                self.work_table.setItem(row, column, table_item)
        self.work_table.resizeColumnsToContents()
        self.work_table.setColumnHidden(6, True)
        self.work_table.horizontalHeader().setStretchLastSection(True)
        self.work_table.blockSignals(False)

    def selected_work_scene_path(self) -> Path | None:
        row = self.work_table.currentRow()
        if row < 0:
            return None
        item = self.work_table.item(row, 6)
        if not item or not item.text().strip():
            return None
        return Path(item.text().strip())

    def _on_work_comment_changed(self, item) -> None:
        if not item or item.column() != 5:
            return
        path_item = self.work_table.item(item.row(), 6)
        if not path_item:
            return
        path = Path(path_item.text().strip())
        if not path:
            return
        try:
            from smartlib.core.metadata import read_json, sidecar_path

            metadata = read_json(sidecar_path(path), {}) or {}
            identity = self.current_identity()
            sequence_identity = self.current_sequence_identity()
            if sequence_identity:
                self.service.write_sequence_work_metadata(
                    path,
                    sequence_identity,
                    metadata.get("department") or self.work_dept_combo.currentText().strip(),
                    tool_name=metadata.get("tool") or metadata.get("option") or "maya",
                    scene_info=metadata.get("scene_info") or {},
                    comment=item.text().strip(),
                    thumbnail=metadata.get("thumbnail") or "",
                )
            elif identity:
                self.service.write_shot_work_metadata(
                    path,
                    identity,
                    metadata.get("department") or self.work_dept_combo.currentText().strip(),
                    option=metadata.get("option") or self._current_shot_option(for_save=True),
                    task=metadata.get("task") or self._current_shot_task(),
                    scene_info=metadata.get("scene_info") or {},
                    comment=item.text().strip(),
                    thumbnail=metadata.get("thumbnail") or "",
                )
            self.status_label.setText(f"Updated comment: {path.name}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Update Comment Failed", str(exc))

    def show_work_context_menu(self, pos) -> None:
        item = self.work_table.itemAt(pos)
        if not item:
            return
        self.work_table.selectRow(item.row())
        path = self.selected_work_scene_path()
        if not path:
            return
        menu = QtWidgets.QMenu(self)
        open_scene = menu.addAction("Open Scene")
        recapture_thumbnail = menu.addAction("Recapture Thumbnail")
        copy_path = menu.addAction("Copy Path")
        action = _exec_menu(menu, self.work_table.mapToGlobal(pos))
        if action == open_scene:
            self.open_work_scene()
        elif action == recapture_thumbnail:
            self.recapture_selected_work_thumbnail()
        elif action == copy_path:
            QtWidgets.QApplication.clipboard().setText(str(path))
            self.status_label.setText(f"Copied: {path}")

    def recapture_selected_work_thumbnail(self) -> None:
        path = self.selected_work_scene_path()
        if not path:
            return
        if not self.is_maya_session:
            QtWidgets.QMessageBox.information(self, "Recapture Thumbnail", "Thumbnail capture is available inside Maya.")
            return
        try:
            from smartlib.dcc.maya.thumbnail import capture_viewport_thumbnail

            thumbnail_path = self.service.thumbnail_path_for_workfile(path)
            capture_viewport_thumbnail(thumbnail_path)
            identity = self.current_identity()
            sequence_identity = self.current_sequence_identity()
            if sequence_identity:
                metadata = {}
                try:
                    from smartlib.core.metadata import read_json, sidecar_path

                    metadata = read_json(sidecar_path(path), {}) or {}
                except Exception:
                    metadata = {}
                self.service.write_sequence_work_metadata(
                    path,
                    sequence_identity,
                    metadata.get("department") or self.work_dept_combo.currentText().strip(),
                    tool_name=metadata.get("tool") or "maya",
                    scene_info=metadata.get("scene_info") or {},
                    comment=metadata.get("comment") or "",
                    thumbnail=str(thumbnail_path),
                )
            elif identity:
                metadata = {}
                try:
                    from smartlib.core.metadata import read_json, sidecar_path

                    metadata = read_json(sidecar_path(path), {}) or {}
                except Exception:
                    metadata = {}
                self.service.write_shot_work_metadata(
                    path,
                    identity,
                    metadata.get("department") or self.work_dept_combo.currentText().strip(),
                    option=metadata.get("option") or self._current_shot_option(for_save=True),
                    task=metadata.get("task") or self._current_shot_task(),
                    scene_info=metadata.get("scene_info") or {},
                    comment=metadata.get("comment") or "",
                    thumbnail=str(thumbnail_path),
                )
            self.status_label.setText(f"Updated thumbnail: {path.name}")
            self.refresh_work_files()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Recapture Thumbnail Failed", str(exc))

    def open_work_scene(self) -> None:
        identity = self.current_identity()
        sequence_identity = self.current_sequence_identity()
        if not identity and not sequence_identity:
            return
        if not self.is_maya_session:
            QtWidgets.QMessageBox.information(self, "Open Work Scene", "Open Work Scene is available inside Maya.")
            return
        path = self.selected_work_scene_path()
        if not path:
            self.status_label.setText("Select a work scene first")
            return
        if identity:
            try:
                from smartlib.apps.review_build_manager.service import (
                    ReviewBuildManagerService,
                )
                from smartlib.core.metadata import read_json, sidecar_path

                metadata = read_json(sidecar_path(path), {}) or {}
                current_construct = metadata.get("construct") or {}
                if current_construct.get("components"):
                    update_service = ReviewBuildManagerService(
                        self.service.project_config
                    )
                    desired_construct = self.service.construct_from_stage_inputs(
                        identity
                    )
                    changes = update_service.construct_diff(
                        identity,
                        current=current_construct,
                        desired=desired_construct,
                    )
                    changed = [
                        row for row in changes
                        if row.get("change") != "UNCHANGED"
                    ]
                    if changed:
                        details = "\n".join(
                            f"{(row.get('after') or row.get('before') or {}).get('name', '-')}: "
                            f"{row.get('change')}"
                            + (
                                f" [{row.get('asset_status')}]"
                                if row.get("asset_status") else ""
                            )
                            for row in changed[:12]
                        )
                        answer = QtWidgets.QMessageBox.question(
                            self,
                            "Construct Updates Available",
                            "Published components have changed.\n\n"
                            f"{details}\n\n"
                            "Yes: UPDATE Buildして最新ConstructをOpen\n"
                            "No: 現在のWork SceneをOpen",
                            QtWidgets.QMessageBox.Yes
                            | QtWidgets.QMessageBox.No
                            | QtWidgets.QMessageBox.Cancel,
                            QtWidgets.QMessageBox.Yes,
                        )
                        if answer == QtWidgets.QMessageBox.Cancel:
                            return
                        if answer == QtWidgets.QMessageBox.Yes:
                            from smartlib.apps.review_build_manager.window import show

                            manager_window = show(
                                self.service.project_config.config_dir,
                                parent=self,
                                initial_scope="Shot",
                            )
                            QtCore.QTimer.singleShot(
                                0,
                                lambda: manager_window.queue_update_and_open(identity),
                            )
                            return
            except Exception as exc:
                self.status_label.setText(
                    f"Update check skipped: {exc}"
                )
        try:
            _ensure_smartlib_on_path()
            import importlib
            from smartlib.dcc.maya import shot_builder

            importlib.invalidate_caches()
            shot_builder = importlib.reload(shot_builder)

            scene_data = self.service.load_sequence(sequence_identity) if sequence_identity else self.service.load_shot(identity)
            shot_builder.open_work_scene(path, scene_data)
            self.status_label.setText(f"Opened work scene: {path.name}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Open Work Scene Failed", str(exc))

    def save_work_scene(self) -> None:
        identity = self.current_identity()
        sequence_identity = self.current_sequence_identity()
        if not identity and not sequence_identity:
            return
        if not self.is_maya_session:
            QtWidgets.QMessageBox.information(self, "Save Work Scene", "Shot work save is available inside Maya.")
            return
        department = self.work_dept_combo.currentText().strip() or self.service.shot_departments[0]
        task = self._current_shot_task()
        option = self._current_shot_option(for_save=True)
        comment = ""
        try:
            _ensure_smartlib_on_path()
            import maya.cmds as cmds
            from smartlib.dcc.maya.shot_builder import save_current_scene
            from smartlib.dcc.maya.thumbnail import capture_viewport_thumbnail

            current_path = cmds.file(query=True, sceneName=True) or None
            selected_path = self.selected_work_scene_path()
            base_path = current_path
            construct_source = bool(
                current_path
                and self._opened_construct_scene_path
                and Path(current_path).resolve() == Path(self._opened_construct_scene_path).resolve()
            )
            if construct_source and identity:
                existing_work = self.service.list_shot_work_files(
                    identity,
                    department=department,
                    option=None if option == "all" else option,
                    task=task,
                )
                base_path = existing_work[0].path if existing_work else None
            elif selected_path and self._should_use_selected_work_as_save_base(current_path, selected_path):
                base_path = selected_path
            if sequence_identity:
                tool_name = "maya"
                target_path = self.service.next_sequence_work_path(
                    sequence_identity,
                    department,
                    current_path=base_path,
                    tool_name=tool_name,
                )
                scene_data = self.service.load_sequence(sequence_identity)
            else:
                tool_name = ""
                target_path = self.service.next_shot_work_path(
                    identity,
                    department,
                    current_path=base_path,
                    option=option,
                    task=task,
                )
                scene_data = self.service.load_shot(identity)
            scene_info = save_current_scene(target_path, scene_data)
            parsed = self.service.parse_work_file(target_path.name) if hasattr(self.service, "parse_work_file") else None
            if parsed is None:
                from smartlib.apps.shot_manager.service import parse_shot_work_file

                parsed = parse_shot_work_file(target_path.name) or {}
            token_context = self.current_token_context(
                department=department,
                task=task,
                tool=tool_name or "maya",
                subset=option,
                version=f"v{int(parsed.get('version') or 0):03d}" if parsed.get("version") else "",
                take=f"{int(parsed.get('take') or 0):02d}" if parsed.get("take") else "",
            ).to_dict()
            thumbnail_path = self.service.thumbnail_path_for_workfile(target_path)
            try:
                capture_viewport_thumbnail(thumbnail_path)
            except Exception:
                thumbnail_path = ""
            if sequence_identity:
                self.service.write_sequence_work_metadata(
                    target_path,
                    sequence_identity,
                    department,
                    tool_name=tool_name,
                    scene_info=scene_info,
                    comment=comment,
                    thumbnail=str(thumbnail_path) if thumbnail_path else "",
                    token_context=token_context,
                )
            else:
                construct_data = {"components": self.construct_table_rows()} if self.construct_table.rowCount() else self.service.load_construct(identity)
                self.service.write_shot_work_metadata(
                    target_path,
                    identity,
                    department,
                    option=option,
                    task=task,
                    scene_info=scene_info,
                    comment=comment,
                    thumbnail=str(thumbnail_path) if thumbnail_path else "",
                    token_context=token_context,
                    construct_data=construct_data,
                )
            self.status_label.setText(f"Saved work scene: {target_path}")
            self._opened_construct_scene_path = ""
            self._opened_construct_record = {}
            self.refresh_work_files()
            self.populate_construct_scene_list(identity) if identity else None
            self.tabs.setCurrentWidget(self.work_tab)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save Work Scene Failed", str(exc))

    def _should_use_selected_work_as_save_base(self, current_path: str | None, selected_path: Path) -> bool:
        if not current_path:
            return True
        current = Path(current_path)
        if current.name.lower() in {"untitled", ""}:
            return True
        if "publish" in {part.lower() for part in current.parts}:
            return True
        try:
            current.resolve().relative_to(selected_path.parent.resolve())
            return False
        except Exception:
            return not current.name == selected_path.name

    def archive_scene_snapshot(self) -> None:
        identity = self.current_identity()
        sequence_identity = self.current_sequence_identity()
        if not identity:
            if sequence_identity:
                QtWidgets.QMessageBox.information(
                    self,
                    "Archive Scene",
                    "Archive Scene is currently for shot work scenes. Select a shot detail first.",
                )
            return
        if not self.is_maya_session:
            QtWidgets.QMessageBox.information(self, "Archive Scene", "Archive Scene is available inside Maya.")
            return
        department = self.work_dept_combo.currentText().strip() or self.service.shot_departments[0]
        option = self._current_shot_option(for_save=True)
        try:
            _ensure_smartlib_on_path()
            import maya.cmds as cmds
            from smartlib.dcc.maya.shot_builder import archive_current_scene

            source_workfile = cmds.file(query=True, sceneName=True) or ""
            ext = Path(source_workfile).suffix.lower().lstrip(".") if source_workfile else "ma"
            if ext not in {"ma", "mb"}:
                ext = "ma"
            target_path = self.service.next_shot_scene_archive_path(identity, department, ext=ext)
            scene_info = archive_current_scene(target_path, self.service.load_shot(identity))
            self.service.register_shot_scene_archive(
                identity,
                department,
                target_path,
                source_workfile=source_workfile,
                option=option,
                scene_info=scene_info,
                token_context=self.current_token_context(
                    department=department,
                    task=self._current_shot_task(),
                    tool="maya",
                    subset=option,
                    version=target_path.parent.name,
                ).to_dict(),
            )
            self.status_label.setText(f"Archived scene snapshot: {target_path}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Archive Scene Failed", str(exc))

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

            contract = dict(cast_data)
            contract["review_layers"] = self.service.review_layers(
                identity,
                self.work_dept_combo.currentText().strip() or "anim",
            )
            result = create_review_display_layers(contract)
            summary = ", ".join(f"{name}: {count}" for name, count in sorted(result.items()))
            self.status_label.setText(f"Created review layers: {summary}")
            self.validate_current_cast(update_tab=True)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Create Review Layers Failed", str(exc))

    def open_review_layer_manager(self) -> None:
        identity = self.current_identity()
        if not identity:
            return
        try:
            import importlib

            _ensure_smartlib_on_path()
            scripts_root = str(Path(__file__).resolve().parent)
            if scripts_root not in sys.path:
                sys.path.insert(0, scripts_root)
            import review_layer_ui

            importlib.reload(review_layer_ui)
            review_layer_ui.show(
                identity=identity,
                config_dir=self.service.project_config.config_dir,
                department=self.work_dept_combo.currentText().strip(),
                parent=self,
            )
            self.status_label.setText(f"Opened Review Layer Manager: {identity.code}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Review Layer Manager Failed", str(exc))

    def plan_review_publish(self) -> None:
        identity = self.current_identity()
        if not identity:
            return
        department = self.work_dept_combo.currentText().strip() or "anim"
        comment, accepted = QtWidgets.QInputDialog.getText(self, "Plan Review Publish", "Comment")
        if not accepted:
            return
        try:
            source_workfile = ""
            if self.is_maya_session:
                try:
                    import maya.cmds as cmds

                    source_workfile = cmds.file(query=True, sceneName=True) or ""
                except Exception:
                    source_workfile = ""
            plan = self.service.plan_review_playblast_take(
                identity,
                department,
                source_workfile=source_workfile,
                comment=comment,
                write=True,
            )
            self.populate_preview_history()
            self.status_label.setText(f"Planned review publish: {plan.version_dir}")
            self._show_review_plan_dialog(plan)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Plan Review Publish Failed", str(exc))

    def _show_review_plan_dialog(self, plan) -> None:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Review Publish Plan")
        dialog.resize(720, 520)
        layout = QtWidgets.QVBoxLayout(dialog)
        path_label = QtWidgets.QLabel(str(plan.version_dir))
        path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(path_label)
        text = QtWidgets.QPlainTextEdit()
        text.setReadOnly(True)
        review_data = dict(plan.review_data)
        review_data.pop("publish", None)
        text.setPlainText(json.dumps(review_data, indent=2, ensure_ascii=False))
        layout.addWidget(text, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def export_beauty_playblast(self) -> None:
        identity = self.current_identity()
        if not identity:
            return
        if not self.is_maya_session:
            QtWidgets.QMessageBox.information(self, "Export Beauty Playblast", "Export Beauty Playblast is available inside Maya.")
            return
        department = self.work_dept_combo.currentText().strip() or "anim"
        comment, accepted = QtWidgets.QInputDialog.getText(self, "Export Beauty Playblast", "Comment")
        if not accepted:
            return
        try:
            import maya.cmds as cmds
            from smartlib.dcc.maya.review_playblast import export_beauty_sequences

            source_workfile = cmds.file(query=True, sceneName=True) or ""
            plan = self.service.plan_review_publish(
                identity,
                department,
                source_workfile=source_workfile,
                comment=comment,
                write=True,
            )
            exported = export_beauty_sequences(plan)
            self.populate_preview_history()
            self.status_label.setText(f"Exported beauty playblast: {plan.version_dir}")
            QtWidgets.QMessageBox.information(
                self,
                "Export Beauty Playblast",
                "Exported beauty sequences:\n"
                + "\n".join(
                    f"{layer}: {data.get('file_count', 0)} frames"
                    for layer, data in sorted(exported.items())
                ),
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Export Beauty Playblast Failed", str(exc))

    def publish_animation_curves(self) -> None:
        identity = self.current_identity()
        if not identity:
            self.status_label.setText("Select a shot first")
            return
        if not self.is_maya_session:
            QtWidgets.QMessageBox.information(self, "Export Animation Curves", "Available inside Maya.")
            return
        comment, accepted = QtWidgets.QInputDialog.getText(self, "Export Animation Curves", "Comment")
        if not accepted:
            return
        try:
            _ensure_smartlib_on_path()
            import maya.cmds as cmds
            from smartlib.dcc.maya.animation_curves import collect_animation_curves_for_cast

            source_workfile = cmds.file(query=True, sceneName=True) or ""
            cast_row = self._selected_data_cast_row_data()
            if not cast_row:
                QtWidgets.QMessageBox.warning(self, "Export Animation Curves", "Select one cast in the Data tab first.")
                return
            curve_data = collect_animation_curves_for_cast(
                cast_key=cast_row["cast_key"],
                asset=cast_row["asset"],
                namespace=cast_row["namespace"] or cast_row["cast_key"],
                source_workfile=source_workfile,
            )
            if not curve_data.get("curves"):
                namespace = cast_row["namespace"] or cast_row["cast_key"]
                QtWidgets.QMessageBox.warning(
                    self,
                    "Export Animation Curves",
                    f"No animation curves were found in namespace {namespace}.",
                )
                return
            path = self.service.export_animation_curves_data(
                identity,
                curve_data,
                target=cast_row["cast_key"],
                subset="curves",
                source_workfile=source_workfile,
                comment=comment.strip(),
            )
            self.status_label.setText(f"Exported animation curve data: {path}")
            self.populate_data_tree()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Export Animation Curves Failed", str(exc))

    def apply_animation_curves(self) -> None:
        identity = self.current_identity()
        if not identity:
            self.status_label.setText("Select a shot first")
            return
        if not self.is_maya_session:
            QtWidgets.QMessageBox.information(self, "Apply Animation Curves", "Available inside Maya.")
            return
        cast_row = self._selected_data_cast_row_data()
        if not cast_row:
            QtWidgets.QMessageBox.warning(self, "Apply Animation Curves", "Select one cast in the Data tab first.")
            return
        try:
            _ensure_smartlib_on_path()
            from smartlib.dcc.maya.animation_curves import (
                AnimationCurveApplyError,
                apply_animation_curves_from_file,
            )

            path = self._selected_animation_curve_data_path(cast_row["cast_key"])
            if not path:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Apply Animation Curves",
                    "Select an animation curve version in the Data tree first.",
                )
                return
            accepted = QtWidgets.QMessageBox.question(
                self,
                "Apply Animation Curves",
                f"Apply animation curves to {cast_row['namespace'] or cast_row['cast_key']}?\n\nVersion file:\n{path}\n\nExisting keys will be cleared.",
            )
            if accepted != QtWidgets.QMessageBox.Yes:
                return
            try:
                result = apply_animation_curves_from_file(
                    path,
                    namespace=cast_row["namespace"] or cast_row["cast_key"],
                    clear_existing=True,
                )
            except AnimationCurveApplyError as exc:
                self._show_animation_remap_report(exc.report, title="Animation Curve Remap Failed")
                raise
            self.status_label.setText(
                f"Applied animation curves: {result.get('applied_destinations', 0)} attrs / {result.get('applied_keys', 0)} keys"
            )
            QtWidgets.QMessageBox.information(
                self,
                "Apply Animation Curves",
                f"Applied {result.get('applied_destinations', 0)} attributes / {result.get('applied_keys', 0)} keys.",
            )
        except Exception as exc:
            if exc.__class__.__name__ == "AnimationCurveApplyError":
                self.status_label.setText(str(exc))
            else:
                QtWidgets.QMessageBox.critical(self, "Apply Animation Curves Failed", str(exc))

    def publish_animation(self) -> None:
        identity = self.current_identity()
        if not identity:
            self.status_label.setText("Select a shot first")
            return
        comment, accepted = QtWidgets.QInputDialog.getText(
            self,
            "Publish Animation Package",
            "Comment",
        )
        if not accepted:
            return
        try:
            published = self.service.build_animation_package_snapshot(
                identity,
                comment=comment.strip(),
                preferred_format="abc",
            )
            self.status_label.setText(f"Published animation package: {published}")
            self._populate_publish_targets()
            self.populate_publish_tree()
            QtWidgets.QMessageBox.information(
                self,
                "Publish Animation Package",
                f"Published animation package:\n{published}",
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Publish Animation Package Failed", str(exc))

    def publish_animation_cache(self) -> None:
        self._publish_animation_cache_format(format_name="usd", subset="cache")

    def publish_animation_alembic_cache(self) -> None:
        self._publish_animation_cache_format(format_name="abc", subset="alembic")

    def _publish_animation_cache_format(self, *, format_name: str, subset: str) -> None:
        identity = self.current_identity()
        if not identity:
            self.status_label.setText("Select a shot first")
            return
        if not self.is_maya_session:
            QtWidgets.QMessageBox.information(self, "Publish Cache", "Available inside Maya.")
            return
        cast_row = self._selected_publish_cast_row_data()
        if not cast_row:
            QtWidgets.QMessageBox.warning(self, "Publish Cache", "Select one cast in the Publish tab first.")
            return
        comment, accepted = QtWidgets.QInputDialog.getText(self, "Publish Cache", "Comment")
        if not accepted:
            return
        try:
            import maya.cmds as cmds
            from smartlib.dcc.maya.animation_curves import (
                collect_animation_curves_for_cast,
                export_animation_geometry_cache,
            )

            namespace = cast_row["namespace"] or cast_row["cast_key"]
            if hasattr(self.service, "shot_frame_range"):
                start, end = self.service.shot_frame_range(identity)
            else:
                shot_data = self.service.load_shot(identity)
                editorial = shot_data.get("editorial") or {}
                frame_range = editorial.get("frame_range") or shot_data.get("frame_range")
                if isinstance(frame_range, (list, tuple)) and len(frame_range) >= 2:
                    start, end = int(frame_range[0]), int(frame_range[1])
                else:
                    start = int(editorial.get("cut_in", shot_data.get("cut_in")))
                    end = int(editorial.get("cut_out", shot_data.get("cut_out")))
                if end < start:
                    raise RuntimeError(f"Invalid shot.json frame range: {start}-{end}")
            source_workfile = cmds.file(query=True, sceneName=True) or ""
            curve_data_path = self._selected_animation_curve_data_path(cast_row["cast_key"])
            if not curve_data_path:
                curve_data = collect_animation_curves_for_cast(
                    cast_key=cast_row["cast_key"],
                    asset=cast_row["asset"],
                    namespace=namespace,
                    source_workfile=source_workfile,
                )
                if curve_data.get("curves"):
                    curve_data_file = self.service.export_animation_curves_data(
                        identity,
                        curve_data,
                        target=cast_row["cast_key"],
                        subset="curves",
                        source_workfile=source_workfile,
                        comment=comment.strip(),
                    )
                    curve_data_path = self.service.publish_animation_from_data(
                        identity,
                        curve_data_file,
                        target=cast_row["cast_key"],
                        subset="curves",
                        comment=comment.strip(),
                    )
            plan = self.service.plan_animation_cache_publish(
                identity,
                target=cast_row["cast_key"],
                subset=subset,
            )
            usd_skel_contract = self.service.project_config.usd_skel_contract
            result = export_animation_geometry_cache(
                namespace=namespace,
                output_dir=plan["version_dir"],
                frame_range=(start, end),
                skeleton_set=usd_skel_contract.get("skeleton_set", "skel_export_set"),
                formats=(format_name,),
            )
            rig_dependency = self.service.resolve_asset_rig_usd_dependency(
                cast_row["asset"],
                cast_row["variant"],
                subset="anim",
                preferred_context="work",
            )
            if format_name == "usd" and result.get("usd_kind") == "usd_skel_animation" and not rig_dependency:
                raise RuntimeError(
                    f"Asset USD dependency was not found for {cast_row['asset']} / "
                    f"{cast_row['variant']}. Pack an Asset Context, or publish rig/anim as a fallback."
                )
            cache_path = self.service.finalize_animation_cache_publish(
                identity,
                result,
                target=cast_row["cast_key"],
                asset=cast_row["asset"],
                variant=cast_row["variant"],
                namespace=namespace,
                source_workfile=source_workfile,
                curve_data_path=curve_data_path,
                rig_dependency=rig_dependency,
                comment=comment.strip(),
                version=plan["version"],
                subset=subset,
            )
            self._populate_publish_targets()
            self.populate_publish_tree()
            label = "Animation USD" if format_name == "usd" else "Alembic cache"
            self.status_label.setText(f"Published {label}: {cache_path}")
            QtWidgets.QMessageBox.information(
                self,
                "Publish Cache",
                f"Published {label}:\n{cache_path.parent}",
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Publish Cache Failed", str(exc))

    def build_animation_package(self) -> None:
        identity = self.current_identity()
        if not identity:
            self.status_label.setText("Select a shot first")
            return
        comment, accepted = QtWidgets.QInputDialog.getText(
            self,
            "Build Animation Package",
            "Comment",
        )
        if not accepted:
            return
        try:
            manifest_path = self.service.build_animation_package_snapshot(
                identity,
                comment=comment.strip(),
                preferred_format="abc",
            )
            from smartlib.core.metadata import read_json

            package_data = read_json(manifest_path, {}) or {}
            missing = package_data.get("missing_casts") or []
            self.populate_data_tree()
            self.status_label.setText(f"Built animation package: {manifest_path}")
            message = f"Built animation package:\n{manifest_path}"
            if missing:
                message += "\n\nNo cache (not included):\n" + "\n".join(
                    f"- {item.get('cast_key')}: {item.get('reason')}"
                    for item in missing
                )
            QtWidgets.QMessageBox.information(self, "Build Animation Package", message)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Build Animation Package Failed", str(exc))

    def build_animation_review_scene(self) -> None:
        identity = self.current_identity()
        if not identity:
            self.status_label.setText("Select a shot first")
            return
        if not self.is_maya_session:
            QtWidgets.QMessageBox.information(
                self,
                "Build Animation Review Scene",
                "Available inside Maya.",
            )
            return
        try:
            import maya.cmds as cmds
            from smartlib.core.metadata import read_json
            from smartlib.dcc.maya.shot_builder import build_animation_review_scene

            package_path = self._selected_animation_package_path()
            plan = self.service.animation_review_build_plan(
                identity,
                package_path=package_path or None,
            )
            manifest = read_json(plan["animation_manifest"], {}) or {}
            animated_cast = set((manifest.get("casts") or {}).keys())
            preview = self.service.build_preview(
                identity,
                department=self.work_dept_combo.currentText().strip() or "anim",
            )
            static_items = [
                item
                for item in preview
                if item.status == "resolved" and item.cast_key not in animated_cast
            ]
            unresolved_static = [
                item
                for item in preview
                if item.cast_key not in animated_cast and item.required and item.status != "resolved"
            ]
            if unresolved_static:
                details = "\n".join(
                    f"- {item.cast_key}: {item.message or item.status}"
                    for item in unresolved_static
                )
                raise RuntimeError(f"Required static cast is unresolved:\n{details}")

            current_scene = cmds.file(query=True, sceneName=True) or "untitled"
            modified = bool(cmds.file(query=True, modified=True))
            message = (
                "The current scene will be replaced by a generated review scene.\n\n"
                f"Current: {current_scene}\n"
                f"Package: {plan['package_version']}\n"
                f"Animated caches: {len(animated_cast)}\n"
                f"Static references: {len(static_items)}\n"
                f"Cameras: {len(plan.get('camera_paths') or [])}\n\n"
                f"Output:\n{plan['scene_path']}"
            )
            if modified:
                message = "The current scene has unsaved changes.\n\n" + message
            accepted = QtWidgets.QMessageBox.question(
                self,
                "Build Animation Review Scene",
                message,
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel,
            )
            if accepted != QtWidgets.QMessageBox.Yes:
                return

            result = build_animation_review_scene(
                plan,
                self.service.load_shot(identity),
                static_items,
                project_root=self.service.project_config.project_root,
            )
            build_manifest = self.service.write_animation_review_build_manifest(plan, result)
            self.status_label.setText(f"Built Animation Review Scene: {result['scene_path']}")
            warning_count = len(result.get("set_dress_warnings") or [])
            completed = (
                f"Built Animation Review Scene:\n{result['scene_path']}\n\n"
                f"Animation caches: {len(result.get('imported_caches') or [])}\n"
                f"Static references: {len(result.get('static_references') or [])}\n"
                f"Cameras: {len(result.get('cameras') or [])}\n"
                f"Set Dress warnings: {warning_count}\n"
                f"Build manifest: {build_manifest}"
            )
            QtWidgets.QMessageBox.information(
                self,
                "Build Animation Review Scene",
                completed,
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "Build Animation Review Scene Failed",
                str(exc),
            )

    def export_camera_data(self) -> None:
        self._export_scene_component_data("camera")

    def export_scene_component_data(self) -> None:
        self._export_scene_component_data(self._current_data_type())

    def apply_scene_component_data(self) -> None:
        self._apply_scene_component_data(self._current_data_type(), from_data_tree=True)

    def apply_camera_data(self) -> None:
        self._apply_scene_component_data("camera")

    def publish_camera_data(self) -> None:
        self._publish_scene_component_data("camera")

    def _export_scene_component_data(self, data_type: str) -> None:
        identity = self.active_shot_identity or self.current_identity()
        sequence_identity = self.active_sequence_identity or self.current_sequence_identity()
        title = str(data_type).replace("_", " ").title()
        if not identity and not sequence_identity:
            self.status_label.setText("Select a shot or sequence first")
            return
        if not self.is_maya_session:
            QtWidgets.QMessageBox.information(self, f"Export {title}", "Available inside Maya.")
            return
        target = self._current_data_target()
        if not target:
            QtWidgets.QMessageBox.warning(self, f"Export {title}", f"Select one {title} first.")
            return
        comment, accepted = QtWidgets.QInputDialog.getText(self, f"Export {title}", "Comment")
        if not accepted:
            return
        try:
            import maya.cmds as cmds
            if data_type == "playblast_settings":
                from smartlib.dcc.maya.review_playblast import load_scene_playblast_settings

                payload = load_scene_playblast_settings(cmds)
                if not payload:
                    raise RuntimeError("Smart Playblast settings were not found in the current scene.")
                kwargs = dict(
                    target=self._data_target_token(target), subset="main",
                    filename="playblast_settings.json",
                    source_workfile=cmds.file(query=True, sceneName=True) or "",
                    comment=comment.strip(),
                )
                if sequence_identity:
                    path = self.service.export_sequence_scene_data(
                        sequence_identity, data_type, payload,
                        department=self.work_dept_combo.currentText().strip() or "layout",
                        **kwargs,
                    )
                else:
                    path = self.service.export_shot_scene_data(identity, data_type, payload, **kwargs)
                self.status_label.setText(f"Exported {title} data: {path}")
                self.populate_data_tree()
                return
            from smartlib.dcc.maya.shot_scene_data import (
                collect_scene_component_data,
                export_scene_component_selection,
            )

            payload = collect_scene_component_data(target, data_type)
            kwargs = dict(
                target=self._data_target_token(target), subset="main",
                filename=f"{data_type}.json",
                source_workfile=cmds.file(query=True, sceneName=True) or "",
                comment=comment.strip(),
            )
            if sequence_identity:
                path = self.service.export_sequence_scene_data(
                    sequence_identity, data_type, payload,
                    department=self.work_dept_combo.currentText().strip() or "layout",
                    **kwargs,
                )
            else:
                path = self.service.export_shot_scene_data(identity, data_type, payload, **kwargs)
            export_result = export_scene_component_selection(target, data_type, path.parent)
            self.service.register_scene_data_files(
                path,
                export_result.get("files") or {},
                errors=export_result.get("errors") or {},
            )
            self.status_label.setText(f"Exported {title} data: {path}")
            self.populate_data_tree()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, f"Export {title} Failed", str(exc))

    def _apply_scene_component_data(self, data_type: str, *, from_data_tree: bool = False) -> None:
        identity = self.active_shot_identity or self.current_identity()
        sequence_identity = self.active_sequence_identity or self.current_sequence_identity()
        title = str(data_type).replace("_", " ").title()
        if not identity and not sequence_identity:
            self.status_label.setText("Select a shot or sequence first")
            return
        if not self.is_maya_session:
            QtWidgets.QMessageBox.information(self, f"Apply {title}", "Available inside Maya.")
            return
        path = self._selected_scene_component_data_path(data_type, from_data_tree=from_data_tree)
        if not path:
            QtWidgets.QMessageBox.warning(
                self,
                f"Apply {title}",
                f"Select a {title} version first.",
            )
            return
        try:
            if data_type == "playblast_settings":
                from smartlib.core.metadata import read_json
                from smartlib.dcc.maya.review_playblast import save_scene_playblast_settings

                save_scene_playblast_settings(read_json(path, {}) or {})
                self.status_label.setText(f"Applied {title}: {path}")
                return
            from smartlib.dcc.maya.shot_scene_data import import_scene_component_package

            result = import_scene_component_package(path)
            self.status_label.setText(f"Applied {title}: {', '.join(result)}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, f"Apply {title} Failed", str(exc))

    def _publish_scene_component_data(self, data_type: str) -> None:
        identity = self.current_identity()
        title = "Camera"
        if not identity:
            self.status_label.setText("Select a shot first")
            return
        if not self.is_maya_session:
            QtWidgets.QMessageBox.information(self, f"Publish {title}", "Available inside Maya.")
            return
        target = self._current_publish_target()
        if not target:
            QtWidgets.QMessageBox.warning(self, f"Publish {title}", f"Select one {title} first.")
            return
        comment, accepted = QtWidgets.QInputDialog.getText(self, f"Publish {title}", "Comment")
        if not accepted:
            return
        try:
            import maya.cmds as cmds
            from smartlib.dcc.maya.shot_scene_data import (
                collect_camera_data,
                export_camera_selection,
            )

            payload = collect_camera_data(target)
            published = self.service.publish_shot_scene_snapshot(
                identity,
                payload,
                data_type=data_type,
                target=self._data_target_token(target),
                subset="main",
                source_workfile=cmds.file(query=True, sceneName=True) or "",
                comment=comment.strip(),
            )
            export_result = export_camera_selection(target, published.parent)
            self.service.register_shot_scene_publish_files(
                published,
                export_result.get("files") or {},
                errors=export_result.get("errors") or {},
            )
            self.status_label.setText(f"Published {title}: {published}")
            self._populate_publish_targets()
            self.populate_publish_tree()
            exported_names = ", ".join(
                sorted((export_result.get("files") or {}).values())
            )
            QtWidgets.QMessageBox.information(
                self,
                f"Publish {title}",
                f"Published:\n{published}\n\nSelection exports: {exported_names}",
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, f"Publish {title} Failed", str(exc))

    def _selected_scene_component_data_path(self, data_type: str, *, from_data_tree: bool = False) -> str:
        tree = self.shot_data_tree if from_data_tree else self.publish_tree
        item = tree.currentItem()
        if not item:
            return ""
        raw_path = item.data(0, QtCore.Qt.UserRole)
        if not raw_path:
            return ""
        path = Path(str(raw_path))
        if path.is_dir():
            path = path / f"{data_type}.json"
        if path.name != f"{data_type}.json" or not path.exists():
            return ""
        return str(path)

    def apply_set_dress(self) -> None:
        if not self.is_maya_session:
            QtWidgets.QMessageBox.information(
                self, "Apply Set Dress", "Available inside Maya."
            )
            return
        item = self.publish_tree.currentItem()
        raw_path = item.data(0, QtCore.Qt.UserRole) if item else ""
        path = Path(str(raw_path)) if raw_path else None
        if not path or not path.exists():
            QtWidgets.QMessageBox.warning(
                self,
                "Apply Set Dress",
                "Select a published Set Dress version in the Publish tree first.",
            )
            return
        try:
            from smartlib.dcc.maya import set_dress

            package = set_dress.load_package(path)
            warnings = set_dress.apply_stack(package.layers)
            message = f"Applied Set Dress: {path.name}"
            if warnings:
                message += f" ({len(warnings)} warnings)"
            self.status_label.setText(message)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, "Apply Set Dress Failed", str(exc)
            )

    def publish_preview_render(self) -> None:
        identity = self.current_identity()
        if not identity:
            self.status_label.setText("Select a shot first")
            return
        comment, accepted = QtWidgets.QInputDialog.getText(
            self,
            "Publish Preview Render",
            "Comment",
        )
        if not accepted:
            return
        try:
            department = self._current_publish_target() or self.work_dept_combo.currentText().strip() or "default"
            path = self.service.publish_preview_render_outputs(
                identity,
                department=self._data_target_token(department),
                comment=comment,
            )
            self.status_label.setText(f"Published Preview Render: {path}")
            self._populate_publish_targets()
            self.populate_publish_tree()
            QtWidgets.QMessageBox.information(
                self,
                "Publish Preview Render",
                f"Published:\n{path}",
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "Publish Preview Render Failed",
                str(exc),
            )

    def _selected_animation_curve_data_path(self, cast_key: str) -> str:
        item = self.shot_data_tree.currentItem()
        if not item:
            return ""
        raw_path = item.data(0, QtCore.Qt.UserRole)
        if not raw_path:
            return ""
        path = Path(str(raw_path))
        if path.is_dir():
            path = path / "animation_curve.json"
        if path.name != "animation_curve.json" or not path.exists():
            return ""
        normalized = path.as_posix()
        expected = f"/data/animation/{cast_key}/curves/"
        legacy_expected = f"/publish/animation/{cast_key}/curves/"
        if expected not in normalized and legacy_expected not in normalized:
            return ""
        return str(path)

    def _selected_animation_package_path(self) -> str:
        item = self.publish_tree.currentItem()
        if not item:
            return ""
        raw_path = item.data(0, QtCore.Qt.UserRole)
        path = Path(str(raw_path)) if raw_path else None
        if not path or path.name != "animation_manifest.json" or not path.is_file():
            return ""
        return str(path)

    def _show_animation_remap_report(self, report: list[dict], *, title: str) -> None:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(900, 520)
        layout = QtWidgets.QVBoxLayout(dialog)
        summary = QtWidgets.QLabel(
            f"Missing: {len([item for item in report if item.get('state') != 'FOUND'])} / {len(report)} destinations"
        )
        table = QtWidgets.QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["State", "Source", "Target", "Curve"])
        table.horizontalHeader().setStretchLastSection(True)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setRowCount(len(report))
        for row, item_data in enumerate(report):
            values = [
                item_data.get("state", ""),
                item_data.get("source", ""),
                item_data.get("target", ""),
                item_data.get("curve", ""),
            ]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(str(value))
                if item_data.get("state") == "MISSING":
                    item.setBackground(QtGui.QColor("#6d3939"))
                table.setItem(row, column, item)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(summary)
        layout.addWidget(table, 1)
        layout.addWidget(close_btn)
        dialog.exec_()

    def _selected_cast_row_data(self) -> dict | None:
        row = self.cast_table.currentRow()
        if row < 0:
            return None
        return self._cast_row_payload(row)

    def _selected_data_cast_row_data(self) -> dict | None:
        item = self.data_cast_list.currentItem()
        if not item:
            return None
        data = item.data(QtCore.Qt.UserRole)
        return dict(data) if isinstance(data, dict) else None

    def _selected_publish_cast_row_data(self) -> dict | None:
        item = self.publish_target_list.currentItem()
        if not item:
            return None
        data = item.data(QtCore.Qt.UserRole)
        return dict(data) if isinstance(data, dict) else None

    def _sync_data_cast_list_from_table(self) -> None:
        self.populate_data_cast_list({"cast": {row["cast_key"]: row for row in self.cast_table_rows() if row.get("cast_key")}})

    def cast_table_rows(self) -> list[dict]:
        rows = []
        for row in range(self.cast_table.rowCount()):
            values = self._cast_row_payload(row)
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
        category: str = "",
        group: str = "",
        status: str = "Wait",
        thumbnail: str = "",
        context_statuses: dict | None = None,
    ) -> None:
        row = self.cast_table.rowCount()
        self.cast_table.insertRow(row)
        payload = {
            "cast_key": cast_key,
            "asset": asset,
            "variant": variant or "default",
            "role": role or "CHA",
            "namespace": namespace or cast_key,
            "asset_publish": asset_publish or "approved",
            "required": bool(required),
            "note": note,
        }
        contexts = context_statuses or {}
        values = ["", category, group, asset, variant or "default"]
        for context_name in ("FAST", "WORK", "FINAL"):
            value = contexts.get(context_name) or contexts.get(context_name.lower()) or "Missing"
            if isinstance(value, dict):
                value = value.get("state") or value.get("status") or value.get("version") or "Missing"
            values.append(str(value))
        values.append(status or "Wait")
        status_colors = {
            "missing": "#ff1744",
            "ready": "#00c853",
            "wip": "#ffc400",
        }
        for column, value in enumerate(values):
            item = QtWidgets.QTableWidgetItem(str(value))
            status_color = status_colors.get(str(value).strip().lower())
            if status_color:
                item.setForeground(QtGui.QBrush(QtGui.QColor(status_color)))
            if column == 0:
                item.setData(QtCore.Qt.UserRole, payload)
                if thumbnail and Path(str(thumbnail)).is_file():
                    item.setIcon(QtGui.QIcon(str(thumbnail)))
            self.cast_table.setItem(row, column, item)

    def _cast_row_payload(self, row: int) -> dict:
        item = self.cast_table.item(row, 0)
        payload = item.data(QtCore.Qt.UserRole) if item else None
        if isinstance(payload, dict):
            return dict(payload)
        return {
            "cast_key": self._table_text(row, 0),
            "asset": self._table_text(row, 3),
            "variant": self._table_text(row, 4) or "default",
            "role": "CHA",
            "namespace": self._table_text(row, 3),
            "asset_publish": "approved",
            "required": True,
            "note": "",
        }

    def _table_text(self, row: int, column: int) -> str:
        item = self.cast_table.item(row, column)
        return item.text().strip() if item else ""

    def _table_required(self, row: int) -> bool:
        return bool(self._cast_row_payload(row).get("required", True))

    def _select_identity(self, identity) -> None:
        for item in self._shot_items():
            data = item.data(0, QtCore.Qt.UserRole)
            item_identity = data.get("identity") if isinstance(data, dict) else data
            if item_identity and item_identity.code == identity.code:
                self.shot_list.setCurrentItem(item)
                return

    def _first_shot_item(self):
        return next(self._shot_items(), None)

    def _shot_items(self):
        for index in range(self.shot_list.topLevelItemCount()):
            item = self.shot_list.topLevelItem(index)
            data = item.data(0, QtCore.Qt.UserRole)
            if data and (not isinstance(data, dict) or data.get("kind") == "shot"):
                yield item


_window = None


def show(config_dir: str | os.PathLike[str] | None = None, parent=None):
    global _window
    try:
        _window.close()
    except Exception:
        pass
    _ensure_smartlib_on_path()
    from smartlib.core.qt import parent_for_maya

    window_parent = parent_for_maya(QtWidgets, parent)
    _window = ShotManagerWindow(config_dir=config_dir, parent=window_parent)
    if window_parent is not None:
        _window.setWindowFlags(_window.windowFlags() | QtCore.Qt.Window)
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window


def _read_json_file(path: str | os.PathLike[str], default=None):
    try:
        with Path(path).open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except Exception:
        return default


if __name__ == "__main__":
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    show()
    sys.exit(app.exec())

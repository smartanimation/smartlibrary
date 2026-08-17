from __future__ import annotations

import os
import sys
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

try:
    if __package__:
        from .asset_manager import Asset, AssetManager
    else:
        from asset_manager import Asset, AssetManager
except ImportError:
    from scripts.asset_manager import Asset, AssetManager


def _qt_modules():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets

        return QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets

        return QtCore, QtGui, QtWidgets


QtCore, QtGui, QtWidgets = _qt_modules()


def _exec_menu(menu, pos):
    """Execute a context menu with either the Qt 6 or Qt 5 API."""
    exec_method = getattr(menu, "exec", None)
    if exec_method is None:
        exec_method = menu.exec_
    return exec_method(pos)


def _ensure_smartlib_on_path() -> None:
    root = (
        os.environ.get("SMARTPIPELINE_ROOT")
        or os.environ.get("SMARTLIBRARY_ROOT")
        or str(Path(__file__).resolve().parents[1])
    )
    package_dir = str(Path(root) / "packages")
    if package_dir not in sys.path:
        sys.path.insert(0, package_dir)


def _asset_service(config_dir: str | os.PathLike[str]):
    _ensure_smartlib_on_path()
    from smartlib.apps.asset_manager import AssetCreateRequest, AssetManagerService
    from smartlib.core.config_loader import ProjectConfig

    return AssetManagerService(ProjectConfig(config_dir)), AssetCreateRequest


def _asset_context_service(config_dir: str | os.PathLike[str]):
    _ensure_smartlib_on_path()
    from smartlib.apps.asset_manager import AssetContextService
    from smartlib.core.config_loader import ProjectConfig

    return AssetContextService(ProjectConfig(config_dir))


def _asset_construct_service(variant_root: str | os.PathLike[str]):
    _ensure_smartlib_on_path()
    from smartlib.apps.asset_manager import AssetConstructService

    return AssetConstructService(variant_root)


def _shot_service(config_dir: str | os.PathLike[str]):
    _ensure_smartlib_on_path()
    from smartlib.apps.shot_manager import ShotManagerService
    from smartlib.core.config_loader import ProjectConfig

    return ShotManagerService(ProjectConfig(config_dir))


class AssetRequestDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, *, title: str = "Create Asset"):
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QtWidgets.QFormLayout(self)
        self.category_edit = QtWidgets.QLineEdit("characters")
        self.group_edit = QtWidgets.QLineEdit("hero")
        self.name_edit = QtWidgets.QLineEdit()
        self.variant_edit = QtWidgets.QLineEdit("default")
        self.description_edit = QtWidgets.QLineEdit()
        layout.addRow("Category", self.category_edit)
        layout.addRow("Group", self.group_edit)
        layout.addRow("Asset", self.name_edit)
        layout.addRow("Variant", self.variant_edit)
        layout.addRow("Description", self.description_edit)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self) -> dict[str, str]:
        return {
            "category": self.category_edit.text().strip(),
            "group": self.group_edit.text().strip(),
            "name": self.name_edit.text().strip(),
            "variant": self.variant_edit.text().strip() or "default",
            "description": self.description_edit.text().strip(),
        }

    def accept(self) -> None:
        values = self.values()
        missing = [key for key in ("category", "group", "name") if not values[key]]
        if missing:
            QtWidgets.QMessageBox.warning(self, "Create Asset", "Category, Group, and Asset are required.")
            return
        super().accept()


class AssetManagerWindow(QtWidgets.QDialog):
    SETTINGS_ORGANIZATION = "smartpipeline"
    SETTINGS_APPLICATION = "AssetManager"
    SETTINGS_GEOMETRY_KEY = "window/geometry"
    SETTINGS_STATE_GROUP = "window/state"

    def __init__(self, manager: AssetManager | None = None, parent=None):
        super().__init__(parent)
        self.manager = manager or AssetManager()
        self.assets: list[Asset] = []
        self.published_work_icon = self.style().standardIcon(QtWidgets.QStyle.SP_DialogApplyButton)
        self.setWindowTitle(f"Asset Manager - {self.manager.project_name}")
        self.resize(760, 460)
        self._build_ui()
        self._restore_window_geometry()
        self.refresh_assets()
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
        asset_key = self._current_asset_key()
        settings.setValue("asset_key", list(asset_key) if asset_key else [])
        settings.setValue("asset_filter", list(self._selected_asset_filter()))
        settings.setValue("search_text", self.search_edit.text())
        settings.setValue("detail_mode", self.detail_panel.isVisible())
        settings.setValue("browser_filter_tab", self.browser_filter_tabs.currentIndex())
        settings.setValue("detail_tab", self.detail_tabs.currentIndex())
        settings.setValue("asset_variant", self._current_asset_variant())
        settings.setValue("department", self._current_department())
        settings.setValue("subset", self._current_variant())
        settings.setValue("work_scene_format", self._selected_work_scene_extension())
        settings.setValue("asset_view", "table" if self.asset_list.viewMode() == QtWidgets.QListView.ListMode else "card")
        settings.setValue("shot_codes", [target["code"] for target in self._selected_cast_targets()])
        settings.setValue("main_splitter", self.main_splitter.saveState())
        settings.setValue("asset_browser_splitter", self.asset_browser_splitter.saveState())
        settings.setValue("detail_content_splitter_v2", self.detail_content_splitter.saveState())
        settings.setValue("work_content_splitter", self.work_content_splitter.saveState())
        settings.endGroup()

    def _restore_window_state(self) -> None:
        settings = self._window_settings()
        settings.beginGroup(self.SETTINGS_STATE_GROUP)
        asset_key = self._settings_string_list(settings.value("asset_key"))
        asset_filter = self._settings_string_list(settings.value("asset_filter"))
        search_text = str(settings.value("search_text", "") or "")
        detail_mode = self._settings_bool(settings.value("detail_mode", False))
        browser_filter_tab = self._settings_int(settings.value("browser_filter_tab"), 0)
        detail_tab = self._settings_int(settings.value("detail_tab"), 0)
        asset_variant = str(settings.value("asset_variant", "default") or "default")
        department = str(settings.value("department", "model") or "model")
        subset = str(settings.value("subset", "") or "")
        work_scene_format = str(settings.value("work_scene_format", "ma") or "ma")
        asset_view = str(settings.value("asset_view", "card") or "card")
        shot_codes = set(self._settings_string_list(settings.value("shot_codes")))
        main_splitter = settings.value("main_splitter")
        asset_browser_splitter = settings.value("asset_browser_splitter")
        detail_content_splitter = settings.value("detail_content_splitter_v2")
        work_content_splitter = settings.value("work_content_splitter")
        settings.endGroup()

        self.search_edit.blockSignals(True)
        self.search_edit.setText(search_text)
        self.search_edit.blockSignals(False)
        if len(asset_filter) == 3:
            self._select_asset_filter(tuple(asset_filter))
        selected_key = tuple(asset_key) if len(asset_key) == 3 else None
        self._apply_filter(selected_key=selected_key)
        self._restore_detail_selection(asset_variant, department, subset)
        format_index = self.work_scene_format_combo.findData(work_scene_format.lstrip("."))
        if format_index >= 0:
            self.work_scene_format_combo.setCurrentIndex(format_index)
        self._populate_variants()
        self._restore_detail_selection(asset_variant, department, subset)
        self._select_shot_codes(shot_codes)
        self.browser_filter_tabs.setCurrentIndex(max(0, min(browser_filter_tab, self.browser_filter_tabs.count() - 1)))
        self.detail_tabs.setCurrentIndex(max(0, min(detail_tab, self.detail_tabs.count() - 1)))
        if asset_view == "table":
            self._set_asset_table_view()
        else:
            self._set_asset_card_view()
        if detail_mode and self._current_asset():
            self._show_detail_mode()
        else:
            self._show_asset_mode()
        for splitter, state in (
            (self.main_splitter, main_splitter),
            (self.asset_browser_splitter, asset_browser_splitter),
            (self.detail_content_splitter, detail_content_splitter),
            (self.work_content_splitter, work_content_splitter),
        ):
            if state:
                splitter.restoreState(state)

    @staticmethod
    def _settings_string_list(value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value]
        return [str(value)]

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
        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(4, 4, 4, 4)
        root_layout.setSpacing(4)

        self.main_splitter = QtWidgets.QSplitter()
        root_layout.addWidget(self.main_splitter, 1)

        self.asset_panel = QtWidgets.QWidget()
        asset_panel_layout = QtWidgets.QVBoxLayout(self.asset_panel)
        asset_panel_layout.setContentsMargins(2, 2, 2, 2)
        asset_panel_layout.setSpacing(4)
        self.asset_browser_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        asset_panel_layout.addWidget(self.asset_browser_splitter, 1)

        self.browser_filter_tabs = QtWidgets.QTabWidget()
        self.browser_filter_tabs.setMinimumWidth(140)
        self.browser_filter_tabs.setMaximumWidth(240)
        self.asset_browser_splitter.addWidget(self.browser_filter_tabs)

        self.asset_filter_tree = QtWidgets.QTreeWidget()
        self.asset_filter_tree.setHeaderHidden(True)
        self.asset_filter_tree.setRootIsDecorated(True)
        self.asset_filter_tree.setIndentation(10)
        self.asset_filter_tree.setStyleSheet("QTreeWidget::item { height: 24px; }")
        self.browser_filter_tabs.addTab(self.asset_filter_tree, "Assets")

        shot_filter_panel = QtWidgets.QWidget()
        shot_filter_layout = QtWidgets.QVBoxLayout(shot_filter_panel)
        shot_filter_layout.setContentsMargins(2, 2, 2, 2)
        shot_filter_layout.setSpacing(4)
        self.shot_tree = QtWidgets.QTreeWidget()
        self.shot_tree.setHeaderHidden(True)
        self.shot_tree.setRootIsDecorated(True)
        self.shot_tree.setIndentation(10)
        self.shot_tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.shot_tree.setStyleSheet("QTreeWidget::item { height: 24px; }")
        self.add_assets_to_cast_btn = QtWidgets.QPushButton("Edit in Smart Casting")
        self.add_assets_to_cast_btn.setToolTip("Open Smart Casting with the selected target and assets. No cast data is written here.")
        shot_filter_layout.addWidget(self.shot_tree, 1)
        shot_filter_layout.addWidget(self.add_assets_to_cast_btn)
        self.browser_filter_tabs.addTab(shot_filter_panel, "Shots")

        asset_browser = QtWidgets.QWidget()
        asset_browser_layout = QtWidgets.QVBoxLayout(asset_browser)
        asset_browser_layout.setContentsMargins(2, 2, 2, 2)
        asset_browser_layout.setSpacing(4)
        self.asset_browser_splitter.addWidget(asset_browser)
        self.asset_browser_splitter.setStretchFactor(0, 0)
        self.asset_browser_splitter.setStretchFactor(1, 1)

        filter_layout = QtWidgets.QHBoxLayout()
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(4)
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Search asset")
        self.search_edit.setClearButtonEnabled(True)
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        filter_layout.addWidget(self.search_edit)
        filter_layout.addWidget(self.refresh_btn)
        asset_browser_layout.addLayout(filter_layout)
        asset_view_layout = QtWidgets.QHBoxLayout()
        asset_view_layout.setContentsMargins(0, 0, 0, 0)
        asset_view_layout.setSpacing(4)
        asset_view_layout.addStretch(1)
        self.create_asset_btn = QtWidgets.QPushButton("Create Asset")
        self.initialize_asset_btn = QtWidgets.QPushButton("Initialize Asset")
        self.initialize_asset_btn.setEnabled(False)
        self.create_variant_btn = QtWidgets.QPushButton("Create Variant")
        self.asset_card_btn = QtWidgets.QPushButton("Card")
        self.asset_table_btn = QtWidgets.QPushButton("Table")
        asset_view_layout.addWidget(self.create_asset_btn)
        asset_view_layout.addWidget(self.initialize_asset_btn)
        asset_view_layout.addWidget(self.create_variant_btn)
        asset_view_layout.addWidget(self.asset_card_btn)
        asset_view_layout.addWidget(self.asset_table_btn)
        asset_browser_layout.addLayout(asset_view_layout)

        self.asset_list = QtWidgets.QListWidget()
        self.asset_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.asset_list.setViewMode(QtWidgets.QListView.IconMode)
        self.asset_list.setResizeMode(QtWidgets.QListView.Adjust)
        self.asset_list.setMovement(QtWidgets.QListView.Static)
        self.asset_list.setIconSize(QtCore.QSize(128, 72))
        self.asset_list.setGridSize(QtCore.QSize(160, 168))
        self.asset_list.setUniformItemSizes(True)
        self.asset_list.setWordWrap(True)
        self.asset_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.asset_list.setStyleSheet("""
            QListWidget {
                background: #2b2b2b;
                border: 1px solid #3a3a3a;
            }
            QListWidget::item {
                background: #383838;
                border: 1px solid #4a4a4a;
                padding: 4px;
                margin: 3px;
                text-align: left;
            }
            QListWidget::item:selected {
                background: #4d6f86;
                border: 1px solid #7fa8c2;
            }
            QListWidget::item:hover {
                background: #424242;
            }
        """)
        asset_browser_layout.addWidget(self.asset_list)
        self.main_splitter.addWidget(self.asset_panel)

        right = QtWidgets.QWidget()
        self.detail_panel = right
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(2, 2, 2, 2)
        right_layout.setSpacing(4)
        self.main_splitter.addWidget(right)

        self.back_to_assets_btn = QtWidgets.QPushButton("Back")
        self.asset_variant_header_label = QtWidgets.QLabel("Variant")
        self.asset_variant_combo = QtWidgets.QComboBox()
        self.asset_variant_combo.setMinimumWidth(120)

        detail_header_layout = QtWidgets.QHBoxLayout()
        detail_header_layout.setContentsMargins(0, 0, 0, 0)
        detail_header_layout.setSpacing(4)
        detail_header_layout.addWidget(self.back_to_assets_btn)
        detail_header_layout.addStretch(1)
        right_layout.addLayout(detail_header_layout)

        self.detail_content_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        right_layout.addWidget(self.detail_content_splitter, 1)

        selector_panel = QtWidgets.QWidget()
        selector_layout = QtWidgets.QVBoxLayout(selector_panel)
        selector_layout.setContentsMargins(2, 2, 2, 2)
        selector_layout.setSpacing(4)
        self.asset_variant_header_label.setVisible(False)
        self.asset_variant_combo.setVisible(False)

        self.asset_variant_list = QtWidgets.QListWidget()
        self.asset_variant_list.setMinimumHeight(80)
        self.asset_variant_list.setStyleSheet("QListWidget::item { height: 22px; }")

        self.dept_list = QtWidgets.QListWidget()
        self.dept_list.setMinimumHeight(90)
        self.dept_list.setStyleSheet("QListWidget::item { height: 22px; }")
        for dept in self.manager.asset_depts:
            self.dept_list.addItem(dept)
        if not self.manager.asset_depts:
            self.dept_list.addItem("model")
        self.dept_list.setCurrentRow(0)

        self.variant_list = QtWidgets.QListWidget()
        self.variant_list.setMinimumHeight(90)
        self.variant_list.setStyleSheet("QListWidget::item { height: 22px; }")
        self.staging_btn = QtWidgets.QPushButton("Stageing")
        self.staging_btn.setStyleSheet(
            "QPushButton { background-color: #5d4f85; color: white; font-weight: bold; }"
            "QPushButton:hover { background-color: #6e60a0; }"
            "QPushButton:disabled { background-color: #4e4b57; color: #b8b8b8; }"
        )
        selector_layout.addStretch(1)
        # Variant/department/subset selectors live in Work Scene. Do not keep
        # their former empty column in the shared detail layout.

        center_panel = QtWidgets.QWidget()
        center_layout = QtWidgets.QVBoxLayout(center_panel)
        center_layout.setContentsMargins(2, 2, 2, 2)
        center_layout.setSpacing(4)

        right_info_panel = QtWidgets.QWidget()
        right_info_layout = QtWidgets.QVBoxLayout(right_info_panel)
        right_info_layout.setContentsMargins(4, 4, 4, 4)
        right_info_layout.setSpacing(6)

        asset_info_layout = QtWidgets.QHBoxLayout()
        asset_info_layout.setContentsMargins(0, 0, 0, 0)
        asset_info_layout.setSpacing(6)
        self.detail_thumbnail = QtWidgets.QLabel()
        self.detail_thumbnail.setFixedSize(150, 84)
        self.detail_thumbnail.setStyleSheet("background: #2f343a; border: 1px solid #4a4a4a;")
        self.detail_thumbnail.setAlignment(QtCore.Qt.AlignCenter)
        self.detail_info = QtWidgets.QLabel("")
        self.detail_info.setTextFormat(QtCore.Qt.RichText)
        self.detail_info.setAlignment(QtCore.Qt.AlignTop)
        asset_info_layout.addWidget(self.detail_thumbnail, 0, QtCore.Qt.AlignHCenter)
        asset_info_layout.addWidget(self.detail_info, 1)
        right_info_layout.addLayout(asset_info_layout)

        self.file_info_table = QtWidgets.QTableWidget(0, 2)
        self.file_info_table.setHorizontalHeaderLabels(["Key", "Value"])
        self.file_info_table.horizontalHeader().setStretchLastSection(True)
        self.file_info_table.verticalHeader().setVisible(False)
        self.file_info_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.file_info_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        right_info_layout.addWidget(self.file_info_table, 1)

        self.detail_tabs = QtWidgets.QTabWidget()
        center_layout.addWidget(self.detail_tabs, 1)
        self.detail_content_splitter.addWidget(center_panel)
        self.detail_content_splitter.addWidget(right_info_panel)
        self.detail_content_splitter.setStretchFactor(0, 1)
        self.detail_content_splitter.setStretchFactor(1, 0)
        self.detail_content_splitter.setSizes([760, 280])

        work_tab = QtWidgets.QWidget()
        self.work_tab = work_tab
        work_tab_layout = QtWidgets.QVBoxLayout(work_tab)
        work_tab_layout.setContentsMargins(4, 4, 4, 4)
        work_tab_layout.setSpacing(4)
        self.work_content_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        work_tab_layout.addWidget(self.work_content_splitter, 1)

        work_selector_panel = QtWidgets.QWidget()
        work_selector_layout = QtWidgets.QVBoxLayout(work_selector_panel)
        work_selector_layout.setContentsMargins(0, 0, 2, 0)
        work_selector_layout.setSpacing(4)
        work_selector_layout.addWidget(QtWidgets.QLabel("Variant"))
        work_selector_layout.addWidget(self.asset_variant_list, 1)
        work_selector_layout.addWidget(QtWidgets.QLabel("Dept"))
        work_selector_layout.addWidget(self.dept_list, 1)
        work_selector_layout.addWidget(QtWidgets.QLabel("Subset"))
        work_selector_layout.addWidget(self.variant_list, 1)
        work_selector_layout.addWidget(self.staging_btn)
        work_selector_panel.setMinimumWidth(120)
        work_selector_panel.setMaximumWidth(190)
        self.work_content_splitter.addWidget(work_selector_panel)

        work_main_panel = QtWidgets.QWidget()
        work_layout = QtWidgets.QVBoxLayout(work_main_panel)
        work_layout.setContentsMargins(2, 0, 0, 0)
        work_layout.setSpacing(4)
        self.work_content_splitter.addWidget(work_main_panel)
        self.work_content_splitter.setStretchFactor(0, 0)
        self.work_content_splitter.setStretchFactor(1, 1)
        self.dept_tabs = QtWidgets.QTabBar()
        self.dept_tabs.setExpanding(False)
        for dept in self.manager.asset_depts:
            self.dept_tabs.addTab(dept)
        if not self.manager.asset_depts:
            self.dept_tabs.addTab("model")
        self.dept_tabs.setVisible(False)


        self.dependency_label = QtWidgets.QLabel("")
        self.dependency_label.setVisible(False)

        work_format_widget = QtWidgets.QWidget()
        work_format_layout = QtWidgets.QHBoxLayout(work_format_widget)
        work_format_layout.setContentsMargins(0, 0, 0, 0)
        work_format_layout.setSpacing(4)
        work_format_layout.addWidget(QtWidgets.QLabel("Scene Format"))
        self.work_scene_format_combo = QtWidgets.QComboBox()
        self.work_scene_format_combo.addItem("Maya ASCII (.ma)", "ma")
        self.work_scene_format_combo.addItem("Maya Binary (.mb)", "mb")
        self.work_scene_format_combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
        work_format_layout.addWidget(self.work_scene_format_combo)
        work_format_layout.addStretch(1)

        self.work_list = QtWidgets.QTableWidget(0, 4)
        self.work_list.setHorizontalHeaderLabels(["Thumbnail", "File", "Updated", "Comment"])
        self.work_list.horizontalHeader().setStretchLastSection(True)
        self.work_list.verticalHeader().setStretchLastSection(False)
        self.work_list.verticalHeader().setVisible(False)
        self.work_list.verticalHeader().setDefaultSectionSize(58)
        self.work_list.setIconSize(QtCore.QSize(88, 50))
        self.work_list.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.work_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.work_list.setSortingEnabled(True)
        work_layout.addWidget(self.work_list)
        button_grid = QtWidgets.QGridLayout()
        self.open_scene_btn = QtWidgets.QPushButton("OPEN")
        self.save_scene_btn = QtWidgets.QPushButton("SAVE")
        green_button_style = (
            "QPushButton { background-color: #2f6f4e; color: white; font-weight: bold; }"
            "QPushButton:hover { background-color: #3d835f; }"
            "QPushButton:disabled { background-color: #4c5a52; color: #b8b8b8; }"
        )
        blue_button_style = (
            "QPushButton { background-color: #2f5f9f; color: white; font-weight: bold; }"
            "QPushButton:hover { background-color: #3b73b8; }"
            "QPushButton:disabled { background-color: #4b5665; color: #b8b8b8; }"
        )
        self.open_scene_btn.setStyleSheet(green_button_style)
        self.save_scene_btn.setStyleSheet(blue_button_style)
        button_grid.addWidget(self.open_scene_btn, 0, 0)
        button_grid.addWidget(work_format_widget, 0, 1, 1, 2)
        button_grid.addWidget(self.save_scene_btn, 1, 0, 1, 3)
        work_layout.addLayout(button_grid)
        self.detail_tabs.addTab(work_tab, "Work Scene")

        publish_tab = QtWidgets.QWidget()
        self.asset_publish_tab = publish_tab
        publish_tab_layout = QtWidgets.QVBoxLayout(publish_tab)
        publish_tab_layout.setContentsMargins(4, 4, 4, 4)
        publish_tab_layout.setSpacing(4)

        publish_header = QtWidgets.QHBoxLayout()
        publish_header.setContentsMargins(0, 0, 0, 0)
        publish_header.setSpacing(4)
        publish_header.addWidget(QtWidgets.QLabel("USD Asset Publish"))
        publish_header.addStretch(1)
        self.refresh_publish_btn = QtWidgets.QPushButton("Refresh")
        self.publish_geometry_usd_btn = QtWidgets.QPushButton("Publish Geometry USD")
        self.publish_geometry_usd_btn.setStyleSheet(blue_button_style)
        self.publish_geometry_usd_btn.setToolTip(
            "Publish the current model subset and always include model.usd"
        )
        self.publish_selected_btn = QtWidgets.QPushButton("Publish Selected")
        self.publish_selected_btn.setStyleSheet(blue_button_style)
        self.publish_selected_btn.setToolTip("Publish the work scene selected in the Work Scene tab")
        publish_header.addWidget(self.refresh_publish_btn)
        publish_header.addWidget(self.publish_geometry_usd_btn)
        publish_header.addWidget(self.publish_selected_btn)
        publish_tab_layout.addLayout(publish_header)

        self.retarget_publish_options = QtWidgets.QWidget()
        retarget_options_layout = QtWidgets.QGridLayout(self.retarget_publish_options)
        retarget_options_layout.setContentsMargins(0, 0, 0, 0)
        retarget_options_layout.setSpacing(4)
        self.retarget_profile_edit = QtWidgets.QLineEdit()
        self.retarget_profile_edit.setPlaceholderText("Asset-specific Retarget profile JSON")
        self.retarget_profile_edit.setToolTip(
            "The schema and retarget tool are shared; ANM/MCR dependencies and offsets are stored per asset."
        )
        self.retarget_profile_browse_btn = QtWidgets.QPushButton("Browse")
        self.retarget_template_edit = QtWidgets.QLineEdit()
        self.retarget_template_edit.setReadOnly(True)
        self.retarget_template_edit.setPlaceholderText("Project Retarget template (created automatically)")
        self.generate_retarget_profile_btn = QtWidgets.QPushButton("Generate Profile")
        self.retarget_anm_edit = QtWidgets.QLineEdit()
        self.retarget_anm_edit.setReadOnly(True)
        self.retarget_anm_edit.setPlaceholderText("ANM from Context Pack")
        self.retarget_mcr_edit = QtWidgets.QLineEdit()
        self.retarget_mcr_edit.setReadOnly(True)
        self.retarget_mcr_edit.setPlaceholderText("MCR from Context Pack")
        self.retarget_test_motion_check = QtWidgets.QCheckBox("Test Motion Check")
        self.retarget_test_motion_check.setChecked(True)
        self.retarget_test_motion_path_label = QtWidgets.QLabel("")
        self.retarget_test_motion_path_label.setWordWrap(True)
        self.retarget_test_motion_path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.retarget_validation_label = QtWidgets.QLabel("Select a Retarget profile JSON.")
        self.retarget_validation_label.setWordWrap(True)
        self.validate_retarget_btn = QtWidgets.QPushButton("Validate")
        self.retarget_data_version_combo = QtWidgets.QComboBox()
        self.retarget_data_version_combo.setMinimumWidth(90)
        self.load_retarget_data_btn = QtWidgets.QPushButton("Load")
        self.save_retarget_data_btn = QtWidgets.QPushButton("Save New Version")
        retarget_options_layout.addWidget(QtWidgets.QLabel("Template"), 0, 0)
        retarget_options_layout.addWidget(self.retarget_template_edit, 0, 1)
        retarget_options_layout.addWidget(self.generate_retarget_profile_btn, 0, 2)
        retarget_options_layout.addWidget(QtWidgets.QLabel("ANM"), 1, 0)
        retarget_options_layout.addWidget(self.retarget_anm_edit, 1, 1, 1, 2)
        retarget_options_layout.addWidget(QtWidgets.QLabel("MCR"), 2, 0)
        retarget_options_layout.addWidget(self.retarget_mcr_edit, 2, 1, 1, 2)
        retarget_options_layout.addWidget(QtWidgets.QLabel("Profile"), 3, 0)
        retarget_options_layout.addWidget(self.retarget_profile_edit, 3, 1)
        retarget_options_layout.addWidget(self.retarget_profile_browse_btn, 3, 2)
        retarget_options_layout.addWidget(self.retarget_test_motion_check, 4, 0)
        retarget_options_layout.addWidget(self.retarget_test_motion_path_label, 4, 1)
        retarget_options_layout.addWidget(self.validate_retarget_btn, 4, 2)
        retarget_options_layout.addWidget(self.retarget_validation_label, 5, 0, 1, 3)
        retarget_options_layout.addWidget(QtWidgets.QLabel("Data Version"), 6, 0)
        retarget_options_layout.addWidget(self.retarget_data_version_combo, 6, 1)
        data_version_actions = QtWidgets.QHBoxLayout()
        data_version_actions.setContentsMargins(0, 0, 0, 0)
        data_version_actions.addWidget(self.load_retarget_data_btn)
        data_version_actions.addWidget(self.save_retarget_data_btn)
        retarget_options_layout.addLayout(data_version_actions, 6, 2)
        self.retarget_publish_options.setVisible(False)

        publish_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.asset_publish_type_list = QtWidgets.QListWidget()
        self.asset_publish_type_list.setFixedWidth(132)
        self.asset_publish_type_list.setStyleSheet("QListWidget::item { height: 26px; }")
        for label, key in (
            ("Geometry", "geometry"),
            ("Rig", "rig"),
            ("Look", "look"),
            ("Groom", "groom"),
            ("Retarget", "retarget"),
        ):
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, key)
            self.asset_publish_type_list.addItem(item)
        self.asset_publish_type_list.setCurrentRow(0)
        publish_splitter.addWidget(self.asset_publish_type_list)

        publish_results_panel = QtWidgets.QWidget()
        publish_results_layout = QtWidgets.QVBoxLayout(publish_results_panel)
        publish_results_layout.setContentsMargins(0, 0, 0, 0)
        publish_results_layout.setSpacing(4)
        publish_results_layout.addWidget(self.retarget_publish_options)

        self.asset_publish_tree = QtWidgets.QTreeWidget()
        self.asset_publish_tree.setHeaderLabels(
            ["Name", "Component", "Representation", "Official", "Latest", "State", "Updated", "Comment"]
        )
        self.asset_publish_tree.setIndentation(12)
        self.asset_publish_tree.setAlternatingRowColors(False)
        self.asset_publish_tree.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.asset_publish_tree.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.asset_publish_tree.header().setStretchLastSection(True)
        self.asset_publish_tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.asset_publish_tree.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.asset_publish_tree.header().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        publish_results_layout.addWidget(self.asset_publish_tree, 1)
        publish_splitter.addWidget(publish_results_panel)
        publish_splitter.setStretchFactor(0, 0)
        publish_splitter.setStretchFactor(1, 1)
        publish_splitter.setCollapsible(0, False)
        publish_splitter.setSizes([132, 900])
        publish_tab_layout.addWidget(publish_splitter, 1)
        self.detail_tabs.addTab(publish_tab, "Publish")

        data_tab = QtWidgets.QWidget()
        self.data_tab = data_tab
        data_layout = QtWidgets.QVBoxLayout(data_tab)
        data_layout.setContentsMargins(4, 4, 4, 4)
        data_layout.setSpacing(4)
        data_header = QtWidgets.QHBoxLayout()
        data_header.setContentsMargins(0, 0, 0, 0)
        data_header.setSpacing(4)
        self.refresh_data_btn = QtWidgets.QPushButton("Refresh")
        self.data_type_combo = QtWidgets.QComboBox()
        self.data_type_combo.addItem("Geo", "geo")
        self.data_type_combo.addItem("Guide", "guide")
        self.data_type_combo.addItem("Skin", "skin")
        self.data_target_combo = QtWidgets.QComboBox()
        self.data_target_combo.setEditable(True)
        self.data_target_combo.addItems(["body", "face", "cloth", "hair"])
        self.data_representation_combo = QtWidgets.QComboBox()
        self.data_representation_combo.setEditable(True)
        self.data_representation_combo.addItems(["low", "high"])
        data_header.addWidget(QtWidgets.QLabel("Type"))
        data_header.addWidget(self.data_type_combo)
        data_header.addWidget(QtWidgets.QLabel("Target"))
        data_header.addWidget(self.data_target_combo, 1)
        data_header.addWidget(QtWidgets.QLabel("Representation"))
        data_header.addWidget(self.data_representation_combo)
        data_header.addWidget(self.refresh_data_btn)
        data_layout.addLayout(data_header)
        self.data_list = QtWidgets.QTreeWidget()
        self.data_list.setHeaderLabels(["Name", "Version", "Date", "Comment"])
        self.data_list.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.data_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        data_layout.addWidget(self.data_list)
        data_buttons = QtWidgets.QHBoxLayout()
        data_buttons.setContentsMargins(0, 0, 0, 0)
        data_buttons.setSpacing(4)
        self.export_mesh_btn = QtWidgets.QPushButton("Export Data")
        self.import_assembly_btn = QtWidgets.QPushButton("Ingest Assembly")
        self.open_assembly_btn = QtWidgets.QPushButton("Open Assembly")
        self.reload_assembly_btn = QtWidgets.QPushButton("Reload ASSEMBLY_USD_PROXY")
        self.save_assembly_btn = QtWidgets.QPushButton("Save Assembly")
        self.publish_assembly_btn = QtWidgets.QPushButton("Publish Assembly")
        self.ingest_fbx_btn = QtWidgets.QPushButton("Ingest Model FBX")
        self.export_guide_btn = QtWidgets.QPushButton("Export Guide")
        self.export_skin_btn = QtWidgets.QPushButton("Export Skin")
        data_buttons.addStretch(1)
        data_buttons.addWidget(self.export_mesh_btn)
        data_buttons.addWidget(self.import_assembly_btn)
        data_buttons.addWidget(self.open_assembly_btn)
        data_buttons.addWidget(self.reload_assembly_btn)
        data_buttons.addWidget(self.save_assembly_btn)
        data_buttons.addWidget(self.publish_assembly_btn)
        data_buttons.addWidget(self.ingest_fbx_btn)
        self.export_guide_btn.setVisible(False)
        self.export_skin_btn.setVisible(False)
        data_layout.addLayout(data_buttons)
        self.detail_tabs.addTab(data_tab, "Data")

        construct_tab = QtWidgets.QWidget()
        self.construct_tab = construct_tab
        construct_layout = QtWidgets.QVBoxLayout(construct_tab)
        construct_layout.setContentsMargins(4, 4, 4, 4)
        construct_layout.setSpacing(4)
        construct_header = QtWidgets.QHBoxLayout()
        construct_header.setContentsMargins(0, 0, 0, 0)
        construct_header.addWidget(QtWidgets.QLabel("Construct Inputs"))
        construct_header.addStretch(1)
        self.refresh_construct_btn = QtWidgets.QPushButton("Refresh")
        self.save_construct_btn = QtWidgets.QPushButton("Save Recipe")
        construct_header.addWidget(self.refresh_construct_btn)
        construct_header.addWidget(self.save_construct_btn)
        construct_layout.addLayout(construct_header)
        self.construct_tree = QtWidgets.QTreeWidget()
        self.construct_tree.setHeaderLabels(
            ["Use", "Type", "Target", "Representation", "Selected", "Latest", "State"]
        )
        self.construct_tree.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.construct_tree.setAlternatingRowColors(False)
        self.construct_tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.construct_tree.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.construct_tree.header().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        self.construct_tree.header().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        self.construct_tree.header().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
        self.construct_tree.header().setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeToContents)
        self.construct_tree.header().setStretchLastSection(True)
        construct_layout.addWidget(self.construct_tree, 1)
        construct_buttons = QtWidgets.QHBoxLayout()
        construct_buttons.setContentsMargins(0, 0, 0, 0)
        self.construct_import_btn = QtWidgets.QPushButton("Import Selected")
        self.construct_apply_btn = QtWidgets.QPushButton("Apply Selected")
        self.construct_build_btn = QtWidgets.QPushButton("Build Work Scene")
        self.construct_import_btn.setStyleSheet(green_button_style)
        self.construct_apply_btn.setStyleSheet(blue_button_style)
        self.construct_build_btn.setStyleSheet(blue_button_style)
        construct_buttons.addStretch(1)
        construct_buttons.addWidget(self.construct_import_btn)
        construct_buttons.addWidget(self.construct_apply_btn)
        construct_buttons.addWidget(self.construct_build_btn)
        construct_layout.addLayout(construct_buttons)
        self.detail_tabs.addTab(construct_tab, "Construct")

        preview_tab = QtWidgets.QWidget()
        self.preview_tab = preview_tab
        preview_layout = QtWidgets.QVBoxLayout(preview_tab)
        preview_layout.setContentsMargins(4, 4, 4, 4)
        preview_layout.setSpacing(4)
        self.preview_list = QtWidgets.QTableWidget(0, 5)
        self.preview_list.setHorizontalHeaderLabels(["Version", "Type", "Updated", "Views", "review.json"])
        self.preview_list.horizontalHeader().setStretchLastSection(True)
        self.preview_list.verticalHeader().setVisible(False)
        self.preview_list.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.preview_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.preview_list.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        preview_layout.addWidget(self.preview_list)
        preview_buttons = QtWidgets.QHBoxLayout()
        preview_buttons.setContentsMargins(0, 0, 0, 0)
        preview_buttons.setSpacing(4)
        self.quick_preview_btn = QtWidgets.QPushButton("Quick Preview")
        self.quick_preview_btn.setStyleSheet(
            "QPushButton { background-color: #6a5a32; color: white; font-weight: bold; }"
            "QPushButton:hover { background-color: #7c6b3d; }"
            "QPushButton:disabled { background-color: #595447; color: #b8b8b8; }"
        )
        self.turntable_scene_btn = QtWidgets.QPushButton("Build Turntable Scene")
        self.turntable_scene_btn.setStyleSheet(
            "QPushButton { background-color: #465a72; color: white; font-weight: bold; }"
            "QPushButton:hover { background-color: #526b88; }"
            "QPushButton:disabled { background-color: #4c4f55; color: #b8b8b8; }"
        )
        self.open_preview_rv_btn = QtWidgets.QPushButton("Open Package in RV")
        self.open_preview_rv_btn.setStyleSheet(green_button_style)
        self.open_preview_usdview_btn = QtWidgets.QPushButton("Open in usdview")
        self.open_preview_usdview_btn.setStyleSheet(green_button_style)
        preview_buttons.addStretch(1)
        preview_buttons.addWidget(self.quick_preview_btn)
        preview_buttons.addWidget(self.turntable_scene_btn)
        preview_buttons.addWidget(self.open_preview_rv_btn)
        preview_buttons.addWidget(self.open_preview_usdview_btn)
        preview_layout.addLayout(preview_buttons)
        self.detail_tabs.addTab(preview_tab, "Preview")

        context_tab = QtWidgets.QWidget()
        self.context_tab = context_tab
        context_layout = QtWidgets.QHBoxLayout(context_tab)
        context_layout.setContentsMargins(4, 4, 4, 4)
        context_layout.setSpacing(4)
        context_selector = QtWidgets.QWidget()
        context_selector_layout = QtWidgets.QVBoxLayout(context_selector)
        context_selector_layout.setContentsMargins(0, 0, 0, 0)
        context_selector_layout.setSpacing(4)
        context_selector_layout.addWidget(QtWidgets.QLabel("Context"))
        self.context_profile_list = QtWidgets.QListWidget()
        self.context_profile_list.setFixedWidth(120)
        self.context_profile_list.setStyleSheet("QListWidget::item { height: 24px; }")
        context_selector_layout.addWidget(self.context_profile_list, 1)
        context_layout.addWidget(context_selector, 0)
        context_main = QtWidgets.QWidget()
        context_main_layout = QtWidgets.QVBoxLayout(context_main)
        context_main_layout.setContentsMargins(0, 0, 0, 0)
        context_main_layout.setSpacing(4)
        context_layout.addWidget(context_main, 1)
        context_header = QtWidgets.QHBoxLayout()
        context_header.setContentsMargins(0, 0, 0, 0)
        context_header.setSpacing(4)
        self.context_version_combo = QtWidgets.QComboBox()
        self.context_version_combo.setVisible(False)
        self.context_use_scene_btn = QtWidgets.QPushButton("USD Current Scene")
        self.context_use_scene_btn.setToolTip(
            "Register the current Maya scene as the verified source used by Assemble and Pack."
        )
        self.context_assemble_btn = QtWidgets.QPushButton("Assemble")
        self.context_pack_btn = QtWidgets.QPushButton("Pack")
        self.context_pack_btn.setEnabled(False)
        context_header.addStretch(1)
        context_header.addWidget(self.context_use_scene_btn)
        context_header.addWidget(self.context_assemble_btn)
        context_header.addWidget(self.context_pack_btn)
        context_main_layout.addLayout(context_header)
        self.context_readiness_label = QtWidgets.QLabel("PACK BLOCKED: Assemble a Context first")
        self.context_readiness_label.setObjectName("context_readiness_label")
        self.context_readiness_label.setStyleSheet(
            "QLabel#context_readiness_label {"
            " padding: 4px 6px;"
            " border: 1px solid #5d5d5d;"
            " background: #353535;"
            " color: #d6b46b;"
            "}"
        )
        context_main_layout.addWidget(self.context_readiness_label)
        self.context_state_table = QtWidgets.QTableWidget(0, 7)
        self.context_state_table.setHorizontalHeaderLabels(
            ["Subset", "Type", "Resolved", "State", "Official", "Latest", "Comment"]
        )
        self.context_state_table.horizontalHeader().setStretchLastSection(True)
        self.context_state_table.verticalHeader().setVisible(False)
        self.context_state_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.context_state_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        context_main_layout.addWidget(self.context_state_table, 1)
        self.context_pack_tree = QtWidgets.QTreeWidget()
        self.context_pack_tree.setHeaderLabels(["Dept", "Subset", "Ver", "Comment"])
        self._apply_context_pack_tree_header()
        self.context_pack_tree.setIndentation(10)
        context_main_layout.addWidget(self.context_pack_tree, 1)
        self.detail_tabs.addTab(context_tab, "Context")

        self.publish_list = QtWidgets.QListWidget()
        self.publish_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)

        self.status_label = QtWidgets.QLabel("")
        root_layout.addWidget(self.status_label)

        self.search_edit.textChanged.connect(self._apply_filter)
        self.asset_filter_tree.currentItemChanged.connect(lambda _current, _previous: self._apply_filter())
        self.add_assets_to_cast_btn.clicked.connect(self._add_selected_assets_to_shot_cast)
        self.refresh_btn.clicked.connect(self.refresh_assets)
        self.asset_list.currentRowChanged.connect(self._show_current_asset)
        self.asset_list.itemSelectionChanged.connect(self._update_asset_action_state)
        self.asset_list.itemDoubleClicked.connect(lambda _item: self._show_detail_mode())
        self.asset_card_btn.clicked.connect(self._set_asset_card_view)
        self.asset_table_btn.clicked.connect(self._set_asset_table_view)
        self.create_asset_btn.clicked.connect(self._create_asset)
        self.initialize_asset_btn.clicked.connect(self._initialize_selected_assets)
        self.create_variant_btn.clicked.connect(self._create_variant)
        self.back_to_assets_btn.clicked.connect(self._show_asset_mode)
        self.asset_variant_combo.currentIndexChanged.connect(lambda _index: self._show_current_asset())
        self.asset_variant_list.currentRowChanged.connect(self._on_asset_variant_selected)
        self.dept_tabs.currentChanged.connect(self._on_department_changed)
        self.dept_list.currentRowChanged.connect(self._on_department_list_changed)
        self.variant_list.currentRowChanged.connect(lambda _row: self._show_current_asset())
        self.detail_tabs.currentChanged.connect(lambda _index: self._update_selected_file_info())
        self.asset_list.customContextMenuRequested.connect(self._show_asset_context_menu)
        self.work_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.work_list.customContextMenuRequested.connect(self._show_work_context_menu)
        self.work_list.itemChanged.connect(self._on_work_item_changed)
        self.work_list.itemSelectionChanged.connect(self._update_selected_file_info)
        self.work_list.itemSelectionChanged.connect(self._update_publish_selected_state)
        self.data_list.customContextMenuRequested.connect(self._show_data_context_menu)
        self.data_list.itemSelectionChanged.connect(self._update_selected_file_info)
        self.preview_list.itemSelectionChanged.connect(self._update_selected_file_info)
        self.preview_list.itemDoubleClicked.connect(lambda _item: self._open_selected_preview_in_rv())
        self.publish_list.customContextMenuRequested.connect(self._show_publish_context_menu)
        self.open_scene_btn.clicked.connect(self._open_selected_scene)
        self.save_scene_btn.clicked.connect(self._save_scene)
        self.refresh_publish_btn.clicked.connect(self._populate_asset_publish_tree)
        self.publish_selected_btn.clicked.connect(self._publish_selected_work)
        self.retarget_profile_browse_btn.clicked.connect(self._browse_retarget_profile)
        self.generate_retarget_profile_btn.clicked.connect(self._generate_retarget_profile)
        self.validate_retarget_btn.clicked.connect(self._validate_retarget_publish)
        self.retarget_profile_edit.textChanged.connect(lambda _text: self._update_publish_selected_state())
        self.load_retarget_data_btn.clicked.connect(self._load_retarget_data_version)
        self.save_retarget_data_btn.clicked.connect(self._save_retarget_data_version)
        self.publish_geometry_usd_btn.clicked.connect(
            lambda: self._publish_selected_work(force_usd=True)
        )
        self.asset_publish_type_list.currentRowChanged.connect(self._on_publish_type_changed)
        self.staging_btn.clicked.connect(self._stage_work_scene)
        self.quick_preview_btn.clicked.connect(self._quick_preview_setup)
        self.turntable_scene_btn.clicked.connect(self._build_turntable_scene)
        self.open_preview_rv_btn.clicked.connect(self._open_selected_preview_in_rv)
        self.open_preview_usdview_btn.clicked.connect(self._open_selected_preview_in_usdview)
        self.context_version_combo.currentTextChanged.connect(self._populate_context_profiles)
        self.context_profile_list.currentRowChanged.connect(self._on_context_profile_selected)
        self.context_assemble_btn.clicked.connect(self._assemble_selected_asset_context)
        self.context_use_scene_btn.clicked.connect(self._use_current_scene_as_assembly)
        self.context_pack_btn.clicked.connect(self._pack_selected_asset_context)
        self.refresh_data_btn.clicked.connect(self._refresh_current_data)
        self.export_mesh_btn.clicked.connect(self._export_selected_data_type)
        self.import_assembly_btn.clicked.connect(self._import_assembly_data)
        self.open_assembly_btn.clicked.connect(lambda: self._open_selected_asset_assembly(reload=False))
        self.reload_assembly_btn.clicked.connect(lambda: self._open_selected_asset_assembly(reload=True))
        self.save_assembly_btn.clicked.connect(self._save_selected_asset_assembly)
        self.publish_assembly_btn.clicked.connect(self._publish_selected_asset_assembly)
        self.ingest_fbx_btn.clicked.connect(self._ingest_model_fbx)
        self.export_guide_btn.clicked.connect(lambda: self._show_export_data_menu("guide"))
        self.export_skin_btn.clicked.connect(lambda: self._show_export_data_menu("skin"))
        self.refresh_construct_btn.clicked.connect(self._populate_construct_tree)
        self.save_construct_btn.clicked.connect(self._save_construct_recipe)
        self.construct_import_btn.clicked.connect(lambda: self._execute_construct_inputs("import"))
        self.construct_apply_btn.clicked.connect(lambda: self._execute_construct_inputs("apply"))
        self.construct_build_btn.clicked.connect(self._build_construct_work_scene)
        self.data_watcher = QtCore.QFileSystemWatcher(self)
        self.data_refresh_timer = QtCore.QTimer(self)
        self.data_refresh_timer.setSingleShot(True)
        self.data_refresh_timer.setInterval(350)
        self.data_watcher.directoryChanged.connect(self._schedule_data_refresh)
        self.data_watcher.fileChanged.connect(self._schedule_data_refresh)
        self.data_refresh_timer.timeout.connect(self._refresh_current_data_from_watcher)
        self.context_assembly = None
        self.context_verification = None
        self._populate_context_versions()
        self._show_asset_mode()

    def refresh_assets(self, keep_selection: bool = True) -> None:
        selected_key = self._current_asset_key() if keep_selection else None
        selected_asset_variant = self._current_asset_variant() if keep_selection else "default"
        selected_department = self._current_department() if keep_selection else "model"
        selected_subset = self._current_variant() if keep_selection else ""
        self.manager.reload_config()
        if not keep_selection:
            self.asset_filter_tree.clear()
        self.assets = self.manager.list_assets_from_sheet(fallback_to_filesystem=True)
        self._populate_asset_filter_tree()
        self._populate_shot_tree()
        self._apply_filter(selected_key=selected_key)
        self._restore_detail_selection(selected_asset_variant, selected_department, selected_subset)
        self._populate_asset_variants()
        self._populate_variants()
        self._restore_detail_selection(selected_asset_variant, selected_department, selected_subset)
        self._show_current_asset()
        if self.manager.last_asset_source.startswith("spreadsheet"):
            self.status_label.setText(
                f"{len(self.assets)} assets from {self.manager.last_asset_source}"
            )
        elif self.manager.last_asset_source_error:
            self.status_label.setText(
                f"{len(self.assets)} assets from folders. {self.manager.last_asset_source_error}"
            )
        else:
            self.status_label.setText(f"{len(self.assets)} assets from folders")

    def _show_detail_mode(self) -> None:
        self.asset_panel.setVisible(False)
        self.detail_panel.setVisible(True)
        self._show_current_asset()

    def _show_asset_mode(self) -> None:
        self.asset_panel.setVisible(True)
        self.detail_panel.setVisible(False)

    def _set_asset_card_view(self) -> None:
        self.asset_list.setViewMode(QtWidgets.QListView.IconMode)
        self.asset_list.setIconSize(QtCore.QSize(128, 72))
        self.asset_list.setGridSize(QtCore.QSize(160, 168))
        self.asset_list.setUniformItemSizes(True)

    def _set_asset_table_view(self) -> None:
        self.asset_list.setViewMode(QtWidgets.QListView.ListMode)
        self.asset_list.setIconSize(QtCore.QSize(80, 45))
        self.asset_list.setGridSize(QtCore.QSize())
        self.asset_list.setUniformItemSizes(False)

    def _apply_filter(self, selected_key: tuple[str, str, str] | None = None) -> None:
        if selected_key is None:
            selected_key = self._current_asset_key()
        text = self.search_edit.text().strip().lower()
        category_filter, group_filter, asset_filter = self._selected_asset_filter()
        self.asset_list.clear()
        row_to_select = -1
        for asset in self.assets:
            category, group, asset_name = self._asset_filter_values(asset)
            if category_filter and category != category_filter:
                continue
            if group_filter and group != group_filter:
                continue
            if asset_filter and asset_name != asset_filter:
                continue
            label = f"{asset.category}/{asset.group}/{asset.name}"
            if text and text not in label.lower():
                continue
            metadata = self.manager.load_asset_metadata(asset)
            item = QtWidgets.QListWidgetItem(self._asset_card_text(asset, metadata))
            item.setIcon(self._asset_icon(asset, metadata))
            item.setToolTip(self._asset_tooltip(asset, metadata))
            item.setData(QtCore.Qt.UserRole, asset)
            if not self.manager.is_asset_initialized(asset):
                item.setForeground(QtGui.QColor("#d9a441"))
            self.asset_list.addItem(item)
            if self._asset_key(asset) == selected_key:
                row_to_select = self.asset_list.count() - 1
        if row_to_select >= 0:
            self.asset_list.setCurrentRow(row_to_select)
        elif self.asset_list.count():
            self.asset_list.setCurrentRow(0)

    def _populate_asset_filter_tree(self) -> None:
        current_filter = self._selected_asset_filter()
        self.asset_filter_tree.blockSignals(True)
        self.asset_filter_tree.clear()
        all_item = QtWidgets.QTreeWidgetItem(["ALL"])
        all_item.setData(0, QtCore.Qt.UserRole, ("", "", ""))
        self.asset_filter_tree.addTopLevelItem(all_item)

        category_items: dict[str, QtWidgets.QTreeWidgetItem] = {}
        group_items: dict[tuple[str, str], QtWidgets.QTreeWidgetItem] = {}
        selected_item = all_item
        for asset in sorted(self.assets, key=lambda item: self._asset_filter_values(item)):
            category, group, asset_name = self._asset_filter_values(asset)
            if not category:
                category = "-"
            if not group:
                group = "-"
            category_item = category_items.get(category)
            if category_item is None:
                category_item = QtWidgets.QTreeWidgetItem([category])
                category_item.setData(0, QtCore.Qt.UserRole, (category, "", ""))
                self.asset_filter_tree.addTopLevelItem(category_item)
                category_items[category] = category_item

            group_key = (category, group)
            group_item = group_items.get(group_key)
            if group_item is None:
                group_item = QtWidgets.QTreeWidgetItem([group])
                group_item.setData(0, QtCore.Qt.UserRole, (category, group, ""))
                category_item.addChild(group_item)
                group_items[group_key] = group_item

            if (category, group, "") == current_filter:
                selected_item = group_item
            elif (category, "", "") == current_filter:
                selected_item = category_item

        for item in category_items.values():
            item.setExpanded(True)
        if selected_item:
            self.asset_filter_tree.setCurrentItem(selected_item)
        self.asset_filter_tree.blockSignals(False)

    def _populate_shot_tree(self) -> None:
        selected_codes = {target["code"] for target in self._selected_cast_targets()}

        self.shot_tree.blockSignals(True)
        self.shot_tree.clear()
        selected_items = []
        try:
            service = _shot_service(self.manager.config_dir)
            episode_items: dict[str, QtWidgets.QTreeWidgetItem] = {}
            sequence_items: dict[tuple[str, str], QtWidgets.QTreeWidgetItem] = {}
            for identity in service.list_shots():
                episode_item = episode_items.get(identity.episode)
                if episode_item is None:
                    episode_item = QtWidgets.QTreeWidgetItem([identity.episode])
                    self.shot_tree.addTopLevelItem(episode_item)
                    episode_items[identity.episode] = episode_item

                sequence_key = (identity.episode, identity.sequence)
                sequence_item = sequence_items.get(sequence_key)
                if sequence_item is None:
                    sequence_item = QtWidgets.QTreeWidgetItem([identity.sequence])
                    sequence_item.setData(
                        0,
                        QtCore.Qt.UserRole,
                        {"kind": "sequence", "episode": identity.episode, "sequence": identity.sequence, "code": f"{identity.episode}_{identity.sequence}"},
                    )
                    sequence_item.setToolTip(0, f"Sequence Cast: {identity.episode}/{identity.sequence}")
                    episode_item.addChild(sequence_item)
                    sequence_items[sequence_key] = sequence_item

                shot_item = QtWidgets.QTreeWidgetItem([identity.shot])
                shot_item.setData(0, QtCore.Qt.UserRole, {"kind": "shot", "identity": identity, "code": identity.code})
                shot_item.setToolTip(0, identity.code)
                sequence_item.addChild(shot_item)
                if identity.code in selected_codes:
                    selected_items.append(shot_item)
                if f"{identity.episode}_{identity.sequence}" in selected_codes and sequence_item not in selected_items:
                    selected_items.append(sequence_item)

            for item in episode_items.values():
                item.setExpanded(True)
            for item in sequence_items.values():
                item.setExpanded(True)
            if not episode_items:
                empty_item = QtWidgets.QTreeWidgetItem(["No shots"])
                empty_item.setFlags(empty_item.flags() & ~QtCore.Qt.ItemIsSelectable)
                self.shot_tree.addTopLevelItem(empty_item)
        except Exception as exc:
            error_item = QtWidgets.QTreeWidgetItem(["Shot list unavailable"])
            error_item.setToolTip(0, str(exc))
            error_item.setFlags(error_item.flags() & ~QtCore.Qt.ItemIsSelectable)
            self.shot_tree.addTopLevelItem(error_item)
        for selected_item in selected_items:
            selected_item.setSelected(True)
        if selected_items:
            self.shot_tree.setCurrentItem(selected_items[0])
        self.shot_tree.blockSignals(False)

    def _selected_asset_filter(self) -> tuple[str, str, str]:
        item = self.asset_filter_tree.currentItem()
        if not item:
            return "", "", ""
        data = item.data(0, QtCore.Qt.UserRole)
        if isinstance(data, tuple) and len(data) == 3:
            return str(data[0]), str(data[1]), str(data[2])
        return "", "", ""

    def _select_asset_filter(self, target: tuple[str, str, str]) -> bool:
        for index in range(self.asset_filter_tree.topLevelItemCount()):
            top_item = self.asset_filter_tree.topLevelItem(index)
            if self._tree_item_data_matches(top_item, target):
                self.asset_filter_tree.setCurrentItem(top_item)
                return True
            for child_index in range(top_item.childCount()):
                child = top_item.child(child_index)
                if self._tree_item_data_matches(child, target):
                    self.asset_filter_tree.setCurrentItem(child)
                    return True
        return False

    @staticmethod
    def _tree_item_data_matches(item, target: tuple[str, str, str]) -> bool:
        data = item.data(0, QtCore.Qt.UserRole)
        if isinstance(data, tuple) and len(data) == 3:
            return tuple(str(value) for value in data) == target
        return False

    def _asset_filter_values(self, asset: Asset) -> tuple[str, str, str]:
        metadata = self.manager.load_asset_metadata(asset)
        category = str(metadata.get("category") or asset.category or "")
        group = str(metadata.get("group") or asset.group or "")
        name = str(metadata.get("asset") or metadata.get("name") or asset.name or "")
        return category, group, name

    def _asset_card_text(self, asset: Asset, metadata: dict) -> str:
        if self.manager.is_asset_initialized(asset):
            status = metadata.get("status") or "-"
        else:
            status = "NOT CREATED"
        asset_type = metadata.get("asset_type") or metadata.get("type") or asset.category
        description = metadata.get("description") or ""
        lines = [
            asset.name,
            f"{asset.category}/{asset.group}",
            f"Status: {status}",
            f"Type: {asset_type}",
        ]
        if description:
            lines.append(str(description)[:34])
        while len(lines) < 5:
            lines.append("")
        return "\n".join(lines[:5])

    def _asset_tooltip(self, asset: Asset, metadata: dict) -> str:
        rows = [
            f"Asset: {asset.name}",
            f"Category: {asset.category}",
            f"Group: {asset.group}",
        ]
        if not self.manager.is_asset_initialized(asset):
            rows.append("Production Asset: NOT CREATED")
            rows.append("Select this card and run Initialize Asset.")
        for key in ("status", "asset_type", "published_by", "published", "description"):
            value = metadata.get(key)
            if value:
                rows.append(f"{key}: {value}")
        return "\n".join(rows)

    def _asset_icon(self, asset: Asset, metadata: dict):
        thumbnail = self.manager.find_asset_thumbnail(asset)
        if thumbnail:
            pixmap = QtGui.QPixmap(str(thumbnail))
            if not pixmap.isNull():
                return QtGui.QIcon(self._thumbnail_canvas(pixmap, asset.name, 128, 72))

        pixmap = QtGui.QPixmap(128, 72)
        pixmap.fill(QtGui.QColor("#2f343a"))
        painter = QtGui.QPainter(pixmap)
        painter.setPen(QtGui.QColor("#9fb6c8"))
        font = painter.font()
        font.setPixelSize(18)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), QtCore.Qt.AlignCenter, asset.name[:12])
        painter.end()
        return QtGui.QIcon(pixmap)

    def _thumbnail_canvas(self, source: QtGui.QPixmap, label: str, width: int = 150, height: int = 84) -> QtGui.QPixmap:
        canvas = QtGui.QPixmap(width, height)
        canvas.fill(QtGui.QColor("#2f343a"))
        scaled = source.scaled(
            max(1, width - 8),
            max(1, height - 8),
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
        painter = QtGui.QPainter(canvas)
        x = (canvas.width() - scaled.width()) // 2
        y = (canvas.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        painter.end()
        return canvas

    def _update_detail_asset_info(self, asset: Asset | None) -> None:
        if not asset:
            self.detail_thumbnail.clear()
            self.detail_info.setText("")
            return
        metadata = self.manager.load_asset_metadata(asset)
        self.detail_thumbnail.setPixmap(self._asset_icon(asset, metadata).pixmap(150, 84))
        status = (
            metadata.get("status", "")
            if self.manager.is_asset_initialized(asset)
            else "NOT CREATED"
        )
        asset_type = metadata.get("asset_type") or metadata.get("type") or asset.category
        description = metadata.get("description", "")
        self.detail_info.setText(
            f"<b>{asset.name}</b><br>"
            f"Category: {asset.category}<br>"
            f"Group: {asset.group}<br>"
            f"Type: {asset_type}<br>"
            f"Status: {status}<br>"
            f"{description}"
        )

    def _current_asset(self) -> Asset | None:
        item = self.asset_list.currentItem()
        if not item:
            return None
        return item.data(QtCore.Qt.UserRole)

    def current_token_context(self, **overrides):
        _ensure_smartlib_on_path()
        from smartlib.core.tokens import TokenContext

        asset = self._current_asset()
        values = {
            "project_root": self.manager.project_root,
            "project_name": self.manager.project_name,
            "department": self._current_department(),
            "tool": current_dcc_name(),
            "subset": self._work_subset_arg(asset) or self._current_variant(),
        }
        if asset:
            values.update(
                {
                    "asset": asset.name,
                    "asset_name": asset.name,
                    "category": asset.category,
                    "group": asset.group,
                    "variant": self._work_variant_arg(asset),
                }
            )
        values.update(overrides)
        return TokenContext.from_mapping(values)

    def _selected_assets(self) -> list[Asset]:
        assets: list[Asset] = []
        for item in self.asset_list.selectedItems():
            asset = item.data(QtCore.Qt.UserRole)
            if isinstance(asset, Asset):
                assets.append(asset)
        current = self._current_asset()
        if not assets and current:
            assets.append(current)
        return assets

    def _selected_uninitialized_assets(self) -> list[Asset]:
        return [
            asset
            for asset in self._selected_assets()
            if not self.manager.is_asset_initialized(asset)
        ]

    def _update_asset_action_state(self) -> None:
        pending = self._selected_uninitialized_assets()
        count = len(pending)
        self.initialize_asset_btn.setEnabled(bool(count))
        self.initialize_asset_btn.setText(
            f"Initialize Asset ({count})" if count > 1 else "Initialize Asset"
        )

        current = self._current_asset()
        initialized = bool(current and self.manager.is_asset_initialized(current))
        self.create_variant_btn.setEnabled(initialized)
        self.staging_btn.setEnabled(initialized)
        message = "" if initialized else "Initialize Asset before creating variants or staging."
        self.create_variant_btn.setToolTip(message)
        self.staging_btn.setToolTip(message)

    def _selected_shot_identities(self) -> list:
        return [target["identity"] for target in self._selected_cast_targets() if target.get("kind") == "shot"]

    def _selected_cast_targets(self) -> list[dict]:
        targets = []
        identities = []
        for item in self.shot_tree.selectedItems():
            target = self._cast_target_from_tree_item(item)
            if target:
                targets.append(target)
        if targets:
            return _unique_cast_targets(targets)
        item = self.shot_tree.currentItem()
        if not item:
            return []
        target = self._cast_target_from_tree_item(item)
        return [target] if target else []

    def _cast_target_from_tree_item(self, item) -> dict | None:
        data = item.data(0, QtCore.Qt.UserRole) if item else None
        if isinstance(data, dict) and data.get("kind") in {"shot", "sequence"}:
            return data
        if data and hasattr(data, "code"):
            return {"kind": "shot", "identity": data, "code": data.code}
        return None

    def _select_shot_codes(self, shot_codes: set[str]) -> None:
        if not shot_codes:
            return
        self.shot_tree.blockSignals(True)
        for episode_index in range(self.shot_tree.topLevelItemCount()):
            episode_item = self.shot_tree.topLevelItem(episode_index)
            for sequence_index in range(episode_item.childCount()):
                sequence_item = episode_item.child(sequence_index)
                sequence_data = sequence_item.data(0, QtCore.Qt.UserRole)
                if isinstance(sequence_data, dict) and sequence_data.get("code") in shot_codes:
                    sequence_item.setSelected(True)
                for shot_index in range(sequence_item.childCount()):
                    shot_item = sequence_item.child(shot_index)
                    data = shot_item.data(0, QtCore.Qt.UserRole)
                    identity = data.get("identity") if isinstance(data, dict) else data
                    if identity and identity.code in shot_codes:
                        shot_item.setSelected(True)
        self.shot_tree.blockSignals(False)

    @staticmethod
    def _asset_key(asset: Asset) -> tuple[str, str, str]:
        return (asset.category, asset.group, asset.name)

    def _current_asset_key(self) -> tuple[str, str, str] | None:
        asset = self._current_asset()
        if not asset:
            return None
        return self._asset_key(asset)

    def _show_current_asset(self) -> None:
        asset = self._current_asset()
        self._update_asset_action_state()
        self._populate_asset_variants()
        self._populate_variants()
        self.work_list.setSortingEnabled(False)
        self.work_list.blockSignals(True)
        self.work_list.setRowCount(0)
        self.work_list.blockSignals(False)
        self.data_list.clear()
        if getattr(self, "construct_tree", None):
            self.construct_tree.clear()
        self.preview_list.setRowCount(0)
        self._clear_context_state()
        self.publish_list.clear()
        self._update_dependency_label(asset)
        self._update_detail_asset_info(asset)
        if not asset:
            return

        department = self._current_department()
        variant = self._work_variant_arg(asset)
        subset = self._work_subset_arg(asset)
        work_files = self.manager.list_work_files(
            asset,
            dcc=current_dcc_name(),
            department=department,
            variant=variant,
            subset=subset,
            extensions=["ma", "mb", "hip", "hiplc", "hipnc"],
        )
        if not work_files:
            self.status_label.setText(
                f"No work scenes found under: {self.manager.work_root_dir(asset, dcc=current_dcc_name(), department=department, variant=variant, subset=subset or '')}"
            )

        self.work_list.blockSignals(True)
        for path in work_files:
            row = self.work_list.rowCount()
            self.work_list.insertRow(row)
            parsed = self.manager.parse_work_file(path) or {}
            publish_record = self.manager.publish_record_for_work_file(asset, path)
            file_item = QtWidgets.QTableWidgetItem(path.name)
            if publish_record:
                file_item.setIcon(self.published_work_icon)
                file_item.setToolTip(
                    f"Published official version: v{int(publish_record['version']):03d}"
                )
            file_item.setData(QtCore.Qt.UserRole, str(path))
            file_item.setFlags(file_item.flags() & ~QtCore.Qt.ItemIsEditable)
            thumb_item = QtWidgets.QTableWidgetItem("")
            thumb_item.setFlags(thumb_item.flags() & ~QtCore.Qt.ItemIsEditable)
            thumbnail = self.manager.thumbnail_path_for_workfile(path)
            if thumbnail.exists():
                thumb_item.setIcon(QtGui.QIcon(str(thumbnail)))
            updated_item = QtWidgets.QTableWidgetItem(self._format_updated(path))
            updated_item.setFlags(updated_item.flags() & ~QtCore.Qt.ItemIsEditable)
            self.work_list.setItem(row, 0, thumb_item)
            self.work_list.setItem(row, 1, file_item)
            self.work_list.setItem(row, 2, updated_item)
            comment_item = QtWidgets.QTableWidgetItem(self.manager.file_comment(path))
            self.work_list.setItem(row, 3, comment_item)
        self.work_list.blockSignals(False)
        self.work_list.setSortingEnabled(True)
        self.work_list.resizeColumnsToContents()

        self._populate_data_tree(asset)
        self._populate_construct_tree(asset)
        self._update_data_watcher(asset)
        self._populate_preview_list(asset)
        self._populate_context_pack_tree()
        self._populate_asset_publish_tree(asset)
        if self._selected_publish_type() == "retarget":
            self._refresh_retarget_publish_tab(asset)
        self._update_selected_file_info()

        for path in self.manager.list_publish_files(asset):
            item = QtWidgets.QListWidgetItem(path.relative_to(asset.root).as_posix())
            item.setData(QtCore.Qt.UserRole, str(path))
            self.publish_list.addItem(item)

    def _refresh_current_data(self) -> None:
        asset = self._current_asset()
        if not asset:
            return
        self._populate_data_tree(asset)
        self._populate_construct_tree(asset)
        self._update_data_watcher(asset)
        self._update_selected_file_info()
        self.status_label.setText("Data refreshed")

    def _populate_asset_publish_tree(self, asset: Asset | None = None) -> None:
        tree = getattr(self, "asset_publish_tree", None)
        if tree is None:
            return
        tree.clear()
        asset = asset or self._current_asset()
        if not asset:
            return

        type_item = self.asset_publish_type_list.currentItem()
        publish_type = str(type_item.data(QtCore.Qt.UserRole) or "geometry") if type_item else "geometry"
        variant = self._current_asset_variant()
        roots = []
        variant_publish = asset.variant_root(variant) / "publish"
        if variant_publish.exists():
            roots.append(variant_publish)
        if asset.publish_dir.exists() and asset.publish_dir not in roots:
            roots.append(asset.publish_dir)

        branches = []
        seen = set()
        for root in roots:
            for latest_path in root.rglob("latest.json"):
                branch = latest_path.parent
                key = branch.as_posix().lower()
                if key in seen or not self._publish_branch_matches(branch, root, publish_type):
                    continue
                seen.add(key)
                branches.append((root, branch))

        for root, branch in sorted(branches, key=lambda value: value[1].as_posix().lower()):
            relative = branch.relative_to(root)
            parts = relative.parts
            department = parts[0] if parts else "publish"
            representation = parts[-1] if len(parts) > 1 else ""
            component = self._publish_component_name(branch, asset.name)
            latest_data = self._read_json_for_table(branch / "latest.json")
            latest = self._metadata_version(latest_data)
            versions_data = self._read_json_for_table(branch / "versions.json")
            official = self._official_publish_version(versions_data)
            versions = sorted(
                (path for path in branch.iterdir() if path.is_dir() and re.fullmatch(r"v\d+", path.name)),
                key=lambda path: int(path.name[1:]),
                reverse=True,
            )
            if not official:
                official = self._official_from_publish_records(versions)

            label = " / ".join(parts) if parts else branch.name
            root_item = QtWidgets.QTreeWidgetItem(
                [label, component, representation, official, latest, self._publish_branch_state(official, latest), "", ""]
            )
            root_item.setData(0, QtCore.Qt.UserRole, str(branch))
            root_item.setToolTip(0, str(branch))
            self._style_publish_state(root_item, 5)
            tree.addTopLevelItem(root_item)

            for version_dir in versions:
                publish_json = version_dir / "publish.json"
                record = self._read_json_for_table(publish_json)
                updated = self._format_updated(publish_json if publish_json.exists() else version_dir)
                comment = str(record.get("comment") or "") if isinstance(record, dict) else ""
                state = "LATEST" if version_dir.name == latest else self._record_publish_status(record)
                child = QtWidgets.QTreeWidgetItem(
                    [version_dir.name, component, representation, official, latest, state, updated, comment]
                )
                child.setData(0, QtCore.Qt.UserRole, str(version_dir))
                child.setToolTip(0, str(version_dir))
                self._style_publish_state(child, 5)
                root_item.addChild(child)
            root_item.setExpanded(True)

        tree.resizeColumnToContents(0)
        tree.resizeColumnToContents(1)
        tree.resizeColumnToContents(2)
        self._update_publish_selected_state()

    def _update_publish_selected_state(self) -> None:
        button = getattr(self, "publish_selected_btn", None)
        if button is not None:
            source_path, source_kind = self._publish_source_path()
            is_retarget = self._selected_publish_type() == "retarget"
            profile_widget = getattr(self, "retarget_profile_edit", None)
            profile_path = profile_widget.text().strip() if profile_widget is not None else ""
            button.setText("Publish Retarget" if is_retarget else "Publish Selected")
            button.setEnabled(bool(self._current_asset() and (profile_path if is_retarget else source_path)))
            if is_retarget:
                button.setToolTip("Validate and version-publish the selected Retarget profile")
            elif source_path:
                button.setToolTip(f"Publish current {source_kind} scene: {Path(source_path).name}")
            else:
                button.setToolTip("Open a saved asset work scene before publishing")
        usd_button = getattr(self, "publish_geometry_usd_btn", None)
        if usd_button is not None:
            is_geometry = self._selected_publish_type() == "geometry"
            usd_button.setVisible(is_geometry)
            usd_button.setEnabled(bool(is_geometry and self._current_asset() and source_path))

    def _on_publish_type_changed(self, _row: int = -1) -> None:
        is_retarget = self._selected_publish_type() == "retarget"
        if getattr(self, "retarget_publish_options", None) is not None:
            self.retarget_publish_options.setVisible(is_retarget)
        if is_retarget:
            self._refresh_retarget_publish_tab(self._current_asset())
        self._populate_asset_publish_tree()
        self._update_publish_selected_state()

    def _publish_source_path(self) -> tuple[str | None, str]:
        current_scene = current_dcc_scene_path()
        if current_scene:
            return current_scene, "open"
        return self._selected_work_path(), "selected work"

    def _selected_publish_type(self) -> str:
        item = getattr(self, "asset_publish_type_list", None)
        current = item.currentItem() if item is not None else None
        return str(current.data(QtCore.Qt.UserRole) or "geometry") if current else "geometry"

    @staticmethod
    def _publish_type_departments(publish_type: str) -> set[str]:
        return {
            "geometry": {"model", "geo", "geometry"},
            "rig": {"rig"},
            "look": {"look", "surfacing"},
            "groom": {"groom"},
            "retarget": {"rig"},
        }.get(str(publish_type).lower(), set())

    @staticmethod
    def _publish_branch_matches(branch: Path, root: Path, publish_type: str) -> bool:
        parts = [part.lower() for part in branch.relative_to(root).parts]
        department = parts[0] if parts else ""
        aliases = {
            "asset": {"asset", "usd"},
            "geometry": {"model", "geo", "geometry"},
            "rig": {"rig", "skeleton", "skin"},
            "look": {"look", "surfacing", "material", "materials"},
            "groom": {"groom", "hair"},
            "retarget": {"retarget"},
        }
        return department in aliases.get(publish_type, {publish_type})

    @staticmethod
    def _metadata_version(data) -> str:
        if not isinstance(data, dict):
            return ""
        version = str(data.get("version") or "").strip()
        if version:
            return version
        path = str(data.get("path") or "").replace("\\", "/")
        match = re.search(r"(?:^|/)(v\d+)(?:/|$)", path)
        return match.group(1) if match else ""

    @staticmethod
    def _official_publish_version(data) -> str:
        entries = data.get("versions", []) if isinstance(data, dict) else data
        if not isinstance(entries, list):
            return ""
        official = ""
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            status = str(entry.get("status") or "").lower()
            if status not in {"approved", "official", "released", "stable"}:
                continue
            version = str(entry.get("version") or "")
            if version and (not official or int(version.lstrip("v") or 0) > int(official.lstrip("v") or 0)):
                official = version
        return official

    def _official_from_publish_records(self, versions: list[Path]) -> str:
        for version_dir in versions:
            record = self._read_json_for_table(version_dir / "publish.json")
            if not isinstance(record, dict):
                continue
            if str(record.get("status") or "").lower() in {"approved", "official", "released", "stable"}:
                return version_dir.name
        return ""

    def _publish_component_name(self, branch: Path, asset_name: str) -> str:
        latest = self._read_json_for_table(branch / "latest.json")
        if isinstance(latest, dict):
            path_value = str(latest.get("path") or "")
            if path_value:
                name = Path(path_value).name
                if name:
                    return name
        versions = sorted((path for path in branch.iterdir() if path.is_dir()), reverse=True)
        for version_dir in versions:
            files = sorted(
                path for path in version_dir.iterdir()
                if path.is_file() and path.name not in {"publish.json", "validation.json"}
            )
            if files:
                return files[0].name
        return f"{asset_name}.usd" if branch.name.lower() == "usd" else ""

    @staticmethod
    def _publish_branch_state(official: str, latest: str) -> str:
        if not latest:
            return "MISSING"
        if official and official != latest:
            return "UPDATE AVAILABLE"
        return "READY"

    @staticmethod
    def _record_publish_status(record) -> str:
        if not isinstance(record, dict):
            return "PUBLISHED"
        return str(record.get("status") or "PUBLISHED").upper()

    @staticmethod
    def _style_publish_state(item, column: int) -> None:
        state = item.text(column).upper()
        color = None
        if state in {"READY", "LATEST", "APPROVED", "OFFICIAL", "RELEASED"}:
            color = QtGui.QColor("#72c48f")
        elif state in {"UPDATE AVAILABLE", "WARNING"}:
            color = QtGui.QColor("#d6b46b")
        elif state in {"MISSING", "ERROR", "FAILED"}:
            color = QtGui.QColor("#df7777")
        if color:
            item.setForeground(column, QtGui.QBrush(color))

    def _schedule_data_refresh(self, _path: str = "") -> None:
        self.data_refresh_timer.start()

    def _refresh_current_data_from_watcher(self) -> None:
        asset = self._current_asset()
        if not asset:
            return
        self._populate_data_tree(asset)
        self._update_data_watcher(asset)
        self._update_selected_file_info()
        self.status_label.setText("Data refreshed from folder change")

    def _update_data_watcher(self, asset: Asset | None) -> None:
        if not getattr(self, "data_watcher", None):
            return
        current_dirs = self.data_watcher.directories()
        if current_dirs:
            self.data_watcher.removePaths(current_dirs)
        current_files = self.data_watcher.files()
        if current_files:
            self.data_watcher.removePaths(current_files)
        if not asset:
            return
        paths = []
        roots = [asset.data_dir]
        for variant in self.manager.asset_variants(asset):
            roots.append(asset.variant_root(variant) / "data")
        for root in roots:
            if not root.exists():
                continue
            paths.append(str(root))
            for path in root.rglob("*"):
                if path.is_dir():
                    paths.append(str(path))
                elif path.name in {"latest.json", "versions.json", "publish.json", "data.json"}:
                    paths.append(str(path))
        existing = []
        seen = set()
        for path in paths:
            if path in seen or not Path(path).exists():
                continue
            existing.append(path)
            seen.add(path)
        if existing:
            self.data_watcher.addPaths(existing)

    def _open_current(self, path_type: str) -> None:
        asset = self._current_asset()
        if not asset:
            return
        path = asset.paths()[path_type]
        path.mkdir(parents=True, exist_ok=True)
        self.manager.open_in_explorer(path)

    def _current_department(self) -> str:
        item = self.dept_list.currentItem()
        if item:
            return item.text()
        index = self.dept_tabs.currentIndex()
        if index >= 0:
            return self.dept_tabs.tabText(index)
        return "model"

    def _selected_work_scene_extension(self) -> str:
        if current_dcc_name() != "maya":
            return current_work_scene_extension()
        combo = getattr(self, "work_scene_format_combo", None)
        return str(combo.currentData() or "ma") if combo is not None else "ma"

    def _current_variant(self) -> str:
        item = self.variant_list.currentItem()
        if item:
            return item.text()
        variants = self.manager.work_subsets(self._current_department(), asset=self._current_asset())
        return variants[0] if variants else "main"

    def _current_asset_variant(self) -> str:
        item = self.asset_variant_list.currentItem()
        if item:
            return item.text().strip() or "default"
        text = self.asset_variant_combo.currentText().strip()
        return text or "default"

    def _work_variant_arg(self, asset: Asset | None) -> str:
        if asset and asset.uses_variant_structure(self._current_asset_variant()):
            return self._current_asset_variant()
        return self._current_variant()

    def _work_subset_arg(self, asset: Asset | None) -> str | None:
        if asset and asset.uses_variant_structure(self._current_asset_variant()):
            return self._current_variant()
        return None

    def _latest_work_file(self, paths: list[Path]) -> Path | None:
        latest_path = None
        latest_key = (-1, -1)
        for path in paths:
            parsed = self.manager.parse_work_file(path)
            if not parsed:
                continue
            key = (parsed["version"], parsed["take"])
            if key > latest_key:
                latest_key = key
                latest_path = path
        return latest_path

    def _format_updated(self, path: Path) -> str:
        try:
            return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            return ""

    def _data_version_for_path(self, path: Path) -> str:
        for part in reversed(path.parts):
            if part.lower().startswith("v") and part[1:].isdigit():
                return part.lower()
        parsed = self.manager.parse_work_file(path)
        if parsed:
            return f"v{parsed['version']:03d}"
        return ""

    def _data_comment_for_path(self, path: Path) -> str:
        comment = self.manager.file_comment(path)
        if comment:
            return comment
        publish_json = path.parent / "publish.json"
        if publish_json.exists():
            try:
                import json
                with publish_json.open("r", encoding="utf-8") as f:
                    return str((json.load(f) or {}).get("comment", ""))
            except Exception:
                return ""
        return ""

    def _selected_info_path(self) -> Path | None:
        if getattr(self, "detail_tabs", None) and self.detail_tabs.currentWidget() == getattr(self, "data_tab", None):
            data_item = self.data_list.currentItem()
            if data_item:
                data_path = data_item.data(0, QtCore.Qt.UserRole)
                if data_path:
                    return Path(str(data_path))
            return None
        if getattr(self, "detail_tabs", None) and self.detail_tabs.currentWidget() == getattr(self, "preview_tab", None):
            preview_path = self._selected_preview_review_json()
            return Path(preview_path) if preview_path else None
        work_path = self._selected_work_path()
        if work_path:
            return Path(work_path)
        data_item = self.data_list.currentItem()
        if data_item:
            data_path = data_item.data(0, QtCore.Qt.UserRole)
            if data_path:
                return Path(str(data_path))
        publish_item = self.publish_list.currentItem()
        if publish_item:
            publish_path = publish_item.data(QtCore.Qt.UserRole)
            if publish_path:
                return Path(str(publish_path))
        return None

    def _json_candidates_for_file(self, path: Path) -> list[Path]:
        candidates = [
            path.parent / f"{path.name}.json",
            path.with_suffix(".json"),
            path.parent / "publish.json",
        ]
        return [candidate for candidate in candidates if candidate.exists()]

    def _update_selected_file_info(self) -> None:
        path = self._selected_info_path()
        if path and path.exists():
            for json_path in self._json_candidates_for_file(path):
                data = self._read_json_for_table(json_path)
                if isinstance(data, dict):
                    self._populate_file_info_table(data)
                    return
            self._populate_file_info_table({"path": str(path), "json": "not found"})
            return

        asset = self._current_asset()
        if asset:
            self._populate_file_info_table(self.manager.load_asset_metadata(asset))
        else:
            self.file_info_table.setRowCount(0)

    def _read_json_for_table(self, path: Path):
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _populate_file_info_table(self, data: dict) -> None:
        self.file_info_table.setRowCount(0)
        for key, value in self._flatten_table_data(data):
            row = self.file_info_table.rowCount()
            self.file_info_table.insertRow(row)
            key_item = QtWidgets.QTableWidgetItem(key)
            value_item = QtWidgets.QTableWidgetItem(value)
            key_item.setFlags(key_item.flags() & ~QtCore.Qt.ItemIsEditable)
            value_item.setFlags(value_item.flags() & ~QtCore.Qt.ItemIsEditable)
            self.file_info_table.setItem(row, 0, key_item)
            self.file_info_table.setItem(row, 1, value_item)
        self.file_info_table.resizeColumnsToContents()
        self.file_info_table.horizontalHeader().setStretchLastSection(True)

    def _flatten_table_data(self, data, prefix: str = "") -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        if isinstance(data, dict):
            for key, value in data.items():
                next_key = f"{prefix}.{key}" if prefix else str(key)
                if isinstance(value, dict):
                    rows.extend(self._flatten_table_data(value, next_key))
                elif isinstance(value, list):
                    rows.append((next_key, ", ".join(str(item) for item in value)))
                else:
                    rows.append((next_key, str(value)))
        return rows

    def _populate_preview_list(self, asset: Asset) -> None:
        self.preview_list.setRowCount(0)
        variant = self._work_variant_arg(asset)
        department = self._current_department()
        subset = self._work_subset_arg(asset) or self._current_variant()
        preview_rows = []
        base_dirs = [
            quick_preview_base_dir(asset, variant, department, subset),
            turntable_preview_base_dir(asset, variant),
        ]
        seen_dirs = set()
        for base_dir in base_dirs:
            if not base_dir.exists() or base_dir in seen_dirs:
                continue
            seen_dirs.add(base_dir)
            for version_dir in sorted(base_dir.glob("v*"), reverse=True):
                review_json = version_dir / "review.json"
                if not version_dir.is_dir() or not review_json.exists():
                    continue
                review_data = self._read_json_for_table(review_json)
                if not isinstance(review_data, dict):
                    continue
                outputs = review_data.get("outputs") or {}
                preview_rows.append((version_dir, review_json, review_data, outputs))

        for version_dir, review_json, review_data, outputs in preview_rows:
            row = self.preview_list.rowCount()
            self.preview_list.insertRow(row)
            values = [
                str(review_data.get("version") or version_dir.name),
                str(review_data.get("type") or "preview"),
                self._format_updated(review_json),
                ", ".join(str(item) for item in review_data.get("views") or outputs.keys()),
                str(review_json),
            ]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                if column == 0:
                    item.setData(QtCore.Qt.UserRole, str(review_json))
                self.preview_list.setItem(row, column, item)
        self.preview_list.resizeColumnsToContents()
        self.preview_list.horizontalHeader().setStretchLastSection(True)
        if self.preview_list.rowCount():
            self.preview_list.setCurrentCell(0, 0)

    def _populate_context_versions(self) -> None:
        self.context_version_combo.blockSignals(True)
        self.context_version_combo.clear()
        try:
            service = _asset_context_service(self.manager.config_dir)
            versions = service.context_versions("asset")
            active = service.active_context_version("asset") if versions else ""
            self.context_version_combo.addItems(versions)
            index = self.context_version_combo.findText(active)
            if index >= 0:
                self.context_version_combo.setCurrentIndex(index)
        except Exception as exc:
            self.status_label.setText(str(exc))
        self.context_version_combo.blockSignals(False)
        self._populate_context_profiles()

    def _populate_context_profiles(self, *_args) -> None:
        current = self._current_context_profile()
        self.context_profile_list.clear()
        version = self.context_version_combo.currentText().strip() or None
        try:
            service = _asset_context_service(self.manager.config_dir)
            asset = self._current_asset()
            profiles = (
                service.quality_profiles_for_asset(
                    self._asset_context_identity(asset), "asset", version
                )
                if asset
                else service.quality_profiles("asset", version)
            )
            self.context_profile_list.addItems(profiles)
            preferred = current or "WORK"
            matches = self.context_profile_list.findItems(preferred, QtCore.Qt.MatchExactly)
            index = self.context_profile_list.row(matches[0]) if matches else 0
            if index >= 0:
                self.context_profile_list.setCurrentRow(index)
        except Exception as exc:
            self.status_label.setText(str(exc))
        self._populate_context_pack_tree()

    def _current_context_profile(self) -> str:
        item = self.context_profile_list.currentItem() if getattr(self, "context_profile_list", None) else None
        return item.text().strip() if item else ""

    def _on_context_profile_selected(self, _row: int) -> None:
        self._populate_context_pack_tree()
        self._assemble_selected_asset_context(silent=True)

    def _clear_context_state(self) -> None:
        if not getattr(self, "context_state_table", None):
            return
        self.context_assembly = None
        self.context_verification = None
        self.context_state_table.setRowCount(0)
        self.context_pack_btn.setEnabled(False)
        self._set_context_readiness("BLOCKED", "Assemble a Context first")
        if getattr(self, "context_pack_tree", None):
            self.context_pack_tree.clear()

    def _asset_context_identity(self, asset: Asset):
        _ensure_smartlib_on_path()
        from smartlib.core.path_resolver import AssetIdentity

        return AssetIdentity(
            category=asset.category,
            group=asset.group,
            name=asset.name,
            variant=self._current_asset_variant() if asset.uses_variant_structure(self._current_asset_variant()) else "default",
        )

    def _use_current_scene_as_assembly(self) -> None:
        asset = self._current_asset()
        if not asset:
            self.status_label.setText("Select an asset first")
            return
        if not self._save_modified_maya_scene("registering the current scene"):
            return
        try:
            import maya.cmds as cmds

            _ensure_smartlib_on_path()
            from smartlib.core.config_loader import ProjectConfig
            from smartlib.core.path_resolver import ProjectPaths

            scene_text = str(cmds.file(query=True, sceneName=True) or "").strip()
            if not scene_text:
                raise RuntimeError("Save the current Maya scene before using it as an assembly input.")

            project_config = ProjectConfig(self.manager.config_dir)
            if project_config.project_root is None:
                raise RuntimeError("project_root is not set in the project configuration.")
            identity = self._asset_context_identity(asset)
            variant_root = ProjectPaths(
                project_config.project_root,
                project_config.templates,
                project_config.project_name,
            ).asset_variant_root(identity)
            work_root = variant_root / "work"
            scene_path = Path(scene_text).resolve()
            try:
                scene_path.relative_to(work_root.resolve())
            except ValueError as exc:
                raise RuntimeError(
                    "The current Maya scene does not belong to the selected asset work area.\n"
                    f"Selected: {identity.category}/{identity.group}/{identity.name}/{identity.variant}\n"
                    f"Expected under: {work_root}\n"
                    f"Current scene: {scene_path}"
                ) from exc

            comment = self._ask_comment("USD Current Scene Comment")
            if comment is None:
                return

            service = _asset_context_service(self.manager.config_dir)
            profile = self._current_context_profile()
            if not profile:
                raise RuntimeError("Select a quality profile first.")
            version = self.context_version_combo.currentText().strip() or None
            resolved = service.assemble(
                identity,
                context_name="asset",
                context_version=version,
                quality_profile=profile,
            )
            self.context_assembly, self.context_verification = service.write_current_scene_assembly(
                resolved,
                scene_path,
                comment=comment,
            )
            self._populate_context_state(self.context_assembly, service)
            self._populate_context_pack_tree()
            self.status_label.setText(
                f"USD Current Scene registered: {self.context_verification.scene_path}"
            )
        except Exception as exc:
            self.status_label.setText(str(exc))
            QtWidgets.QMessageBox.critical(self, "USD Current Scene Failed", str(exc))

    def _assemble_selected_asset_context(self, silent: bool = False) -> None:
        asset = self._current_asset()
        if not asset:
            self.status_label.setText("Select an asset first")
            return
        version = self.context_version_combo.currentText().strip() or None
        profile = self._current_context_profile()
        if not profile:
            self.status_label.setText("Select a quality profile first")
            return
        try:
            service = _asset_context_service(self.manager.config_dir)
            self.context_assembly = service.assemble(
                self._asset_context_identity(asset),
                context_name="asset",
                context_version=version,
                quality_profile=profile,
            )
            self.context_verification = service.current_assembly(self.context_assembly)
            if not silent and not self.context_assembly.errors:
                maya_scene_builder = None
                maya_preview = None
                try:
                    import maya.cmds  # noqa: F401
                    from smartlib.dcc.maya.asset_context import (
                        open_context_asset_assembly,
                        write_context_asset_snapshot,
                    )

                    look_scenes = self._context_scene_paths(self.context_assembly, "look")
                    # A rig/model publish without an additional look layer is
                    # already an immutable scene snapshot. Copy it byte-for-byte
                    # so missing optional plug-ins cannot make Maya reject an
                    # otherwise readable binary scene during open/resave.
                    if look_scenes:
                        maya_scene_builder = lambda source, target: write_context_asset_snapshot(
                            source,
                            target,
                            look_scenes=look_scenes,
                            restore_previous=False,
                        )
                    maya_preview = open_context_asset_assembly
                except ImportError:
                    maya_scene_builder = None
                self.context_verification = service.write_assembly(
                    self.context_assembly,
                    maya_scene_builder=maya_scene_builder,
                )
                if maya_preview and self.context_verification.scene_path.suffix.lower() in {".ma", ".mb"}:
                    try:
                        maya_preview(
                            self.context_verification.scene_path,
                            asset.name,
                            resolve_asset_work_template(self.manager, "model"),
                        )
                    except Exception as exc:
                        raise RuntimeError(
                            "Context snapshot was written, but Maya could not open the verification scene.\n"
                            f"Snapshot: {self.context_verification.scene_path}\n{exc}"
                        ) from exc
            self._populate_context_state(self.context_assembly, service)
            self._populate_context_pack_tree()
            if self.context_verification and not silent and not self.context_assembly.errors:
                self.status_label.setText(
                    f"Context verifying: {asset.name} {self.context_assembly.context_version} {profile}"
                )
            else:
                self.status_label.setText(
                    f"Context resolved: {asset.name} {self.context_assembly.context_version} {profile}"
                )
        except Exception as exc:
            if self.context_assembly:
                self._populate_context_state(self.context_assembly)
                self._populate_context_pack_tree()
            else:
                self._clear_context_state()
            if silent:
                self.status_label.setText(str(exc))
            else:
                QtWidgets.QMessageBox.critical(self, "Context Assemble Failed", str(exc))

    def _populate_context_state(self, assembly, service=None) -> None:
        official_versions = self._context_official_versions(assembly, service)
        self._populate_context_entries(assembly.entries, official_versions)
        can_pack = not assembly.errors
        if assembly.errors:
            self._set_context_readiness("BLOCKED", f"{len(assembly.errors)} representation missing")
        elif can_pack and service:
            self.context_verification = service.current_assembly(assembly)
            has_changes = service.has_pack_changes(assembly)
            is_verified = service.is_current_assembly(assembly, self.context_verification)
            can_pack = has_changes and is_verified
            if not has_changes:
                self.status_label.setText("Context pack is unchanged from latest")
                self._set_context_readiness("UNCHANGED", "Official versions already match latest Pack")
            elif not is_verified:
                self.status_label.setText("Assemble a verification scene before Pack")
                self._set_context_readiness("BLOCKED", "Verification assembly is missing or stale")
            else:
                newer_count = self._context_newer_latest_count(assembly.entries, official_versions)
                suffix = f"; {newer_count} newer latest available" if newer_count else ""
                self._set_context_readiness(
                    "READY",
                    f"Verification assembly is current; {len(assembly.entries)} representations resolved{suffix}",
                )
        elif can_pack:
            self._set_context_readiness("RESOLVED", f"{len(assembly.entries)} representations resolved")
        self.context_pack_btn.setEnabled(can_pack)

    def _populate_context_entries(self, entries, official_versions=None) -> None:
        official_versions = official_versions or {}
        self.context_state_table.setRowCount(0)
        for entry in entries:
            row = self.context_state_table.rowCount()
            self.context_state_table.insertRow(row)
            official_version = official_versions.get(
                self._context_entry_key(entry.publish_type, entry.requested_subset),
                "",
            )
            values = [
                entry.publish_type,
                entry.requested_subset,
                entry.resolved_subset,
                entry.status,
                official_version,
                entry.latest_version,
                entry.comment,
            ]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(str(value))
                if entry.status == "MISSING":
                    item.setForeground(QtGui.QColor("#d88888"))
                elif entry.status == "SKIPPED":
                    item.setForeground(QtGui.QColor("#888888"))
                elif entry.status == "FALLBACK":
                    item.setForeground(QtGui.QColor("#d6b46b"))
                elif (
                    column in (4, 5)
                    and official_version
                    and entry.latest_version
                    and official_version != entry.latest_version
                ):
                    item.setForeground(QtGui.QColor("#d6b46b"))
                self.context_state_table.setItem(row, column, item)
        self.context_state_table.resizeColumnsToContents()
        self.context_state_table.horizontalHeader().setStretchLastSection(True)

    def _set_context_readiness(self, state: str, message: str) -> None:
        if not getattr(self, "context_readiness_label", None):
            return
        colors = {
            "READY": ("#244638", "#9edcb8"),
            "RESOLVED": ("#31485b", "#a9d4f3"),
            "UNCHANGED": ("#353535", "#b8b8b8"),
            "BLOCKED": ("#4a3925", "#e0be78"),
        }
        background, text = colors.get(state, colors["BLOCKED"])
        self.context_readiness_label.setText(f"PACK {state}: {message}")
        self.context_readiness_label.setStyleSheet(
            "QLabel#context_readiness_label {"
            " padding: 4px 6px;"
            " border: 1px solid #5d5d5d;"
            f" background: {background};"
            f" color: {text};"
            "}"
        )

    @staticmethod
    def _context_entry_key(publish_type: str, requested_subset: str) -> tuple[str, str]:
        return str(publish_type), str(requested_subset)

    def _context_official_versions(self, assembly, service) -> dict[tuple[str, str], str]:
        if not service:
            return {}
        try:
            packs = service.list_packs(
                assembly.identity,
                quality_profile=assembly.quality_profile,
                context_name=assembly.context_name,
            )
        except Exception:
            return {}
        if not packs:
            return {}
        versions = {}
        for entry in packs[0]["manifest"].get("resolved_representations") or []:
            if not isinstance(entry, dict):
                continue
            versions[
                self._context_entry_key(
                    str(entry.get("publish_type") or ""),
                    str(entry.get("requested_subset") or ""),
                )
            ] = str(entry.get("version") or "")
        return versions

    @staticmethod
    def _context_scene_paths(assembly, publish_type: str) -> list[Path]:
        paths = []
        for entry in assembly.entries:
            if entry.publish_type != publish_type:
                continue
            for key in ("ma", "mb"):
                raw_path = str((entry.files or {}).get(key) or "").strip()
                if not raw_path:
                    continue
                path = Path(raw_path)
                if path.is_file():
                    paths.append(path)
                    break
        return paths

    @staticmethod
    def _context_newer_latest_count(entries, official_versions) -> int:
        return sum(
            1
            for entry in entries
            if official_versions.get((str(entry.publish_type), str(entry.requested_subset)))
            and entry.latest_version
            and official_versions[(str(entry.publish_type), str(entry.requested_subset))] != entry.latest_version
        )

    def _pack_selected_asset_context(self) -> None:
        if not self.context_assembly:
            self.status_label.setText("Assemble a context first")
            return
        if not self._save_modified_maya_scene("packing the Context"):
            return
        try:
            service = _asset_context_service(self.manager.config_dir)
            maya_usd_builder = None
            try:
                import maya.cmds  # noqa: F401
                from smartlib.dcc.maya.asset_context import write_context_asset_usd_snapshot

                contract = current_usd_skel_contract()
                maya_usd_builder = lambda source, target, asset_name: write_context_asset_usd_snapshot(
                    source,
                    target,
                    asset_name=asset_name,
                    contract=contract,
                )
            except ImportError:
                maya_usd_builder = None
            packed = service.pack(
                self.context_assembly,
                assembled=self.context_verification,
                maya_usd_builder=maya_usd_builder,
            )
            self.status_label.setText(f"Context packed: {packed.version_dir}")
            self._populate_context_state(self.context_assembly, service)
            self._populate_context_pack_tree()
            QtWidgets.QMessageBox.information(
                self,
                "Context Pack",
                "Context pack was created.\n"
                f"Path: {packed.version_dir}",
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Context Pack Failed", str(exc))

    def _populate_context_pack_tree(self) -> None:
        if not getattr(self, "context_pack_tree", None):
            return
        self.context_pack_tree.clear()
        asset = self._current_asset()
        profile = self._current_context_profile()
        if not asset or not profile:
            return
        try:
            service = _asset_context_service(self.manager.config_dir)
            resolved = service.assemble(
                self._asset_context_identity(asset),
                context_name="asset",
                context_version=self.context_version_combo.currentText().strip() or None,
                quality_profile=profile,
            )
            verification = service.current_assembly(resolved)
            packs = service.list_packs(self._asset_context_identity(asset), quality_profile=profile)
        except Exception as exc:
            self.status_label.setText(str(exc))
            return
        if verification and service.is_current_assembly(resolved, verification):
            try:
                with verification.assembly_json.open("r", encoding="utf-8") as stream:
                    status = str((json.load(stream) or {}).get("status") or "verifying").upper()
            except Exception:
                status = "VERIFYING"
            assembly_item = QtWidgets.QTreeWidgetItem(["_assembly", "", status, "verification scene"])
            assembly_item.setForeground(0, QtGui.QColor("#d6b46b"))
            assembly_item.setExpanded(True)
            self.context_pack_tree.addTopLevelItem(assembly_item)
            for entry in resolved.manifest.get("resolved_representations") or []:
                assembly_item.addChild(
                    QtWidgets.QTreeWidgetItem(
                        [
                            str(entry.get("publish_type") or ""),
                            str(entry.get("resolved_subset") or entry.get("requested_subset") or ""),
                            str(entry.get("version") or ""),
                            str(entry.get("comment") or ""),
                        ]
                    )
                )
        for pack in packs:
            version_item = QtWidgets.QTreeWidgetItem([pack["version"], "", "", pack.get("comment", "")])
            version_item.setData(0, QtCore.Qt.UserRole, pack["manifest"])
            version_item.setExpanded(True)
            self.context_pack_tree.addTopLevelItem(version_item)
            for entry in pack["manifest"].get("resolved_representations") or []:
                version_item.addChild(
                    QtWidgets.QTreeWidgetItem(
                        [
                            str(entry.get("publish_type") or ""),
                            str(entry.get("resolved_subset") or entry.get("requested_subset") or ""),
                            str(entry.get("version") or ""),
                            str(entry.get("comment") or ""),
                        ]
                    )
                )
        self._apply_context_pack_tree_header()
        self.context_pack_tree.expandAll()

    def _apply_context_pack_tree_header(self) -> None:
        if not getattr(self, "context_pack_tree", None):
            return
        header = self.context_pack_tree.header()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        header.setStretchLastSection(True)
    def _populate_data_tree(self, asset: Asset) -> None:
        self.data_list.clear()
        roots: dict[Path, QtWidgets.QTreeWidgetItem] = {}
        ignored = {"publish.json", "data.json", "source.json", "latest.json", "versions.json"}
        selected_asset_variant = self._current_asset_variant()
        if asset.uses_variant_structure(selected_asset_variant):
            data_roots = [asset.variant_root(selected_asset_variant) / "data"]
        else:
            data_roots = [asset.data_dir]
        files = []
        for data_root in data_roots:
            if not data_root.exists():
                continue
            files.extend(
                path for path in data_root.rglob("*")
                if path.is_file() and path.name not in ignored and not path.name.endswith(".json")
            )
        files = sorted(set(files), key=lambda path: path.as_posix().lower())

        def get_dir_item(dir_path: Path) -> QtWidgets.QTreeWidgetItem:
            if dir_path in roots:
                return roots[dir_path]
            if dir_path in data_roots or dir_path.parent == dir_path:
                item = self.data_list.invisibleRootItem()
                roots[dir_path] = item
                return item
            parent = get_dir_item(dir_path.parent)
            item = QtWidgets.QTreeWidgetItem([dir_path.name, "", "", ""])
            parent.addChild(item)
            item.setExpanded(True)
            roots[dir_path] = item
            return item

        for path in files:
            parent = get_dir_item(path.parent)
            item = QtWidgets.QTreeWidgetItem([
                path.name,
                self._data_version_for_path(path),
                self._format_updated(path),
                self._data_comment_for_path(path),
            ])
            item.setData(0, QtCore.Qt.UserRole, str(path))
            parent.addChild(item)
        self.data_list.expandAll()

    def _construct_service(self, asset: Asset | None = None):
        asset = asset or self._current_asset()
        if not asset:
            return None
        return _asset_construct_service(asset.variant_root(self._current_asset_variant()))

    def _populate_construct_tree(self, asset: Asset | None = None) -> None:
        tree = getattr(self, "construct_tree", None)
        if tree is None:
            return
        tree.clear()
        self._construct_inputs = {}
        asset = asset or self._current_asset()
        service = self._construct_service(asset)
        if not asset or not service:
            return
        try:
            inputs = service.discover()
        except Exception as exc:
            self.status_label.setText(f"Construct discovery failed: {exc}")
            return
        department = self._current_department().lower()
        if department == "model":
            inputs = [item for item in inputs if item.data_type == "geo"]
        elif department == "rig":
            inputs = [item for item in inputs if item.data_type in {"rig/guide", "rig/skin", "guide", "skin"}]
        for construct_input in inputs:
            item = QtWidgets.QTreeWidgetItem(
                [
                    "",
                    construct_input.data_type,
                    construct_input.target,
                    construct_input.representation,
                    "",
                    construct_input.latest_version,
                    construct_input.state,
                ]
            )
            item.setData(0, QtCore.Qt.UserRole, construct_input.logical_id)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(0, QtCore.Qt.Checked if construct_input.use else QtCore.Qt.Unchecked)
            combo = QtWidgets.QComboBox()
            for version in reversed(construct_input.versions):
                combo.addItem(version.version, str(version.path))
            index = combo.findText(construct_input.selected_version)
            combo.setCurrentIndex(max(0, index))
            combo.currentTextChanged.connect(
                lambda _text, row=item: self._update_construct_row_state(row)
            )
            tree.addTopLevelItem(item)
            tree.setItemWidget(item, 4, combo)
            self._construct_inputs[construct_input.logical_id] = construct_input
            self._update_construct_row_state(item)
        self.status_label.setText(f"Construct inputs: {len(inputs)}")

    def _export_selected_data_type(self) -> None:
        data_type = str(self.data_type_combo.currentData() or "geo")
        export_kind = "mesh" if data_type == "geo" else data_type
        self._show_export_data_menu(export_kind)

    def _update_construct_row_state(self, item) -> None:
        logical_id = str(item.data(0, QtCore.Qt.UserRole) or "")
        construct_input = getattr(self, "_construct_inputs", {}).get(logical_id)
        combo = self.construct_tree.itemWidget(item, 4)
        if not construct_input or combo is None:
            return
        construct_input.selected_version = combo.currentText()
        construct_input.use = item.checkState(0) == QtCore.Qt.Checked
        state = construct_input.state
        item.setText(6, state)
        color = "#70b77e" if state == "LATEST" else "#d6b46b" if state == "UPDATE AVAILABLE" else "#d46a6a"
        item.setForeground(6, QtGui.QColor(color))

    def _current_construct_inputs(self):
        inputs = []
        for row in range(self.construct_tree.topLevelItemCount()):
            item = self.construct_tree.topLevelItem(row)
            self._update_construct_row_state(item)
            logical_id = str(item.data(0, QtCore.Qt.UserRole) or "")
            construct_input = getattr(self, "_construct_inputs", {}).get(logical_id)
            if construct_input:
                inputs.append(construct_input)
        return inputs

    def _save_construct_recipe(self, *, quiet: bool = False) -> Path | None:
        asset = self._current_asset()
        service = self._construct_service(asset)
        if not asset or not service:
            return None
        try:
            path = service.save_recipe(
                self._current_construct_inputs(),
                asset=asset.name,
                variant=self._current_asset_variant(),
                department=self._current_department(),
            )
            if not quiet:
                self.status_label.setText(f"Construct recipe saved: {path}")
            return path
        except Exception as exc:
            if not quiet:
                QtWidgets.QMessageBox.critical(self, "Save Construct Recipe Failed", str(exc))
            return None

    def _execute_construct_inputs(self, operation: str) -> None:
        import_extensions = {".ma", ".mb", ".fbx", ".abc", ".usd", ".usda", ".usdc"}
        apply_extensions = {".sgt", ".gskinpack", ".jskin"}
        selected = [item for item in self._current_construct_inputs() if item.use and item.selected]
        if operation == "import":
            selected = [item for item in selected if item.selected.path.suffix.lower() in import_extensions]
        else:
            selected = [item for item in selected if item.selected.path.suffix.lower() in apply_extensions]
        if not selected:
            self.status_label.setText(f"No {operation} inputs are enabled")
            return
        self._save_construct_recipe(quiet=True)
        completed = []
        failures = []
        for construct_input in selected:
            path = construct_input.selected.path
            try:
                import_data_file_to_current_dcc(path)
                completed.append(path.name)
            except Exception as exc:
                failures.append(f"{path.name}: {exc}")
        if failures:
            QtWidgets.QMessageBox.warning(
                self,
                "Construct Inputs",
                f"Completed: {len(completed)}\n\n" + "\n".join(failures),
            )
        self.status_label.setText(
            f"Construct {operation}: {len(completed)} completed, {len(failures)} failed"
        )

    def _build_construct_work_scene(self) -> None:
        """Apply the saved recipe to a staged/current scene and persist it as work."""
        asset = self._current_asset()
        if not asset:
            self.status_label.setText("Select an asset first")
            return
        enabled = [item for item in self._current_construct_inputs() if item.use and item.selected]
        if not enabled:
            self.status_label.setText("No Construct inputs are enabled")
            return
        self._save_construct_recipe(quiet=True)
        try:
            import maya.cmds as cmds
        except ImportError:
            QtWidgets.QMessageBox.information(
                self,
                "Build Work Scene",
                "Build Work Scene is currently available inside Maya.\n"
                "Houdini/Blender adapters can continue to use Import Selected.",
            )
            return

        try:
            scene_path = str(cmds.file(query=True, sceneName=True) or "")
            if not scene_path:
                self._stage_work_scene()
                scene_path = str(cmds.file(query=True, sceneName=True) or "")
            if not scene_path:
                raise RuntimeError("Stage or open a Work Scene before building Construct inputs.")

            department = self._current_department().lower()
            if department == "model":
                self._execute_construct_inputs("import")
            elif department == "rig":
                self._execute_construct_inputs("apply")
            else:
                self._execute_construct_inputs("import")
                self._execute_construct_inputs("apply")
            cmds.file(save=True)
            self.status_label.setText(f"Construct work scene saved: {Path(scene_path).name}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Build Work Scene Failed", str(exc))

    def _latest_published_scene(self) -> Path | None:
        asset = self._current_asset()
        if not asset:
            return None
        latest = self.manager.latest_publish_info(
            asset,
            department=self._current_department(),
            variant=self._work_variant_arg(asset),
            subset=self._work_subset_arg(asset),
            publish_format="ma",
        )
        if latest and latest.get("absolute_path"):
            return Path(latest["absolute_path"])
        return None

    def _on_department_changed(self, _index: int) -> None:
        self._populate_variants()
        self._show_current_asset()

    def _on_department_list_changed(self, _row: int) -> None:
        department = self._current_department()
        for index in range(self.dept_tabs.count()):
            if self.dept_tabs.tabText(index) == department:
                self.dept_tabs.blockSignals(True)
                self.dept_tabs.setCurrentIndex(index)
                self.dept_tabs.blockSignals(False)
                break
        self._populate_variants()
        self._show_current_asset()

    def _on_asset_variant_selected(self, row: int) -> None:
        if row < 0:
            return
        variant = self._current_asset_variant()
        index = self.asset_variant_combo.findText(variant)
        if index >= 0:
            self.asset_variant_combo.blockSignals(True)
            self.asset_variant_combo.setCurrentIndex(index)
            self.asset_variant_combo.blockSignals(False)
        self._show_current_asset()

    def _populate_asset_variants(self) -> None:
        asset = self._current_asset()
        current = self._current_asset_variant()
        self.asset_variant_combo.blockSignals(True)
        self.asset_variant_list.blockSignals(True)
        self.asset_variant_combo.clear()
        self.asset_variant_list.clear()
        variants = (
            self.manager.asset_variants(asset)
            if asset and self.manager.is_asset_initialized(asset)
            else []
        )
        selected = 0
        for index, variant in enumerate(variants):
            self.asset_variant_combo.addItem(variant)
            self.asset_variant_list.addItem(variant)
            if variant == current:
                selected = index
        if variants:
            self.asset_variant_combo.setCurrentIndex(selected)
            self.asset_variant_list.setCurrentRow(selected)
        self.asset_variant_combo.blockSignals(False)
        self.asset_variant_list.blockSignals(False)

    def _populate_variants(self) -> None:
        current = self._current_variant()
        self.variant_list.blockSignals(True)
        self.variant_list.clear()
        selected_row = 0
        for index, variant in enumerate(self.manager.work_subsets(self._current_department(), asset=self._current_asset())):
            self.variant_list.addItem(variant)
            if variant == current:
                selected_row = index
        if self.variant_list.count():
            self.variant_list.setCurrentRow(selected_row)
        self.variant_list.blockSignals(False)

    def _restore_detail_selection(self, asset_variant: str, department: str, subset: str) -> None:
        self._set_list_current_text(self.asset_variant_list, asset_variant)
        index = self.asset_variant_combo.findText(asset_variant)
        if index >= 0:
            self.asset_variant_combo.blockSignals(True)
            self.asset_variant_combo.setCurrentIndex(index)
            self.asset_variant_combo.blockSignals(False)
        self._set_list_current_text(self.dept_list, department)
        for tab_index in range(self.dept_tabs.count()):
            if self.dept_tabs.tabText(tab_index) == department:
                self.dept_tabs.blockSignals(True)
                self.dept_tabs.setCurrentIndex(tab_index)
                self.dept_tabs.blockSignals(False)
                break
        self._set_list_current_text(self.variant_list, subset)

    @staticmethod
    def _set_list_current_text(list_widget, text: str) -> bool:
        if not text:
            return False
        for row in range(list_widget.count()):
            if list_widget.item(row).text() == text:
                list_widget.blockSignals(True)
                list_widget.setCurrentRow(row)
                list_widget.blockSignals(False)
                return True
        return False

    def _update_dependency_label(self, asset: Asset | None) -> None:
        if not asset:
            self.dependency_label.setText("")
            return
        department = self._current_department()
        if department not in {"rig", "look"}:
            self.dependency_label.setText("")
            return
        latest = self.manager.latest_publish_info(
            asset,
            department="model",
            variant=self._current_asset_variant() if asset.uses_variant_structure(self._current_asset_variant()) else "hires",
            subset="hires" if asset.uses_variant_structure(self._current_asset_variant()) else None,
            publish_format="ma",
        )
        if latest:
            self.dependency_label.setText(
                f"Model hires latest: {latest.get('version')}  {latest.get('path')}"
            )
        else:
            self.dependency_label.setText("Model hires latest: not published")

    def _copy_selected_path(self) -> None:
        work_path = self._selected_work_path()
        if work_path:
            text = work_path
        elif self.data_list.currentItem():
            text = self.data_list.currentItem().data(0, QtCore.Qt.UserRole)
        elif self.publish_list.currentItem():
            text = self.publish_list.currentItem().data(QtCore.Qt.UserRole)
        else:
            asset = self._current_asset()
            text = str(asset.root) if asset else ""
        if text:
            QtWidgets.QApplication.clipboard().setText(text)
            self.status_label.setText(f"Copied: {text}")

    def _copy_text(self, text: str) -> None:
        if text:
            QtWidgets.QApplication.clipboard().setText(text)
            self.status_label.setText(f"Copied: {text}")

    def _show_asset_context_menu(self, pos) -> None:
        item = self.asset_list.itemAt(pos)
        if item:
            if not item.isSelected():
                self.asset_list.clearSelection()
                item.setSelected(True)
            self.asset_list.setCurrentItem(
                item, QtCore.QItemSelectionModel.NoUpdate
            )
        asset = self._current_asset()
        menu = QtWidgets.QMenu(self)
        create_asset = menu.addAction("Create Asset")
        initialize_assets = menu.addAction("Initialize Selected Assets")
        initialize_assets.setEnabled(bool(self._selected_uninitialized_assets()))
        create_variant = menu.addAction("Create Variant")
        create_variant.setEnabled(bool(asset and self.manager.is_asset_initialized(asset)))
        menu.addSeparator()
        open_root = menu.addAction("Open Asset Root")
        open_data = menu.addAction("Open Data")
        open_work = menu.addAction("Open Work")
        open_publish = menu.addAction("Open Publish")
        open_root.setEnabled(asset is not None)
        open_data.setEnabled(asset is not None)
        open_work.setEnabled(asset is not None)
        open_publish.setEnabled(asset is not None)
        menu.addSeparator()
        reference_latest_rig = menu.addAction("Reference Latest Rig")
        reference_latest_rig.setEnabled(asset is not None)
        send_to_shot_cast = menu.addAction("Edit Cast in Smart Casting")
        send_to_shot_cast.setEnabled(asset is not None)
        menu.addSeparator()
        create_folders = menu.addAction("Create Asset Folders")
        create_folders.setEnabled(asset is not None)
        set_thumbnail = menu.addAction("Set Thumbnail...")
        capture_viewport_thumbnail = menu.addAction("Capture Viewport Thumbnail")
        set_thumbnail.setEnabled(asset is not None)
        capture_viewport_thumbnail.setEnabled(asset is not None)
        menu.addSeparator()
        copy_root = menu.addAction("Copy Asset Root")
        copy_data = menu.addAction("Copy Data Path")
        copy_work = menu.addAction("Copy Work Path")
        copy_publish = menu.addAction("Copy Publish Path")
        copy_root.setEnabled(asset is not None)
        copy_data.setEnabled(asset is not None)
        copy_work.setEnabled(asset is not None)
        copy_publish.setEnabled(asset is not None)
        action = _exec_menu(menu, self.asset_list.mapToGlobal(pos))
        if action == create_asset:
            self._create_asset()
        elif action == initialize_assets:
            self._initialize_selected_assets()
        elif action == create_variant:
            self._create_variant()
        elif action == open_root:
            self._open_current("root")
        elif action == open_data:
            self._open_current("data")
        elif action == open_work:
            self._open_current("work")
        elif action == open_publish:
            self._open_current("publish")
        elif action == reference_latest_rig:
            self._reference_latest_rig(asset)
        elif action == send_to_shot_cast:
            self._send_selected_asset_to_shot_cast(asset)
        elif action == create_folders:
            self.manager.ensure_asset_structure(asset)
            self.status_label.setText(f"Created folders: {asset.name}")
            self._show_current_asset()
        elif action == set_thumbnail:
            self._set_asset_thumbnail(asset)
        elif action == capture_viewport_thumbnail:
            self._capture_asset_viewport_thumbnail(asset)
        elif action == copy_root:
            self._copy_text(str(asset.root))
        elif action == copy_data:
            self._copy_text(str(asset.data_dir))
        elif action == copy_work:
            self._copy_text(str(asset.work_dir))
        elif action == copy_publish:
            self._copy_text(str(asset.publish_dir))

    def _set_asset_thumbnail(self, asset: Asset | None) -> None:
        if not asset:
            return
        path, _selected_filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Set Asset Thumbnail",
            str(asset.root if asset.root.exists() else self.manager.assets_root),
            "Images (*.jpg *.jpeg *.png *.webp *.bmp);;All Files (*)",
        )
        if not path:
            return
        source = Path(path)
        suffix = source.suffix.lower() or ".jpg"
        target = asset.root / f"thumbnail{suffix}"
        try:
            asset.root.mkdir(parents=True, exist_ok=True)
            same_file = target.exists() and source.resolve() == target.resolve()
            if not same_file:
                shutil.copy2(source, target)
            metadata_path = asset.root / "asset.json"
            metadata = self._read_json_for_table(metadata_path) if metadata_path.exists() else {}
            if not isinstance(metadata, dict):
                metadata = {}
            metadata.setdefault("asset", asset.name)
            metadata.setdefault("category", asset.category)
            metadata.setdefault("group", asset.group)
            metadata["thumbnail"] = target.name
            write_json_file(metadata_path, metadata)
            self.status_label.setText(f"Thumbnail set: {asset.name}")
            self.refresh_assets(keep_selection=True)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Set Thumbnail Failed", str(exc))

    def _capture_asset_viewport_thumbnail(self, asset: Asset | None) -> None:
        if not asset:
            return
        try:
            from smartlib.dcc.maya.thumbnail import capture_viewport_thumbnail

            asset.root.mkdir(parents=True, exist_ok=True)
            target = asset.root / "thumbnail.jpg"
            capture_viewport_thumbnail(target, width=320, height=180)
            metadata_path = asset.root / "asset.json"
            metadata = self._read_json_for_table(metadata_path) if metadata_path.exists() else {}
            if not isinstance(metadata, dict):
                metadata = {}
            metadata.setdefault("asset", asset.name)
            metadata.setdefault("category", asset.category)
            metadata.setdefault("group", asset.group)
            metadata["thumbnail"] = target.name
            write_json_file(metadata_path, metadata)
            self.status_label.setText(f"Captured viewport thumbnail: {asset.name}")
            self.refresh_assets(keep_selection=True)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Capture Viewport Thumbnail Failed", str(exc))

    def _send_selected_asset_to_shot_cast(self, asset: Asset | None) -> None:
        if not asset:
            return
        self._open_smart_casting(asset_names=[asset.name])

    def _add_selected_assets_to_shot_cast(self) -> None:
        targets = self._selected_cast_targets()
        if not targets:
            QtWidgets.QMessageBox.information(self, "Add to Cast", "Select one or more shots or sequences in the Shot tree first.")
            return
        assets = self._selected_assets()
        if not assets:
            QtWidgets.QMessageBox.information(self, "Add to Cast", "Select one or more assets first.")
            return
        target = targets[0]
        identity = target.get("identity")
        self._open_smart_casting(
            episode=target.get("episode") or getattr(identity, "episode", ""),
            sequence=target.get("sequence") or getattr(identity, "sequence", ""),
            shot=getattr(identity, "shot", ""),
            asset_names=[asset.name for asset in assets],
        )

    def _open_smart_casting(
        self,
        *,
        episode: str = "",
        sequence: str = "",
        shot: str = "",
        asset_names: list[str] | None = None,
    ) -> None:
        try:
            _ensure_smartlib_on_path()
            from smartlib.apps.smart_casting.ui import show

            show(
                config_dir=self.manager.config_dir,
                parent=self,
                episode=episode,
                sequence=sequence,
                shot=shot,
                asset_names=asset_names,
            )
            self.status_label.setText("Opened Smart Casting. Cast changes are managed there.")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Open Smart Casting Failed", str(exc))

    def _create_asset(self) -> None:
        dialog = AssetRequestDialog(self, title="Create Asset")
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        values = dialog.values()
        try:
            service, request_cls = _asset_service(self.manager.config_dir)
            result = service.create_asset(request_cls(**values))
            target = self.manager.get_asset(values["category"], values["group"], values["name"])
            self.status_label.setText(f"Created asset: {result.asset_root}")
            self.refresh_assets(keep_selection=False)
            self._select_asset(target)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Create Asset Failed", str(exc))

    def _initialize_selected_assets(self) -> None:
        assets = self._selected_uninitialized_assets()
        if not assets:
            QtWidgets.QMessageBox.information(
                self,
                "Initialize Asset",
                "All selected assets are already initialized.",
            )
            return

        preview = "\n".join(
            f"- {asset.category}/{asset.group}/{asset.name}" for asset in assets[:12]
        )
        if len(assets) > 12:
            preview += f"\n- ... and {len(assets) - 12} more"
        answer = QtWidgets.QMessageBox.question(
            self,
            "Initialize Assets",
            f"Initialize {len(assets)} selected asset(s)?\n\n{preview}",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Yes,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return

        try:
            service, request_cls = _asset_service(self.manager.config_dir)
            requests = []
            for asset in assets:
                metadata = self.manager.load_asset_metadata(asset)
                requests.append(
                    request_cls(
                        category=asset.category,
                        group=asset.group,
                        name=asset.name,
                        variant="default",
                        description=str(metadata.get("description") or ""),
                    )
                )
            results = service.initialize_assets(requests)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Initialize Assets Failed", str(exc))
            return

        selected_keys = {self._asset_key(asset) for asset in assets}
        self.refresh_assets(keep_selection=False)
        self._select_asset_keys(selected_keys)
        self.status_label.setText(f"Initialized {len(results)} asset(s)")

    def _select_asset_keys(self, keys: set[tuple[str, str, str]]) -> None:
        self.asset_list.clearSelection()
        first_item = None
        for row in range(self.asset_list.count()):
            item = self.asset_list.item(row)
            asset = item.data(QtCore.Qt.UserRole)
            if not isinstance(asset, Asset) or self._asset_key(asset) not in keys:
                continue
            item.setSelected(True)
            if first_item is None:
                first_item = item
        if first_item is not None:
            self.asset_list.setCurrentItem(
                first_item, QtCore.QItemSelectionModel.NoUpdate
            )
        self._update_asset_action_state()

    def _create_variant(self) -> None:
        asset = self._current_asset()
        if not asset:
            QtWidgets.QMessageBox.information(self, "Create Variant", "Select an asset first.")
            return
        variant, ok = QtWidgets.QInputDialog.getText(
            self,
            "Create Variant",
            "Variant name:",
            text="default",
        )
        if not ok:
            return
        variant = variant.strip()
        if not variant:
            return
        try:
            service, request_cls = _asset_service(self.manager.config_dir)
            result = service.create_variant(
                request_cls(
                    category=asset.category,
                    group=asset.group,
                    name=asset.name,
                    variant=variant,
                )
            )
            self.status_label.setText(f"Created variant: {result.variant_root}")
            self.refresh_assets(keep_selection=True)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Create Variant Failed", str(exc))

    def _select_asset(self, target: Asset) -> None:
        key = self._asset_key(target)
        for row in range(self.asset_list.count()):
            asset = self.asset_list.item(row).data(QtCore.Qt.UserRole)
            if asset and self._asset_key(asset) == key:
                self.asset_list.setCurrentRow(row)
                return

    def _reference_latest_rig(self, asset: Asset) -> None:
        latest = None
        for variant in ("anim", "layout"):
            latest = self.manager.latest_publish_info(
                asset,
                department="rig",
                variant=self._current_asset_variant() if asset.uses_variant_structure(self._current_asset_variant()) else variant,
                subset=variant if asset.uses_variant_structure(self._current_asset_variant()) else None,
                publish_format="ma",
            )
            if latest and latest.get("absolute_path"):
                break
        if not latest or not latest.get("absolute_path"):
            self.status_label.setText(f"No latest rig publish found: {asset.name}")
            return
        try:
            reference_file_to_current_dcc(latest["absolute_path"], namespace=asset.name)
            self.status_label.setText(f"Referenced latest rig: {asset.name}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Reference Rig Failed", str(exc))

    def _show_work_context_menu(self, pos) -> None:
        item = self.work_list.itemAt(pos)
        if not item:
            return
        self.work_list.selectRow(item.row())
        path_text = self._selected_work_path()
        if not path_text:
            return
        path = Path(path_text)
        menu = QtWidgets.QMenu(self)
        open_scene = menu.addAction("Open Scene")
        open_folder = menu.addAction("Open Folder")
        recapture_thumbnail = menu.addAction("Recapture Thumbnail")
        copy_path = menu.addAction("Copy Path")
        action = _exec_menu(menu, self.work_list.mapToGlobal(pos))
        if action == open_scene:
            self._open_selected_scene()
        elif action == open_folder:
            self.manager.open_in_explorer(path.parent)
        elif action == recapture_thumbnail:
            self._recapture_selected_work_thumbnail()
        elif action == copy_path:
            self._copy_text(str(path))

    def _show_work_row_action_menu(self, row: int) -> None:
        self.work_list.selectRow(row)
        path_text = self._selected_work_path()
        if not path_text:
            return
        path = Path(path_text)
        menu = QtWidgets.QMenu(self)
        open_scene = menu.addAction("Open")
        reference_scene = menu.addAction("Reference")
        publish_scene = menu.addAction("Publish")
        recapture_thumbnail = menu.addAction("Recapture Thumbnail")
        menu.addSeparator()
        open_folder = menu.addAction("Open Folder")
        copy_path = menu.addAction("Copy Path")
        action = _exec_menu(menu, QtGui.QCursor.pos())
        if action == open_scene:
            self._open_work_path(path)
        elif action == reference_scene:
            asset = self._current_asset()
            reference_file_to_current_dcc(path, namespace=asset.name if asset else None)
        elif action == publish_scene:
            self._publish_selected_work()
        elif action == recapture_thumbnail:
            self._recapture_selected_work_thumbnail()
        elif action == open_folder:
            self.manager.open_in_explorer(path.parent)
        elif action == copy_path:
            self._copy_text(str(path))

    def _recapture_selected_work_thumbnail(self) -> None:
        path_text = self._selected_work_path()
        if not path_text:
            return
        path = Path(path_text)
        try:
            thumbnail = capture_work_thumbnail_in_current_dcc(self.manager.thumbnail_path_for_workfile(path))
            if not thumbnail:
                raise RuntimeError("Thumbnail capture did not return a file path.")
            self.manager.update_file_metadata(path, thumbnail=thumbnail)
            self.status_label.setText(f"Updated thumbnail: {path.name}")
            self._show_current_asset()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Recapture Thumbnail Failed", str(exc))

    def _show_data_context_menu(self, pos) -> None:
        item = self.data_list.itemAt(pos)
        if not item or not item.data(0, QtCore.Qt.UserRole):
            return
        path = Path(item.data(0, QtCore.Qt.UserRole))
        menu = QtWidgets.QMenu(self)
        open_folder = menu.addAction("Open Folder")
        copy_path = menu.addAction("Copy Path")
        action = _exec_menu(menu, self.data_list.mapToGlobal(pos))
        if action == open_folder:
            self.manager.open_in_explorer(path.parent)
        elif action == copy_path:
            self._copy_text(str(path))

    def _selected_data_path(self) -> Path | None:
        item = self.data_list.currentItem()
        if not item:
            return None
        path = item.data(0, QtCore.Qt.UserRole)
        return Path(path) if path else None

    def _import_selected_data(self) -> None:
        path = self._selected_data_path()
        if not path:
            self.status_label.setText("Select a data file first")
            return
        try:
            import_data_file_to_current_dcc(path)
            self.status_label.setText(f"Imported: {path.name}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Import Data Failed", str(exc))

    def _show_publish_context_menu(self, pos) -> None:
        item = self.publish_list.itemAt(pos)
        if not item or not item.data(QtCore.Qt.UserRole):
            return
        path = Path(item.data(QtCore.Qt.UserRole))
        menu = QtWidgets.QMenu(self)
        import_file = menu.addAction("Import")
        open_folder = menu.addAction("Open Folder")
        copy_path = menu.addAction("Copy Path")
        action = _exec_menu(menu, self.publish_list.mapToGlobal(pos))
        if action == import_file:
            import_file_to_current_dcc(path)
        elif action == open_folder:
            self.manager.open_in_explorer(path.parent)
        elif action == copy_path:
            self._copy_text(str(path))

    def _open_selected_scene(self) -> None:
        selected_path = self._selected_work_path()
        if not selected_path:
            self.status_label.setText("Select a work scene first")
            return

        try:
            self._open_work_path(selected_path)
            self.status_label.setText(f"Opened: {Path(selected_path).name}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Open Scene Failed", str(exc))

    def _open_work_path(self, path: str | os.PathLike[str]) -> None:
        open_scene_in_current_dcc(path)

    def _reference_latest_publish(self) -> None:
        asset = self._current_asset()
        path = self._latest_published_scene()
        if not path:
            self.status_label.setText("No published scene found")
            return
        try:
            reference_file_to_current_dcc(path, namespace=asset.name if asset else None)
            self.status_label.setText(f"Referenced: {Path(path).name}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Reference Failed", str(exc))

    def _selected_work_path(self) -> str | None:
        row = self.work_list.currentRow()
        if row < 0:
            return None
        item = self.work_list.item(row, 1)
        if not item:
            return None
        return item.data(QtCore.Qt.UserRole)

    def _select_work_path(self, path: str | os.PathLike[str]) -> bool:
        wanted = str(Path(path))
        for row in range(self.work_list.rowCount()):
            item = self.work_list.item(row, 1)
            if item and item.data(QtCore.Qt.UserRole) == wanted:
                self.work_list.selectRow(row)
                self.work_list.setCurrentCell(row, 1)
                self.work_list.scrollToItem(item)
                self._update_selected_file_info()
                return True
        return False

    def _refresh_assets_with_work_selection(self, path: str | os.PathLike[str]) -> None:
        self.refresh_assets(keep_selection=True)
        self._select_work_path(path)

    def _on_work_item_changed(self, item) -> None:
        if item.column() != 3:
            return
        path = self.work_list.item(item.row(), 1).data(QtCore.Qt.UserRole)
        if path:
            self.manager.set_file_comment(path, item.text())

    def _ask_comment(self, title: str) -> str | None:
        comment, ok = QtWidgets.QInputDialog.getMultiLineText(self, title, "Comment:")
        if not ok:
            return None
        return comment

    def _save_modified_maya_scene(self, action: str) -> bool:
        """Save a named, modified Maya scene before a disk-based operation."""
        try:
            import maya.cmds as cmds
        except ImportError:
            return True

        if not bool(cmds.file(query=True, modified=True)):
            return True

        scene_path = str(cmds.file(query=True, sceneName=True) or "").strip()
        if not scene_path:
            QtWidgets.QMessageBox.warning(
                self,
                "Unsaved Maya Scene",
                "The current Maya scene has not been saved yet.\n"
                "Use Save As in Maya, then run this action again.",
            )
            return False

        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowTitle("Unsaved Maya Scene")
        dialog.setIcon(QtWidgets.QMessageBox.Warning)
        dialog.setText("The current Maya scene has unsaved changes.")
        dialog.setInformativeText(f"Save the scene before {action}?")
        save_button = dialog.addButton("Save and Continue", QtWidgets.QMessageBox.AcceptRole)
        dialog.addButton(QtWidgets.QMessageBox.Cancel)
        dialog.setDefaultButton(save_button)
        dialog.exec_()
        if dialog.clickedButton() is not save_button:
            return False

        try:
            # Omitting the type preserves the current .ma/.mb format and avoids
            # Maya attempting a format conversion for scenes with unknown data.
            cmds.file(save=True, force=True)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "Save Maya Scene Failed",
                f"The current scene could not be saved.\n{exc}",
            )
            return False
        return True

    def _save_scene(self) -> None:
        asset = self._current_asset()
        if not asset:
            self.status_label.setText("Select an asset first")
            return

        selected_path = self._selected_work_path()
        department = self._current_department()
        dcc = current_dcc_name()
        ext = self._selected_work_scene_extension()
        subset = self._effective_work_subset(asset, dcc)
        if dcc == "houdini" and department in {"model", "look"}:
            subset = self._current_variant()

        if not selected_path:
            target = self.manager.next_work_take_path(
                asset,
                dcc=dcc,
                department=department,
                variant=self._work_variant_arg(asset),
                subset=subset,
                ext=ext,
            )
            comment = ""
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                save_scene_in_current_dcc(target)
                thumbnail = capture_work_thumbnail_in_current_dcc(self.manager.thumbnail_path_for_workfile(target))
                self.manager.update_file_metadata(
                    target,
                    comment=comment,
                    thumbnail=thumbnail,
                    scene_info=collect_scene_info(),
                )
                self.status_label.setText(f"Saved: {target.name}")
                self._refresh_assets_with_work_selection(target)
            except Exception as exc:
                QtWidgets.QMessageBox.critical(self, "Save Scene Failed", str(exc))
            return

        if self.manager.any_publish_record_for_work_file(asset, selected_path):
            parsed = self.manager.parse_work_file(selected_path) or {}
            target = self.manager.next_work_take_path(
                asset,
                dcc=dcc,
                department=parsed.get("department") or department,
                variant=parsed.get("variant") or self._work_variant_arg(asset),
                subset=subset,
                version=int(parsed.get("version") or 0) + 1,
                ext=ext,
            )
        else:
            target = self.manager.next_work_take_path(
                asset,
                current_path=selected_path,
                dcc=dcc,
                department=department,
                variant=self._work_variant_arg(asset),
                subset=subset,
                ext=ext,
                use_current_extension=False,
            )

        comment = ""
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            save_scene_in_current_dcc(target)
            thumbnail = capture_work_thumbnail_in_current_dcc(self.manager.thumbnail_path_for_workfile(target))
            self.manager.update_file_metadata(
                target,
                comment=comment,
                thumbnail=thumbnail,
                scene_info=collect_scene_info(),
            )
            self.status_label.setText(f"Saved: {target.name}")
            self._refresh_assets_with_work_selection(target)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save Scene Failed", str(exc))

    def _stage_work_scene(self) -> None:
        asset = self._current_asset()
        if not asset:
            self.status_label.setText("Select an asset first")
            return
        department = self._current_department()
        variant = self._work_variant_arg(asset)
        dcc = current_dcc_name()
        ext = self._selected_work_scene_extension()
        subset = self._effective_work_subset(asset, dcc)
        if dcc == "houdini" and department in {"model", "look"}:
            subset = self._current_variant()

        target = self.manager.next_work_after_latest_publish_path(
            asset,
            dcc=dcc,
            department=department,
            variant=variant,
            subset=subset,
            ext=ext,
        )
        try:
            dependency_plan = resolve_staging_dependency(asset, self.manager, department, variant, subset)
            if dependency_plan is None and dcc == "houdini":
                dependency_plan = resolve_houdini_latest_model_sublayer(asset, self.manager, department, variant, subset)
            if dcc == "houdini":
                dependency_plan = ensure_houdini_usd_dependency_plan(
                    asset,
                    self.manager,
                    department,
                    variant,
                    subset,
                    dependency_plan,
                )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Staging Failed", str(exc))
            return
        comment = self._ask_comment("Staging Comment")
        if comment is None:
            return
        try:
            dependency_info = stage_asset_work_scene(
                asset,
                self.manager,
                target,
                department,
                variant,
                subset,
                dependency_plan=dependency_plan,
            )
            thumbnail = capture_work_thumbnail_in_current_dcc(self.manager.thumbnail_path_for_workfile(target))
            self.manager.update_file_metadata(
                target,
                comment=comment,
                thumbnail=thumbnail,
                scene_info=collect_scene_info(),
                staging={
                    "department": department,
                    "variant": variant,
                    "subset": subset,
                    **dependency_info,
                },
            )
            self.status_label.setText(f"Staged: {target.name}")
            self._refresh_assets_with_work_selection(target)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Staging Failed", str(exc))

    def _quick_preview_setup(self) -> None:
        asset = self._current_asset()
        if not asset:
            self.status_label.setText("Select an asset first")
            return
        try:
            result = create_quick_preview_package(
                asset,
                self.manager,
                department=self._current_department(),
                variant=self._work_variant_arg(asset),
                subset=self._work_subset_arg(asset) or self._current_variant(),
            )
            manual = []
            if result.get("focus_manual"):
                manual.append("preview_focus_LOC")
            if result.get("camera_manual"):
                manual.append("preview_cam_LOC")
            suffix = f" manual override exists: {', '.join(manual)}" if manual else ""
            self.status_label.setText(f"Quick Preview: {result.get('version_dir')}{suffix}")
            self._populate_preview_list(asset)
            dialog = QtWidgets.QMessageBox(self)
            dialog.setWindowTitle("Quick Preview")
            dialog.setIcon(QtWidgets.QMessageBox.Information)
            dialog.setText(
                "Quick Preview package was created.\n"
                f"Path: {result.get('version_dir')}\n"
                f"Focus: {result.get('focus')}\n"
                f"Camera: {result.get('camera')}"
                + (f"\nManual override exists: {', '.join(manual)}" if manual else "")
            )
            open_rv_btn = dialog.addButton("Open Package in RV", QtWidgets.QMessageBox.AcceptRole)
            dialog.addButton(QtWidgets.QMessageBox.Close)
            dialog.exec()
            if dialog.clickedButton() == open_rv_btn:
                self._open_preview_review_json_in_rv(result.get("review_json"))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Quick Preview Failed", str(exc))

    def _build_turntable_scene(self) -> None:
        asset = self._current_asset()
        if not asset:
            self.status_label.setText("Select an asset first")
            return
        rotate_lights = (
            QtWidgets.QMessageBox.question(
                self,
                "Build Turntable Scene",
                "Rotate lights with the asset?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            == QtWidgets.QMessageBox.Yes
        )
        try:
            result = build_turntable_scene_package(
                asset,
                self.manager,
                variant=self._work_variant_arg(asset),
                subset=self._work_subset_arg(asset) or self._current_variant(),
                rotate_lights=rotate_lights,
            )
            self.status_label.setText(f"Turntable Scene: {result.get('version_dir')}")
            self._populate_preview_list(asset)
            dialog = QtWidgets.QMessageBox(self)
            dialog.setWindowTitle("Build Turntable Scene")
            dialog.setIcon(QtWidgets.QMessageBox.Information)
            dialog.setText(
                "Turntable scene was created.\n"
                f"Path: {result.get('turntable_usd')}\n"
                f"Source: {result.get('source_usd')}\n"
                f"Rotate lights: {rotate_lights}"
            )
            dialog.addButton(QtWidgets.QMessageBox.Close)
            dialog.exec()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Build Turntable Scene Failed", str(exc))

    def _effective_work_subset(self, asset: Asset | None, dcc: str | None = None) -> str | None:
        subset = self._work_subset_arg(asset)
        if subset:
            return subset
        if dcc == "houdini" and asset and asset.uses_variant_structure(self._current_asset_variant()):
            return self._current_variant()
        return subset

    def _open_preview_review_json_in_rv(self, review_json: str | os.PathLike[str] | None) -> None:
        if not review_json:
            return
        _ensure_smartlib_on_path()
        from smartlib.apps.viewer import ViewerService
        from smartlib.core.config_loader import ProjectConfig

        service = ViewerService(ProjectConfig(self.manager.config_dir))
        package = service.review_package_from_json(review_json)
        if not package:
            self.status_label.setText("Preview package could not be read")
            return
        args = service.rv_args_for_package(package)
        if not args:
            self.status_label.setText("No preview sequence files were found for RV")
            return
        rv = service.rv_executable()
        if not rv:
            QtWidgets.QMessageBox.warning(
                self,
                "OpenRV Not Found",
                "Set tools.openrv.path in config/STKB/tools.yml or set OPENRV_PATH.",
            )
            return
        rvpush = service.rvpush_executable()
        if rvpush:
            env = os.environ.copy()
            env.setdefault("RVPUSH_RV_EXECUTABLE_PATH", str(rv))
            merge_args = [str(rvpush), "merge", *args] if args[:1] == ["-tile"] else [str(rvpush), "merge", "[", *args, "]"]
            subprocess.Popen(merge_args, env=env)
            self.status_label.setText(f"Sent to RV: {package.shot} {package.version}")
        else:
            subprocess.Popen([str(rv), *args])
            self.status_label.setText(f"Launched RV: {package.shot} {package.version}")

    def _selected_preview_review_json(self) -> str | None:
        if not getattr(self, "preview_list", None):
            return None
        row = self.preview_list.currentRow()
        if row < 0:
            return None
        item = self.preview_list.item(row, 0)
        if not item:
            return None
        return item.data(QtCore.Qt.UserRole)

    def _open_selected_preview_in_rv(self) -> None:
        review_json = self._selected_preview_review_json()
        if not review_json:
            self.status_label.setText("Select a preview package first")
            return
        self._open_preview_review_json_in_rv(review_json)

    def _open_selected_preview_in_usdview(self) -> None:
        review_json = self._selected_preview_review_json()
        if not review_json:
            self.status_label.setText("Select a preview package first")
            return
        usd_path = self._preview_usd_path(review_json)
        if not usd_path:
            self.status_label.setText("Selected preview package has no USD scene")
            return
        usdview = resolve_usdview_path(self.manager)
        if not usdview:
            QtWidgets.QMessageBox.warning(
                self,
                "usdview Not Found",
                "Set tools.usdview.path in tools.yml or install tools/usd/usdview.bat.",
            )
            return
        try:
            log_path = launch_usdview(usdview, usd_path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Open usdview Failed", str(exc))
            self.status_label.setText(str(exc))
            return
        self.status_label.setText(f"Opened usdview: {usd_path.name} (log: {log_path})")

    def _preview_usd_path(self, review_json: str | os.PathLike[str]) -> Path | None:
        review_path = Path(review_json)
        data = self._read_json_for_table(review_path)
        if not isinstance(data, dict):
            return None
        candidates = []
        for key in ("turntable_usd", "usd"):
            value = str(data.get(key) or "").strip()
            if value:
                candidates.append(value)
        manifest_value = str(data.get("manifest") or "").strip()
        if manifest_value:
            manifest_path = review_path.parent / manifest_value
            manifest = self._read_json_for_table(manifest_path)
            if isinstance(manifest, dict):
                value = str(manifest.get("turntable_usd") or "").strip()
                if value:
                    candidates.append(value)
        for value in candidates:
            path = Path(value)
            if not path.is_absolute():
                path = review_path.parent / value
            if path.exists():
                return path
        fallback = review_path.parent / "turntable.usd"
        return fallback if fallback.exists() else None

    @staticmethod
    def _retarget_publish_api():
        _ensure_smartlib_on_path()
        from smartlib.apps.asset_manager.retarget_publish import (
            generate_retarget_asset_profile,
            latest_retarget_data_profile,
            list_retarget_data_versions,
            list_retarget_versions,
            list_project_retarget_templates,
            publish_retarget_profile,
            retarget_data_root,
            resolve_retarget_context_rigs,
            save_retarget_data_version,
            standard_test_motion,
            validate_retarget_profile,
        )
        return {
            "generate": generate_retarget_asset_profile,
            "latest_data": latest_retarget_data_profile,
            "list_data": list_retarget_data_versions,
            "list_publish": list_retarget_versions,
            "list_templates": list_project_retarget_templates,
            "publish": publish_retarget_profile,
            "data_root": retarget_data_root,
            "resolve_rigs": resolve_retarget_context_rigs,
            "save_data": save_retarget_data_version,
            "test_motion": standard_test_motion,
            "validate": validate_retarget_profile,
        }

    def _default_retarget_profile(self, asset: Asset) -> Path:
        variant = self._current_asset_variant()
        candidates = [
            asset.variant_root(variant) / "work" / "rig" / "retarget" / f"{asset.name}_retarget.json",
            asset.root / "rig" / "retarget" / f"{asset.name}.json",
            Path(__file__).resolve().parents[1] / "config" / "maya" / "retarget" / f"{asset.name.lower()}.json",
        ]
        return next((path for path in candidates if path.is_file()), candidates[0])

    def _refresh_retarget_publish_tab(self, asset: Asset | None = None) -> None:
        if not getattr(self, "retarget_profile_edit", None):
            return
        asset = asset or self._current_asset()
        if not asset:
            self.retarget_test_motion_path_label.setText("")
            self._update_publish_selected_state()
            return
        api = self._retarget_publish_api()
        test_path = api["test_motion"](self.manager.project_root)
        self.retarget_test_motion_path_label.setText(test_path.as_posix())
        templates = api["list_templates"](self.manager.project_root)
        bundled = Path(__file__).resolve().parents[1] / "config" / "maya" / "retarget" / "templates" / "elcd_humanoid_v001.json"
        self.retarget_template_edit.setText(str(templates[0] if templates else bundled))
        rigs = api["resolve_rigs"](asset.root, self._current_asset_variant())
        self.retarget_anm_edit.setText(str(rigs.get("animation_rig_scene") or "Not found in Context Pack"))
        self.retarget_mcr_edit.setText(str(rigs.get("mcr_scene") or "Not found in Context Pack"))
        asset_key = (asset.root.as_posix(), self._current_asset_variant())
        if getattr(self, "_retarget_profile_asset_key", None) != asset_key:
            default_profile = api["latest_data"](asset.root, self._current_asset_variant()) or self._default_retarget_profile(asset)
            self.retarget_profile_edit.setText(str(default_profile))
            self._retarget_profile_asset_key = asset_key
        selected_version = self.retarget_data_version_combo.currentText()
        self.retarget_data_version_combo.blockSignals(True)
        self.retarget_data_version_combo.clear()
        for entry in api["list_data"](asset.root, self._current_asset_variant()):
            self.retarget_data_version_combo.addItem(entry["version"], str(entry["profile"]))
        if selected_version:
            index = self.retarget_data_version_combo.findText(selected_version)
            if index >= 0:
                self.retarget_data_version_combo.setCurrentIndex(index)
        self.retarget_data_version_combo.blockSignals(False)
        self._update_publish_selected_state()

    def _generate_retarget_profile(self) -> None:
        asset = self._current_asset()
        if not asset:
            self.status_label.setText("Select an asset first")
            return
        api = self._retarget_publish_api()
        bundled = Path(__file__).resolve().parents[1] / "config" / "maya" / "retarget" / "templates" / "elcd_humanoid_v001.json"
        target = asset.variant_root(self._current_asset_variant()) / "work" / "rig" / "retarget" / f"{asset.name}_retarget.json"
        overwrite = False
        if target.exists():
            answer = QtWidgets.QMessageBox.question(
                self,
                "Replace Retarget Work Profile",
                f"The editable Retarget profile already exists. Replace it?\n\n{target}",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return
            overwrite = True
        try:
            result = api["generate"](
                asset.root,
                self._current_asset_variant(),
                project_root=self.manager.project_root,
                bundled_template=bundled,
                output_path=target,
                overwrite=overwrite,
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Generate Retarget Profile Failed", str(exc))
            self.status_label.setText(str(exc))
            return
        self.retarget_profile_edit.setText(str(result["profile"]))
        self.retarget_template_edit.setText(str(result["template"]))
        self.retarget_anm_edit.setText(str(result["animation_rig_scene"]))
        self.retarget_mcr_edit.setText(str(result["mcr_scene"]))
        self._validate_retarget_publish()
        self.status_label.setText(f"Generated Retarget profile: {result['profile']}")

    def _load_retarget_data_version(self) -> None:
        path = self.retarget_data_version_combo.currentData()
        if not path:
            self.status_label.setText("No Retarget Data Version is available")
            return
        self.retarget_profile_edit.setText(str(path))
        self._validate_retarget_publish()
        self.status_label.setText(f"Loaded Retarget Data {self.retarget_data_version_combo.currentText()}")

    def _save_retarget_data_version(self) -> None:
        asset = self._current_asset()
        profile_text = self.retarget_profile_edit.text().strip()
        if not asset or not profile_text:
            self.status_label.setText("Select an asset and Retarget profile first")
            return
        comment = self._ask_comment("Save Retarget Data Version Comment")
        if comment is None:
            return
        api = self._retarget_publish_api()
        try:
            result = api["save_data"](
                asset.root,
                self._current_asset_variant(),
                Path(profile_text),
                comment=comment,
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save Retarget Data Failed", str(exc))
            self.status_label.setText(str(exc))
            return
        self.retarget_profile_edit.setText(str(result["profile"]))
        self._refresh_retarget_publish_tab(asset)
        index = self.retarget_data_version_combo.findText(result["version"])
        if index >= 0:
            self.retarget_data_version_combo.setCurrentIndex(index)
        self._populate_data_tree(asset)
        self.status_label.setText(f"Saved Retarget Data {result['version']}: {result['profile'].name}")

    def _browse_retarget_profile(self) -> None:
        asset = self._current_asset()
        start = str(asset.root if asset else self.manager.project_root)
        path, _selected_filter = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select Retarget Profile", start, "Retarget Profile (*.json)"
        )
        if path:
            self.retarget_profile_edit.setText(path)
            self._update_publish_selected_state()
            self._validate_retarget_publish()

    def _validate_retarget_publish(self) -> dict | None:
        asset = self._current_asset()
        profile_text = self.retarget_profile_edit.text().strip()
        if not asset or not profile_text:
            self.retarget_validation_label.setText("Select an asset and Retarget profile.")
            return None
        profile = Path(profile_text)
        api = self._retarget_publish_api()
        validation = api["validate"](
            profile,
            project_root=self.manager.project_root,
            test_motion=self.retarget_test_motion_check.isChecked(),
        )
        status = validation["status"].upper()
        messages = list(validation["errors"]) + list(validation["warnings"])
        test_status = validation["test_motion"]["status"]
        text = f"{status} | Test Motion: {test_status}"
        if messages:
            text += "\n" + "\n".join(messages)
        colors = {"passed": "#72c48f", "warning": "#d6b46b", "failed": "#df7777"}
        self.retarget_validation_label.setStyleSheet(f"color: {colors.get(validation['status'], '#dddddd')};")
        self.retarget_validation_label.setText(text)
        self.status_label.setText(f"Retarget validation: {validation['status']}")
        return validation

    def _publish_retarget_profile(self) -> None:
        asset = self._current_asset()
        if not asset:
            self.status_label.setText("Select an asset first")
            return
        validation = self._validate_retarget_publish()
        if not validation or validation["errors"]:
            QtWidgets.QMessageBox.warning(self, "Retarget Publish", "Retarget validation failed. Fix the listed errors before publishing.")
            return
        comment = self._ask_comment("Publish Retarget Comment")
        if comment is None:
            return
        api = self._retarget_publish_api()
        profile_path = Path(self.retarget_profile_edit.text().strip())
        data_root = api["data_root"](asset.root, self._current_asset_variant())
        try:
            profile_path.resolve().relative_to(data_root.resolve())
        except ValueError:
            QtWidgets.QMessageBox.warning(
                self,
                "Save Data Version Required",
                "Save the Retarget profile as a Data Version before publishing.",
            )
            return
        try:
            result = api["publish"](
                asset.root,
                self._current_asset_variant(),
                profile_path,
                project_root=self.manager.project_root,
                comment=comment,
                run_test_motion=self.retarget_test_motion_check.isChecked(),
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Retarget Publish Failed", str(exc))
            self.status_label.setText(str(exc))
            return
        self._populate_asset_publish_tree(asset)
        self._refresh_retarget_publish_tab(asset)
        self.status_label.setText(f"Published Retarget {result['version']}: {result['profile'].name}")

    def _publish_selected_work(self, _checked: bool = False, *, force_usd: bool = False) -> None:
        if self._selected_publish_type() == "retarget":
            self._publish_retarget_profile()
            return
        asset = self._current_asset()
        source_path, source_kind = self._publish_source_path()
        if not asset:
            self.status_label.setText("Select an asset first")
            return
        if not source_path:
            self.status_label.setText("Open a saved asset work scene first")
            return

        parsed = self.manager.parse_work_file(source_path)
        if not parsed:
            QtWidgets.QMessageBox.warning(
                self,
                "Publish Failed",
                f"The {source_kind} scene does not match the asset work naming rule.\n\n"
                f"Source Scene: {source_path}",
            )
            return

        try:
            Path(source_path).resolve().relative_to(asset.root.resolve())
        except ValueError:
            QtWidgets.QMessageBox.warning(
                self,
                "Publish Failed",
                "The open scene does not belong to the selected asset.\n\n"
                f"Selected Asset: {asset.name}\n"
                f"Asset Root: {asset.root}\n"
                f"Source Scene: {source_path}",
            )
            return

        publish_type = self._selected_publish_type()
        expected_departments = self._publish_type_departments(publish_type)
        current_department = str(parsed.get("department") or "").lower()
        if expected_departments and current_department not in expected_departments:
            QtWidgets.QMessageBox.warning(
                self,
                "Publish Type Mismatch",
                f"{publish_type.title()} Publish cannot use the current scene.\n\n"
                f"Expected Department: {', '.join(sorted(expected_departments))}\n"
                f"Current Department: {current_department or '-'}\n"
                f"Source Scene: {source_path}",
            )
            return

        subset = self.manager.work_subset_for_path(
            asset,
            source_path,
            parsed["department"],
            parsed["variant"],
        )
        publish_formats = self.manager.publish_formats_for_work_file(
            asset,
            source_path,
            subset=subset,
        )
        if force_usd and "usd" not in publish_formats:
            publish_formats.append("usd")
        if parsed["department"] == "rig" and "usd" not in publish_formats:
            publish_formats.append("usd")
        publish_version = self.manager.next_publish_version(
            asset,
            department=parsed["department"],
            variant=parsed["variant"],
            subset=subset,
            publish_formats=publish_formats,
        )
        targets = {
            publish_format: self.manager.publish_file_path(
                asset,
                department=parsed["department"],
                variant=parsed["variant"],
                subset=subset,
                version=publish_version,
                ext=publish_format,
            )
            for publish_format in publish_formats
        }
        overwrite = False
        existing_targets = [path for path in targets.values() if path.exists()]
        if existing_targets:
            result = QtWidgets.QMessageBox.question(
                self,
                "Publish Exists",
                "Publish files already exist. Overwrite them?\n"
                + "\n".join(path.name for path in existing_targets),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if result != QtWidgets.QMessageBox.Yes:
                return
            overwrite = True

        comment = self._ask_comment("Publish Comment")
        if comment is None:
            return
        try:
            validation = validate_variant_publish(asset, self.manager, Path(source_path), subset=subset)
            validation = merge_publish_dependency_info(
                validation,
                collect_publish_dependency_info(asset, self.manager, Path(source_path), subset=subset),
            )
            if not self._confirm_publish_validation(validation):
                return
            published = publish_work_outputs(
                asset,
                self.manager,
                Path(source_path),
                targets,
                overwrite=overwrite,
                comment=comment,
                subset=subset,
                publish_version=publish_version,
                dependency_info=validation,
            )
            self.status_label.setText(
                f"Published v{int(publish_version):03d}: "
                + ", ".join(path.name for path in published)
            )
            self._show_current_asset()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Publish Failed", str(exc))

    def _confirm_publish_validation(self, validation: dict) -> bool:
        issues = validation.get("validation", {}).get("issues", [])
        overrides = validation.get("overrides", [])
        dependencies = validation.get("dependencies", {})
        lines = ["Publish validation"]
        if dependencies:
            base_variant = validation.get("base_variant")
            if base_variant:
                lines.append(f"base_variant: {base_variant}")
            references = dependencies.get("references") or []
            if references:
                lines.append(f"references: {len(references)}")
        if overrides:
            lines.append("overrides: " + ", ".join(overrides[:12]))
            if len(overrides) > 12:
                lines.append(f"... and {len(overrides) - 12} more")
        if issues:
            lines.append("issues:")
            lines.extend(f"- {issue}" for issue in issues)
        else:
            lines.append("issues: none")
        result = QtWidgets.QMessageBox.question(
            self,
            "Publish Validation",
            "\n".join(lines) + "\n\nContinue publish?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes,
        )
        return result == QtWidgets.QMessageBox.Yes

    def _show_export_data_menu(self, export_kind: str = "mesh") -> None:
        asset = self._current_asset()
        if not asset:
            self.status_label.setText("Select an asset first")
            return

        menu = QtWidgets.QMenu(self)
        export_fbx = None
        export_guide = export_skin = None
        if export_kind == "mesh":
            export_fbx = menu.addAction("Selected Mesh: FBX")
        elif export_kind == "guide":
            export_guide = menu.addAction("mGear Guide")
        elif export_kind == "skin":
            export_skin = menu.addAction("mGear Skin")
        action = _exec_menu(menu, QtGui.QCursor.pos())
        if not action:
            return

        comment = self._ask_comment("Data Export Comment")
        if comment is None:
            return
        try:
            if action == export_fbx:
                target = self.data_target_combo.currentText().strip() or "body"
                representation = self.data_representation_combo.currentText().strip() or "high"
                paths = export_selected_geo_data(
                    asset,
                    self.manager,
                    self._work_variant_arg(asset),
                    representation,
                    "fbx",
                    comment,
                    target=target,
                )
            elif action == export_guide:
                target = self.data_target_combo.currentText().strip() or "body"
                representation = self.data_representation_combo.currentText().strip() or "high"
                paths = [export_mgear_guide(asset, self.manager, self._work_variant_arg(asset), f"{target}/{representation}")]
            elif action == export_skin:
                target = self.data_target_combo.currentText().strip() or "body"
                representation = self.data_representation_combo.currentText().strip() or "high"
                paths = [export_mgear_skin(asset, self.manager, self._work_variant_arg(asset), f"{target}/{representation}")]
            else:
                return
            for path in paths:
                self.manager.set_file_comment(path, comment)
            self.status_label.setText("Exported: " + ", ".join(path.name for path in paths))
            current_asset = self._current_asset()
            if current_asset:
                self._populate_data_tree(current_asset)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Export Data Failed", str(exc))

    def _ingest_model_fbx(self) -> None:
        asset = self._current_asset()
        if not asset:
            self.status_label.setText("Select an asset first")
            return
        source, _selected_filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Ingest Model FBX",
            "",
            "FBX Files (*.fbx)",
        )
        if not source:
            return
        comment = self._ask_comment("Data Ingest Comment")
        if comment is None:
            return
        try:
            path = ingest_model_fbx_data(
                asset,
                self.manager,
                self._work_variant_arg(asset),
                self._work_subset_arg(asset) or self._current_variant(),
                source,
                comment,
            )
            self.manager.set_file_comment(path, comment)
            self.status_label.setText(f"Ingested: {path.name}")
            current_asset = self._current_asset()
            if current_asset:
                self._populate_data_tree(current_asset)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Ingest Model FBX Failed", str(exc))

    def _import_assembly_data(self) -> None:
        asset = self._current_asset()
        if not asset:
            self.status_label.setText("Select an asset first")
            return
        source, _selected_filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Import Assembly",
            "",
            "Assembly Files (*.ma *.mb *.usd *.usda *.usdc *.fbx *.abc);;All Files (*.*)",
        )
        if not source:
            return
        comment = self._ask_comment("Import Assembly Comment")
        if comment is None:
            return
        try:
            path = import_assembly_data(
                asset,
                self.manager,
                self._asset_context_identity(asset).variant,
                source,
                comment,
            )
            self.status_label.setText(f"Imported assembly: {path.name}")
            self._populate_data_tree(asset)
            QtWidgets.QMessageBox.information(self, "Import Assembly", f"Imported assembly data:\n{path}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Import Assembly Failed", str(exc))

    def _set_selected_asset_assembly_context(self):
        asset = self._current_asset()
        if not asset:
            self.status_label.setText("Select an asset first")
            return None
        _ensure_smartlib_on_path()
        from smartlib.core.config_loader import ProjectConfig
        from smartlib.dcc.maya import asset_assembly

        identity = self._asset_context_identity(asset)
        context = asset_assembly.set_assembly_context(
            identity.category,
            identity.group,
            identity.name,
            identity.variant,
        )
        return ProjectConfig(self.manager.config_dir), context

    def _open_selected_asset_assembly(self, *, reload: bool = False) -> None:
        try:
            setup = self._set_selected_asset_assembly_context()
            if not setup:
                return
            project_config, _context = setup
            from smartlib.dcc.maya import asset_assembly

            path = asset_assembly.open_assembly_usd(project_config, reload=reload)
            action = "Reloaded" if reload else "Opened"
            self.status_label.setText(f"{action} assembly: {Path(path).name}")
        except Exception as exc:
            title = "Reload Assembly Failed" if reload else "Open Assembly Failed"
            self.status_label.setText(str(exc))
            QtWidgets.QMessageBox.critical(self, title, str(exc))

    def _save_selected_asset_assembly(self) -> None:
        try:
            setup = self._set_selected_asset_assembly_context()
            if not setup:
                return
            project_config, _context = setup
            from smartlib.dcc.maya import asset_assembly

            comment = self._ask_comment("Save Assembly Comment")
            if comment is None:
                return
            path = asset_assembly.save_assembly(project_config, comment=comment)
            self.status_label.setText(f"Saved assembly: {Path(path).parent.name}/{Path(path).name}")
            current_asset = self._current_asset()
            if current_asset:
                self._populate_data_tree(current_asset)
        except Exception as exc:
            self.status_label.setText(str(exc))
            QtWidgets.QMessageBox.critical(self, "Save Assembly Failed", str(exc))

    def _publish_selected_asset_assembly(self) -> None:
        try:
            setup = self._set_selected_asset_assembly_context()
            if not setup:
                return
            project_config, _context = setup
            from smartlib.dcc.maya import asset_assembly

            comment = self._ask_comment("Publish Assembly Comment")
            if comment is None:
                return
            path = asset_assembly.publish_assembly(project_config, comment=comment)
            self.status_label.setText(f"Published assembly: {Path(path).name}")
            current_asset = self._current_asset()
            if current_asset:
                self._populate_data_tree(current_asset)
        except Exception as exc:
            self.status_label.setText(str(exc))
            QtWidgets.QMessageBox.critical(self, "Publish Assembly Failed", str(exc))

    def _publish_client_assembly(self) -> None:
        asset = self._current_asset()
        if not asset:
            self.status_label.setText("Select an asset first")
            return
        context_name = self._current_context_profile()
        if not context_name:
            self.status_label.setText("Select a Context first")
            return
        comment = self._ask_comment("Publish Assembly Client Comment")
        if comment is None:
            return
        try:
            path = publish_client_assembly(
                asset,
                self.manager,
                self._asset_context_identity(asset).variant,
                comment,
                context_name=context_name,
                context_version=self.context_version_combo.currentText().strip(),
            )
            self.status_label.setText(f"Published client assembly: {path.name}")
            self._populate_context_pack_tree()
            QtWidgets.QMessageBox.information(self, "Publish Assembly Client", f"Published client assembly:\n{path}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Publish Assembly Client Failed", str(exc))

    def _import_latest_publish(self) -> None:
        asset = self._current_asset()
        if not asset:
            return
        publish = self.manager.latest_publish(asset)
        if not publish:
            self.status_label.setText("No publish file found")
            return
        import_file_to_current_dcc(publish)
        self.status_label.setText(f"Imported: {publish.name}")


def save_scene_in_current_dcc(path: str | os.PathLike[str]) -> None:
    file_path = str(Path(path))
    ext = Path(file_path).suffix.lower()
    try:
        import maya.cmds as cmds

        scene_type = "mayaBinary" if ext == ".mb" else "mayaAscii"
        cmds.file(rename=file_path)
        cmds.file(save=True, type=scene_type)
        return
    except ImportError:
        pass

    try:
        import hou

        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        hou.hipFile.save(file_name=file_path)
        return
    except ImportError:
        pass

    raise RuntimeError("Save Scene is available inside Maya or Houdini.")


def current_dcc_name() -> str:
    try:
        import hou  # noqa: F401

        return "houdini"
    except ImportError:
        pass
    try:
        import maya.cmds  # noqa: F401

        return "maya"
    except ImportError:
        pass
    return "maya"


def current_dcc_scene_path() -> str | None:
    """Return the saved scene open in the host DCC, if one exists."""

    try:
        import hou

        scene_name = str(hou.hipFile.path() or "").strip()
        if scene_name and Path(scene_name).is_file():
            return str(Path(scene_name))
    except ImportError:
        pass
    try:
        import maya.cmds as cmds

        scene_name = str(cmds.file(query=True, sceneName=True) or "").strip()
        if scene_name and Path(scene_name).is_file():
            return str(Path(scene_name))
    except ImportError:
        pass
    return None


def current_work_scene_extension() -> str:
    if current_dcc_name() == "houdini":
        return "hip"
    return "ma"


def stage_asset_work_scene(
    asset: Asset,
    manager: AssetManager,
    target: Path,
    department: str,
    variant: str,
    subset: str | None,
    dependency_plan: dict | None = None,
) -> dict:
    try:
        import maya.cmds as cmds
    except ImportError:
        cmds = None

    if cmds is None:
        return stage_houdini_asset_work_scene(
            asset,
            manager,
            target,
            department,
            variant,
            subset,
            dependency_plan=dependency_plan,
        )

    template = resolve_asset_work_template(manager, department)
    if template and template.exists():
        cmds.file(str(template), open=True, force=True)
    else:
        cmds.file(new=True, force=True)

    dependency_info: dict = {
        "template": str(template).replace("\\", "/") if template else "",
        "dependencies": {},
    }
    ensure_asset_top_structure(cmds, asset.name)
    if dependency_plan:
        groups = ensure_pipeline_work_structure(cmds)
        reference_file_to_current_dcc(
            dependency_plan["path"],
            namespace=dependency_plan["namespace"],
            parent_group=groups.get("ref"),
        )
        if dependency_plan.get("base_variant"):
            dependency_info["base_variant"] = dependency_plan["base_variant"]
        dependency_info["dependencies"] = {
            "base_variant": dependency_plan.get("base_variant", ""),
            "references": [
                {
                    "variant": dependency_plan["variant"],
                    "department": dependency_plan["department"],
                    "subset": dependency_plan["subset"],
                    "publish_format": dependency_plan["publish_format"],
                    "version": dependency_plan.get("version", ""),
                    "path": str(dependency_plan["path"]).replace("\\", "/"),
                    "namespace": dependency_plan["namespace"],
                    "reason": dependency_plan["reason"],
                }
            ],
        }

    target.parent.mkdir(parents=True, exist_ok=True)
    scene_type = "mayaBinary" if target.suffix.lower() == ".mb" else "mayaAscii"
    cmds.file(rename=str(target))
    cmds.file(save=True, type=scene_type)
    return dependency_info


def stage_houdini_asset_work_scene(
    asset: Asset,
    manager: AssetManager,
    target: Path,
    department: str,
    variant: str,
    subset: str | None,
    dependency_plan: dict | None = None,
) -> dict:
    try:
        import hou
    except ImportError as exc:
        raise RuntimeError("Staging is available inside Maya or Houdini.") from exc

    if hou.hipFile.hasUnsavedChanges():
        result = hou.ui.displayMessage(
            "Current scene has unsaved changes. Start staging scene?",
            buttons=("Stage", "Cancel"),
            default_choice=0,
            close_choice=1,
        )
        if result != 0:
            raise RuntimeError("Staging was canceled.")

    hou.hipFile.clear(suppress_save_prompt=True)
    dependency_info: dict = {
        "template": "",
        "dependencies": {},
        "dcc": "houdini",
    }
    if dependency_plan:
        dependency_info["dependencies"] = {
            "base_variant": dependency_plan.get("base_variant", ""),
            "sublayers": [
                {
                    "variant": dependency_plan["variant"],
                    "department": dependency_plan["department"],
                    "subset": dependency_plan["subset"],
                    "publish_format": dependency_plan["publish_format"],
                    "version": dependency_plan.get("version", ""),
                    "path": str(dependency_plan["path"]).replace("\\", "/"),
                    "reason": dependency_plan["reason"],
                }
            ],
        }
        if dependency_plan.get("base_variant"):
            dependency_info["base_variant"] = dependency_plan["base_variant"]
        _create_houdini_sublayer_node(dependency_plan["path"], f"{dependency_plan['department']}_{dependency_plan['subset']}")

    target.parent.mkdir(parents=True, exist_ok=True)
    hou.hipFile.save(str(target))
    return dependency_info


def _create_houdini_sublayer_node(path: str | os.PathLike[str], name: str) -> None:
    import hou

    source = Path(path)
    if source.suffix.lower() not in {".usd", ".usda", ".usdc"}:
        raise RuntimeError(f"Houdini sublayer source must be USD: {source}")

    stage = hou.node("/stage")
    if stage is None:
        stage = hou.node("/").createNode("lopnet", "stage")
    node_name = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or "asset_sublayer"
    sublayer = stage.createNode("sublayer", node_name=node_name)
    file_path = str(source).replace("\\", "/")
    _set_first_existing_houdini_parm(
        sublayer,
        ("filepath1", "filepath", "layerpath1", "sublayer1", "file1"),
        file_path,
    )
    try:
        sublayer.setDisplayFlag(True)
        sublayer.setRenderFlag(True)
    except Exception:
        pass
    stage.layoutChildren()


def _set_first_existing_houdini_parm(node, names: tuple[str, ...], value: str) -> bool:
    for name in names:
        parm = node.parm(name)
        if parm is not None:
            parm.set(value)
            return True
    raise RuntimeError(f"Could not find a file path parameter on Houdini node: {node.path()}")


def resolve_staging_dependency(
    asset: Asset,
    manager: AssetManager,
    department: str,
    variant: str,
    subset: str | None,
) -> dict | None:
    if department == "model" and variant == "default":
        return None

    candidates = staging_dependency_candidates_from_config(manager, department, variant, subset)
    if not candidates:
        candidates = fallback_staging_dependency_candidates(department, variant, subset)

    if not candidates:
        return None

    for candidate_variant, candidate_department, candidate_subset, publish_format, reason in candidates:
        latest = manager.latest_publish_info(
            asset,
            department=candidate_department,
            variant=candidate_variant,
            subset=candidate_subset,
            publish_format=publish_format,
        )
        if latest and latest.get("absolute_path"):
            return {
                "variant": candidate_variant,
                "department": candidate_department,
                "subset": candidate_subset,
                "publish_format": publish_format,
                "version": latest.get("version", ""),
                "path": str(latest["absolute_path"]),
                "namespace": "default" if candidate_variant == "default" else "model",
                "base_variant": "default" if candidate_variant == "default" else "",
                "reason": reason,
            }

    expected = "\n".join(
        f"- {candidate_variant}/{candidate_department}/{candidate_subset}/{publish_format} latest"
        for candidate_variant, candidate_department, candidate_subset, publish_format, _reason in candidates
    )
    raise RuntimeError(
        "Required model publish was not found before staging.\n"
        f"Asset: {asset.name}\n"
        f"Variant: {variant}\n"
        f"Department: {department}\n"
        f"Subset: {subset or ''}\n"
        "Expected one of:\n"
        f"{expected}"
    )


def resolve_houdini_latest_model_sublayer(
    asset: Asset,
    manager: AssetManager,
    department: str,
    variant: str,
    subset: str | None,
) -> dict | None:
    if department != "model":
        return None
    source_variant = variant
    latest = manager.latest_publish_info(
        asset,
        department="model",
        variant=variant,
        subset=subset,
        publish_format="usd",
    )
    if not latest and variant != "default":
        source_variant = "default"
        latest = manager.latest_publish_info(
            asset,
            department="model",
            variant="default",
            subset=subset,
            publish_format="usd",
        )
    if not latest or not latest.get("absolute_path"):
        return None
    return {
        "variant": source_variant,
        "department": "model",
        "subset": subset or "",
        "publish_format": "usd",
        "version": latest.get("version", ""),
        "path": str(latest["absolute_path"]),
        "namespace": "model",
        "base_variant": "default" if source_variant == "default" else "",
        "reason": "houdini_model_from_latest_usd",
    }


def ensure_houdini_usd_dependency_plan(
    asset: Asset,
    manager: AssetManager,
    department: str,
    variant: str,
    subset: str | None,
    dependency_plan: dict | None,
) -> dict | None:
    if dependency_plan:
        path = Path(str(dependency_plan.get("path") or ""))
        publish_format = str(dependency_plan.get("publish_format") or "").lower().lstrip(".")
        if publish_format in {"usd", "usda", "usdc"} and path.suffix.lower() in {".usd", ".usda", ".usdc"}:
            return dependency_plan

    if department in {"model", "look"}:
        replacement = resolve_houdini_latest_model_sublayer(
            asset,
            manager,
            "model",
            variant,
            subset,
        )
        if replacement:
            replacement["reason"] = f"houdini_usd_sublayer_for_{department}_{subset or ''}"
            return replacement

    if dependency_plan:
        raise RuntimeError(
            "Houdini Stage can only sublayer USD publishes.\n"
            f"Resolved non-USD dependency: {dependency_plan.get('path')}\n"
            f"Department: {department}\n"
            f"Subset: {subset or ''}"
        )
    return None


def staging_dependency_candidates_from_config(
    manager: AssetManager,
    department: str,
    variant: str,
    subset: str | None,
) -> list[tuple[str, str, str, str, str]]:
    rules = manager.staging_dependencies.get(department) or {}
    rule = rules.get(subset or "") or rules.get("default") or {}
    sources = rule.get("sources") or _staging_flat_rule_sources(rule)
    candidates = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_department = str(source.get("department") or "").strip()
        publish_format = str(source.get("format") or "ma").strip().lstrip(".")
        if not source_department or not publish_format:
            continue
        source_variant = _staging_source_variant(str(source.get("variant") or "current"), variant)
        subsets = source.get("subsets") or []
        if isinstance(subsets, str):
            subsets = [subsets]
        for source_subset in subsets:
            resolved_subset = subset if str(source_subset) == "selected" else str(source_subset)
            if not resolved_subset:
                continue
            candidates.append(
                (
                    source_variant,
                    source_department,
                    resolved_subset,
                    publish_format,
                    f"{department}_from_{source_variant}_{source_department}_{resolved_subset}",
                )
            )
    return _unique_staging_candidates(candidates)


def _staging_flat_rule_sources(rule: dict) -> list[dict]:
    source_department = rule.get("department")
    subsets = rule.get("subsets")
    if not source_department or not subsets:
        return []
    variants = rule.get("variants") or ["current"]
    if isinstance(variants, str):
        variants = [variants]
    return [
        {
            "variant": source_variant,
            "department": source_department,
            "subsets": subsets,
            "format": rule.get("format") or "ma",
        }
        for source_variant in variants
    ]


def _staging_source_variant(config_variant: str, current_variant: str) -> str:
    if config_variant in {"current", "selected"}:
        return current_variant
    return config_variant or current_variant


def _unique_staging_candidates(
    candidates: list[tuple[str, str, str, str, str]],
) -> list[tuple[str, str, str, str, str]]:
    unique = []
    seen = set()
    for candidate in candidates:
        key = candidate[:4]
        if key in seen:
            continue
        unique.append(candidate)
        seen.add(key)
    return unique


def fallback_staging_dependency_candidates(
    department: str,
    variant: str,
    subset: str | None,
) -> list[tuple[str, str, str, str, str]]:
    candidates: list[tuple[str, str, str, str, str]] = []
    if department == "model":
        candidates.append(("default", "model", subset or "render", "ma", "variant_model_from_default"))
    elif department in {"rig", "look", "groom"}:
        model_subsets = ["render", "hires"]
        if department == "rig" and subset == "layout":
            model_subsets = ["proxy", "guide", *model_subsets]
        if variant == "default":
            candidates.extend(
                ("default", "model", model_subset, "ma", f"{department}_from_default_model_{model_subset}")
                for model_subset in model_subsets
            )
        else:
            candidates.extend(
                (variant, "model", model_subset, "ma", f"{department}_from_variant_model_{model_subset}")
                for model_subset in model_subsets
            )
            candidates.extend(
                ("default", "model", model_subset, "ma", f"{department}_from_default_model_{model_subset}")
                for model_subset in model_subsets
            )
    return candidates


def resolve_asset_work_template(manager: AssetManager, department: str) -> Path | None:
    filename = f"{department}_base.ma"
    configured = str(
        (
            (
                (getattr(manager, "base_config", {}).get("template_files") or {}).get(
                    "maya"
                )
                or {}
            ).get("asset")
            or {}
        ).get(str(department).lower())
        or ""
    ).strip()
    if configured:
        pipeline_root = Path(__file__).resolve().parents[1]
        configured_path = Path(
            os.path.expandvars(
                configured
                .replace("{project_root}", str(manager.project_root))
                .replace("{pipeline_root}", str(pipeline_root))
            )
        )
        if configured_path.is_file():
            return configured_path
    project_template = manager.project_root / "settings" / "templates" / "maya" / "asset" / filename
    if project_template.exists():
        return project_template
    pipeline_root = Path(__file__).resolve().parents[1]
    common_template = pipeline_root / "templates" / "maya" / "asset" / filename
    if common_template.exists():
        return common_template
    return None


def ensure_asset_top_structure(cmds, asset_name: str) -> None:
    top = _ensure_exact_top_node(cmds, asset_name)
    _adopt_or_create_asset_child(cmds, top, "geo", ("geo", "geo_grp"))
    ensure_pipeline_work_structure(cmds)


def ensure_pipeline_work_structure(cmds) -> dict[str, str]:
    pipeline_grp = _ensure_group(cmds, "PIPELINE_GRP")
    ref_grp = _ensure_group(cmds, "REF_GRP", parent=pipeline_grp)
    work_grp = _ensure_group(cmds, "WORK_GRP", parent=pipeline_grp)
    preview_grp = _ensure_group(cmds, "PREVIEW_GRP", parent=pipeline_grp)
    return {
        "pipeline": pipeline_grp,
        "ref": ref_grp,
        "work": work_grp,
        "preview": preview_grp,
    }


def _adopt_or_create_asset_child(cmds, parent: str, target: str, aliases: tuple[str, ...]) -> str:
    existing = _child_with_leaf_name(cmds, parent, target)
    if existing:
        return existing

    candidate = _template_child_candidate(cmds, parent, aliases)
    if candidate:
        if _node_leaf_name(candidate) != target:
            candidate = cmds.rename(candidate, target)
        parents = cmds.listRelatives(candidate, parent=True, fullPath=True) or []
        if not parents or parents[0] != parent:
            candidate = (cmds.parent(candidate, parent) or [candidate])[0]
        return (cmds.ls(candidate, long=True) or [candidate])[0]

    created = cmds.group(empty=True, name=target, parent=parent)
    return (cmds.ls(created, long=True) or [created])[0]


def _child_with_leaf_name(cmds, parent: str, name: str) -> str | None:
    children = cmds.listRelatives(parent, children=True, type="transform", fullPath=True) or []
    matches = [child for child in children if _node_leaf_name(child) == name]
    if len(matches) > 1:
        raise RuntimeError(f"Multiple children named '{name}' were found under {parent}.")
    return matches[0] if matches else None


def _template_child_candidate(cmds, parent: str, aliases: tuple[str, ...]) -> str | None:
    children = cmds.listRelatives(parent, children=True, type="transform", fullPath=True) or []
    nested = [child for child in children if _node_leaf_name(child) in aliases]
    assemblies = []
    for alias in aliases:
        assemblies.extend(cmds.ls(alias, assemblies=True, long=True) or [])
    candidates = _unique_nodes_by_path(nested + assemblies)
    if len(candidates) > 1:
        raise RuntimeError(f"Multiple template groups match {aliases}: {', '.join(candidates)}")
    return candidates[0] if candidates else None


def _node_leaf_name(node: str) -> str:
    return node.split("|")[-1].split(":")[-1]


def _unique_nodes_by_path(nodes: list[str]) -> list[str]:
    unique = []
    seen = set()
    for node in nodes:
        if node not in seen:
            unique.append(node)
            seen.add(node)
    return unique


def _ensure_exact_top_node(cmds, asset_name: str) -> str:
    matches = cmds.ls(asset_name, assemblies=True, long=True) or []
    if matches:
        if len(matches) > 1:
            raise RuntimeError(f"Multiple top nodes match asset name: {asset_name}")
        leaf = matches[0].split("|")[-1]
        if leaf != asset_name:
            raise RuntimeError(f"Top node name must be exactly '{asset_name}', found '{leaf}'.")
        return matches[0]

    created = cmds.group(empty=True, name=asset_name)
    leaf = created.split("|")[-1]
    if leaf != asset_name:
        if cmds.objExists(created):
            cmds.delete(created)
        raise RuntimeError(
            f"Could not create exact top node '{asset_name}'. Maya created '{leaf}'. "
            "Check for an existing conflicting node."
        )
    long_name = (cmds.ls(created, long=True) or [created])[0]
    return long_name


def setup_quick_preview_locators(asset_name: str) -> dict:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Quick Preview setup is available inside Maya.") from exc

    top = _find_exact_top_node(cmds, asset_name)
    if not top:
        raise RuntimeError(f"Asset top node was not found: {asset_name}")

    pipeline_grp = _ensure_group(cmds, "PIPELINE_GRP")
    preview_grp = _ensure_group(cmds, "PREVIEW_GRP", parent=pipeline_grp)
    preview_tops = _preview_asset_top_nodes(cmds, asset_name, top)
    bbox = _combined_world_bbox(cmds, preview_tops)
    center = [
        (bbox[0] + bbox[3]) * 0.5,
        (bbox[1] + bbox[4]) * 0.5,
        (bbox[2] + bbox[5]) * 0.5,
    ]
    size = [bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]]
    diagonal = max((size[0] ** 2 + size[1] ** 2 + size[2] ** 2) ** 0.5, 1.0)
    camera_distance = diagonal * 1.8
    focus_position = [center[0], bbox[1] + size[1] * 0.6 if size[1] > 0 else center[1], center[2]]
    camera_position = [center[0], focus_position[1], center[2] + camera_distance]

    focus = _ensure_locator(cmds, "preview_focus_LOC", parent=preview_grp)
    camera = _ensure_locator(cmds, "preview_cam_LOC", parent=preview_grp)
    focus_manual = _is_manual_preview_locator(cmds, focus)
    camera_manual = _is_manual_preview_locator(cmds, camera)
    if not focus_manual:
        cmds.xform(focus, worldSpace=True, translation=focus_position)
        _set_auto_generated(cmds, focus, True)
    if not camera_manual:
        cmds.xform(camera, worldSpace=True, translation=camera_position)
        _set_auto_generated(cmds, camera, True)

    return {
        "pipeline_group": pipeline_grp,
        "preview_group": preview_grp,
        "focus": focus,
        "camera": camera,
        "focus_manual": focus_manual,
        "camera_manual": camera_manual,
        "preview_tops": preview_tops,
        "bbox": bbox,
        "camera_distance": camera_distance,
    }


def _preview_asset_top_nodes(cmds, asset_name: str, local_top: str) -> list[str]:
    tops = [local_top]
    for node in cmds.ls(assemblies=True, type="transform", long=True) or []:
        if node != local_top and _node_leaf_name(node) == asset_name:
            tops.append(node)
    return _unique_nodes_by_path(tops)


def _combined_world_bbox(cmds, nodes: list[str]) -> list[float]:
    if not nodes:
        raise RuntimeError("No asset top nodes were found for Quick Preview framing.")
    boxes = [cmds.exactWorldBoundingBox(node) for node in nodes]
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        min(box[2] for box in boxes),
        max(box[3] for box in boxes),
        max(box[4] for box in boxes),
        max(box[5] for box in boxes),
    ]


def create_quick_preview_package(
    asset: Asset,
    manager: AssetManager,
    *,
    department: str,
    variant: str,
    subset: str,
) -> dict:
    result = setup_quick_preview_locators(asset.name)
    base_dir = quick_preview_base_dir(asset, variant, department, subset)
    version = next_review_version(base_dir)
    version_label = f"v{version:03d}"
    version_dir = base_dir / version_label
    outputs = export_quick_preview_images(asset.name, result, version_dir)
    review_data = {
        "asset": asset.name,
        "category": asset.category,
        "group": asset.group,
        "variant": variant,
        "department": department,
        "subset": subset,
        "version": version_label,
        "type": "quick_preview",
        "views": ["front", "side", "top"],
        "outputs": outputs,
        "locators": {
            "focus": result.get("focus"),
            "camera": result.get("camera"),
            "focus_manual": result.get("focus_manual"),
            "camera_manual": result.get("camera_manual"),
        },
        "preview_tops": result.get("preview_tops") or [],
        "bbox": result.get("bbox"),
        "camera_distance": result.get("camera_distance"),
    }
    publish_data = {
        "asset": asset.name,
        "publish_type": "review",
        "department": department,
        "subset": subset,
        "variant": variant,
        "version": version,
        "review": "review.json",
        "files": {
            key: [Path(path).relative_to(version_dir).as_posix() for path in paths]
            for key, paths in outputs.items()
        },
    }
    write_json_file(version_dir / "review.json", review_data)
    write_json_file(version_dir / "publish.json", publish_data)
    write_json_file(base_dir / "latest.json", {"version": version_label, "path": f"{version_label}/review.json"})
    update_versions_json(base_dir / "versions.json", version_label)
    result["version_dir"] = str(version_dir)
    result["review_json"] = str(version_dir / "review.json")
    return result


def quick_preview_base_dir(asset: Asset, variant: str, department: str, subset: str) -> Path:
    if asset.uses_variant_structure(variant):
        return asset.variant_root(variant) / "publish" / "review" / department / subset
    return asset.publish_dir / "review" / department / subset


def turntable_preview_base_dir(asset: Asset, variant: str) -> Path:
    if asset.uses_variant_structure(variant):
        return asset.variant_root(variant) / "publish" / "review" / "look" / "turntable"
    return asset.publish_dir / "review" / "look" / "turntable"


def build_turntable_scene_package(
    asset: Asset,
    manager: AssetManager,
    *,
    variant: str,
    subset: str,
    rotate_lights: bool = False,
    frame_count: int = 120,
) -> dict:
    base_dir = turntable_preview_base_dir(asset, variant)
    version = next_review_version(base_dir)
    version_label = f"v{version:03d}"
    version_dir = base_dir / version_label
    version_dir.mkdir(parents=True, exist_ok=True)

    source = resolve_turntable_source(asset, manager, variant=variant, subset=subset)
    camera = turntable_camera_data(asset, manager, variant=variant, subset=subset)
    templates = turntable_template_paths(manager, version_dir)
    turntable_usd = version_dir / "turntable.usd"
    write_turntable_usd(
        turntable_usd,
        asset_name=asset.name,
        source_usd=source["source_usd"],
        templates=templates,
        camera=camera,
        rotate_lights=rotate_lights,
        frame_count=frame_count,
    )
    validation = validate_usd_file(manager, turntable_usd)

    manifest = {
        "asset": asset.name,
        "category": asset.category,
        "group": asset.group,
        "variant": variant,
        "department": "look",
        "subset": "turntable",
        "version": version_label,
        "type": "turntable_scene",
        "turntable_usd": "turntable.usd",
        "source": source,
        "templates": templates,
        "camera": camera,
        "options": {
            "rotate_asset": True,
            "rotate_lights": bool(rotate_lights),
            "frame_count": frame_count,
            "preview_format": "jpg",
            "final_format": "exr",
        },
        "validation": validation,
    }
    review_data = {
        "asset": asset.name,
        "category": asset.category,
        "group": asset.group,
        "variant": variant,
        "department": "look",
        "subset": "turntable",
        "version": version_label,
        "type": "turntable_scene",
        "views": ["turntable"],
        "turntable_usd": "turntable.usd",
        "manifest": "turntable_manifest.json",
        "outputs": {},
    }
    publish_data = {
        "asset": asset.name,
        "publish_type": "review",
        "department": "look",
        "subset": "turntable",
        "variant": variant,
        "version": version,
        "files": {
            "usd": "turntable.usd",
            "manifest": "turntable_manifest.json",
            "review": "review.json",
        },
        "source": source,
        "validation": validation,
        "comment": "turntable scene build",
    }
    write_json_file(version_dir / "turntable_manifest.json", manifest)
    write_json_file(version_dir / "review.json", review_data)
    write_json_file(version_dir / "publish.json", publish_data)
    write_json_file(base_dir / "latest.json", {"version": version_label, "path": f"{version_label}/review.json"})
    update_versions_json(base_dir / "versions.json", version_label)
    return {
        "version_dir": str(version_dir),
        "turntable_usd": str(turntable_usd),
        "review_json": str(version_dir / "review.json"),
        "source_usd": source.get("source_usd", ""),
        "validation": validation,
    }


def resolve_turntable_source(asset: Asset, manager: AssetManager, *, variant: str, subset: str) -> dict:
    look_info = manager.latest_publish_info(
        asset,
        department="look",
        variant=variant,
        subset=subset,
        publish_format="usd",
    )
    model_info = None
    if look_info and look_info.get("absolute_path"):
        look_path = Path(look_info["absolute_path"])
        publish_record = read_json_file(look_path.parent / "publish.json", {}) or {}
        model_dependency = publish_record.get("model_dependency") or (publish_record.get("dependencies") or {}).get("model") or {}
        model_path = _dependency_project_path(manager, model_dependency)
        return {
            "mode": "look",
            "source_usd": look_path.as_posix(),
            "look": _publish_info_summary(look_info, look_path),
            "model_dependency": model_dependency,
            "model_usd": model_path.as_posix() if model_path else "",
        }

    candidate_subsets = [subset, "high", "render", "proxy", "low"]
    seen = set()
    for candidate in candidate_subsets:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        model_info = manager.latest_publish_info(
            asset,
            department="model",
            variant=variant,
            subset=candidate,
            publish_format="usd",
        )
        if model_info and model_info.get("absolute_path"):
            model_path = Path(model_info["absolute_path"])
            return {
                "mode": "model",
                "source_usd": model_path.as_posix(),
                "model": _publish_info_summary(model_info, model_path),
            }
    raise FileNotFoundError(
        "Turntable source USD was not found. Expected latest look/{subset}/usd or model/{subset}/usd publish."
    )


def _publish_info_summary(info: dict, path: Path) -> dict:
    return {
        "version": str(info.get("version") or ""),
        "path": path.as_posix(),
    }


def _dependency_project_path(manager: AssetManager, dependency: dict) -> Path | None:
    raw = str(dependency.get("path") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        return path
    return manager.project_root / raw


def validate_usd_file(manager: AssetManager, path: Path) -> dict:
    usdcat = resolve_usdcat_path(manager)
    if not usdcat:
        return {
            "status": "skipped",
            "tool": "usdcat",
            "message": "usdcat was not found.",
        }
    try:
        command = [str(usdcat), str(path)]
        if usdcat.suffix.lower() in {".bat", ".cmd"}:
            command = ["cmd.exe", "/c", *command]
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=30,
            env=clean_external_usd_env(),
        )
    except Exception as exc:
        return {
            "status": "error",
            "tool": str(usdcat),
            "message": str(exc),
        }
    status = "ok" if result.returncode == 0 else "error"
    return {
        "status": status,
        "tool": str(usdcat),
        "returncode": result.returncode,
        "stdout": (result.stdout or "")[-2000:],
        "stderr": (result.stderr or "")[-2000:],
    }


def launch_usdview(usdview: Path, usd_path: Path) -> Path:
    env = clean_external_usd_env()
    log_path = usdview_launch_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [str(usdview), str(usd_path)]
    if usdview.suffix.lower() in {".bat", ".cmd"}:
        command = ["cmd.exe", "/d", "/s", "/c", str(usdview), str(usd_path)]
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    with log_path.open("a", encoding="utf-8", errors="replace") as log:
        log.write("\n")
        log.write(f"[{datetime.now().isoformat(timespec='seconds')}] launch usdview\n")
        log.write("command: " + " ".join(command) + "\n")
        log.write(f"cwd: {usdview.parent}\n")
        log.write(f"usd: {usd_path}\n")
        log.write(f"env PATH: {env.get('PATH', '')}\n")
        log.write(f"env SystemRoot: {env.get('SystemRoot', '')}\n")
        log.write(f"env ComSpec: {env.get('ComSpec', '')}\n")
        log.flush()
        subprocess.Popen(
            command,
            cwd=str(usdview.parent),
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            env=env,
            creationflags=creationflags,
        )
    return log_path


def usdview_launch_log_path() -> Path:
    root = Path(
        os.environ.get("SMARTPIPELINE_ROOT")
        or os.environ.get("SMARTLIBRARY_ROOT")
        or Path(__file__).resolve().parents[1]
    )
    return root / "runtime" / "logs" / "usdview_launch.log"


def clean_external_usd_env() -> dict:
    env = {}
    for key in (
        "SystemRoot",
        "WINDIR",
        "ComSpec",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "ProgramData",
        "USERNAME",
        "USERDOMAIN",
        "COMPUTERNAME",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "SMARTPIPELINE_ROOT",
        "SMARTLIBRARY_ROOT",
    ):
        value = os.environ.get(key)
        if value:
            env[key] = value
    system_root = env.get("SystemRoot") or env.get("WINDIR") or r"C:\Windows"
    env.setdefault("SystemRoot", system_root)
    env.setdefault("WINDIR", system_root)
    env.setdefault("ComSpec", str(Path(system_root) / "System32" / "cmd.exe"))
    env["PATH"] = os.pathsep.join(
        [
            str(Path(system_root) / "System32"),
            system_root,
            str(Path(system_root) / "System32" / "Wbem"),
        ]
    )
    return env


def _is_dcc_runtime_path(path: str) -> bool:
    text = path.replace("\\", "/").lower()
    return any(
        token in text
        for token in (
            "/autodesk/maya",
            "/side effects software/houdini",
            "/houdini",
            "/maya20",
        )
    )


def resolve_usdcat_path(manager: AssetManager) -> Path | None:
    return resolve_tool_path(manager, "usdcat", "tools/usd/usdcat.bat")


def resolve_usdview_path(manager: AssetManager) -> Path | None:
    return resolve_tool_path(manager, "usdview", "tools/usd/usdview.bat")


def resolve_tool_path(manager: AssetManager, tool_name: str, fallback_relative: str) -> Path | None:
    _ensure_smartlib_on_path()
    from smartlib.core.config_loader import ProjectConfig, expand_config_tokens, smartpipeline_tools_root

    project_config = ProjectConfig(manager.config_dir)
    tools = read_json_file(manager.config_dir / "tools.json", {}) or project_config.load("tools.yml")
    raw = (((tools.get("tools") or {}).get(tool_name) or {}).get("path") or "").strip()
    root = Path(os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT") or Path(__file__).resolve().parents[1])
    if raw:
        raw = expand_config_tokens(raw, project_config)
        path = Path(raw)
        if path.exists():
            return path
    tools_fallback = smartpipeline_tools_root() / fallback_relative.replace("tools/", "")
    if tools_fallback.exists():
        return tools_fallback
    fallback = root / fallback_relative
    return fallback if fallback.exists() else None


def _read_yaml_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml

        with path.open("r", encoding="utf-8") as stream:
            return yaml.safe_load(stream) or {}
    except Exception:
        return {}


def turntable_template_paths(manager: AssetManager, version_dir: Path) -> dict[str, str]:
    root = Path(os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT") or Path(__file__).resolve().parents[1])
    template_dir = root / "templates" / "usd" / "look"
    paths = {
        "look_geo": template_dir / "look_geo.usda",
        "look_studiolight": template_dir / "look_studiolight.usda",
    }
    return {key: _usd_asset_path(path, version_dir) for key, path in paths.items() if path.exists()}


def turntable_camera_data(asset: Asset, manager: AssetManager, *, variant: str, subset: str) -> dict:
    maya_data = _turntable_camera_from_maya(asset.name)
    if maya_data:
        return maya_data
    bbox_data = _latest_preview_bbox(asset, variant, "model", subset) or _latest_preview_bbox(asset, variant, "look", subset)
    bbox = bbox_data.get("bbox") if bbox_data else None
    if bbox and len(bbox) == 6:
        center = [(bbox[0] + bbox[3]) * 0.5, (bbox[1] + bbox[4]) * 0.5, (bbox[2] + bbox[5]) * 0.5]
        size = [bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]]
        diagonal = max((size[0] ** 2 + size[1] ** 2 + size[2] ** 2) ** 0.5, 1.0)
        focus = [center[0], bbox[1] + size[1] * 0.55, center[2]]
        camera = [center[0], focus[1], center[2] + diagonal * 1.8]
        return {
            "source": "quick_preview_bbox",
            "focus": focus,
            "position": camera,
            "rotation": [0.0, 0.0, 0.0],
            "focal_length": 70.0,
            "bbox": bbox,
        }
    return {
        "source": "default",
        "focus": [0.0, 90.0, 0.0],
        "position": [0.0, 90.0, 320.0],
        "rotation": [0.0, 0.0, 0.0],
        "focal_length": 70.0,
        "bbox": [],
    }


def _turntable_camera_from_maya(asset_name: str) -> dict | None:
    try:
        import maya.cmds as cmds
    except ImportError:
        return None
    if not cmds.objExists("preview_focus_LOC") or not cmds.objExists("preview_cam_LOC"):
        return None
    focus = cmds.xform("preview_focus_LOC", query=True, worldSpace=True, translation=True)
    camera = cmds.xform("preview_cam_LOC", query=True, worldSpace=True, translation=True)
    return {
        "source": "preview_locators",
        "focus": [float(value) for value in focus],
        "position": [float(value) for value in camera],
        "rotation": [0.0, 0.0, 0.0],
        "focal_length": 70.0,
        "bbox": [],
    }


def _latest_preview_bbox(asset: Asset, variant: str, department: str, subset: str) -> dict:
    base_dir = quick_preview_base_dir(asset, variant, department, subset)
    if not base_dir.exists():
        return {}
    version_dirs = sorted((path for path in base_dir.glob("v*") if path.is_dir()), reverse=True)
    for version_dir in version_dirs:
        data = read_json_file(version_dir / "review.json", {}) or {}
        if data.get("bbox"):
            return data
    return {}


def write_turntable_usd(
    path: Path,
    *,
    asset_name: str,
    source_usd: str,
    templates: dict[str, str],
    camera: dict,
    rotate_lights: bool,
    frame_count: int,
) -> None:
    source_layer = _usd_asset_path(Path(source_usd), path.parent)
    sublayers = [source_layer]
    sublayers.extend(templates[key] for key in ("look_geo", "look_studiolight") if templates.get(key))
    sublayer_block = "\n".join(f"        @{item}@," for item in sublayers)
    position = camera.get("position") or [0.0, 90.0, 320.0]
    focus = camera.get("focus") or [0.0, 90.0, 0.0]
    rotation = camera.get("rotation") or [0.0, 0.0, 0.0]
    focal_length = float(camera.get("focal_length") or 70.0)
    lights_block = ""
    if rotate_lights:
        lights_block = f"""
over Xform "lights"
{{
    double3 xformOp:rotateXYZ.timeSamples = {{
        1: (0, 0, 0),
        {frame_count}: (0, 360, 0),
    }}
    uniform token[] xformOpOrder = ["xformOp:rotateXYZ"]
}}
"""
    text = f"""#usda 1.0
(
    defaultPrim = "turntable"
    startTimeCode = 1
    endTimeCode = {frame_count}
    framesPerSecond = 24
    timeCodesPerSecond = 24
    metersPerUnit = 1
    upAxis = "Y"
    subLayers = [
{sublayer_block}
    ]
)

def Xform "turntable"
{{
    def Xform "turntable_axis"
    {{
    }}

    def Xform "camera_rig"
    {{
        double3 xformOp:translate = ({_usd_float(position[0])}, {_usd_float(position[1])}, {_usd_float(position[2])})
        double3 xformOp:rotateXYZ = ({_usd_float(rotation[0])}, {_usd_float(rotation[1])}, {_usd_float(rotation[2])})
        uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ"]

        def Camera "turntable_camera"
        {{
            float focalLength = {_usd_float(focal_length)}
            float horizontalAperture = 20.955
            float verticalAperture = 11.789
            custom double3 smartpipeline:focus = ({_usd_float(focus[0])}, {_usd_float(focus[1])}, {_usd_float(focus[2])})
        }}
    }}
}}

over Xform "{asset_name}"
{{
        double3 xformOp:rotateXYZ.timeSamples = {{
            1: (0, 0, 0),
            {frame_count}: (0, 360, 0),
        }}
        uniform token[] xformOpOrder = ["xformOp:rotateXYZ"]
}}
{lights_block}
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _usd_asset_path(path: Path, anchor_dir: Path) -> str:
    try:
        value = path.relative_to(anchor_dir).as_posix()
    except ValueError:
        value = path.as_posix()
    return value.replace("\\", "/")


def _usd_float(value) -> str:
    try:
        return f"{float(value):.6g}"
    except (TypeError, ValueError):
        return "0"


def next_review_version(base_dir: Path) -> int:
    max_version = 0
    if base_dir.exists():
        for path in base_dir.iterdir():
            if path.is_dir() and path.name.lower().startswith("v") and path.name[1:].isdigit():
                max_version = max(max_version, int(path.name[1:]))
    return max_version + 1


def update_versions_json(path: Path, version_label: str) -> None:
    versions = []
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                versions = json.load(f) or []
        except Exception:
            versions = []
    next_versions = []
    seen = False
    for item in versions:
        if not isinstance(item, dict):
            continue
        if item.get("version") == version_label:
            item = dict(item)
            item["status"] = "latest"
            seen = True
        elif item.get("status") == "latest":
            item = dict(item)
            item["status"] = "available"
        next_versions.append(item)
    if not seen:
        next_versions.append({"version": version_label, "status": "latest"})
    write_json_file(path, next_versions)


def write_json_file(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def read_json_file(path: Path, default=None):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def export_quick_preview_images(asset_name: str, locator_data: dict, version_dir: Path) -> dict[str, list[str]]:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Quick Preview image export is available inside Maya.") from exc

    version_dir.mkdir(parents=True, exist_ok=True)
    focus_position = cmds.xform(locator_data["focus"], query=True, worldSpace=True, translation=True)
    bbox = locator_data["bbox"]
    size = [bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]]
    distance = float(locator_data["camera_distance"])
    views = [
        ("front", [focus_position[0], focus_position[1], focus_position[2] + distance], [0, 0, 0]),
        ("side", [focus_position[0] + distance, focus_position[1], focus_position[2]], [0, 90, 0]),
        ("top", [focus_position[0], focus_position[1] + distance, focus_position[2]], [-90, 0, 0]),
    ]
    modes = ("beauty", "wireframe", "bbox")
    panel = _active_model_panel(cmds)
    original_camera = cmds.modelPanel(panel, query=True, camera=True)
    original_selection = cmds.ls(selection=True, long=True) or []
    outputs: dict[str, list[str]] = {key: [] for key in modes}
    cameras = []
    bbox_curve = None
    original_bg = _capture_viewport_background(cmds)
    try:
        cmds.select(clear=True)
        bbox_curve = _create_preview_bbox_curve(cmds, bbox)
        _set_viewport_background(cmds, (0.48, 0.48, 0.48))
        for index, (_view_name, position, rotation) in enumerate(views, start=1):
            camera_transform, _camera_shape = cmds.camera(name=f"quickPreview_{index:02d}_CAM")
            cameras.append(camera_transform)
            cmds.xform(camera_transform, worldSpace=True, translation=position, rotation=rotation)
            cmds.modelPanel(panel, edit=True, camera=camera_transform)
            for mode in modes:
                output_dir = version_dir / mode
                output_dir.mkdir(parents=True, exist_ok=True)
                output_path = output_dir / f"{asset_name}_{mode}_{index:04d}.jpg"
                _apply_quick_preview_mode(cmds, panel, mode, bbox_curve)
                cmds.playblast(
                    completeFilename=str(output_path),
                    forceOverwrite=True,
                    format="image",
                    compression="jpg",
                    viewer=False,
                    showOrnaments=False,
                    offScreen=True,
                    frame=[cmds.currentTime(query=True)],
                    widthHeight=[1280, 720],
                    percent=100,
                )
                outputs[mode].append(str(output_path))
    finally:
        try:
            cmds.modelPanel(panel, edit=True, camera=original_camera)
        except Exception:
            pass
        for camera in cameras:
            if cmds.objExists(camera):
                cmds.delete(camera)
        if bbox_curve and cmds.objExists(bbox_curve):
            cmds.delete(bbox_curve)
        _restore_viewport_background(cmds, original_bg)
        if original_selection:
            existing = [node for node in original_selection if cmds.objExists(node)]
            if existing:
                cmds.select(existing, replace=True)
    return outputs


def _active_model_panel(cmds) -> str:
    panel = cmds.getPanel(withFocus=True)
    if panel and cmds.getPanel(typeOf=panel) == "modelPanel":
        return panel
    panels = cmds.getPanel(type="modelPanel") or []
    if not panels:
        raise RuntimeError("No Maya modelPanel was found for Quick Preview.")
    return panels[0]


def _apply_quick_preview_mode(cmds, panel: str, mode: str, bbox_curve: str | None) -> None:
    if bbox_curve and cmds.objExists(bbox_curve):
        cmds.setAttr(f"{bbox_curve}.visibility", mode == "bbox")
    if mode == "beauty":
        display_appearance = "smoothShaded"
        wireframe_on_shaded = False
        display_textures = True
    elif mode == "wireframe":
        display_appearance = "smoothShaded"
        wireframe_on_shaded = True
        display_textures = False
    else:
        display_appearance = "smoothShaded"
        wireframe_on_shaded = False
        display_textures = False
    cmds.modelEditor(
        panel,
        edit=True,
        displayAppearance=display_appearance,
        wireframeOnShaded=wireframe_on_shaded,
        displayTextures=display_textures,
        grid=False,
        manipulators=False,
        locators=False,
        cameras=False,
        joints=True,
    )


def _create_preview_bbox_curve(cmds, bbox: list[float]) -> str:
    x0, y0, z0, x1, y1, z1 = bbox
    points = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (x0, y0, z1),
        (x1, y0, z1), (x1, y0, z0), (x1, y1, z0), (x1, y1, z1),
        (x0, y1, z1), (x0, y1, z0),
    ]
    curve = cmds.curve(degree=1, point=points, name="quickPreview_bbox_CRV")
    shapes = cmds.listRelatives(curve, shapes=True, fullPath=True) or []
    for shape in shapes:
        try:
            cmds.setAttr(f"{shape}.overrideEnabled", True)
            cmds.setAttr(f"{shape}.overrideRGBColors", True)
            cmds.setAttr(f"{shape}.overrideColorRGB", 0.1, 1.0, 0.1)
            cmds.setAttr(f"{shape}.lineWidth", 2)
        except Exception:
            pass
    cmds.setAttr(f"{curve}.visibility", False)
    return curve


def _capture_viewport_background(cmds) -> dict:
    keys = [
        "background",
        "backgroundTop",
        "backgroundBottom",
    ]
    data = {}
    for key in keys:
        try:
            data[key] = cmds.displayRGBColor(key, query=True)
        except Exception:
            pass
    return data


def _set_viewport_background(cmds, color: tuple[float, float, float]) -> None:
    for key in ("background", "backgroundTop", "backgroundBottom"):
        try:
            cmds.displayRGBColor(key, *color)
        except Exception:
            pass


def _restore_viewport_background(cmds, data: dict) -> None:
    for key, value in data.items():
        try:
            cmds.displayRGBColor(key, *value)
        except Exception:
            pass


def _find_exact_top_node(cmds, asset_name: str) -> str | None:
    matches = cmds.ls(asset_name, assemblies=True, long=True) or []
    exact = [node for node in matches if node.split("|")[-1] == asset_name]
    if len(exact) > 1:
        raise RuntimeError(f"Multiple top nodes match asset name: {asset_name}")
    return exact[0] if exact else None


def _ensure_group(cmds, name: str, parent: str | None = None) -> str:
    matches = cmds.ls(name, long=True) or []
    node = matches[0] if matches else cmds.group(empty=True, name=name)
    if parent:
        parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
        if not parents or parents[0] != parent:
            node = (cmds.parent(node, parent) or [node])[0]
    return (cmds.ls(node, long=True) or [node])[0]


def _ensure_locator(cmds, name: str, parent: str) -> str:
    matches = cmds.ls(name, type="transform", long=True) or []
    node = matches[0] if matches else cmds.spaceLocator(name=name)[0]
    parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
    if not parents or parents[0] != parent:
        node = (cmds.parent(node, parent) or [node])[0]
    node = (cmds.ls(node, long=True) or [node])[0]
    _ensure_auto_generated_attr(cmds, node)
    return node


def _ensure_auto_generated_attr(cmds, node: str) -> None:
    if not cmds.attributeQuery("autoGenerated", node=node, exists=True):
        cmds.addAttr(node, longName="autoGenerated", attributeType="bool", defaultValue=True)
        cmds.setAttr(f"{node}.autoGenerated", True)


def _set_auto_generated(cmds, node: str, value: bool) -> None:
    _ensure_auto_generated_attr(cmds, node)
    cmds.setAttr(f"{node}.autoGenerated", bool(value))


def _is_manual_preview_locator(cmds, node: str) -> bool:
    _ensure_auto_generated_attr(cmds, node)
    return not bool(cmds.getAttr(f"{node}.autoGenerated"))


def capture_work_thumbnail_in_current_dcc(path: str | os.PathLike[str]) -> str:
    try:
        from smartlib.dcc.maya.thumbnail import capture_viewport_thumbnail

        return str(capture_viewport_thumbnail(path))
    except Exception:
        return ""


def publish_work_outputs(
    asset: Asset,
    manager: AssetManager,
    source_workfile: Path,
    targets: dict[str, Path],
    *,
    overwrite: bool,
    comment: str,
    subset: str | None,
    publish_version: int | str | None = None,
    dependency_info: dict | None = None,
) -> list[Path]:
    published: list[Path] = []
    files: dict[str, str] = {}
    parsed = manager.parse_work_file(source_workfile) or {}
    source_ext = source_workfile.suffix.lower().lstrip(".")
    extra_formats = [fmt for fmt in targets if fmt != source_ext]
    collect_rig_metadata = parsed.get("department") == "rig"
    if extra_formats or collect_rig_metadata:
        ensure_current_dcc_scene_matches(source_workfile)
    rig_metadata = None
    if collect_rig_metadata:
        rig_metadata = collect_rig_publish_metadata(
            asset.name,
            subset or parsed.get("variant") or "",
            source_workfile,
            dependency_info,
        )
        if "usd" in targets:
            validate_rig_usd_skel(rig_metadata)
    usd_skel_outputs: dict[str, Path] = {}
    snapshot_active = False
    try:
        if should_import_references_for_publish(dependency_info):
            compose_variant_snapshot_scene(asset.name, source_workfile, dependency_info)
            snapshot_active = True
        for publish_format, target in targets.items():
            if target.exists() and not overwrite:
                raise FileExistsError(f"Publish already exists: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            if publish_format == source_ext:
                export_root = asset.name if parsed.get("department") == "model" else None
                if export_root and publish_format in {"ma", "mb"}:
                    export_current_scene_for_publish(
                        target,
                        publish_format,
                        source_workfile,
                        export_root=export_root,
                    )
                elif publish_format in {"ma", "mb"} and snapshot_active:
                    save_maya_snapshot_copy(target, source_workfile)
                else:
                    shutil.copy2(source_workfile, target)
            elif publish_format == "usd" and rig_metadata is not None:
                usd_skel_outputs = publish_rig_usd_skel(
                    target.parent,
                    rig_metadata,
                    overwrite=overwrite,
                )
                target = usd_skel_outputs["rig_usd"]
            elif publish_format in {"ma", "mb"} and parsed.get("department") != "model":
                # Rig/look/groom scene publishes are complete DCC snapshots. They
                # do not have a single asset top node that can safely represent
                # controllers, sets, skeletons, connections, and scene settings.
                save_maya_snapshot_copy(target, source_workfile)
            else:
                export_root = asset.name if parsed.get("department") == "model" else None
                export_current_scene_for_publish(
                    target,
                    publish_format,
                    source_workfile,
                    export_root=export_root,
                )
            published.append(target)
            files[publish_format] = target.name
            if rig_metadata is not None:
                write_rig_publish_metadata(target.parent, rig_metadata)
        if usd_skel_outputs:
            for key in ("skeleton_usd", "skin_usd", "validation"):
                output = usd_skel_outputs[key]
                if output not in published:
                    published.append(output)
    finally:
        if snapshot_active:
            reopen_source_workfile(source_workfile)

    manager.register_publish_files_for_work_file(
        asset,
        source_workfile,
        files=files,
        comment=comment,
        subset=subset,
        version=publish_version,
        dependency_info=dependency_info,
    )
    if usd_skel_outputs:
        augment_usd_skel_publish_record(usd_skel_outputs, rig_metadata or {})
    return published


def publish_rig_usd_skel(
    version_dir: Path,
    rig_metadata: dict,
    *,
    overwrite: bool,
) -> dict[str, Path]:
    from smartlib.dcc.maya.usd_skel import publish_usd_skel_package

    contract = current_usd_skel_contract()
    return publish_usd_skel_package(
        version_dir,
        rig_metadata=rig_metadata,
        contract=contract,
        overwrite=overwrite,
    )


def validate_rig_usd_skel(rig_metadata: dict) -> dict:
    from smartlib.dcc.maya.usd_skel import validate_usd_skel_scene

    validation, _export_data = validate_usd_skel_scene(
        rig_metadata=rig_metadata,
        contract=current_usd_skel_contract(),
    )
    issues = validation.get("issues") or []
    if issues:
        raise RuntimeError("USD Skel validation failed:\n- " + "\n- ".join(issues))
    return validation


def current_usd_skel_contract() -> dict[str, str]:
    from smartlib.core.config_loader import current_project_config

    project_config = current_project_config()
    if project_config:
        return project_config.usd_skel_contract
    return {
        "geometry_set": "cache_geo_set",
        "skeleton_set": "skel_export_set",
        "root_joint_source": "rig_metadata",
        "root_joint_metadata_key": "root_joint",
        "root_joint_detection": "skin_influence_root",
    }


def augment_usd_skel_publish_record(outputs: dict[str, Path], rig_metadata: dict) -> Path:
    version_dir = outputs["rig_usd"].parent
    publish_path = version_dir / "publish.json"
    record = read_json_file(publish_path, {}) or {}
    record.setdefault("files", {})["usd"] = outputs["rig_usd"].name
    record["usd_skel"] = {
        "schema": "smartpipeline.usd_skel.v1",
        "root_joint": rig_metadata.get("root_joint", ""),
        "root_joints": (
            read_json_file(outputs["validation"], {}).get("root_joints", [])
        ),
        "rig": outputs["rig_usd"].name,
        "skeleton": outputs["skeleton_usd"].name,
        "skin": outputs["skin_usd"].name,
        "validation": outputs["validation"].name,
    }
    write_json_file(publish_path, record)
    return publish_path


def collect_rig_publish_metadata(
    asset_name: str,
    subset: str,
    source_workfile: Path,
    dependency_info: dict | None,
) -> dict:
    from smartlib.dcc.maya.rig_metadata import collect_rig_metadata

    return collect_rig_metadata(
        asset_name=asset_name,
        subset=subset,
        source_workfile=source_workfile,
        dependency_info=dependency_info,
    )


def write_rig_publish_metadata(version_dir: Path, metadata: dict) -> Path:
    metadata_dir = version_dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    path = metadata_dir / "rig.json"
    write_json_file(path, metadata)
    return path


def should_import_references_for_publish(dependency_info: dict | None) -> bool:
    if not dependency_info:
        return False
    return bool((dependency_info.get("dependencies") or {}).get("references"))


def compose_variant_snapshot_scene(asset_name: str, source_workfile: Path, dependency_info: dict | None) -> dict:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Maya snapshot publish is available inside Maya.") from exc

    ensure_current_dcc_scene_matches(source_workfile)
    imported = _import_all_scene_references(cmds)
    local_top = _find_exact_top_node(cmds, asset_name)
    if not local_top:
        raise RuntimeError(f"Variant local top node was not found after importing references: {asset_name}")
    base_tops = [
        node for node in (cmds.ls(assemblies=True, type="transform", long=True) or [])
        if node != local_top and _node_leaf_name(node) == asset_name
    ]
    local_mesh_names = _mesh_leaf_names_under_top(cmds, local_top)
    merged = []
    for base_top in base_tops:
        _merge_base_top_into_variant(cmds, base_top, local_top, local_mesh_names)
        merged.append(base_top)
    return {
        "imported_references": imported,
        "merged_base_tops": merged,
        "overrides": list((dependency_info or {}).get("overrides") or []),
    }


def _import_all_scene_references(cmds) -> list[str]:
    imported = []
    for _iteration in range(100):
        refs = cmds.file(query=True, reference=True) or []
        if not refs:
            return imported
        progressed = False
        for ref in refs:
            try:
                ref_node = cmds.referenceQuery(ref, referenceNode=True)
                cmds.file(ref, importReference=True)
                imported.append(ref_node)
                progressed = True
            except Exception:
                continue
        if not progressed:
            break
    remaining = cmds.file(query=True, reference=True) or []
    if remaining:
        raise RuntimeError("Could not import all references for publish snapshot: " + ", ".join(remaining))
    return imported


def save_maya_snapshot_copy(target: Path, source_workfile: Path) -> Path:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Maya snapshot publish is available inside Maya.") from exc
    scene_type = "mayaBinary" if target.suffix.lower() == ".mb" else "mayaAscii"
    cmds.file(rename=str(target))
    cmds.file(save=True, type=scene_type)
    cmds.file(rename=str(source_workfile))
    return target


def reopen_source_workfile(source_workfile: Path) -> None:
    try:
        import maya.cmds as cmds
    except ImportError:
        return
    cmds.file(str(source_workfile), open=True, force=True)


def _merge_base_top_into_variant(cmds, base_top: str, local_top: str, local_mesh_names: set[str]) -> None:
    for child in cmds.listRelatives(base_top, children=True, type="transform", fullPath=True) or []:
        child_leaf = _node_leaf_name(child)
        if child_leaf in {"geo", "rig", "groom", "look"}:
            local_group = _adopt_or_create_asset_child(cmds, local_top, child_leaf, (child_leaf, f"{child_leaf}_grp"))
            _merge_base_group_children(cmds, child, local_group, local_mesh_names)
        else:
            _move_base_child_if_not_overridden(cmds, child, local_top, local_mesh_names)
    if cmds.objExists(base_top):
        cmds.delete(base_top)


def _merge_base_group_children(cmds, base_group: str, local_group: str, local_mesh_names: set[str]) -> None:
    for child in cmds.listRelatives(base_group, children=True, type="transform", fullPath=True) or []:
        child_leaf = _node_leaf_name(child)
        if _node_leaf_name(local_group) == "geo" and child_leaf in {"render", "proxy", "guide"}:
            local_subset = _adopt_or_create_asset_child(cmds, local_group, child_leaf, (child_leaf, f"{child_leaf}_grp"))
            _merge_base_group_children(cmds, child, local_subset, local_mesh_names)
        else:
            _move_base_child_if_not_overridden(cmds, child, local_group, local_mesh_names)


def _move_base_child_if_not_overridden(cmds, child: str, parent: str, local_mesh_names: set[str]) -> None:
    if _subtree_mesh_names(cmds, child).intersection(local_mesh_names):
        cmds.delete(child)
        return
    moved = (cmds.parent(child, parent) or [child])[0]
    _strip_dag_namespaces(cmds, moved)


def _subtree_mesh_names(cmds, node: str) -> set[str]:
    names = set()
    transforms = [node] + (cmds.listRelatives(node, allDescendents=True, type="transform", fullPath=True) or [])
    for transform in transforms:
        shapes = cmds.listRelatives(transform, shapes=True, fullPath=True) or []
        if any(cmds.nodeType(shape) == "mesh" for shape in shapes):
            names.add(_node_leaf_name(transform))
    return names


def _strip_dag_namespaces(cmds, node: str) -> str:
    for child in cmds.listRelatives(node, children=True, type="transform", fullPath=True) or []:
        _strip_dag_namespaces(cmds, child)
    leaf = node.split("|")[-1]
    clean_leaf = leaf.split(":")[-1]
    if leaf != clean_leaf:
        try:
            node = cmds.rename(node, clean_leaf)
        except Exception:
            pass
    return node


def ensure_current_dcc_scene_matches(source_workfile: str | os.PathLike[str]) -> None:
    try:
        import maya.cmds as cmds
    except ImportError:
        cmds = None

    if cmds is None:
        try:
            import hou
        except ImportError as exc:
            raise RuntimeError("Extra publish format export is available inside Maya or Houdini.") from exc
        current_scene_name = hou.hipFile.path()
    else:
        current_scene_name = cmds.file(query=True, sceneName=True) or ""

    if not current_scene_name:
        raise RuntimeError("Open the selected work scene before publishing extra formats.")
    current_scene = Path(current_scene_name)
    if current_scene.resolve() != Path(source_workfile).resolve():
        raise RuntimeError(
            "Open the selected work scene before publishing extra formats. "
            f"Current: {current_scene} / Selected: {source_workfile}"
        )


def validate_variant_publish(
    asset: Asset,
    manager: AssetManager,
    source_workfile: Path,
    *,
    subset: str | None,
) -> dict:
    parsed = manager.parse_work_file(source_workfile) or {}
    variant = parsed.get("variant") or ""
    data = {
        "dependencies": {},
        "validation": {"issues": []},
    }
    if not variant or variant == "default":
        data["validation"]["status"] = "ok"
        return data

    data["base_variant"] = "default"
    try:
        import maya.cmds as cmds
    except ImportError:
        data["validation"]["issues"].append("Maya is required to validate variant override dependencies.")
        return data

    try:
        ensure_current_dcc_scene_matches(source_workfile)
    except Exception as exc:
        data["validation"]["issues"].append(str(exc))
        return data

    references = []
    default_reference_names = set()
    for ref_node in cmds.ls(type="reference") or []:
        if ref_node == "sharedReferenceNode":
            continue
        try:
            ref_path = cmds.referenceQuery(ref_node, filename=True, withoutCopyNumber=True)
            namespace = cmds.referenceQuery(ref_node, namespace=True).lstrip(":")
        except Exception:
            continue
        is_default = "/default/" in str(ref_path).replace("\\", "/").lower()
        references.append({
            "node": ref_node,
            "namespace": namespace,
            "path": str(ref_path).replace("\\", "/"),
            "base_variant": "default" if is_default else "",
        })
        if is_default:
            default_reference_names.update(_mesh_transform_leaf_names(cmds, namespace=namespace))

    local_top = _find_exact_top_node(cmds, asset.name)
    local_names = _mesh_leaf_names_under_top(cmds, local_top) if local_top else set()
    overrides = sorted(default_reference_names.intersection(local_names))
    adds = sorted(local_names.difference(default_reference_names))
    data["dependencies"] = {
        "base_variant": "default",
        "references": references,
    }
    data["overrides"] = overrides
    data["adds"] = adds
    if not references:
        data["validation"]["issues"].append("Variant publish has no references. Expected default variant reference.")
    data["validation"]["status"] = "warning" if data["validation"]["issues"] else "ok"
    return data


def collect_publish_dependency_info(
    asset: Asset,
    manager: AssetManager,
    source_workfile: Path,
    *,
    subset: str | None,
) -> dict:
    parsed = manager.parse_work_file(source_workfile) or {}
    dcc = current_dcc_name()
    if dcc == "maya" and parsed.get("department") == "rig":
        return collect_maya_reference_dependency_info(asset, manager)
    if dcc != "houdini" or parsed.get("department") != "look":
        return {}
    sublayers = collect_houdini_sublayer_dependencies(asset, manager)
    model_dependency = next(
        (
            dependency
            for dependency in sublayers
            if dependency.get("publish_type") == "model"
            and (not subset or dependency.get("subset") == subset)
        ),
        None,
    )
    data: dict = {
        "dependencies": {
            "sublayers": sublayers,
        },
        "validation": {"issues": [], "status": "ok"},
    }
    if model_dependency:
        data["model_dependency"] = model_dependency
        data["dependencies"]["model"] = model_dependency
    return data


def collect_maya_reference_dependency_info(asset: Asset, manager: AssetManager) -> dict:
    try:
        import maya.cmds as cmds
    except ImportError:
        return {}

    references: list[dict] = []
    seen: set[str] = set()
    for ref_node in cmds.ls(type="reference") or []:
        if ref_node == "sharedReferenceNode":
            continue
        try:
            ref_path = cmds.referenceQuery(ref_node, filename=True, withoutCopyNumber=True)
            namespace = cmds.referenceQuery(ref_node, namespace=True).lstrip(":")
        except Exception:
            continue
        dependency = dependency_from_publish_path(asset, manager, Path(ref_path))
        if not dependency:
            continue
        dependency["namespace"] = namespace
        dependency["reference_node"] = ref_node
        key = dependency.get("path", "")
        if key in seen:
            continue
        references.append(dependency)
        seen.add(key)

    model_dependency = next(
        (
            dependency
            for dependency in references
            if dependency.get("publish_type") == "model"
        ),
        None,
    )
    data: dict = {
        "dependencies": {
            "references": references,
        },
        "validation": {"issues": [], "status": "ok"},
    }
    if model_dependency:
        data["model_dependency"] = model_dependency
        data["dependencies"]["model"] = model_dependency
    else:
        data["validation"] = {
            "issues": ["Rig publish has no model publish reference dependency."],
            "status": "warning",
        }
    return data


def collect_houdini_sublayer_dependencies(asset: Asset, manager: AssetManager) -> list[dict]:
    try:
        import hou
    except ImportError:
        return []

    dependencies: list[dict] = []
    seen: set[str] = set()
    stage = hou.node("/stage")
    if stage is None:
        return dependencies
    for node in stage.allSubChildren():
        if node.type().name() != "sublayer":
            continue
        for file_path in _houdini_node_file_paths(node):
            dependency = dependency_from_publish_path(asset, manager, Path(file_path))
            if not dependency:
                continue
            key = dependency.get("path", "")
            if key in seen:
                continue
            dependencies.append(dependency)
            seen.add(key)
    return dependencies


def _houdini_node_file_paths(node) -> list[str]:
    paths: list[str] = []
    for parm in node.parms():
        try:
            value = parm.eval()
        except Exception:
            continue
        if not isinstance(value, str):
            continue
        if not value.lower().endswith((".usd", ".usda", ".usdc")):
            continue
        paths.append(value.replace("\\", "/"))
    return paths


def dependency_from_publish_path(asset: Asset, manager: AssetManager, path: Path) -> dict | None:
    relative = None
    source_variant = "default"
    for variant in manager.asset_variants(asset):
        try:
            relative = path.relative_to(asset.variant_root(variant))
            source_variant = variant
            break
        except ValueError:
            continue
    if relative is None:
        try:
            relative = path.relative_to(asset.root)
        except ValueError:
            return None
    parts = relative.parts
    if len(parts) < 5 or parts[0] != "publish":
        return None
    publish_type, subset, version = parts[1], parts[2], parts[3]
    publish_json = path.parent / "publish.json"
    record = read_json_file(publish_json, {}) if publish_json.exists() else {}
    return {
        "asset": asset.name,
        "variant": record.get("variant") or source_variant,
        "publish_type": publish_type,
        "department": publish_type,
        "subset": subset,
        "version": version,
        "file": path.name,
        "path": _relative_to_project_path(manager, path),
    }


def merge_publish_dependency_info(base: dict, dependency_info: dict) -> dict:
    if not dependency_info:
        return base
    merged = dict(base or {})
    dependencies = dict(merged.get("dependencies") or {})
    dependencies.update(dependency_info.get("dependencies") or {})
    merged["dependencies"] = dependencies
    if dependency_info.get("model_dependency"):
        merged["model_dependency"] = dependency_info["model_dependency"]
    validation = dict(merged.get("validation") or {})
    dependency_validation = dependency_info.get("validation") or {}
    issues = list(validation.get("issues") or [])
    issues.extend(dependency_validation.get("issues") or [])
    validation["issues"] = issues
    validation["status"] = "warning" if issues else validation.get("status") or dependency_validation.get("status") or "ok"
    merged["validation"] = validation
    return merged


def _relative_to_project_path(manager: AssetManager, path: Path) -> str:
    try:
        return path.relative_to(manager.project_root).as_posix()
    except ValueError:
        return path.as_posix()


def _mesh_transform_leaf_names(cmds, namespace: str = "") -> set[str]:
    names = set()
    if namespace:
        transforms = cmds.ls(f"{namespace}:*", type="transform", long=True) or []
    else:
        transforms = [
            node for node in (cmds.ls(assemblies=True, long=True) or [])
            if ":" not in node.split("|")[-1]
        ]
    for node in transforms:
        shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
        if any(cmds.nodeType(shape) == "mesh" for shape in shapes):
            names.add(node.split("|")[-1].split(":")[-1])
    return names


def _mesh_leaf_names_under_top(cmds, top: str | None) -> set[str]:
    if not top:
        return set()
    names = set()
    transforms = [top] + (cmds.listRelatives(top, allDescendents=True, type="transform", fullPath=True) or [])
    for node in transforms:
        shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
        if any(cmds.nodeType(shape) == "mesh" for shape in shapes):
            names.add(_node_leaf_name(node))
    return names


def export_current_scene_for_publish(
    target: str | os.PathLike[str],
    publish_format: str,
    source_workfile: str | os.PathLike[str],
    *,
    export_root: str | None = None,
) -> Path:
    clean_format = publish_format.lower().lstrip(".")
    target_path = Path(target)
    try:
        import maya.cmds as cmds
    except ImportError:
        cmds = None

    ensure_current_dcc_scene_matches(source_workfile)

    if cmds is None:
        return export_current_houdini_scene_for_publish(target_path, clean_format)

    if clean_format == "usd":
        if not cmds.pluginInfo("mayaUsdPlugin", query=True, loaded=True):
            cmds.loadPlugin("mayaUsdPlugin")
        if export_root:
            if not cmds.objExists(export_root):
                raise RuntimeError(f"USD export root was not found: {export_root}")
            selection = cmds.ls(selection=True, long=True) or []
            try:
                cmds.select(export_root, replace=True)
                cmds.file(
                    str(target_path),
                    force=True,
                    options=";",
                    type="USD Export",
                    exportSelected=True,
                )
            finally:
                if selection:
                    cmds.select(selection, replace=True)
                else:
                    cmds.select(clear=True)
        else:
            cmds.file(
                str(target_path),
                force=True,
                options=";",
                type="USD Export",
                exportAll=True,
            )
    elif clean_format in {"ma", "mb"}:
        if not export_root:
            raise RuntimeError("Maya scene publish export requires an export root.")
        if not cmds.objExists(export_root):
            raise RuntimeError(f"Maya export root was not found: {export_root}")
        selection = cmds.ls(selection=True, long=True) or []
        try:
            cmds.select(export_root, replace=True)
            scene_type = "mayaBinary" if clean_format == "mb" else "mayaAscii"
            cmds.file(
                str(target_path),
                force=True,
                options="v=0;",
                type=scene_type,
                exportSelected=True,
            )
        finally:
            if selection:
                cmds.select(selection, replace=True)
            else:
                cmds.select(clear=True)
    elif clean_format == "fbx":
        if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            cmds.loadPlugin("fbxmaya")
        cmds.file(
            str(target_path),
            force=True,
            options="v=0;",
            type="FBX export",
            exportAll=True,
        )
    elif clean_format == "abc":
        if not cmds.pluginInfo("AbcExport", query=True, loaded=True):
            cmds.loadPlugin("AbcExport")
        frame = int(cmds.currentTime(query=True))
        roots = cmds.ls(assemblies=True, long=True) or []
        root_args = " ".join(f'-root "{node}"' for node in roots)
        job = f'-frameRange {frame} {frame} {root_args} -file "{target_path.as_posix()}"'
        cmds.AbcExport(j=job)
    else:
        raise RuntimeError(f"Unsupported publish format: {publish_format}")
    return target_path


def export_current_houdini_scene_for_publish(target_path: Path, publish_format: str) -> Path:
    try:
        import hou
    except ImportError as exc:
        raise RuntimeError(f"{publish_format} publish export is available inside Maya or Houdini.") from exc

    if publish_format != "usd":
        raise RuntimeError(f"Unsupported Houdini publish format: {publish_format}")

    stage_root = hou.node("/stage")
    if stage_root is None:
        raise RuntimeError("Houdini USD publish requires a Solaris /stage network.")
    lop = stage_root.displayNode()
    if lop is None:
        children = stage_root.children()
        lop = children[-1] if children else None
    if lop is None:
        raise RuntimeError("Houdini USD publish requires at least one LOP node under /stage.")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    stage = lop.stage()
    if stage is None:
        raise RuntimeError(f"Could not cook USD stage from {lop.path()}.")
    stage.Export(str(target_path).replace("\\", "/"))
    return target_path


def collect_scene_info() -> dict:
    try:
        from smartlib.dcc.maya.scene_info import collect_scene_info as collect_maya_scene_info

        return collect_maya_scene_info()
    except Exception:
        pass

    try:
        import maya.cmds as cmds
    except ImportError:
        return {}

    renderer = cmds.getAttr("defaultRenderGlobals.currentRenderer") if cmds.objExists("defaultRenderGlobals") else ""
    cameras = []
    default_cameras = {"persp", "top", "front", "side"}
    for shape in cmds.ls(type="camera") or []:
        try:
            if cmds.getAttr(f"{shape}.renderable"):
                parent = cmds.listRelatives(shape, parent=True, fullPath=False) or [shape]
                camera_name = parent[0].split("|")[-1]
                if camera_name not in default_cameras:
                    cameras.append(camera_name)
        except Exception:
            continue

    layers = []
    for layer in cmds.ls(type="renderLayer") or []:
        if layer.split("|")[-1].split(":")[-1] != "defaultRenderLayer":
            layers.append(layer)

    references = []
    for ref_node in cmds.ls(type="reference") or []:
        if ref_node == "sharedReferenceNode":
            continue
        try:
            namespace = cmds.referenceQuery(ref_node, namespace=True)
            references.append(namespace.lstrip(":"))
        except Exception:
            continue

    width = int(cmds.getAttr("defaultResolution.width")) if cmds.objExists("defaultResolution") else 0
    height = int(cmds.getAttr("defaultResolution.height")) if cmds.objExists("defaultResolution") else 0

    return {
        "unit": cmds.currentUnit(query=True, linear=True),
        "rendersize": [width, height],
        "renderer": renderer,
        "timerange": [
            float(cmds.playbackOptions(query=True, minTime=True)),
            float(cmds.playbackOptions(query=True, maxTime=True)),
        ],
        "cameras": sorted(set(cameras)),
        "layers": sorted(set(layers)),
        "references": sorted(set(references)),
    }


def export_selected_geo_data(
    asset: Asset,
    manager: AssetManager,
    variant: str,
    subset: str,
    data_format: str,
    comment: str = "",
    *,
    target: str = "body",
) -> list[Path]:
    try:
        import maya.cmds as cmds
    except ImportError:
        raise RuntimeError("Geo data export is available inside Maya.")

    selection = cmds.ls(selection=True, long=True) or []
    if not selection:
        raise RuntimeError("Select mesh objects to export.")

    variant = variant or "default"
    subset = subset or "hires"
    target = target.strip().replace("\\", "/").strip("/") or "body"
    clean_format = data_format.lower().lstrip(".")
    if clean_format != "fbx":
        raise RuntimeError(f"Unsupported geo data format: {data_format}")
    version = manager.next_data_version(
        asset,
        department=f"geo/{target}",
        variant=variant,
        subset=subset,
    )
    data_path = manager.data_version_dir(
        asset,
        department=f"geo/{target}",
        variant=variant,
        subset=subset,
        version=version,
    ) / f"geo.{clean_format}"

    data_path.parent.mkdir(parents=True, exist_ok=True)
    cmds.select(selection, replace=True)

    if clean_format == "fbx":
        if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            cmds.loadPlugin("fbxmaya")
        cmds.file(
            str(data_path),
            force=True,
            options="v=0;",
            type="FBX export",
            exportSelected=True,
        )
    source_workfile = cmds.file(query=True, sceneName=True) or ""
    manager.register_data_export(
        asset,
        department=f"geo/{target}",
        variant=variant,
        subset=subset,
        version=version,
        files={clean_format: data_path.name},
        source_workfile=source_workfile,
        comment=comment,
    )
    return [data_path]


def ingest_model_fbx_data(
    asset: Asset,
    manager: AssetManager,
    variant: str,
    subset: str,
    source: str | os.PathLike[str],
    comment: str = "",
) -> Path:
    source_path = Path(source)
    if source_path.suffix.lower() != ".fbx":
        raise RuntimeError("Only FBX files can be ingested as model data.")
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    variant = variant or "default"
    subset = subset or "hires"
    data_format = "fbx"
    version = manager.next_data_version(
        asset,
        department="model",
        variant=variant,
        subset=subset,
        data_format=data_format,
    )
    version_dir = manager.data_version_dir(
        asset,
        department="model",
        variant=variant,
        subset=subset,
        data_format=data_format,
        version=version,
    )
    target_path = version_dir / "model.fbx"
    version_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)

    manager.register_data_export(
        asset,
        department="model",
        variant=variant,
        subset=subset,
        data_format=data_format,
        version=version,
        files={data_format: target_path.name},
        source_workfile=source_path,
        comment=comment,
    )
    source_data = {
        "source_type": "external_fbx",
        "received_from": "external",
        "original_file": str(source_path).replace("\\", "/"),
        "ingested_at": datetime.now().isoformat(timespec="seconds"),
        "asset": asset.name,
        "variant": variant,
        "subset": subset,
        "format": data_format,
        "comment": comment,
    }
    with (version_dir / "source.json").open("w", encoding="utf-8") as f:
        json.dump(source_data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return target_path


def import_assembly_data(
    asset: Asset,
    manager: AssetManager,
    variant: str,
    source: str | os.PathLike[str],
    comment: str = "",
) -> Path:
    source_path = Path(source)
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    if not source_path.is_file():
        raise RuntimeError("Import Assembly currently expects a single file.")

    variant = variant or "default"
    base_dir = asset.variant_root(variant) / "data" / "assembly" / "client"
    version_label = _next_folder_version_label(base_dir)
    version_dir = base_dir / version_label
    version_dir.mkdir(parents=True, exist_ok=True)

    target_path = version_dir / source_path.name
    shutil.copy2(source_path, target_path)

    manifest = {
        "asset": asset.name,
        "category": asset.category,
        "group": asset.group,
        "variant": variant,
        "data_type": "assembly",
        "subset": "client",
        "version": version_label,
        "files": {
            "assembly": target_path.name,
        },
        "source_file": str(source_path).replace("\\", "/"),
        "imported_at": datetime.now().isoformat(timespec="seconds"),
        "comment": comment,
    }
    write_json_file(version_dir / "manifest.json", manifest)
    write_json_file(
        base_dir / "latest.json",
        {
            "version": version_label,
            "path": f"{version_label}/{target_path.name}",
            "manifest": f"{version_label}/manifest.json",
        },
    )
    _update_versions_json_with_comment(base_dir / "versions.json", version_label, comment)
    return target_path


def publish_client_assembly(
    asset: Asset,
    manager: AssetManager,
    variant: str,
    comment: str = "",
    *,
    context_name: str = "work",
    context_version: str = "",
) -> Path:
    variant = variant or "default"
    data_base = asset.variant_root(variant) / "data" / "assembly" / "client"
    source_path = _latest_data_assembly_file(data_base)
    if source_path is None:
        raise RuntimeError("Imported assembly data was not found. Use Data > Import Assembly first.")
    if source_path.suffix.lower() not in {".ma", ".mb"}:
        raise RuntimeError(
            "Publish Assembly Client for shot staging requires a Maya scene (.ma or .mb). "
            f"Imported file was: {source_path.name}"
        )

    source_manifest = read_json_file(source_path.parent / "manifest.json", {}) or {}
    context_label = str(context_name or "work").strip()
    context_slug = re.sub(r"[^a-z0-9_-]+", "_", context_label.lower()).strip("_")
    if not context_slug:
        raise RuntimeError("A valid Context is required for Publish Assembly Client.")
    publish_base = asset.variant_root(variant) / "publish" / "asset" / context_slug
    version_label = _next_folder_version_label(publish_base)
    version_dir = publish_base / version_label
    version_dir.mkdir(parents=True, exist_ok=True)

    target_name = f"{asset.name}{source_path.suffix.lower()}"
    target_path = version_dir / target_name
    shutil.copy2(source_path, target_path)

    build_manifest = {
        "asset": asset.name,
        "category": asset.category,
        "group": asset.group,
        "variant": variant,
        "context": {
            "name": context_slug,
            "version": context_version,
            "quality_profile": context_label,
            "source": "client_assembly",
        },
        "resolved_representations": [],
        "source_data": _relative_to_project_path(manager, source_path),
        "source_manifest": _relative_to_project_path(manager, source_path.parent / "manifest.json"),
        "source": source_manifest,
        "validation": {
            "status": "OK",
            "errors": [],
        },
    }
    write_json_file(version_dir / "build_manifest.json", build_manifest)

    scene_key = source_path.suffix.lower().lstrip(".")
    publish_data = {
        "asset": asset.name,
        "category": asset.category,
        "group": asset.group,
        "variant": variant,
        "publish_type": "asset",
        "subset": context_slug,
        "version": version_label,
        "files": {
            scene_key: target_path.name,
            "build_manifest": "build_manifest.json",
        },
        "context": build_manifest["context"],
        "composition": {
            "mode": "client_assembly_snapshot",
            "client_assembly": _relative_to_project_path(manager, source_path),
        },
        "source_data": _relative_to_project_path(manager, source_path),
        "source_manifest": _relative_to_project_path(manager, source_path.parent / "manifest.json"),
        "source": source_manifest,
        "published_at": datetime.now().isoformat(timespec="seconds"),
        "comment": comment,
    }
    write_json_file(version_dir / "publish.json", publish_data)
    write_json_file(
        publish_base / "latest.json",
        {
            "version": version_label,
            "path": f"{version_label}/{target_path.name}",
            "publish": f"{version_label}/publish.json",
        },
    )
    _update_versions_json_with_comment(publish_base / "versions.json", version_label, comment)
    return target_path


def _latest_data_assembly_file(base_dir: Path) -> Path | None:
    latest = read_json_file(base_dir / "latest.json", {}) or {}
    raw_path = latest.get("path")
    if raw_path:
        candidate = base_dir / str(raw_path)
        if candidate.exists() and candidate.is_file():
            return candidate

    version_dirs = _version_dirs(base_dir)
    if not version_dirs:
        return None
    newest = version_dirs[-1]
    manifest = read_json_file(newest / "manifest.json", {}) or {}
    assembly_name = ((manifest.get("files") or {}).get("assembly") or "").strip()
    if assembly_name:
        candidate = newest / assembly_name
        if candidate.exists() and candidate.is_file():
            return candidate

    for candidate in newest.iterdir():
        if candidate.is_file() and candidate.name not in {"manifest.json", "latest.json", "versions.json"}:
            return candidate
    return None


def _next_folder_version_label(base_dir: Path) -> str:
    versions = [
        int(path.name[1:])
        for path in _version_dirs(base_dir)
        if path.name[1:].isdigit()
    ]
    return f"v{(max(versions) if versions else 0) + 1:03d}"


def _version_dirs(base_dir: Path) -> list[Path]:
    if not base_dir.exists():
        return []
    return sorted(
        [
            path
            for path in base_dir.iterdir()
            if path.is_dir() and path.name.lower().startswith("v") and path.name[1:].isdigit()
        ],
        key=lambda path: int(path.name[1:]),
    )


def _update_versions_json_with_comment(path: Path, version_label: str, comment: str = "") -> None:
    versions = read_json_file(path, []) or []
    next_versions = []
    seen = False
    for item in versions:
        if not isinstance(item, dict):
            continue
        item = dict(item)
        if item.get("version") == version_label:
            item["status"] = "latest"
            if comment:
                item["comment"] = comment
            seen = True
        elif item.get("status") == "latest":
            item["status"] = "available"
        next_versions.append(item)
    if not seen:
        item = {"version": version_label, "status": "latest"}
        if comment:
            item["comment"] = comment
        next_versions.append(item)
    write_json_file(path, next_versions)


def export_mgear_guide(asset: Asset, manager: AssetManager, variant: str = "default", subset: str = "guide") -> Path:
    try:
        import maya.cmds as cmds
    except ImportError:
        raise RuntimeError("mGear guide export is available inside Maya.")

    path = manager.next_data_version_path(
        asset,
        department="guide",
        variant=variant,
        subset=subset,
        ext="sgt",
        name="guide",
    )
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        from mgear.shifter import io as shifter_io
    except ImportError:
        raise RuntimeError("mGear shifter io module was not found in this Maya session.")

    selection = cmds.ls(selection=True) or []
    guide_root = selection[0] if selection else None
    try:
        if guide_root:
            shifter_io.export_guide_template(str(path), guide_root)
        else:
            shifter_io.export_guide_template(str(path))
    except AttributeError:
        raise RuntimeError("mGear guide export API was not found. Check your mGear version.")
    source_workfile = cmds.file(query=True, sceneName=True) or ""
    manager.register_data_export(
        asset,
        department="guide",
        variant=variant,
        subset=subset,
        version=path.parent.name,
        files={"sgt": path.name},
        source_workfile=source_workfile,
    )
    return path


def export_mgear_skin(asset: Asset, manager: AssetManager, variant: str, subset: str) -> Path:
    try:
        import maya.cmds as cmds
    except ImportError:
        raise RuntimeError("mGear skin export is available inside Maya.")

    version = manager.next_data_version(asset, department="skin", variant=variant, subset=subset)
    subset_name = subset.replace("\\", "/").strip("/").replace("/", "_")
    path = manager.data_file_path(
        asset,
        department="skin",
        variant=variant,
        subset=subset,
        version=version,
        ext="gSkinPack",
        name=f"{asset.name}_{subset_name}",
    )
    path.parent.mkdir(parents=True, exist_ok=True)

    selection = cmds.ls(selection=True, long=True) or []
    if not selection:
        raise RuntimeError("Select skinned meshes to export.")

    try:
        from mgear.core import skin
    except ImportError:
        raise RuntimeError("mGear skin module was not found in this Maya session.")

    exported = False
    for candidate in ("exportSkinPack", "exportSkin", "exportSkinPackBinary"):
        exporter = getattr(skin, candidate, None)
        if exporter:
            try:
                exporter(str(path), selection)
            except TypeError:
                exporter(selection, str(path))
            exported = True
            break

    if not exported:
        raise RuntimeError("mGear skin export API was not found. Check your mGear version.")

    source_workfile = cmds.file(query=True, sceneName=True) or ""
    manager.register_data_export(
        asset,
        department="skin",
        variant=variant,
        subset=subset,
        version=path.parent.name,
        files={"gSkinPack": path.name},
        source_workfile=source_workfile,
    )
    return path


def open_scene_in_current_dcc(path: str | os.PathLike[str]) -> None:
    file_path = str(Path(path))
    try:
        import maya.cmds as cmds

        if cmds.file(query=True, modified=True):
            result = cmds.confirmDialog(
                title="Open Scene",
                message="Current scene has unsaved changes. Open selected scene?",
                button=["Open", "Cancel"],
                defaultButton="Open",
                cancelButton="Cancel",
                dismissString="Cancel",
            )
            if result != "Open":
                return
        cmds.file(file_path, open=True, force=True)
        return
    except ImportError:
        pass

    try:
        import hou

        if hou.hipFile.hasUnsavedChanges():
            result = hou.ui.displayMessage(
                "Current scene has unsaved changes. Open selected scene?",
                buttons=("Open", "Cancel"),
                default_choice=0,
                close_choice=1,
            )
            if result != 0:
                return
        hou.hipFile.load(file_path, suppress_save_prompt=True)
        return
    except ImportError:
        pass

    raise RuntimeError("Open Scene is available inside Maya or Houdini.")


def reference_file_to_current_dcc(
    path: str | os.PathLike[str],
    namespace: str | None = None,
    parent_group: str | None = None,
) -> None:
    file_path = str(Path(path))
    try:
        import maya.cmds as cmds

        namespace = namespace or Path(file_path).stem
        namespace = namespace.replace(".", "_").replace("-", "_")
        new_nodes = cmds.file(
            file_path,
            reference=True,
            ignoreVersion=True,
            mergeNamespacesOnClash=False,
            namespace=namespace,
            returnNewNodes=True,
        )
        if parent_group:
            _parent_new_transform_roots(cmds, new_nodes or [], parent_group)
        return
    except ImportError:
        pass

    try:
        import hou

        ext = Path(file_path).suffix.lower()
        if ext in {".hip", ".hiplc", ".hipnc"}:
            hou.hipFile.merge(file_path)
        else:
            obj = hou.node("/obj") or hou.node("/")
            geo = obj.createNode("geo", node_name=Path(file_path).stem)
            file_sop = geo.createNode("file")
            file_sop.parm("file").set(file_path)
            geo.layoutChildren()
        return
    except ImportError:
        pass

    raise RuntimeError("Reference is available inside Maya or Houdini.")


def _parent_new_transform_roots(cmds, nodes: list[str], parent_group: str) -> list[str]:
    if not parent_group or not cmds.objExists(parent_group):
        return []
    long_nodes = set(cmds.ls(nodes, long=True) or nodes)
    roots = []
    for node in nodes:
        if not node or not cmds.objExists(node):
            continue
        try:
            if cmds.nodeType(node) != "transform":
                continue
        except Exception:
            continue
        long_node = (cmds.ls(node, long=True) or [node])[0]
        parents = cmds.listRelatives(long_node, parent=True, fullPath=True) or []
        if parents and parents[0] in long_nodes:
            continue
        if long_node == parent_group or long_node.startswith(f"{parent_group}|"):
            continue
        roots.append(long_node)
    parented = []
    for root in roots:
        if not cmds.objExists(root):
            continue
        try:
            result = cmds.parent(root, parent_group)
            parented.extend(result or [root])
        except Exception:
            pass
    return parented


def import_file_to_current_dcc(path: str | os.PathLike[str]) -> None:
    file_path = str(Path(path))
    try:
        import maya.cmds as cmds

        namespace = Path(file_path).stem.replace(".", "_").replace("-", "_")
        cmds.file(
            file_path,
            i=True,
            ignoreVersion=True,
            mergeNamespacesOnClash=False,
            namespace=namespace,
        )
        return
    except ImportError:
        pass

    try:
        import hou

        ext = Path(file_path).suffix.lower()
        if ext in {".hip", ".hiplc", ".hipnc"}:
            hou.hipFile.merge(file_path)
        else:
            obj = hou.node("/obj") or hou.node("/")
            geo = obj.createNode("geo", node_name=Path(file_path).stem)
            file_sop = geo.createNode("file")
            file_sop.parm("file").set(file_path)
            geo.layoutChildren()
        return
    except ImportError:
        pass

    raise RuntimeError("This import action is available inside Maya or Houdini.")


def import_data_file_to_current_dcc(path: str | os.PathLike[str]) -> None:
    file_path = str(Path(path))
    ext = Path(file_path).suffix.lower()

    if ext in {".fbx", ".abc", ".usd", ".ma", ".mb"}:
        import_file_to_current_dcc(file_path)
        return

    try:
        import maya.cmds as cmds
    except ImportError:
        raise RuntimeError("Data import is available inside Maya for this file type.")

    if ext == ".sgt":
        try:
            from mgear.shifter import io as shifter_io
        except ImportError:
            raise RuntimeError("mGear shifter io module was not found in this Maya session.")
        for candidate in ("import_guide_template", "importGuideTemplate"):
            importer = getattr(shifter_io, candidate, None)
            if importer:
                importer(file_path)
                return
        raise RuntimeError("mGear guide import API was not found. Check your mGear version.")

    if ext == ".gskinpack":
        try:
            from mgear.core import skin
        except ImportError:
            raise RuntimeError("mGear skin module was not found in this Maya session.")
        selection = cmds.ls(selection=True, long=True) or []
        for candidate in ("importSkinPack", "importSkin", "importSkinPackBinary"):
            importer = getattr(skin, candidate, None)
            if importer:
                try:
                    importer(file_path, selection)
                except TypeError:
                    importer(file_path)
                return
        raise RuntimeError("mGear skin import API was not found. Check your mGear version.")

    raise RuntimeError(f"Unsupported data file type: {ext}")


def _unique_cast_targets(targets: list[dict]) -> list[dict]:
    unique = []
    seen = set()
    for target in targets:
        code = target.get("code")
        if not code or code in seen:
            continue
        seen.add(code)
        unique.append(target)
    return unique


_WINDOW = None


def show(parent=None) -> AssetManagerWindow:
    global _WINDOW
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    _ensure_smartlib_on_path()
    from smartlib.core.qt import parent_for_maya

    window_parent = parent_for_maya(QtWidgets, parent)
    _WINDOW = AssetManagerWindow(parent=window_parent)
    if window_parent is not None:
        _WINDOW.setWindowFlags(_WINDOW.windowFlags() | QtCore.Qt.Window)
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    return _WINDOW


if __name__ == "__main__":
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = show()
    app.exec()

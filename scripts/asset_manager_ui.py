from __future__ import annotations

import os
import sys
import json
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
        settings.setValue("asset_view", "table" if self.asset_list.viewMode() == QtWidgets.QListView.ListMode else "card")
        settings.setValue("shot_codes", [target["code"] for target in self._selected_cast_targets()])
        settings.setValue("main_splitter", self.main_splitter.saveState())
        settings.setValue("asset_browser_splitter", self.asset_browser_splitter.saveState())
        settings.setValue("detail_content_splitter", self.detail_content_splitter.saveState())
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
        asset_view = str(settings.value("asset_view", "card") or "card")
        shot_codes = set(self._settings_string_list(settings.value("shot_codes")))
        main_splitter = settings.value("main_splitter")
        asset_browser_splitter = settings.value("asset_browser_splitter")
        detail_content_splitter = settings.value("detail_content_splitter")
        settings.endGroup()

        self.search_edit.blockSignals(True)
        self.search_edit.setText(search_text)
        self.search_edit.blockSignals(False)
        if len(asset_filter) == 3:
            self._select_asset_filter(tuple(asset_filter))
        selected_key = tuple(asset_key) if len(asset_key) == 3 else None
        self._apply_filter(selected_key=selected_key)
        self._restore_detail_selection(asset_variant, department, subset)
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
        self.add_assets_to_cast_btn = QtWidgets.QPushButton("Add Selected to Cast")
        self.add_assets_to_cast_btn.setToolTip("Add the selected assets to each selected shot cast.")
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
        self.create_variant_btn = QtWidgets.QPushButton("Create Variant")
        self.asset_card_btn = QtWidgets.QPushButton("Card")
        self.asset_table_btn = QtWidgets.QPushButton("Table")
        asset_view_layout.addWidget(self.create_asset_btn)
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

        self.detail_content_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        right_layout.addWidget(self.detail_content_splitter, 1)

        selector_panel = QtWidgets.QWidget()
        selector_layout = QtWidgets.QVBoxLayout(selector_panel)
        selector_layout.setContentsMargins(2, 2, 2, 2)
        selector_layout.setSpacing(4)
        self.asset_variant_header_label.setVisible(False)
        self.asset_variant_combo.setVisible(False)

        selector_layout.addWidget(self.back_to_assets_btn)
        selector_layout.addWidget(QtWidgets.QLabel("Variant"))
        self.asset_variant_list = QtWidgets.QListWidget()
        self.asset_variant_list.setMinimumHeight(80)
        self.asset_variant_list.setStyleSheet("QListWidget::item { height: 22px; }")
        selector_layout.addWidget(self.asset_variant_list)

        selector_layout.addWidget(QtWidgets.QLabel("dept"))
        self.dept_list = QtWidgets.QListWidget()
        self.dept_list.setMinimumHeight(90)
        self.dept_list.setStyleSheet("QListWidget::item { height: 22px; }")
        for dept in self.manager.asset_depts:
            self.dept_list.addItem(dept)
        if not self.manager.asset_depts:
            self.dept_list.addItem("model")
        self.dept_list.setCurrentRow(0)
        selector_layout.addWidget(self.dept_list)

        selector_layout.addWidget(QtWidgets.QLabel("Subset"))
        self.variant_list = QtWidgets.QListWidget()
        self.variant_list.setMinimumHeight(90)
        self.variant_list.setStyleSheet("QListWidget::item { height: 22px; }")
        selector_layout.addWidget(self.variant_list)
        self.staging_btn = QtWidgets.QPushButton("Stageing")
        self.staging_btn.setStyleSheet(
            "QPushButton { background-color: #5d4f85; color: white; font-weight: bold; }"
            "QPushButton:hover { background-color: #6e60a0; }"
            "QPushButton:disabled { background-color: #4e4b57; color: #b8b8b8; }"
        )
        selector_layout.addWidget(self.staging_btn)
        selector_layout.addStretch(1)
        self.detail_content_splitter.addWidget(selector_panel)

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
        self.detail_content_splitter.setStretchFactor(0, 0)
        self.detail_content_splitter.setStretchFactor(1, 1)
        self.detail_content_splitter.setStretchFactor(2, 0)

        work_tab = QtWidgets.QWidget()
        self.work_tab = work_tab
        work_layout = QtWidgets.QVBoxLayout(work_tab)
        work_layout.setContentsMargins(4, 4, 4, 4)
        work_layout.setSpacing(4)
        self.dept_tabs = QtWidgets.QTabBar()
        self.dept_tabs.setExpanding(False)
        for dept in self.manager.asset_depts:
            self.dept_tabs.addTab(dept)
        if not self.manager.asset_depts:
            self.dept_tabs.addTab("model")
        self.dept_tabs.setVisible(False)


        self.dependency_label = QtWidgets.QLabel("")
        self.dependency_label.setVisible(False)

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
        self.reference_btn = QtWidgets.QPushButton("REFERENCE")
        self.save_scene_btn = QtWidgets.QPushButton("SAVE")
        self.publish_btn = QtWidgets.QPushButton("Publish")
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
        self.reference_btn.setStyleSheet(green_button_style)
        self.save_scene_btn.setStyleSheet(blue_button_style)
        self.publish_btn.setStyleSheet(blue_button_style)
        button_grid.addWidget(self.open_scene_btn, 0, 0)
        button_grid.addWidget(self.reference_btn, 0, 1, 1, 2)
        button_grid.addWidget(self.save_scene_btn, 1, 0)
        button_grid.addWidget(self.publish_btn, 1, 1, 1, 2)
        work_layout.addLayout(button_grid)
        self.detail_tabs.addTab(work_tab, "Work Scene")

        data_tab = QtWidgets.QWidget()
        self.data_tab = data_tab
        data_layout = QtWidgets.QVBoxLayout(data_tab)
        data_layout.setContentsMargins(4, 4, 4, 4)
        data_layout.setSpacing(4)
        self.data_list = QtWidgets.QTreeWidget()
        self.data_list.setHeaderLabels(["Name", "Version", "Date", "Comment"])
        self.data_list.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.data_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        data_layout.addWidget(self.data_list)
        data_buttons = QtWidgets.QHBoxLayout()
        data_buttons.setContentsMargins(0, 0, 0, 0)
        data_buttons.setSpacing(4)
        self.export_mesh_btn = QtWidgets.QPushButton("Export Mesh")
        self.export_guide_btn = QtWidgets.QPushButton("Export Guide")
        self.export_skin_btn = QtWidgets.QPushButton("Export Skin")
        self.import_data_btn = QtWidgets.QPushButton("Import")
        data_buttons.addStretch(1)
        data_buttons.addWidget(self.export_mesh_btn)
        data_buttons.addWidget(self.export_guide_btn)
        data_buttons.addWidget(self.export_skin_btn)
        data_buttons.addWidget(self.import_data_btn)
        data_layout.addLayout(data_buttons)
        self.detail_tabs.addTab(data_tab, "Data")

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
        self.open_preview_rv_btn = QtWidgets.QPushButton("Open Package in RV")
        self.open_preview_rv_btn.setStyleSheet(green_button_style)
        preview_buttons.addStretch(1)
        preview_buttons.addWidget(self.quick_preview_btn)
        preview_buttons.addWidget(self.open_preview_rv_btn)
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
        self.context_assemble_btn = QtWidgets.QPushButton("Assemble")
        self.context_pack_btn = QtWidgets.QPushButton("Pack")
        self.context_pack_btn.setEnabled(False)
        context_header.addStretch(1)
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
        self.asset_list.itemDoubleClicked.connect(lambda _item: self._show_detail_mode())
        self.asset_card_btn.clicked.connect(self._set_asset_card_view)
        self.asset_table_btn.clicked.connect(self._set_asset_table_view)
        self.create_asset_btn.clicked.connect(self._create_asset)
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
        self.data_list.customContextMenuRequested.connect(self._show_data_context_menu)
        self.data_list.itemSelectionChanged.connect(self._update_selected_file_info)
        self.preview_list.itemSelectionChanged.connect(self._update_selected_file_info)
        self.preview_list.itemDoubleClicked.connect(lambda _item: self._open_selected_preview_in_rv())
        self.publish_list.customContextMenuRequested.connect(self._show_publish_context_menu)
        self.open_scene_btn.clicked.connect(self._open_selected_scene)
        self.reference_btn.clicked.connect(self._reference_latest_publish)
        self.save_scene_btn.clicked.connect(self._save_scene)
        self.publish_btn.clicked.connect(self._publish_selected_work)
        self.staging_btn.clicked.connect(self._stage_work_scene)
        self.quick_preview_btn.clicked.connect(self._quick_preview_setup)
        self.open_preview_rv_btn.clicked.connect(self._open_selected_preview_in_rv)
        self.context_version_combo.currentTextChanged.connect(self._populate_context_profiles)
        self.context_profile_list.currentRowChanged.connect(self._on_context_profile_selected)
        self.context_assemble_btn.clicked.connect(self._assemble_selected_asset_context)
        self.context_pack_btn.clicked.connect(self._pack_selected_asset_context)
        self.export_mesh_btn.clicked.connect(lambda: self._show_export_data_menu("mesh"))
        self.export_guide_btn.clicked.connect(lambda: self._show_export_data_menu("guide"))
        self.export_skin_btn.clicked.connect(lambda: self._show_export_data_menu("skin"))
        self.import_data_btn.clicked.connect(self._import_selected_data)
        self.context_assembly = None
        self.context_verification = None
        self._populate_context_versions()
        self._show_asset_mode()

    def refresh_assets(self, keep_selection: bool = True) -> None:
        selected_key = self._current_asset_key() if keep_selection else None
        selected_asset_variant = self._current_asset_variant() if keep_selection else "default"
        selected_department = self._current_department() if keep_selection else "model"
        selected_subset = self._current_variant() if keep_selection else ""
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
        if self.manager.last_asset_source == "spreadsheet":
            self.status_label.setText(f"{len(self.assets)} assets from spreadsheet")
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
        status = metadata.get("status") or "-"
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
        status = metadata.get("status", "")
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
        self._populate_asset_variants()
        self.work_list.setSortingEnabled(False)
        self.work_list.blockSignals(True)
        self.work_list.setRowCount(0)
        self.work_list.blockSignals(False)
        self.data_list.clear()
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
            department=department,
            variant=variant,
            subset=subset,
            extensions=["ma", "mb", "hip", "hiplc", "hipnc"],
        )
        if not work_files:
            self.status_label.setText(
                f"No work scenes found under: {self.manager.work_root_dir(asset, department=department, variant=variant, subset=subset or '')}"
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
        self._populate_preview_list(asset)
        self._populate_context_pack_tree()
        self._update_selected_file_info()

        for path in self.manager.list_publish_files(asset):
            item = QtWidgets.QListWidgetItem(path.relative_to(asset.root).as_posix())
            item.setData(QtCore.Qt.UserRole, str(path))
            self.publish_list.addItem(item)

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

    def _current_variant(self) -> str:
        item = self.variant_list.currentItem()
        if item:
            return item.text()
        variants = self.manager.work_subsets(self._current_department())
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
        base_dir = quick_preview_base_dir(asset, variant, department, subset)
        if not base_dir.exists():
            return
        preview_rows = []
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
            profiles = service.quality_profiles("asset", version)
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
                    maya_scene_builder = lambda source, target: write_context_asset_snapshot(
                        source,
                        target,
                        look_scenes=look_scenes,
                    )
                    maya_preview = open_context_asset_assembly
                except ImportError:
                    maya_scene_builder = None
                self.context_verification = service.write_assembly(
                    self.context_assembly,
                    maya_scene_builder=maya_scene_builder,
                )
                if maya_preview:
                    maya_preview(
                        self.context_verification.scene_path,
                        asset.name,
                        resolve_asset_work_template(self.manager, "model"),
                    )
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
                path = Path(str((entry.files or {}).get(key) or ""))
                if path.exists():
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
        try:
            service = _asset_context_service(self.manager.config_dir)
            packed = service.pack(self.context_assembly, assembled=self.context_verification)
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
        ignored = {"publish.json", "latest.json", "versions.json"}
        data_roots = [asset.data_dir]
        data_roots.extend(asset.variant_root(variant) / "data" for variant in self.manager.asset_variants(asset))
        files = [
            path for path in self.manager.list_data_files(asset)
            if path.name not in ignored and not path.name.endswith(".json")
        ]

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
        variants = self.manager.asset_variants(asset) if asset else ["default"]
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
        for index, variant in enumerate(self.manager.work_subsets(self._current_department())):
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
            self.asset_list.setCurrentItem(item)
        asset = self._current_asset()
        menu = QtWidgets.QMenu(self)
        create_asset = menu.addAction("Create Asset")
        create_variant = menu.addAction("Create Variant")
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
        send_to_shot_cast = menu.addAction("Send to Shot Cast")
        send_to_shot_cast.setEnabled(asset is not None)
        menu.addSeparator()
        create_folders = menu.addAction("Create Asset Folders")
        create_folders.setEnabled(asset is not None)
        set_thumbnail = menu.addAction("Set Thumbnail...")
        set_thumbnail.setEnabled(asset is not None)
        menu.addSeparator()
        copy_root = menu.addAction("Copy Asset Root")
        copy_data = menu.addAction("Copy Data Path")
        copy_work = menu.addAction("Copy Work Path")
        copy_publish = menu.addAction("Copy Publish Path")
        copy_root.setEnabled(asset is not None)
        copy_data.setEnabled(asset is not None)
        copy_work.setEnabled(asset is not None)
        copy_publish.setEnabled(asset is not None)
        action = menu.exec(self.asset_list.mapToGlobal(pos))
        if action == create_asset:
            self._create_asset()
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

    def _send_selected_asset_to_shot_cast(self, asset: Asset | None) -> None:
        if not asset:
            return
        try:
            _ensure_smartlib_on_path()
            from smartlib.core.config_loader import ProjectConfig
            from smartlib.core.selection_context import write_selected_asset

            metadata = self.manager.load_asset_metadata(asset)
            payload = {
                "asset": asset.name,
                "category": asset.category,
                "group": asset.group,
                "variant": self._current_asset_variant(),
                "asset_type": metadata.get("asset_type") or metadata.get("type") or asset.category,
                "root": str(asset.root),
            }
            path = write_selected_asset(ProjectConfig(self.manager.config_dir), payload)
            self.status_label.setText(f"Sent to Shot Cast: {asset.name} ({path})")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Send to Shot Cast Failed", str(exc))

    def _add_selected_assets_to_shot_cast(self) -> None:
        targets = self._selected_cast_targets()
        if not targets:
            QtWidgets.QMessageBox.information(self, "Add to Cast", "Select one or more shots or sequences in the Shot tree first.")
            return
        assets = self._selected_assets()
        if not assets:
            QtWidgets.QMessageBox.information(self, "Add to Cast", "Select one or more assets first.")
            return
        try:
            selections = []
            for asset in assets:
                metadata = self.manager.load_asset_metadata(asset)
                selections.append(
                    {
                        "asset": asset.name,
                        "category": asset.category,
                        "group": asset.group,
                        "variant": metadata.get("default_variant") or "default",
                        "asset_type": metadata.get("asset_type") or metadata.get("type") or asset.category,
                        "root": str(asset.root),
                    }
                )
            service = _shot_service(self.manager.config_dir)
            added_count = 0
            changed_targets = []
            for target in targets:
                if target.get("kind") == "sequence":
                    _cast_path, rows = service.add_asset_selections_to_sequence_cast(
                        target["episode"],
                        target["sequence"],
                        selections,
                    )
                    changed_targets.append(f"{target['episode']}/{target['sequence']}")
                else:
                    identity = target["identity"]
                    _cast_path, rows = service.add_asset_selections_to_cast(identity, selections)
                    changed_targets.append(identity.code)
                added_count += len(rows)
            target_label = ", ".join(changed_targets)
            self.status_label.setText(f"Added {added_count} cast entries to: {target_label}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Add to Cast Failed", str(exc))

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
        action = menu.exec(self.work_list.mapToGlobal(pos))
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
        action = menu.exec(QtGui.QCursor.pos())
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
        import_file = menu.addAction("Import")
        open_folder = menu.addAction("Open Folder")
        copy_path = menu.addAction("Copy Path")
        action = menu.exec(self.data_list.mapToGlobal(pos))
        if action == import_file:
            import_data_file_to_current_dcc(path)
        elif action == open_folder:
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
        action = menu.exec(self.publish_list.mapToGlobal(pos))
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

    def _save_scene(self) -> None:
        asset = self._current_asset()
        if not asset:
            self.status_label.setText("Select an asset first")
            return

        selected_path = self._selected_work_path()
        department = self._current_department()

        if not selected_path:
            target = self.manager.next_work_take_path(
                asset,
                department=department,
                variant=self._work_variant_arg(asset),
                subset=self._work_subset_arg(asset),
            )
            comment = self._ask_comment("Save Scene Comment")
            if comment is None:
                return
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
                department=parsed.get("department") or department,
                variant=parsed.get("variant") or self._work_variant_arg(asset),
                subset=self._work_subset_arg(asset),
                version=int(parsed.get("version") or 0) + 1,
                ext=parsed.get("ext") or "ma",
            )
        else:
            target = self.manager.next_work_take_path(
                asset,
                current_path=selected_path,
                department=department,
                variant=self._work_variant_arg(asset),
                subset=self._work_subset_arg(asset),
            )

        comment = self._ask_comment("Save Scene Comment")
        if comment is None:
            return
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
        subset = self._work_subset_arg(asset)

        target = self.manager.next_work_after_latest_publish_path(
            asset,
            department=department,
            variant=variant,
            subset=subset,
        )
        try:
            dependency_plan = resolve_staging_dependency(asset, self.manager, department, variant, subset)
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
            subprocess.Popen([str(rvpush), "merge", "[", *args, "]"], env=env)
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

    def _publish_selected_work(self) -> None:
        asset = self._current_asset()
        source_path = self._selected_work_path()
        if not asset:
            self.status_label.setText("Select an asset first")
            return
        if not source_path:
            self.status_label.setText("Select a work scene first")
            return

        parsed = self.manager.parse_work_file(source_path)
        if not parsed:
            QtWidgets.QMessageBox.warning(
                self,
                "Publish Failed",
                "Selected work scene does not match the naming rule.",
            )
            return

        publish_formats = self.manager.publish_formats_for_work_file(
            asset,
            source_path,
            subset=self._work_subset_arg(asset),
        )
        targets = {
            publish_format: self.manager.publish_file_path(
                asset,
                department=parsed["department"],
                variant=parsed["variant"],
                subset=self._work_subset_arg(asset),
                version=parsed["version"],
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
            validation = validate_variant_publish(asset, self.manager, Path(source_path), subset=self._work_subset_arg(asset))
            if not self._confirm_publish_validation(validation):
                return
            published = publish_work_outputs(
                asset,
                self.manager,
                Path(source_path),
                targets,
                overwrite=overwrite,
                comment=comment,
                subset=self._work_subset_arg(asset),
                dependency_info=validation,
            )
            self.status_label.setText("Published: " + ", ".join(path.name for path in published))
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
        export_fbx = export_abc = export_usd = None
        export_guide = export_skin_high = export_skin_low = None
        if export_kind == "mesh":
            export_fbx = menu.addAction("Selected Mesh: .fbx")
            export_abc = menu.addAction("Selected Mesh: .abc")
            export_usd = menu.addAction("Selected Mesh: .usd")
        elif export_kind == "guide":
            export_guide = menu.addAction("mGear Guide")
        elif export_kind == "skin":
            export_skin_high = menu.addAction("mGear Skin: high")
            export_skin_low = menu.addAction("mGear Skin: low")
        action = menu.exec(QtGui.QCursor.pos())
        if not action:
            return

        comment = self._ask_comment("Data Export Comment")
        if comment is None:
            return
        try:
            if action == export_fbx:
                paths = export_selected_model_data(asset, self.manager, self._work_variant_arg(asset), self._work_subset_arg(asset) or self._current_variant(), "fbx", comment)
            elif action == export_abc:
                paths = export_selected_model_data(asset, self.manager, self._work_variant_arg(asset), self._work_subset_arg(asset) or self._current_variant(), "abc", comment)
            elif action == export_usd:
                paths = export_selected_model_data(asset, self.manager, self._work_variant_arg(asset), self._work_subset_arg(asset) or self._current_variant(), "usd", comment)
            elif action == export_guide:
                paths = [export_mgear_guide(asset, self.manager, self._work_variant_arg(asset), self._work_subset_arg(asset) or "guide")]
            elif action == export_skin_high:
                paths = [export_mgear_skin(asset, self.manager, self._work_variant_arg(asset), "high")]
            elif action == export_skin_low:
                paths = [export_mgear_skin(asset, self.manager, self._work_variant_arg(asset), "low")]
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

        hou.hipFile.save(file_path)
        return
    except ImportError:
        pass

    raise RuntimeError("Save Scene is available inside Maya or Houdini.")


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
    except ImportError as exc:
        raise RuntimeError("Staging is available inside Maya.") from exc

    template = resolve_asset_work_template(manager, department)
    if template and template.exists():
        cmds.file(str(template), open=True, force=True)
    else:
        cmds.file(new=True, force=True)

    dependency_info: dict = {
        "template": str(template).replace("\\", "/") if template else "",
        "dependencies": {},
    }
    if dependency_plan:
        reference_file_to_current_dcc(dependency_plan["path"], namespace=dependency_plan["namespace"])
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

    ensure_asset_top_structure(cmds, asset.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    scene_type = "mayaBinary" if target.suffix.lower() == ".mb" else "mayaAscii"
    cmds.file(rename=str(target))
    cmds.file(save=True, type=scene_type)
    return dependency_info


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
    geo = _adopt_or_create_asset_child(cmds, top, "geo", ("geo", "geo_grp"))
    for child in ("rig", "groom", "look"):
        _adopt_or_create_asset_child(cmds, top, child, (child, f"{child}_grp"))
    for subset in ("render", "proxy", "guide"):
        _adopt_or_create_asset_child(cmds, geo, subset, (subset, f"{subset}_grp"))


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
                if publish_format in {"ma", "mb"} and snapshot_active:
                    save_maya_snapshot_copy(target, source_workfile)
                else:
                    shutil.copy2(source_workfile, target)
            else:
                export_current_scene_for_publish(target, publish_format, source_workfile)
            published.append(target)
            files[publish_format] = target.name
            if rig_metadata is not None:
                write_rig_publish_metadata(target.parent, rig_metadata)
    finally:
        if snapshot_active:
            reopen_source_workfile(source_workfile)

    manager.register_publish_files_for_work_file(
        asset,
        source_workfile,
        files=files,
        comment=comment,
        subset=subset,
        dependency_info=dependency_info,
    )
    return published


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
    except ImportError as exc:
        raise RuntimeError("Extra publish format export is available inside Maya.") from exc
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
) -> Path:
    clean_format = publish_format.lower().lstrip(".")
    target_path = Path(target)
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError(f"{clean_format} publish export is available inside Maya.") from exc

    ensure_current_dcc_scene_matches(source_workfile)

    if clean_format == "usd":
        if not cmds.pluginInfo("mayaUsdPlugin", query=True, loaded=True):
            cmds.loadPlugin("mayaUsdPlugin")
        cmds.file(
            str(target_path),
            force=True,
            options=";",
            type="USD Export",
            exportAll=True,
        )
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


def export_selected_model_data(
    asset: Asset,
    manager: AssetManager,
    variant: str,
    subset: str,
    data_format: str,
    comment: str = "",
) -> list[Path]:
    try:
        import maya.cmds as cmds
    except ImportError:
        raise RuntimeError("Model data export is available inside Maya.")

    selection = cmds.ls(selection=True, long=True) or []
    if not selection:
        raise RuntimeError("Select mesh objects to export.")

    variant = variant or "default"
    subset = subset or "hires"
    clean_format = data_format.lower().lstrip(".")
    base_name = f"{asset.name}_model_{subset}"
    version = manager.next_data_version(
        asset,
        department="model",
        variant=variant,
        subset=subset,
    )
    data_path = manager.data_file_path(
        asset,
        department="model",
        variant=variant,
        subset=subset,
        version=version,
        ext=clean_format,
        name=base_name,
    )

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
    elif clean_format == "abc":
        if not cmds.pluginInfo("AbcExport", query=True, loaded=True):
            cmds.loadPlugin("AbcExport")
        frame = int(cmds.currentTime(query=True))
        roots = " ".join(f'-root "{node}"' for node in selection)
        job = f'-frameRange {frame} {frame} {roots} -file "{data_path.as_posix()}"'
        cmds.AbcExport(j=job)
    elif clean_format == "usd":
        if not cmds.pluginInfo("mayaUsdPlugin", query=True, loaded=True):
            cmds.loadPlugin("mayaUsdPlugin")
        cmds.file(
            str(data_path),
            force=True,
            options=";",
            type="USD Export",
            exportSelected=True,
        )
    else:
        raise RuntimeError(f"Unsupported data format: {data_format}")

    source_workfile = cmds.file(query=True, sceneName=True) or ""
    manager.register_data_export(
        asset,
        department="model",
        variant=variant,
        subset=subset,
        version=version,
        files={clean_format: data_path.name},
        source_workfile=source_workfile,
        comment=comment,
    )
    return [data_path]


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
    path = manager.data_file_path(
        asset,
        department="skin",
        variant=variant,
        subset=subset,
        version=version,
        ext="gSkinPack",
        name=f"{asset.name}_{subset}",
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
) -> None:
    file_path = str(Path(path))
    try:
        import maya.cmds as cmds

        namespace = namespace or Path(file_path).stem
        namespace = namespace.replace(".", "_").replace("-", "_")
        cmds.file(
            file_path,
            reference=True,
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

    raise RuntimeError("Reference is available inside Maya or Houdini.")


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


def show() -> AssetManagerWindow:
    global _WINDOW
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    _WINDOW = AssetManagerWindow()
    _WINDOW.show()
    return _WINDOW


if __name__ == "__main__":
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = show()
    app.exec()

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from smartlib.apps.common.asset_cards import (
    asset_card_text,
    asset_icon,
    asset_tooltip,
    configure_asset_card_list,
)
from smartlib.core.config_loader import ProjectConfig


def _qt_modules():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets

        return QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets

        return QtCore, QtGui, QtWidgets


QtCore, QtGui, QtWidgets = _qt_modules()


def _default_config_dir() -> Path:
    env_path = os.environ.get("PROJECT_CONFIG_DIR")
    if env_path:
        return Path(env_path)
    root = Path(os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT") or Path.cwd())
    return root / "config" / "STKB"


class ComponentAssetDialog(QtWidgets.QDialog):
    def __init__(self, component, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Replace Asset")
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.category_edit = QtWidgets.QLineEdit(component.category or "prop")
        self.group_edit = QtWidgets.QLineEdit(component.group or "bp")
        self.asset_edit = QtWidgets.QLineEdit(component.asset or "")
        self.variant_edit = QtWidgets.QLineEdit(component.variant or "default")
        form.addRow("Category", self.category_edit)
        form.addRow("Group", self.group_edit)
        form.addRow("Asset", self.asset_edit)
        form.addRow("Variant", self.variant_edit)
        layout.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict[str, str]:
        return {
            "category": self.category_edit.text().strip() or "prop",
            "group": self.group_edit.text().strip() or "bp",
            "asset": self.asset_edit.text().strip(),
            "variant": self.variant_edit.text().strip() or "default",
        }


class AssetAssemblyWindow(QtWidgets.QMainWindow):
    def __init__(self, config_dir: str | os.PathLike[str] | None = None, parent=None):
        super().__init__(parent)
        self.project_config = ProjectConfig(config_dir or _default_config_dir())
        self.context = None
        self.setWindowTitle(f"Asset Assembly - {self.project_config.project_name}")
        self.resize(520, 760)
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)
        self.setCentralWidget(central)

        self.assembly_category_edit = QtWidgets.QLineEdit("env")
        self.assembly_group_edit = QtWidgets.QLineEdit("set")
        self.assembly_asset_edit = QtWidgets.QLineEdit("kitchen")
        self.assembly_variant_edit = QtWidgets.QLineEdit("default")
        self.apply_context_btn = QtWidgets.QPushButton("Set Current Context")
        self.place_target_edit = QtWidgets.QLineEdit("component")
        self.create_loc_btn = QtWidgets.QPushButton("Create place_LOC")
        self.match_transform_btn = QtWidgets.QPushButton("Match to Viewport Selection")
        self.restore_saved_btn = QtWidgets.QPushButton("Restore Saved Locators")
        self.register_selected_btn = QtWidgets.QPushButton("Register Selected Mesh")
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.apply_context_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DialogApplyButton))
        self.create_loc_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_FileDialogNewFolder))
        self.match_transform_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_ArrowRight))
        self.restore_saved_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DialogOpenButton))
        self.register_selected_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DialogSaveButton))
        self.refresh_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_BrowserReload))
        self._configure_icon_button(self.refresh_btn, "Refresh")
        self._configure_icon_button(self.create_loc_btn, "Create place_LOC")
        self._configure_icon_button(self.match_transform_btn, "Match to Viewport Selection")
        self._configure_icon_button(self.restore_saved_btn, "Restore Saved Locators")
        self._configure_icon_button(self.register_selected_btn, "Register Selected Mesh")
        self.place_tree = QtWidgets.QTreeWidget()
        self.place_tree.setColumnCount(1)
        self.place_tree.setHeaderLabels(["place_LOC"])
        self.place_tree.header().setStretchLastSection(True)
        self.place_tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.place_tree.setAlternatingRowColors(True)
        self.place_tree.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.EditKeyPressed)
        self.place_tree.setIndentation(10)

        self.assembly_tabs = QtWidgets.QTabWidget()
        assembly_panel = QtWidgets.QWidget()
        assembly_layout = QtWidgets.QVBoxLayout(assembly_panel)
        assembly_layout.setContentsMargins(4, 4, 4, 4)
        assembly_layout.setSpacing(4)
        self.component_table = QtWidgets.QTableWidget(0, 7)
        self.component_table.setHorizontalHeaderLabels(["Target", "Asset", "Category", "Group", "Variant", "USD Mode", "Locator"])
        self.component_table.horizontalHeader().setStretchLastSection(True)
        self.component_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.component_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.component_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.component_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        asset_browser_header = QtWidgets.QHBoxLayout()
        asset_browser_header.setContentsMargins(0, 0, 0, 0)
        asset_browser_header.setSpacing(6)
        self.place_asset_search_edit = QtWidgets.QLineEdit()
        self.place_asset_search_edit.setPlaceholderText("Search BP published asset")
        self.place_asset_search_edit.setClearButtonEnabled(True)
        self.place_scene_assets_only_check = QtWidgets.QCheckBox("Scene assets only")
        self.place_usd_mode_combo = QtWidgets.QComboBox()
        self.place_usd_mode_combo.addItems(["Reference", "Instance"])
        self.refresh_place_assets_btn = QtWidgets.QPushButton("Refresh Assets")
        asset_browser_header.addWidget(self.place_asset_search_edit, 1)
        asset_browser_header.addWidget(self.place_scene_assets_only_check)
        asset_browser_header.addWidget(QtWidgets.QLabel("USD Mode"))
        asset_browser_header.addWidget(self.place_usd_mode_combo)
        asset_browser_header.addWidget(self.refresh_place_assets_btn)
        self.place_asset_list = QtWidgets.QListWidget()
        self.place_asset_list.setMinimumHeight(130)
        configure_asset_card_list(self.place_asset_list, QtCore, QtWidgets, compact=True)
        self.place_asset_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.place_asset_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        action_layout = QtWidgets.QHBoxLayout()
        self.place_asset_to_locator_btn = QtWidgets.QPushButton("Place Asset To LOC")
        self.replace_selected_node_btn = QtWidgets.QPushButton("Replace Selected Node")
        self.duplicate_placement_btn = QtWidgets.QPushButton("Duplicate Placement")
        self.replace_asset_btn = QtWidgets.QPushButton("Replace Asset")
        self.set_variant_btn = QtWidgets.QPushButton("Set Variant")
        self.place_asset_to_locator_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DialogApplyButton))
        self.duplicate_placement_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_FileDialogNewFolder))
        self.replace_asset_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_BrowserReload))
        self.set_variant_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_FileDialogDetailedView))
        self.place_asset_to_locator_btn.setStyleSheet("QPushButton { background-color:#2d5d86; color:white; font-weight:bold; }")
        self.replace_selected_node_btn.setStyleSheet("QPushButton { background-color:#2d5d86; color:white; font-weight:bold; }")
        action_layout.addWidget(self.refresh_btn)
        action_layout.addWidget(self.create_loc_btn)
        action_layout.addWidget(self.match_transform_btn)
        action_layout.addWidget(self.restore_saved_btn)
        action_layout.addWidget(self.register_selected_btn)
        action_layout.addStretch(1)
        action_layout.addWidget(self.place_asset_to_locator_btn)
        action_layout.addWidget(self.replace_selected_node_btn)
        action_layout.addWidget(self.duplicate_placement_btn)
        action_layout.addWidget(self.replace_asset_btn)
        action_layout.addWidget(self.set_variant_btn)
        self.detail_table = QtWidgets.QTableWidget(0, 2)
        self.detail_table.setHorizontalHeaderLabels(["Key", "Value"])
        self.detail_table.horizontalHeader().setStretchLastSection(True)
        self.detail_table.verticalHeader().setVisible(False)
        self.detail_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        placement_hint = QtWidgets.QLabel("Place published assets back into the scene, then edit placement asset and variant.")
        placement_hint.setStyleSheet("QLabel { color: #9f9f9f; }")
        assembly_layout.addWidget(placement_hint)
        assembly_layout.addLayout(asset_browser_header)
        assembly_layout.addWidget(self.place_asset_list)
        assembly_layout.addLayout(action_layout)
        self.extract_panel = self._build_extract_tab()
        self.assembly_tabs.addTab(self.extract_panel, "Extract / Publish")
        self.assembly_tabs.addTab(assembly_panel, "Place Asset")

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(False)
        self.status_label.setMaximumHeight(18)

        root.addWidget(self.assembly_tabs, 1)
        root.addWidget(self.status_label)

        self.apply_context_btn.clicked.connect(self.apply_assembly_context)
        self.create_loc_btn.clicked.connect(self.create_locator)
        self.match_transform_btn.clicked.connect(self.match_selected_locator_to_viewport_selection)
        self.restore_saved_btn.clicked.connect(self.restore_saved_locators)
        self.register_selected_btn.clicked.connect(self.register_selected)
        self.refresh_btn.clicked.connect(self.refresh)
        self.duplicate_placement_btn.clicked.connect(self.duplicate_placement)
        self.replace_asset_btn.clicked.connect(self.replace_asset)
        self.set_variant_btn.clicked.connect(self.set_variant)
        self.component_table.itemSelectionChanged.connect(self.populate_detail)
        self.place_asset_search_edit.textChanged.connect(self.populate_place_asset_list)
        self.place_scene_assets_only_check.toggled.connect(self.populate_place_asset_list)
        self.refresh_place_assets_btn.clicked.connect(self.populate_place_asset_list)
        self.place_asset_list.itemClicked.connect(self.apply_place_asset_selection)
        self.place_asset_list.customContextMenuRequested.connect(self.show_place_asset_menu)
        self.place_asset_to_locator_btn.clicked.connect(self.place_asset_on_selected_locator)
        self.replace_selected_node_btn.clicked.connect(self.replace_selected_node_with_place_asset)
        self.place_tree.itemSelectionChanged.connect(self.select_component_from_place_tree)
        self.place_tree.itemChanged.connect(self.rename_place_tree_item)

        self._last_selection_target = ""
        self.selection_timer = QtCore.QTimer(self)
        self.selection_timer.setInterval(350)
        self.selection_timer.timeout.connect(self.sync_target_from_selection)
        self.selection_timer.start()

    def _configure_icon_button(self, button, tooltip: str) -> None:
        button.setText("")
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setFixedSize(28, 28)
        button.setIconSize(QtCore.QSize(16, 16))

    def _build_extract_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        root_layout = QtWidgets.QVBoxLayout(tab)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(10)

        title = QtWidgets.QLabel("Extract Candidates")
        title.setStyleSheet("QLabel { font-weight: bold; font-size: 13px; }")
        root_layout.addWidget(title)
        hint = QtWidgets.QLabel("Extract selected Maya objects into component assets, then publish the model payload.")
        hint.setStyleSheet("QLabel { color: #9f9f9f; }")
        root_layout.addWidget(hint)

        preview_layout = QtWidgets.QHBoxLayout()
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(8)
        self.extract_preview = QtWidgets.QLabel("")
        self.extract_preview.setFixedSize(128, 72)
        self.extract_preview.setAlignment(QtCore.Qt.AlignCenter)
        self.extract_preview.setStyleSheet("QLabel { background: #565656; border: 1px solid #454545; }")
        self.extract_candidate_state_label = QtWidgets.QLabel("Candidates are not published yet.")
        self.extract_candidate_state_label.setStyleSheet("QLabel { color: #9f9f9f; }")
        preview_layout.addWidget(self.extract_preview)
        preview_layout.addWidget(self.extract_candidate_state_label, 1)
        root_layout.addLayout(preview_layout)

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        root_layout.addWidget(line)

        naming_label = QtWidgets.QLabel("Naming Defaults")
        naming_label.setStyleSheet("QLabel { font-weight: bold; }")
        root_layout.addWidget(naming_label)

        self.extract_target_edit = QtWidgets.QLineEdit()
        self.extract_asset_edit = QtWidgets.QLineEdit()
        self.extract_category_edit = QtWidgets.QLineEdit("prop")
        self.extract_group_edit = QtWidgets.QLineEdit("bp")
        self.extract_variant_edit = QtWidgets.QLineEdit("default")
        self.extract_department_edit = QtWidgets.QLineEdit("model")
        self.extract_subset_combo = QtWidgets.QComboBox()
        self.extract_subset_combo.setEditable(True)
        self.extract_subset_combo.addItems(["proxy", "render"])
        self.extract_auto_split_check = QtWidgets.QCheckBox("Auto-split by top transform")
        self.extract_auto_split_check.setChecked(True)
        self.extract_center_check = QtWidgets.QCheckBox("Center to origin")
        self.extract_center_check.setChecked(True)

        naming_grid = QtWidgets.QGridLayout()
        naming_grid.setContentsMargins(0, 0, 0, 0)
        naming_grid.setSpacing(6)
        naming_grid.addWidget(QtWidgets.QLabel("Category"), 0, 0)
        naming_grid.addWidget(self.extract_category_edit, 0, 1)
        naming_grid.addWidget(QtWidgets.QLabel("Group"), 0, 2)
        naming_grid.addWidget(self.extract_group_edit, 0, 3)
        naming_grid.addWidget(QtWidgets.QLabel("Variant"), 1, 0)
        naming_grid.addWidget(self.extract_variant_edit, 1, 1)
        naming_grid.addWidget(QtWidgets.QLabel("Department"), 1, 2)
        naming_grid.addWidget(self.extract_department_edit, 1, 3)
        naming_grid.addWidget(QtWidgets.QLabel("Subset"), 2, 0)
        naming_grid.addWidget(self.extract_subset_combo, 2, 1)
        naming_grid.addWidget(self.extract_auto_split_check, 2, 2, 1, 2)
        root_layout.addLayout(naming_grid)

        detail_line = QtWidgets.QFrame()
        detail_line.setFrameShape(QtWidgets.QFrame.HLine)
        detail_line.setFrameShadow(QtWidgets.QFrame.Sunken)
        root_layout.addWidget(detail_line)

        detail_label = QtWidgets.QLabel("Candidate Details")
        detail_label.setStyleSheet("QLabel { font-weight: bold; }")
        root_layout.addWidget(detail_label)

        detail_form = QtWidgets.QFormLayout()
        detail_form.setContentsMargins(0, 0, 0, 0)
        detail_form.setSpacing(6)
        detail_form.addRow("Target", self.extract_target_edit)
        self.extract_path_label = QtWidgets.QLabel("")
        self.extract_path_label.setWordWrap(True)
        detail_form.addRow("Asset", self.extract_asset_edit)
        detail_form.addRow("Output", self.extract_path_label)
        root_layout.addLayout(detail_form)

        self.extract_btn = QtWidgets.QPushButton("Extract Selected Candidate")
        self.publish_component_btn = QtWidgets.QPushButton("Publish Component Model")
        self.compose_asset_usd_btn = QtWidgets.QPushButton("Compose Asset USD")
        self.extract_btn.setMinimumHeight(36)
        self.publish_component_btn.setMinimumHeight(36)
        self.compose_asset_usd_btn.setMinimumHeight(36)
        self.extract_btn.setStyleSheet("QPushButton { background-color:#2d5d86; color:white; font-weight:bold; }")
        self.publish_component_btn.setStyleSheet("QPushButton { background-color:#246ba3; color:white; font-weight:bold; }")
        self.compose_asset_usd_btn.setStyleSheet("QPushButton { background-color:#246ba3; color:white; font-weight:bold; }")

        self.component_table.setMinimumHeight(190)
        root_layout.addWidget(self.component_table, 1)

        command_layout = QtWidgets.QHBoxLayout()
        command_layout.addStretch(1)
        command_layout.addWidget(self.publish_component_btn)
        command_layout.addWidget(self.compose_asset_usd_btn)
        command_layout.addWidget(self.extract_btn)
        root_layout.addLayout(command_layout)
        self.extract_btn.clicked.connect(self.extract_component)
        self.publish_component_btn.clicked.connect(self.publish_component_model)
        self.compose_asset_usd_btn.clicked.connect(self.compose_asset_usd)
        self.extract_asset_edit.textChanged.connect(self._update_extract_path_label)
        self.extract_category_edit.textChanged.connect(self._update_extract_path_label)
        self.extract_group_edit.textChanged.connect(self._update_extract_path_label)
        self.extract_variant_edit.textChanged.connect(self._update_extract_path_label)
        self.extract_department_edit.textChanged.connect(self._update_extract_path_label)
        self.extract_subset_combo.currentTextChanged.connect(self._update_extract_path_label)
        return tab

    def refresh(self) -> None:
        from smartlib.dcc.maya import asset_assembly

        try:
            self.context = asset_assembly.current_assembly_context(self.project_config)
            if self.context:
                self.context = asset_assembly.set_assembly_context(
                    self.context.category,
                    self.context.group,
                    self.context.asset,
                    self.context.variant,
                )
            self.components = asset_assembly.list_components()
            self._populate_context_fields()
            self.status_label.setText("")
        except Exception as exc:
            self.components = []
            self.status_label.setText(str(exc))
        self.populate_components()
        self.populate_place_asset_list()

    def _populate_context_fields(self) -> None:
        if not self.context:
            return
        self.assembly_category_edit.setText(getattr(self.context, "category", "") or "env")
        self.assembly_group_edit.setText(getattr(self.context, "group", "") or "set")
        self.assembly_asset_edit.setText(getattr(self.context, "asset", "") or "kitchen")
        self.assembly_variant_edit.setText(getattr(self.context, "variant", "") or "default")

    def populate_components(self) -> None:
        self.component_table.setRowCount(0)
        self.place_tree.clear()
        for component in self.components:
            row = self.component_table.rowCount()
            self.component_table.insertRow(row)
            values = [
                component.target,
                component.asset,
                component.category,
                component.group,
                component.variant,
                getattr(component, "usd_mode", "reference"),
                component.locator,
            ]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(str(value))
                item.setData(QtCore.Qt.UserRole, component)
                self.component_table.setItem(row, column, item)
            tree_item = QtWidgets.QTreeWidgetItem([component.locator])
            tree_item.setData(0, QtCore.Qt.UserRole, component.locator)
            tree_item.setFlags(tree_item.flags() | QtCore.Qt.ItemIsEditable)
            self.place_tree.addTopLevelItem(tree_item)
        self.component_table.resizeColumnsToContents()
        self.component_table.horizontalHeader().setStretchLastSection(True)

    def populate_place_asset_list(self) -> None:
        if not getattr(self, "place_asset_list", None):
            return
        query = self.place_asset_search_edit.text().strip().lower()
        scene_only = self.place_scene_assets_only_check.isChecked()
        scene_keys = {
            (
                str(component.category or "").lower(),
                str(component.group or "").lower(),
                str(component.asset or "").lower(),
            )
            for component in getattr(self, "components", [])
        }
        self.place_asset_list.clear()
        for asset_info in self._bp_published_assets():
            key = (
                asset_info["category"].lower(),
                asset_info["group"].lower(),
                asset_info["asset"].lower(),
            )
            label = asset_card_text(
                asset=asset_info["asset"],
                category=asset_info["category"],
                group=asset_info["group"],
                variant=asset_info["variant"],
                status=asset_info.get("status", ""),
                asset_type=asset_info["category"],
            )
            if query and query not in label.lower():
                continue
            if scene_only and key not in scene_keys:
                continue
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, asset_info)
            item.setToolTip(
                asset_tooltip(
                    asset=asset_info["asset"],
                    category=asset_info["category"],
                    group=asset_info["group"],
                    variant=asset_info["variant"],
                    status=asset_info.get("status", ""),
                    extra={"publish": asset_info.get("publish", "")},
                )
            )
            item.setIcon(asset_icon(QtCore, QtGui, thumbnail=asset_info.get("thumbnail", ""), label=asset_info["asset"]))
            self.place_asset_list.addItem(item)
        if self.place_asset_list.count() == 0:
            item = QtWidgets.QListWidgetItem("No BP published assets")
            item.setFlags(item.flags() & ~QtCore.Qt.ItemIsSelectable)
            self.place_asset_list.addItem(item)

    def apply_place_asset_selection(self, item) -> None:
        asset_info = item.data(QtCore.Qt.UserRole) if item else None
        if not isinstance(asset_info, dict):
            return
        component = self._selected_component()
        if component:
            self.status_label.setText(f"Selected asset for {component.locator}: {asset_info['asset']}")
            return
        self.extract_category_edit.setText(asset_info["category"])
        self.extract_group_edit.setText(asset_info["group"])
        self.extract_asset_edit.setText(asset_info["asset"])
        self.extract_variant_edit.setText(asset_info["variant"])
        self._update_extract_path_label()
        self.status_label.setText(f"Selected asset: {asset_info['asset']}")

    def show_place_asset_menu(self, pos) -> None:
        item = self.place_asset_list.itemAt(pos)
        if item:
            self.place_asset_list.setCurrentItem(item)
        asset_info = item.data(QtCore.Qt.UserRole) if item else None
        if not isinstance(asset_info, dict):
            return
        menu = QtWidgets.QMenu(self)
        capture_action = menu.addAction("Capture Viewport Thumbnail")
        global_pos = self.place_asset_list.mapToGlobal(pos)
        action = menu.exec_(global_pos) if hasattr(menu, "exec_") else menu.exec(global_pos)
        if action == capture_action:
            self.capture_selected_asset_thumbnail(asset_info)

    def capture_selected_asset_thumbnail(self, asset_info: dict) -> None:
        try:
            from smartlib.dcc.maya import asset_assembly

            path = asset_assembly.capture_asset_viewport_thumbnail(
                self.project_config,
                category=asset_info["category"],
                group=asset_info["group"],
                asset=asset_info["asset"],
                variant=asset_info.get("variant", "default"),
            )
            self.status_label.setText(f"Updated thumbnail: {asset_info['asset']}")
            self.populate_place_asset_list()
            self._select_place_asset_card(asset_info)
            QtWidgets.QMessageBox.information(self, "Capture Viewport Thumbnail", f"Updated thumbnail:\n{path}")
        except Exception as exc:
            self.status_label.setText(str(exc))
            QtWidgets.QMessageBox.critical(self, "Capture Viewport Thumbnail Failed", str(exc))

    def place_asset_on_selected_locator(self) -> None:
        asset_info = self._selected_place_asset_info()
        if not asset_info:
            self.status_label.setText("Select a BP published asset first.")
            return
        locator = self._selected_locator()
        if not locator:
            self.status_label.setText("Select a place_LOC.")
            return
        usd_mode = self.place_usd_mode_combo.currentText().strip().lower() or "reference"
        try:
            from smartlib.dcc.maya import asset_assembly

            component = asset_assembly.place_published_asset_at_locator(
                self.project_config,
                locator,
                category=asset_info["category"],
                group=asset_info["group"],
                asset=asset_info["asset"],
                variant=asset_info["variant"],
                usd_mode=usd_mode,
            )
            self.status_label.setText(f"Placed {component.asset} to {component.locator} ({component.usd_mode})")
            self.refresh()
            self._select_locator_in_views(component.locator)
        except Exception as exc:
            self.status_label.setText(str(exc))
            QtWidgets.QMessageBox.critical(self, "Place Asset To LOC Failed", str(exc))

    def replace_selected_node_with_place_asset(self) -> None:
        asset_info = self._selected_place_asset_info()
        if not asset_info:
            self.status_label.setText("Select a BP published asset first.")
            return
        usd_mode = self.place_usd_mode_combo.currentText().strip().lower() or "reference"
        try:
            from smartlib.dcc.maya import asset_assembly

            component = asset_assembly.place_published_asset_at_selection(
                self.project_config,
                category=asset_info["category"],
                group=asset_info["group"],
                asset=asset_info["asset"],
                variant=asset_info["variant"],
                usd_mode=usd_mode,
            )
            self.status_label.setText(
                f"Replaced selected node with {component.asset} ({getattr(component, 'usd_mode', usd_mode)})"
            )
            self.refresh()
            for table_row in range(self.component_table.rowCount()):
                item = self.component_table.item(table_row, 6)
                if item and item.text() == component.locator:
                    self.component_table.selectRow(table_row)
                    break
        except Exception as exc:
            self.status_label.setText(str(exc))
            QtWidgets.QMessageBox.critical(self, "Replace Selected Node Failed", str(exc))

    def _selected_place_asset_info(self) -> dict | None:
        item = self.place_asset_list.currentItem()
        data = item.data(QtCore.Qt.UserRole) if item else None
        return data if isinstance(data, dict) else None

    def _select_place_asset_card(self, asset_info: dict) -> None:
        for index in range(self.place_asset_list.count()):
            item = self.place_asset_list.item(index)
            data = item.data(QtCore.Qt.UserRole) if item else None
            if not isinstance(data, dict):
                continue
            if (
                data.get("category") == asset_info.get("category")
                and data.get("group") == asset_info.get("group")
                and data.get("asset") == asset_info.get("asset")
                and data.get("variant") == asset_info.get("variant")
            ):
                self.place_asset_list.setCurrentItem(item)
                return

    def populate_detail(self) -> None:
        component = self._selected_component()
        self.detail_table.setRowCount(0)
        if not component:
            return
        self._populate_extract_fields(component)
        rows = [
            ("target", component.target),
            ("asset", component.asset),
            ("category", component.category),
            ("group", component.group),
            ("variant", component.variant),
            ("usd_mode", getattr(component, "usd_mode", "reference")),
            ("locator", component.locator),
            ("source_nodes", ", ".join(component.source_nodes)),
        ]
        for key, value in rows:
            row = self.detail_table.rowCount()
            self.detail_table.insertRow(row)
            self.detail_table.setItem(row, 0, QtWidgets.QTableWidgetItem(key))
            self.detail_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(value)))

    def _populate_extract_fields(self, component) -> None:
        self.extract_target_edit.setText(component.target)
        self.extract_asset_edit.setText(component.asset or component.target)
        self.extract_category_edit.setText(component.category or "prop")
        self.extract_group_edit.setText(component.group or "bp")
        self.extract_variant_edit.setText(component.variant or "default")
        self._update_extract_path_label()

    def create_locator(self) -> None:
        from smartlib.dcc.maya import asset_assembly

        try:
            node = asset_assembly.create_place_locator(self.place_target_edit.text().strip() or "component")
            self.status_label.setText(f"Created {node}")
            self.place_target_edit.setText("component")
            self.refresh()
            self._select_locator_in_views(node)
            return
        except Exception as exc:
            self.status_label.setText(str(exc))
        self.refresh()

    def match_selected_locator_to_viewport_selection(self) -> None:
        from smartlib.dcc.maya import asset_assembly

        locator = self._selected_locator()
        if not locator:
            self.status_label.setText("Select a place_LOC.")
            return
        try:
            target = asset_assembly.match_locator_to_viewport_selection(locator)
            self.status_label.setText(f"Matched {locator} to {target}")
        except Exception as exc:
            self.status_label.setText(str(exc))
        self.refresh()

    def restore_saved_locators(self) -> None:
        from smartlib.dcc.maya import asset_assembly

        try:
            count = asset_assembly.restore_saved_assembly_locators(self.project_config)
            self.status_label.setText(f"Restored {count} saved locators")
        except Exception as exc:
            self.status_label.setText(str(exc))
        self.refresh()

    def apply_assembly_context(self) -> None:
        from smartlib.dcc.maya import asset_assembly

        try:
            self.context = asset_assembly.set_assembly_context(
                self.assembly_category_edit.text().strip() or "env",
                self.assembly_group_edit.text().strip() or "set",
                self.assembly_asset_edit.text().strip() or "kitchen",
                self.assembly_variant_edit.text().strip() or "default",
            )
            self.status_label.setText(
                f"Assembly context: {self.context.category}/{self.context.group}/{self.context.asset}/{self.context.variant}"
            )
        except Exception as exc:
            self.status_label.setText(str(exc))
        self.refresh()

    def register_selected(self) -> None:
        from smartlib.dcc.maya import asset_assembly

        target = self.extract_target_edit.text().strip() or self.place_target_edit.text().strip() or "component"
        asset_name = self.extract_asset_edit.text().strip() or self._asset_name_from_target(target)
        try:
            row = asset_assembly.register_selected_component(
                target,
                asset=asset_name,
                category=self.extract_category_edit.text().strip() or "prop",
                group=self.extract_group_edit.text().strip() or "bp",
                variant=self.extract_variant_edit.text().strip() or "default",
            )
            self.status_label.setText(f"Registered {row.target}")
        except Exception as exc:
            self.status_label.setText(str(exc))
        self.refresh()

    def create_component_thumbnail(self) -> None:
        component = self._component_from_extract_fields()
        if not component:
            component = self._register_selection_candidate()
        if not component:
            self.status_label.setText("Select objects to snapshot.")
            return
        try:
            path = self._capture_component_thumbnail_preview(component)
            QtWidgets.QMessageBox.information(self, "Create Thumbnail", f"Created thumbnail:\n{path}")
        except Exception as exc:
            self.status_label.setText(str(exc))
            QtWidgets.QMessageBox.critical(self, "Create Thumbnail Failed", str(exc))

    def _capture_component_thumbnail_preview(self, component) -> Path:
        from smartlib.dcc.maya import asset_assembly

        path = asset_assembly.capture_component_thumbnail(self.project_config, component)
        self.status_label.setText(f"Thumbnail {Path(path).name}")
        pixmap = QtGui.QPixmap(str(path))
        if not pixmap.isNull():
            self.extract_preview.setPixmap(
                pixmap.scaled(
                    self.extract_preview.size(),
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation,
                )
            )
        return Path(path)

    def save_assembly(self) -> None:
        from smartlib.dcc.maya import asset_assembly

        try:
            comment = self._comment_dialog("Save Assembly")
            path = asset_assembly.save_assembly(self.project_config, comment=comment)
            self.status_label.setText(f"Saved {Path(path).name}")
        except Exception as exc:
            self.status_label.setText(str(exc))
        self.refresh()

    def publish_assembly(self) -> None:
        from smartlib.dcc.maya import asset_assembly

        try:
            comment = self._comment_dialog("Publish Assembly")
            path = asset_assembly.publish_assembly(self.project_config, comment=comment)
            self.status_label.setText(f"Published {Path(path).name}")
        except Exception as exc:
            self.status_label.setText(str(exc))
        self.refresh()

    def extract_component(self) -> None:
        from smartlib.dcc.maya import asset_assembly

        component = self._selected_component()
        if not component:
            component = self._register_selection_candidate()
            if not component:
                self.status_label.setText("Select objects to extract.")
                return
        component = self._component_from_extract_fields()
        thumbnail_warning = ""
        try:
            self._capture_component_thumbnail_preview(component)
        except Exception as exc:
            thumbnail_warning = f"Thumbnail warning: {exc}"
        try:
            result = asset_assembly.extract_component(
                self.project_config,
                component,
                department=self.extract_department_edit.text().strip() or "model",
                subset=self._extract_subset(),
                center_to_origin=self.extract_center_check.isChecked(),
            )
            warnings = []
            if thumbnail_warning:
                warnings.append(thumbnail_warning)
            if result.warning:
                warnings.append(result.warning)
            message = f"Extracted work: {result.workfile}"
            if result.publishfile:
                message = f"{message}\nPublished model: {result.publishfile}"
            if result.assembly_usd_path:
                message = f"{message}\nAssembly preview: {result.assembly_usd_path}"
            if warnings:
                message = f"{message}\n" + "\n".join(warnings)
            self.extract_path_label.setText(str(result.workfile).replace("\\", "/"))
            self.refresh()
            self.status_label.setText("Extracted component with warning" if warnings else "Extracted component")
            if warnings:
                QtWidgets.QMessageBox.warning(self, "Extract Component", message)
        except Exception as exc:
            self.status_label.setText(str(exc))
            QtWidgets.QMessageBox.critical(self, "Extract Component Failed", str(exc))
            self.refresh()

    def publish_component_model(self) -> None:
        from smartlib.dcc.maya import asset_assembly

        component = self._component_from_extract_fields()
        if not component:
            self.status_label.setText("Select a component.")
            return
        try:
            comment = self._comment_dialog("Publish Component Model")
            result = asset_assembly.publish_component_model(
                self.project_config,
                component,
                subset=self._extract_subset(),
                comment=comment,
            )
            message = f"Published {result.usd_path}"
            if result.asset_usd_path:
                message = f"{message}\nComposed {result.asset_usd_path}"
            if result.usd_error:
                message = f"{message}\nUSD export warning: {result.usd_error}"
            self.status_label.setText("Published component model")
            self.extract_path_label.setText(str(result.publish_dir).replace("\\", "/"))
        except Exception as exc:
            self.status_label.setText(str(exc))
        self.refresh()

    def compose_asset_usd(self) -> None:
        from smartlib.dcc.maya import asset_assembly

        component = self._component_from_extract_fields()
        if not component:
            self.status_label.setText("Select a component.")
            return
        try:
            comment = self._comment_dialog("Compose Asset USD")
            path = asset_assembly.compose_component_asset_usd(
                self.project_config,
                component,
                subset=self._extract_subset(),
                comment=comment,
            )
            self.status_label.setText(f"Composed {Path(path).name}")
            self.extract_path_label.setText(str(path.parent).replace("\\", "/"))
        except Exception as exc:
            self.status_label.setText(str(exc))
        self.refresh()

    def duplicate_placement(self) -> None:
        from smartlib.dcc.maya import asset_assembly

        component = self._selected_component()
        if not component:
            self.status_label.setText("Select a component.")
            return
        new_target, accepted = QtWidgets.QInputDialog.getText(
            self,
            "Duplicate Placement",
            "New target:",
            text=f"{component.target}_A",
        )
        if not accepted:
            return
        try:
            duplicated = asset_assembly.duplicate_placement(component, str(new_target).strip())
            self.status_label.setText(f"Duplicated placement: {duplicated.target}")
        except Exception as exc:
            self.status_label.setText(str(exc))
        self.refresh()

    def replace_asset(self) -> None:
        component = self._selected_component()
        if not component:
            self.status_label.setText("Select a component.")
            return
        dialog = ComponentAssetDialog(component, self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        self._update_component_from_dialog(component, dialog)

    def set_variant(self) -> None:
        component = self._selected_component()
        if not component:
            self.status_label.setText("Select a component.")
            return
        variant, accepted = QtWidgets.QInputDialog.getText(
            self,
            "Set Variant",
            "Variant:",
            text=component.variant or "default",
        )
        if not accepted:
            return
        self._update_component_from_dialog(component, None, variant=str(variant).strip() or "default")

    def _update_component_from_dialog(self, component, dialog=None, *, variant: str | None = None) -> None:
        from smartlib.dcc.maya import asset_assembly

        try:
            if dialog:
                values = dialog.values()
                updated = asset_assembly.update_component_asset(component, **values)
            else:
                updated = asset_assembly.update_component_asset(component, variant=variant)
            self.status_label.setText(
                f"Updated component: {updated.target} -> {updated.category}/{updated.group}/{updated.asset}/{updated.variant}"
            )
        except Exception as exc:
            self.status_label.setText(str(exc))
        self.refresh()

    def open_assembly(self) -> None:
        from smartlib.dcc.maya import asset_assembly

        try:
            path = asset_assembly.open_assembly_usd(self.project_config, reload=False)
            self.status_label.setText(f"Opened {Path(path).name}")
        except Exception as exc:
            self.status_label.setText(str(exc))

    def reload_assembly(self) -> None:
        from smartlib.dcc.maya import asset_assembly

        try:
            path = asset_assembly.open_assembly_usd(self.project_config, reload=True)
            self.status_label.setText(f"Reloaded {Path(path).name}")
        except Exception as exc:
            self.status_label.setText(str(exc))

    def select_component_from_place_tree(self) -> None:
        item = self.place_tree.currentItem()
        locator = str(item.data(0, QtCore.Qt.UserRole) or "") if item else ""
        if locator:
            from smartlib.dcc.maya import asset_assembly

            try:
                asset_assembly.select_node(locator)
            except Exception as exc:
                self.status_label.setText(str(exc))
        for row in range(self.component_table.rowCount()):
            table_item = self.component_table.item(row, 6)
            if table_item and table_item.text() == locator:
                self.component_table.selectRow(row)
                return

    def rename_place_tree_item(self, item, column: int) -> None:
        if column != 0:
            return
        old_locator = str(item.data(0, QtCore.Qt.UserRole) or "")
        new_name = str(item.text(0) or "").strip()
        if not old_locator or not new_name or old_locator == new_name:
            return
        from smartlib.dcc.maya import asset_assembly

        try:
            renamed = asset_assembly.rename_place_locator(old_locator, new_name)
            item.setData(0, QtCore.Qt.UserRole, renamed)
            item.setText(0, renamed)
            self.status_label.setText(f"Renamed locator: {renamed}")
        except Exception as exc:
            item.setText(0, old_locator)
            self.status_label.setText(str(exc))
        self.refresh()

    def _selected_locator(self) -> str:
        try:
            from smartlib.dcc.maya import asset_assembly

            viewport_locator = asset_assembly.selected_place_locator()
            if viewport_locator:
                return viewport_locator
        except Exception:
            pass
        item = self.place_tree.currentItem()
        if item:
            return str(item.data(0, QtCore.Qt.UserRole) or item.text(0) or "")
        component = self._selected_component()
        if component:
            return component.locator
        return ""

    def _select_locator_in_views(self, locator: str) -> None:
        if not locator:
            return
        for index in range(self.place_tree.topLevelItemCount()):
            item = self.place_tree.topLevelItem(index)
            if self._same_locator(str(item.data(0, QtCore.Qt.UserRole) or item.text(0) or ""), locator):
                self.place_tree.setCurrentItem(item)
                break
        for row in range(self.component_table.rowCount()):
            item = self.component_table.item(row, 6)
            if item and self._same_locator(item.text(), locator):
                self.component_table.selectRow(row)
                break

    def _same_locator(self, left: str, right: str) -> bool:
        return bool(left and right and (left == right or left.split("|")[-1] == right.split("|")[-1]))

    def _selected_component(self):
        row = self.component_table.currentRow()
        if row < 0:
            return None
        item = self.component_table.item(row, 0)
        return item.data(QtCore.Qt.UserRole) if item else None

    def sync_target_from_selection(self) -> None:
        if self.extract_target_edit.hasFocus():
            return
        try:
            from smartlib.dcc.maya import asset_assembly

            target, nodes = asset_assembly.selected_extract_target()
        except Exception:
            return
        if not target or target == self._last_selection_target:
            return
        self._last_selection_target = target
        self.component_table.clearSelection()
        self.component_table.setCurrentCell(-1, -1)
        self.detail_table.setRowCount(0)
        self.extract_target_edit.setText(target)
        self.place_target_edit.setText(self._asset_name_from_target(target))
        if not self.extract_asset_edit.hasFocus():
            self.extract_asset_edit.setText(self._asset_name_from_target(target))
        self.extract_candidate_state_label.setText(f"{len(nodes)} selected object(s). Candidates are not published yet.")
        self._update_extract_path_label()

    def _register_selection_candidate(self):
        from smartlib.dcc.maya import asset_assembly

        target = self.extract_target_edit.text().strip()
        if not target:
            target, _nodes = asset_assembly.selected_extract_target()
            self.extract_target_edit.setText(target)
        if not target:
            return None
        asset_name = self.extract_asset_edit.text().strip() or self._asset_name_from_target(target)
        row = asset_assembly.register_selected_component(
            target,
            asset=asset_name,
            category=self.extract_category_edit.text().strip() or "prop",
            group=self.extract_group_edit.text().strip() or "bp",
            variant=self.extract_variant_edit.text().strip() or "default",
        )
        self.refresh()
        for table_row in range(self.component_table.rowCount()):
            item = self.component_table.item(table_row, 6)
            if item and item.text() == row.locator:
                self.component_table.selectRow(table_row)
                break
        return row

    @staticmethod
    def _asset_name_from_target(target: str) -> str:
        leaf = str(target or "").split("|")[-1].split(":")[-1]
        leaf = re.sub(r"(_geo|_grp|_mesh|Shape)$", "", leaf, flags=re.IGNORECASE)
        leaf = re.sub(r"[^A-Za-z0-9_]+", "_", leaf).strip("_")
        return leaf or "component"

    def _component_from_extract_fields(self):
        from smartlib.dcc.maya import asset_assembly

        component = self._selected_component()
        if not component:
            return None
        return asset_assembly.AssemblyComponent(
            target=self.extract_target_edit.text().strip() or component.target,
            asset=self.extract_asset_edit.text().strip() or component.asset or component.target,
            category=self.extract_category_edit.text().strip() or component.category or "prop",
            group=self.extract_group_edit.text().strip() or component.group or "bp",
            variant=self.extract_variant_edit.text().strip() or component.variant or "default",
            locator=component.locator,
            source_nodes=component.source_nodes,
            usd_mode=getattr(component, "usd_mode", "reference"),
            local_offset_y=getattr(component, "local_offset_y", 0.0),
        )

    def _component_source_nodes(self, locator: str) -> list[str]:
        from smartlib.dcc.maya import asset_assembly

        try:
            for component in asset_assembly.list_components():
                if component.locator == locator:
                    return component.source_nodes
        except Exception:
            return []
        return []

    def _bp_published_assets(self) -> list[dict[str, str]]:
        root = Path(self.project_config.project_root or "")
        assets_root = root / "assets"
        if not assets_root.exists():
            return []
        rows: list[dict[str, str]] = []
        for category_dir in sorted(path for path in assets_root.iterdir() if path.is_dir()):
            group_dir = category_dir / "bp"
            if not group_dir.exists():
                continue
            for asset_dir in sorted(path for path in group_dir.iterdir() if path.is_dir()):
                variant_dirs = self._asset_variant_dirs(asset_dir)
                for variant_dir in variant_dirs:
                    publish_path = self._published_asset_path(variant_dir, asset_dir.name)
                    if not publish_path:
                        continue
                    thumbnail = asset_dir / "thumbnail.jpg"
                    rows.append(
                        {
                            "category": category_dir.name,
                            "group": "bp",
                            "asset": asset_dir.name,
                            "variant": variant_dir.name if variant_dir != asset_dir else "default",
                            "publish": str(publish_path),
                            "thumbnail": str(thumbnail) if thumbnail.exists() else "",
                        }
                    )
        return rows

    @staticmethod
    def _asset_variant_dirs(asset_dir: Path) -> list[Path]:
        variants = sorted(
            path
            for path in asset_dir.iterdir()
            if path.is_dir() and ((path / "variant.json").exists() or path.name == "default")
        )
        default = asset_dir / "default"
        if default.exists() and default not in variants:
            variants.insert(0, default)
        return variants or [asset_dir]

    @staticmethod
    def _published_asset_path(variant_dir: Path, asset_name: str) -> Path | None:
        candidates = [
            variant_dir / "publish" / "usd" / "latest.json",
            variant_dir / "publish" / "model" / "proxy" / "latest.json",
            variant_dir / "publish" / "model" / "render" / "latest.json",
        ]
        for latest in candidates:
            if latest.exists():
                return latest
        direct_candidates = [
            variant_dir / "publish" / "usd",
            variant_dir / "publish" / "model" / "proxy",
            variant_dir / "publish" / "model" / "render",
        ]
        for base in direct_candidates:
            if not base.exists():
                continue
            for path in base.rglob("*.usd"):
                if path.name in {f"{asset_name}.usd", "model.usd"}:
                    return path
        return None

    def _update_extract_path_label(self) -> None:
        asset = self.extract_asset_edit.text().strip()
        category = self.extract_category_edit.text().strip()
        group = self.extract_group_edit.text().strip()
        variant = self.extract_variant_edit.text().strip() or "default"
        department = self.extract_department_edit.text().strip() or "model"
        subset = self._extract_subset()
        if not (asset and category and group):
            self.extract_path_label.setText("")
            return
        root = self.project_config.project_root
        if root:
            path = (
                Path(root)
                / "assets"
                / category
                / group
                / asset
                / variant
                / department
                / "work"
                / "maya"
                / subset
            )
            self.extract_path_label.setText(str(path).replace("\\", "/"))

    def _extract_subset(self) -> str:
        return self.extract_subset_combo.currentText().strip() or "proxy"

    def _comment_dialog(self, title: str) -> str:
        text, accepted = QtWidgets.QInputDialog.getText(self, title, "Comment:")
        return str(text) if accepted else ""


_WINDOW = None


def show(config_dir: str | os.PathLike[str] | None = None, parent=None):
    global _WINDOW
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    from smartlib.core.qt import parent_for_maya

    window_parent = parent_for_maya(QtWidgets, parent)
    _WINDOW = AssetAssemblyWindow(config_dir=config_dir, parent=window_parent)
    if window_parent is not None:
        _WINDOW.setWindowFlags(_WINDOW.windowFlags() | QtCore.Qt.Window)
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    return _WINDOW

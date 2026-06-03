from __future__ import annotations

import os
import sys
from pathlib import Path

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
        self.resize(980, 680)
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        root = QtWidgets.QHBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)
        self.setCentralWidget(central)

        left = QtWidgets.QVBoxLayout()
        left.setSpacing(5)
        self.assembly_category_edit = QtWidgets.QLineEdit("env")
        self.assembly_group_edit = QtWidgets.QLineEdit("set")
        self.assembly_asset_edit = QtWidgets.QLineEdit("kitchen")
        self.assembly_variant_edit = QtWidgets.QLineEdit("default")
        self.apply_context_btn = QtWidgets.QPushButton("Apply Assembly Context")
        self.place_target_edit = QtWidgets.QLineEdit("component")
        self.create_loc_btn = QtWidgets.QPushButton("Create place_LOC")
        self.register_selected_btn = QtWidgets.QPushButton("Register Selected Mesh")
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.apply_context_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DialogApplyButton))
        self.create_loc_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_FileDialogNewFolder))
        self.register_selected_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DialogSaveButton))
        self.refresh_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_BrowserReload))
        self.place_tree = QtWidgets.QTreeWidget()
        self.place_tree.setColumnCount(3)
        self.place_tree.setHeaderLabels(["place_LOC", "target", "asset"])
        self.place_tree.header().setStretchLastSection(True)
        self.place_tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.place_tree.setAlternatingRowColors(True)
        self.place_tree.setIndentation(10)
        left.addWidget(QtWidgets.QLabel("place_LOC"))
        left.addWidget(self.place_target_edit)
        left.addWidget(self.create_loc_btn)
        left.addWidget(self.register_selected_btn)
        left.addWidget(self.refresh_btn)
        left.addWidget(self.place_tree, 1)

        center = QtWidgets.QVBoxLayout()
        self.tabs = QtWidgets.QTabWidget()
        assembly_tab = QtWidgets.QWidget()
        assembly_layout = QtWidgets.QVBoxLayout(assembly_tab)
        assembly_layout.setContentsMargins(4, 4, 4, 4)
        self.component_table = QtWidgets.QTableWidget(0, 6)
        self.component_table.setHorizontalHeaderLabels(["Target", "Asset", "Category", "Group", "Variant", "Locator"])
        self.component_table.horizontalHeader().setStretchLastSection(True)
        self.component_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.component_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.component_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.component_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        action_layout = QtWidgets.QHBoxLayout()
        self.duplicate_placement_btn = QtWidgets.QPushButton("Duplicate Placement")
        self.replace_asset_btn = QtWidgets.QPushButton("Replace Asset")
        self.set_variant_btn = QtWidgets.QPushButton("Set Variant")
        self.duplicate_placement_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_FileDialogNewFolder))
        self.replace_asset_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_BrowserReload))
        self.set_variant_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_FileDialogDetailedView))
        action_layout.addWidget(self.duplicate_placement_btn)
        action_layout.addWidget(self.replace_asset_btn)
        action_layout.addWidget(self.set_variant_btn)
        self.detail_table = QtWidgets.QTableWidget(0, 2)
        self.detail_table.setHorizontalHeaderLabels(["Key", "Value"])
        self.detail_table.horizontalHeader().setStretchLastSection(True)
        self.detail_table.verticalHeader().setVisible(False)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        splitter.addWidget(self.component_table)
        splitter.addWidget(self.detail_table)
        splitter.setSizes([420, 180])
        assembly_layout.addLayout(action_layout)
        assembly_layout.addWidget(splitter)
        self.tabs.addTab(assembly_tab, "Assembly")
        self.extract_tab = self._build_extract_tab()
        self.tabs.addTab(self.extract_tab, "Extract Component")
        self.tabs.addTab(QtWidgets.QWidget(), "Data")
        self.tabs.addTab(QtWidgets.QWidget(), "Publish")
        center.addWidget(self.tabs, 1)

        right = QtWidgets.QVBoxLayout()
        right.setSpacing(8)
        context_group = QtWidgets.QGroupBox("Assembly Context")
        context_layout = QtWidgets.QFormLayout(context_group)
        context_layout.setContentsMargins(8, 8, 8, 8)
        context_layout.setSpacing(6)
        context_layout.addRow("Category", self.assembly_category_edit)
        context_layout.addRow("Group", self.assembly_group_edit)
        context_layout.addRow("Asset", self.assembly_asset_edit)
        context_layout.addRow("Variant", self.assembly_variant_edit)
        context_layout.addRow("", self.apply_context_btn)
        self.info_table = QtWidgets.QTableWidget(0, 2)
        self.info_table.setHorizontalHeaderLabels(["Key", "Value"])
        self.info_table.horizontalHeader().setStretchLastSection(True)
        self.info_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.info_table.verticalHeader().setVisible(False)
        self.info_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.open_btn = QtWidgets.QPushButton("Open Assembly")
        self.reload_btn = QtWidgets.QPushButton("Reload Assembly")
        self.save_btn = QtWidgets.QPushButton("Save Assembly")
        self.publish_btn = QtWidgets.QPushButton("Publish Assembly")
        self.open_btn.setMinimumHeight(44)
        self.reload_btn.setMinimumHeight(36)
        self.save_btn.setMinimumHeight(44)
        self.publish_btn.setMinimumHeight(44)
        self.open_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DirOpenIcon))
        self.reload_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_BrowserReload))
        self.save_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DialogSaveButton))
        self.publish_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_ArrowUp))
        self.open_btn.setStyleSheet("QPushButton { background-color:#3d7f36; color:white; font-weight:bold; }")
        self.reload_btn.setStyleSheet("QPushButton { background-color:#4e6f3a; color:white; font-weight:bold; }")
        self.save_btn.setStyleSheet("QPushButton { background-color:#2d5d86; color:white; font-weight:bold; }")
        self.publish_btn.setStyleSheet("QPushButton { background-color:#246ba3; color:white; font-weight:bold; }")
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)
        right.addWidget(context_group)
        right.addWidget(QtWidgets.QLabel("Assembly Info"))
        right.addWidget(self.info_table)
        right.addWidget(self.open_btn)
        right.addWidget(self.reload_btn)
        right.addWidget(self.save_btn)
        right.addWidget(self.publish_btn)
        right.addWidget(self.status_label)

        root.addLayout(left, 1)
        root.addLayout(center, 3)
        root.addLayout(right, 1)

        self.apply_context_btn.clicked.connect(self.apply_assembly_context)
        self.create_loc_btn.clicked.connect(self.create_locator)
        self.register_selected_btn.clicked.connect(self.register_selected)
        self.refresh_btn.clicked.connect(self.refresh)
        self.open_btn.clicked.connect(self.open_assembly)
        self.reload_btn.clicked.connect(self.reload_assembly)
        self.save_btn.clicked.connect(self.save_assembly)
        self.publish_btn.clicked.connect(self.publish_assembly)
        self.duplicate_placement_btn.clicked.connect(self.duplicate_placement)
        self.replace_asset_btn.clicked.connect(self.replace_asset)
        self.set_variant_btn.clicked.connect(self.set_variant)
        self.component_table.itemSelectionChanged.connect(self.populate_detail)
        self.place_tree.itemSelectionChanged.connect(self.select_component_from_place_tree)

    def _build_extract_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        form = QtWidgets.QFormLayout()
        self.extract_target_edit = QtWidgets.QLineEdit()
        self.extract_asset_edit = QtWidgets.QLineEdit()
        self.extract_category_edit = QtWidgets.QLineEdit("prop")
        self.extract_group_edit = QtWidgets.QLineEdit("bp")
        self.extract_variant_edit = QtWidgets.QLineEdit("default")
        self.extract_department_edit = QtWidgets.QLineEdit("model")
        self.extract_subset_edit = QtWidgets.QLineEdit("render")
        self.extract_center_check = QtWidgets.QCheckBox("Center to origin")
        self.extract_center_check.setChecked(True)
        form.addRow("Target", self.extract_target_edit)
        form.addRow("Asset", self.extract_asset_edit)
        form.addRow("Category", self.extract_category_edit)
        form.addRow("Group", self.extract_group_edit)
        form.addRow("Variant", self.extract_variant_edit)
        form.addRow("Department", self.extract_department_edit)
        form.addRow("Subset", self.extract_subset_edit)
        form.addRow("", self.extract_center_check)
        layout.addLayout(form)

        self.extract_path_label = QtWidgets.QLabel("")
        self.extract_path_label.setWordWrap(True)
        self.extract_btn = QtWidgets.QPushButton("Extract Selected Component")
        self.publish_component_btn = QtWidgets.QPushButton("Publish Component Model")
        self.compose_asset_usd_btn = QtWidgets.QPushButton("Compose Asset USD")
        self.extract_btn.setMinimumHeight(36)
        self.publish_component_btn.setMinimumHeight(36)
        self.compose_asset_usd_btn.setMinimumHeight(36)
        self.extract_btn.setStyleSheet("QPushButton { background-color:#2d5d86; color:white; font-weight:bold; }")
        self.publish_component_btn.setStyleSheet("QPushButton { background-color:#246ba3; color:white; font-weight:bold; }")
        self.compose_asset_usd_btn.setStyleSheet("QPushButton { background-color:#246ba3; color:white; font-weight:bold; }")
        layout.addWidget(self.extract_path_label)
        layout.addStretch(1)
        layout.addWidget(self.extract_btn)
        layout.addWidget(self.publish_component_btn)
        layout.addWidget(self.compose_asset_usd_btn)
        self.extract_btn.clicked.connect(self.extract_component)
        self.publish_component_btn.clicked.connect(self.publish_component_model)
        self.compose_asset_usd_btn.clicked.connect(self.compose_asset_usd)
        return tab

    def refresh(self) -> None:
        from smartlib.dcc.maya import asset_assembly

        try:
            self.context = asset_assembly.current_assembly_context(self.project_config)
            self.components = asset_assembly.list_components()
            self._populate_context_fields()
            self.status_label.setText("")
        except Exception as exc:
            self.components = []
            self.status_label.setText(str(exc))
        self.populate_components()
        self.populate_info()

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
            values = [component.target, component.asset, component.category, component.group, component.variant, component.locator]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(str(value))
                item.setData(QtCore.Qt.UserRole, component)
                self.component_table.setItem(row, column, item)
            tree_item = QtWidgets.QTreeWidgetItem([component.locator, component.target, component.asset])
            tree_item.setData(0, QtCore.Qt.UserRole, component.locator)
            self.place_tree.addTopLevelItem(tree_item)
        self.component_table.resizeColumnsToContents()
        self.component_table.horizontalHeader().setStretchLastSection(True)

    def populate_info(self) -> None:
        context = self.context
        rows = [
            ("asset", getattr(context, "asset", "")),
            ("category", getattr(context, "category", "")),
            ("group", getattr(context, "group", "")),
            ("variant", getattr(context, "variant", "")),
            ("components", str(len(getattr(self, "components", [])))),
            ("publish", "assembly/render/latest"),
        ]
        self.info_table.setRowCount(0)
        for key, value in rows:
            row = self.info_table.rowCount()
            self.info_table.insertRow(row)
            self.info_table.setItem(row, 0, QtWidgets.QTableWidgetItem(key))
            self.info_table.setItem(row, 1, QtWidgets.QTableWidgetItem(value))

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

        target = self.place_target_edit.text().strip() or "component"
        try:
            row = asset_assembly.register_selected_component(
                target,
                asset=target,
                category="prop",
                group="bp",
                variant="default",
            )
            self.status_label.setText(f"Registered {row.target}")
        except Exception as exc:
            self.status_label.setText(str(exc))
        self.refresh()

    def save_assembly(self) -> None:
        from smartlib.dcc.maya import asset_assembly

        try:
            comment = self._comment_dialog("Save Assembly")
            path = asset_assembly.save_assembly(self.project_config, comment=comment)
            self.status_label.setText(f"Saved {path}")
        except Exception as exc:
            self.status_label.setText(str(exc))
        self.refresh()

    def publish_assembly(self) -> None:
        from smartlib.dcc.maya import asset_assembly

        try:
            comment = self._comment_dialog("Publish Assembly")
            path = asset_assembly.publish_assembly(self.project_config, comment=comment)
            self.status_label.setText(f"Published {path}")
        except Exception as exc:
            self.status_label.setText(str(exc))
        self.refresh()

    def extract_component(self) -> None:
        from smartlib.dcc.maya import asset_assembly

        component = self._selected_component()
        if not component:
            self.status_label.setText("Select a component.")
            return
        component = self._component_from_extract_fields()
        try:
            result = asset_assembly.extract_component(
                self.project_config,
                component,
                department=self.extract_department_edit.text().strip() or "model",
                subset=self.extract_subset_edit.text().strip() or "render",
                center_to_origin=self.extract_center_check.isChecked(),
            )
            message = f"Extracted {result.workfile}"
            if result.warning:
                message = f"{message}\n{result.warning}"
            self.status_label.setText(message)
            self.extract_path_label.setText(str(result.workfile).replace("\\", "/"))
        except Exception as exc:
            self.status_label.setText(str(exc))
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
                subset=self.extract_subset_edit.text().strip() or "render",
                comment=comment,
            )
            message = f"Published {result.usd_path}"
            if result.asset_usd_path:
                message = f"{message}\nComposed {result.asset_usd_path}"
            if result.usd_error:
                message = f"{message}\nUSD export warning: {result.usd_error}"
            self.status_label.setText(message)
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
                subset=self.extract_subset_edit.text().strip() or "render",
                comment=comment,
            )
            self.status_label.setText(f"Composed {path}")
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
            self.status_label.setText(f"Opened assembly USD: {path}")
        except Exception as exc:
            self.status_label.setText(str(exc))

    def reload_assembly(self) -> None:
        from smartlib.dcc.maya import asset_assembly

        try:
            path = asset_assembly.open_assembly_usd(self.project_config, reload=True)
            self.status_label.setText(f"Reloaded assembly USD: {path}")
        except Exception as exc:
            self.status_label.setText(str(exc))

    def select_component_from_place_tree(self) -> None:
        item = self.place_tree.currentItem()
        locator = str(item.data(0, QtCore.Qt.UserRole) or "") if item else ""
        for row in range(self.component_table.rowCount()):
            table_item = self.component_table.item(row, 5)
            if table_item and table_item.text() == locator:
                self.component_table.selectRow(row)
                return

    def _selected_component(self):
        row = self.component_table.currentRow()
        if row < 0:
            return None
        item = self.component_table.item(row, 0)
        return item.data(QtCore.Qt.UserRole) if item else None

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
        )

    def _update_extract_path_label(self) -> None:
        asset = self.extract_asset_edit.text().strip()
        category = self.extract_category_edit.text().strip()
        group = self.extract_group_edit.text().strip()
        variant = self.extract_variant_edit.text().strip() or "default"
        department = self.extract_department_edit.text().strip() or "model"
        subset = self.extract_subset_edit.text().strip() or "render"
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

    def _comment_dialog(self, title: str) -> str:
        text, accepted = QtWidgets.QInputDialog.getText(self, title, "Comment:")
        return str(text) if accepted else ""


_WINDOW = None


def show(config_dir: str | os.PathLike[str] | None = None):
    global _WINDOW
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    _WINDOW = AssetAssemblyWindow(config_dir=config_dir)
    _WINDOW.show()
    return _WINDOW

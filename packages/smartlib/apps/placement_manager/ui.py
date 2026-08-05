from __future__ import annotations

import os
from pathlib import Path

from smartlib.apps.common.asset_cards import (
    asset_card_text,
    asset_icon,
    asset_tooltip,
    configure_asset_card_list,
    set_label_thumbnail,
)
from smartlib.apps.smart_casting.service import CastingAsset, SmartCastingService
from smartlib.core.config_loader import ProjectConfig


def _qt_modules():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets

        return QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets

        return QtCore, QtGui, QtWidgets


QtCore, QtGui, QtWidgets = _qt_modules()

try:
    _USER_ROLE = int(QtCore.Qt.UserRole)
except TypeError:
    _USER_ROLE = int(QtCore.Qt.UserRole.value)

TREE_KIND_ROLE = _USER_ROLE + 1
TREE_MEMBER_ROLE = _USER_ROLE + 2
TREE_KIND_LOCATOR = "locator"
TREE_KIND_ASSET = "asset"


class PlacementTreeWidget(QtWidgets.QTreeWidget):
    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner

    def dropEvent(self, event):
        dragged_nodes = []
        for item in self.selectedItems():
            node = item.data(0, QtCore.Qt.UserRole)
            if node:
                dragged_nodes.append(str(node))
        super().dropEvent(event)
        for node in dragged_nodes:
            item = self._find_item_by_node(node)
            if item is None:
                continue
            parent_item = item.parent()
            parent_node = str(parent_item.data(0, QtCore.Qt.UserRole)) if parent_item else ""
            self.owner.apply_tree_parent(node, parent_node)

    def _find_item_by_node(self, node: str):
        root = self.invisibleRootItem()
        stack = [root.child(index) for index in range(root.childCount())]
        while stack:
            item = stack.pop(0)
            if str(item.data(0, QtCore.Qt.UserRole) or "") == node:
                return item
            stack.extend(item.child(index) for index in range(item.childCount()))
        return None


def _default_config_dir() -> Path:
    env_path = os.environ.get("PROJECT_CONFIG_DIR")
    if env_path:
        return Path(env_path)
    root = Path(os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT") or Path(__file__).resolve().parents[4])
    return root / "config" / "STKB"


class SmartMakerWindow(QtWidgets.QMainWindow):
    def __init__(self, config_dir: str | os.PathLike[str] | None = None, parent=None):
        super().__init__(parent)
        self.project_config = ProjectConfig(config_dir or _default_config_dir())
        self.asset_service = SmartCastingService(self.project_config)
        self.asset_rows: list[CastingAsset] = []
        self.cast_members = []
        self._populating_tree = False
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        self.setWindowTitle(f"Smart Maker - {self.project_config.project_name}")
        self.resize(760, 720)
        central = QtWidgets.QWidget()
        root_layout = QtWidgets.QVBoxLayout(central)
        root_layout.setContentsMargins(6, 6, 6, 6)
        root_layout.setSpacing(6)
        self.setCentralWidget(central)

        self.tabs = QtWidgets.QTabWidget()
        self.stage_tab = QtWidgets.QWidget()
        self.assets_tab = QtWidgets.QWidget()
        self.tabs.addTab(self.stage_tab, "Stage")
        self.tabs.addTab(self.assets_tab, "Asset")
        root_layout.addWidget(self.tabs, 1)

        root_layout = QtWidgets.QVBoxLayout(self.stage_tab)
        root_layout.setContentsMargins(4, 4, 4, 4)
        root_layout.setSpacing(6)

        top_layout = QtWidgets.QHBoxLayout()
        top_layout.addStretch(1)
        self.create_btn = QtWidgets.QPushButton("Create Place Locator")
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        top_layout.addWidget(self.create_btn)
        top_layout.addWidget(self.refresh_btn)
        root_layout.addLayout(top_layout)

        self.placement_tree = PlacementTreeWidget(self)
        self.placement_tree.setColumnCount(3)
        self.placement_tree.setHeaderLabels(["name", "category", "group"])
        self.placement_tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.placement_tree.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.placement_tree.setDefaultDropAction(QtCore.Qt.MoveAction)
        self.placement_tree.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.EditKeyPressed)
        self.placement_tree.setAlternatingRowColors(True)
        self.placement_tree.setIndentation(14)
        self.placement_tree.setIconSize(QtCore.QSize(64, 36))
        self.placement_tree.header().setStretchLastSection(True)
        self.placement_tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.placement_tree.setMinimumHeight(260)
        root_layout.addWidget(self.placement_tree, 2)

        cast_header = QtWidgets.QHBoxLayout()
        cast_header.addWidget(QtWidgets.QLabel("Cast Members:"))
        cast_header.addStretch(1)
        self.assign_btn = QtWidgets.QPushButton("Assign To Placement")
        self.constrain_btn = QtWidgets.QPushButton("Constrain To Placement")
        self.attach_btn = QtWidgets.QPushButton("Hierarchy Attach")
        self.parent_btn = QtWidgets.QPushButton("Parent")
        self.delete_btn = QtWidgets.QPushButton("Delete")
        cast_header.addWidget(self.assign_btn)
        cast_header.addWidget(self.constrain_btn)
        cast_header.addWidget(self.attach_btn)
        cast_header.addWidget(self.parent_btn)
        cast_header.addWidget(self.delete_btn)
        root_layout.addLayout(cast_header)

        self.cast_table = QtWidgets.QTableWidget()
        self.cast_table.setColumnCount(3)
        self.cast_table.setHorizontalHeaderLabels(["name", "category", "group"])
        self.cast_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.cast_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.cast_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.cast_table.verticalHeader().setVisible(False)
        self.cast_table.verticalHeader().setDefaultSectionSize(28)
        self.cast_table.horizontalHeader().setStretchLastSection(True)
        self.cast_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        root_layout.addWidget(self.cast_table, 1)

        bottom_layout = QtWidgets.QHBoxLayout()
        self.export_btn = QtWidgets.QPushButton("Export Metadata")
        self.publish_btn = QtWidgets.QPushButton("Publish Placement")
        self.status_label = QtWidgets.QLabel("")
        bottom_layout.addWidget(self.export_btn)
        bottom_layout.addWidget(self.publish_btn)
        bottom_layout.addWidget(self.status_label, 1)
        root_layout.addLayout(bottom_layout)

        self._build_assets_tab()

        self.create_btn.clicked.connect(self.create_locator)
        self.refresh_btn.clicked.connect(self.refresh)
        self.assign_btn.clicked.connect(self.assign_to_placement)
        self.constrain_btn.clicked.connect(self.constrain_to_placement)
        self.attach_btn.clicked.connect(self.attach_hierarchy)
        self.parent_btn.clicked.connect(self.parent_placements)
        self.delete_btn.clicked.connect(self.delete_placements)
        self.export_btn.clicked.connect(self.export_metadata)
        self.publish_btn.clicked.connect(self.publish_placement)
        self.placement_tree.itemChanged.connect(self.rename_placement_item)

    def _build_assets_tab(self) -> None:
        layout = QtWidgets.QHBoxLayout(self.assets_tab)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        left = QtWidgets.QVBoxLayout()
        filter_row = QtWidgets.QHBoxLayout()
        self.asset_search = QtWidgets.QLineEdit()
        self.asset_search.setPlaceholderText("Search asset")
        self.asset_search.setClearButtonEnabled(True)
        self.asset_refresh_btn = QtWidgets.QPushButton("Refresh")
        filter_row.addWidget(self.asset_search, 1)
        filter_row.addWidget(self.asset_refresh_btn)

        self.asset_list = QtWidgets.QListWidget()
        configure_asset_card_list(self.asset_list, QtCore, QtWidgets)
        self.asset_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        left.addLayout(filter_row)
        left.addWidget(self.asset_list, 1)

        action_row = QtWidgets.QHBoxLayout()
        self.add_asset_cast_btn = QtWidgets.QPushButton("Edit in Smart Casting")
        self.reference_asset_btn = QtWidgets.QPushButton("Reference")
        self.add_reference_asset_btn = QtWidgets.QPushButton("Reference Selected")
        self.add_reference_asset_btn.setStyleSheet("QPushButton { background-color:#2d5d86; color:white; font-weight:bold; }")
        action_row.addStretch(1)
        action_row.addWidget(self.add_asset_cast_btn)
        action_row.addWidget(self.reference_asset_btn)
        action_row.addWidget(self.add_reference_asset_btn)
        left.addLayout(action_row)

        right = QtWidgets.QVBoxLayout()
        self.asset_thumb = QtWidgets.QLabel("Thumbnail")
        self.asset_thumb.setFixedSize(220, 124)
        self.asset_thumb.setAlignment(QtCore.Qt.AlignCenter)
        self.asset_thumb.setStyleSheet("background:#30363d; border:1px solid #4a4a4a;")
        self.asset_detail = QtWidgets.QTableWidget(0, 2)
        self.asset_detail.setHorizontalHeaderLabels(["Key", "Value"])
        self.asset_detail.horizontalHeader().setStretchLastSection(True)
        self.asset_detail.verticalHeader().setVisible(False)
        self.asset_detail.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        right.addWidget(self.asset_thumb, 0, QtCore.Qt.AlignHCenter)
        right.addWidget(self.asset_detail, 1)

        layout.addLayout(left, 3)
        layout.addLayout(right, 1)

        self.asset_search.textChanged.connect(lambda _text: self.populate_asset_cards())
        self.asset_refresh_btn.clicked.connect(self.refresh)
        self.asset_list.itemSelectionChanged.connect(self.populate_asset_detail)
        self.add_asset_cast_btn.clicked.connect(self.add_selected_assets_to_cast)
        self.reference_asset_btn.clicked.connect(self.reference_selected_assets)
        self.add_reference_asset_btn.clicked.connect(self.add_and_reference_selected_assets)

    def refresh(self) -> None:
        from smartlib.dcc.maya import placement

        try:
            self.asset_rows = self.asset_service.list_assets()
        except Exception as exc:
            self.asset_rows = []
            self.status_label.setText(str(exc))

        try:
            self.cast_members = placement.list_cast_members(self.project_config)
            locators = placement.list_placement_locators()
        except Exception as exc:
            self.status_label.setText(str(exc))
            self.cast_members = []
            locators = []
        self.populate_asset_cards()
        self._populate_cast_table()
        self._populate_placement_tree(locators)

    def populate_asset_cards(self) -> None:
        if not getattr(self, "asset_list", None):
            return
        query = self.asset_search.text().strip().lower()
        self.asset_list.clear()
        for asset in self.asset_rows:
            label = asset_card_text(
                asset=asset.asset,
                category=asset.category,
                group=asset.group,
                variant=asset.variant,
                status=asset.status,
                asset_type=asset.category,
                description=asset.description,
            )
            haystack = " ".join([label, asset.category, asset.group, asset.asset, asset.variant, asset.status, asset.description]).lower()
            if query and query not in haystack:
                continue
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, asset)
            item.setToolTip(
                asset_tooltip(
                    asset=asset.asset,
                    category=asset.category,
                    group=asset.group,
                    variant=asset.variant,
                    status=asset.status,
                    description=asset.description,
                    extra={"path": asset.path},
                )
            )
            item.setIcon(asset_icon(QtCore, QtGui, thumbnail=asset.thumbnail, label=asset.asset))
            self.asset_list.addItem(item)
        if self.asset_list.count() == 0:
            item = QtWidgets.QListWidgetItem("No assets")
            item.setFlags(item.flags() & ~QtCore.Qt.ItemIsSelectable)
            self.asset_list.addItem(item)

    def populate_asset_detail(self) -> None:
        assets = self._selected_assets()
        self.asset_detail.setRowCount(0)
        if not assets:
            self.asset_thumb.clear()
            self.asset_thumb.setText("Thumbnail")
            return
        asset = assets[0]
        set_label_thumbnail(QtCore, QtGui, self.asset_thumb, asset.thumbnail)
        rows = {
            "Asset": asset.asset,
            "Category": asset.category,
            "Group": asset.group,
            "Variant": asset.variant,
            "Status": asset.status,
            "Path": asset.path,
        }
        for key, value in rows.items():
            row = self.asset_detail.rowCount()
            self.asset_detail.insertRow(row)
            self.asset_detail.setItem(row, 0, QtWidgets.QTableWidgetItem(str(key)))
            self.asset_detail.setItem(row, 1, QtWidgets.QTableWidgetItem(str(value)))

    def add_selected_assets_to_cast(self) -> None:
        assets = self._selected_assets()
        if not assets:
            self.status_label.setText("Select assets.")
            return
        try:
            from smartlib.apps.smart_casting.ui import show

            show(
                config_dir=self.project_config.config_dir,
                parent=self,
                asset_names=[asset.asset for asset in assets],
            )
            self.status_label.setText("Opened Smart Casting. Cast changes are managed there.")
        except Exception as exc:
            self.status_label.setText(str(exc))
            QtWidgets.QMessageBox.critical(self, "Add To Cast Failed", str(exc))

    def reference_selected_assets(self) -> None:
        from smartlib.dcc.maya import placement

        assets = self._selected_assets()
        if not assets:
            self.status_label.setText("Select assets.")
            return
        try:
            referenced = placement.reference_assets_to_scene(self.project_config, assets)
            self.status_label.setText(f"Referenced {len(referenced)} asset(s)")
            self.refresh()
        except Exception as exc:
            self.status_label.setText(str(exc))
            QtWidgets.QMessageBox.critical(self, "Reference Failed", str(exc))

    def add_and_reference_selected_assets(self) -> None:
        from smartlib.dcc.maya import placement

        assets = self._selected_assets()
        if not assets:
            self.status_label.setText("Select assets.")
            return
        try:
            referenced = placement.reference_assets_to_scene(self.project_config, assets)
            self.status_label.setText(f"Referenced {len(referenced)} asset(s). Use Smart Casting to edit cast.")
            self.refresh()
        except Exception as exc:
            self.status_label.setText(str(exc))
            QtWidgets.QMessageBox.critical(self, "Add + Reference Failed", str(exc))

    def create_locator(self) -> None:
        from smartlib.dcc.maya import placement

        member = self._selected_cast_member()
        cast_id = member.role if member and member.role else (member.name if member else "PLACEMENT")
        try:
            node = placement.create_placement_locator(
                cast_id,
                category=member.category if member else "",
                group=member.group if member else "",
            )
            if member:
                placement.assign_member_to_placement(node, member)
            self.status_label.setText(f"Created {node}")
        except Exception as exc:
            self.status_label.setText(str(exc))
        self.refresh()

    def assign_to_placement(self) -> None:
        from smartlib.dcc.maya import placement

        member = self._selected_cast_member()
        locator = self._selected_locator()
        if not member:
            self.status_label.setText("Select a cast member.")
            return
        try:
            if not locator:
                locator = placement.selected_placement_locator()
            placement.assign_member_to_placement(locator, member)
            self.status_label.setText(f"Assigned {member.name} to {locator}")
        except Exception as exc:
            self.status_label.setText(str(exc))
        self.refresh()

    def constrain_to_placement(self) -> None:
        from smartlib.dcc.maya import placement

        locator = self._selected_locator() or placement.selected_placement_locator()
        try:
            target = placement.constrain_member_to_placement(self.project_config, locator)
            message = f"Constrained {target.target} to {locator}"
            if target.warning:
                message = f"{message} ({target.warning}; used {target.source})"
            self.status_label.setText(message)
        except Exception as exc:
            self.status_label.setText(str(exc))
        self.refresh()

    def attach_hierarchy(self) -> None:
        from smartlib.dcc.maya import placement

        locator = self._selected_locator() or placement.selected_placement_locator()
        try:
            attached = placement.attach_selected_hierarchy(locator)
            self.status_label.setText(f"Attached {len(attached)} nodes")
        except Exception as exc:
            self.status_label.setText(str(exc))
        self.refresh()

    def parent_placements(self) -> None:
        from smartlib.dcc.maya import placement

        selected = self._selected_locators()
        if len(selected) != 2:
            self.status_label.setText("Select child placement, then parent placement.")
            return
        try:
            placement.parent_placements(selected[0], selected[1])
            self.status_label.setText(f"Parented {selected[0]} under {selected[1]}")
        except Exception as exc:
            self.status_label.setText(str(exc))
        self.refresh()

    def apply_tree_parent(self, child: str, parent: str) -> None:
        from smartlib.dcc.maya import placement

        try:
            placement.set_parent_placement(child, parent)
            if parent:
                self.status_label.setText(f"Parented {child} under {parent}")
            else:
                self.status_label.setText(f"Moved {child} to world")
        except Exception as exc:
            self.status_label.setText(str(exc))
            self.refresh()

    def rename_placement_item(self, item, column: int) -> None:
        if self._populating_tree or column != 0:
            return
        from smartlib.dcc.maya import placement

        old_node = str(item.data(0, QtCore.Qt.UserRole) or "")
        new_name = item.text(0).strip()
        if not old_node or not new_name:
            self.refresh()
            return
        try:
            renamed = placement.rename_placement_locator(old_node, new_name)
            self._populating_tree = True
            try:
                item.setText(0, renamed.split("|")[-1])
                item.setData(0, QtCore.Qt.UserRole, renamed)
            finally:
                self._populating_tree = False
            self.status_label.setText(f"Renamed {old_node} to {renamed}")
        except Exception as exc:
            self.status_label.setText(str(exc))
            self.refresh()

    def delete_placements(self) -> None:
        from smartlib.dcc.maya import placement

        selected = self._selected_locators()
        try:
            placement.delete_placements(selected)
            self.status_label.setText(f"Deleted {len(selected)} placements")
        except Exception as exc:
            self.status_label.setText(str(exc))
        self.refresh()

    def export_metadata(self) -> None:
        from smartlib.dcc.maya import placement

        try:
            placements_path, members_path = placement.export_metadata(self.project_config)
            self.status_label.setText(f"Exported {placements_path.parent}")
        except Exception as exc:
            self.status_label.setText(str(exc))

    def publish_placement(self) -> None:
        from smartlib.dcc.maya import placement

        comment, accepted = QtWidgets.QInputDialog.getText(self, "Publish Placement", "Comment")
        if not accepted:
            return
        try:
            version_dir = placement.publish_placement(self.project_config, comment=comment.strip())
            self.status_label.setText(f"Published {version_dir}")
        except Exception as exc:
            self.status_label.setText(str(exc))

    def _populate_cast_table(self) -> None:
        self.cast_table.setRowCount(0)
        for row, member in enumerate(self.cast_members):
            self.cast_table.insertRow(row)
            for column, value in enumerate((member.name, member.category, member.group)):
                item = QtWidgets.QTableWidgetItem(str(value))
                item.setData(QtCore.Qt.UserRole, member.name)
                self.cast_table.setItem(row, column, item)

    def _populate_placement_tree(self, locators) -> None:
        self._populating_tree = True
        try:
            self.placement_tree.clear()
            member_by_name = {member.name: member for member in self.cast_members}
            items = {}
            for row in locators:
                item = QtWidgets.QTreeWidgetItem([row.name, row.category, row.group])
                item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable | QtCore.Qt.ItemIsDragEnabled | QtCore.Qt.ItemIsDropEnabled)
                item.setData(0, QtCore.Qt.UserRole, row.node)
                item.setData(0, TREE_KIND_ROLE, TREE_KIND_LOCATOR)
                if row.member:
                    item.setData(0, TREE_MEMBER_ROLE, row.member)
                items[row.node] = item
            for row in locators:
                item = items[row.node]
                parent_item = items.get(row.parent)
                if parent_item:
                    parent_item.addChild(item)
                else:
                    self.placement_tree.addTopLevelItem(item)
                if row.member:
                    member = member_by_name.get(row.member)
                    if member:
                        item.addChild(self._asset_tree_item(member))
            self.placement_tree.expandAll()
        finally:
            self._populating_tree = False

    def _asset_tree_item(self, member) -> QtWidgets.QTreeWidgetItem:
        asset = self._asset_for_member(member)
        label = member.asset or member.name
        if member.name and member.name != label:
            label = f"{label}  ({member.name})"
        variant = getattr(member, "variant", "default") or "default"
        path_text = "/".join(part for part in (member.category, member.group, variant if variant != "default" else "") if part)
        item = QtWidgets.QTreeWidgetItem([label, member.category, member.group])
        item.setData(0, QtCore.Qt.UserRole, "")
        item.setData(0, TREE_KIND_ROLE, TREE_KIND_ASSET)
        item.setData(0, TREE_MEMBER_ROLE, member.name)
        item.setSizeHint(0, QtCore.QSize(220, 42))
        item.setToolTip(
            0,
            asset_tooltip(
                asset=member.asset,
                category=member.category,
                group=member.group,
                variant=variant,
                status=getattr(asset, "status", ""),
                description=getattr(asset, "description", ""),
                extra={"cast": member.name, "namespace": member.namespace, "path": path_text},
            ),
        )
        item.setIcon(0, asset_icon(QtCore, QtGui, thumbnail=getattr(asset, "thumbnail", ""), label=member.asset, width=64, height=36))
        flags = item.flags()
        flags &= ~QtCore.Qt.ItemIsEditable
        flags &= ~QtCore.Qt.ItemIsDragEnabled
        flags &= ~QtCore.Qt.ItemIsDropEnabled
        item.setFlags(flags)
        return item

    def _asset_for_member(self, member) -> CastingAsset | None:
        asset_name = str(getattr(member, "asset", "") or "")
        variant = str(getattr(member, "variant", "default") or "default")
        category = str(getattr(member, "category", "") or "")
        group = str(getattr(member, "group", "") or "")
        matches = [
            row
            for row in self.asset_rows
            if row.asset == asset_name
            and (not category or row.category == category)
            and (not group or row.group == group)
        ]
        if not matches:
            return None
        return next((row for row in matches if row.variant == variant), matches[0])

    def _selected_cast_member(self):
        rows = self.cast_table.selectionModel().selectedRows() if self.cast_table.selectionModel() else []
        if not rows:
            return None
        row = rows[0].row()
        if row < 0 or row >= len(self.cast_members):
            return None
        return self.cast_members[row]

    def _selected_locator(self) -> str:
        locators = self._selected_locators()
        return locators[0] if locators else ""

    def _selected_locators(self) -> list[str]:
        nodes = []
        for item in self.placement_tree.selectedItems():
            node = item.data(0, QtCore.Qt.UserRole)
            if node:
                nodes.append(str(node))
        return nodes

    def _selected_assets(self) -> list[CastingAsset]:
        rows = []
        for item in self.asset_list.selectedItems():
            data = item.data(QtCore.Qt.UserRole)
            if isinstance(data, CastingAsset):
                rows.append(data)
        return rows


_WINDOW = None
PlacementManagerWindow = SmartMakerWindow


def show(config_dir: str | os.PathLike[str] | None = None, parent=None):
    global _WINDOW
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    from smartlib.core.qt import parent_for_maya

    window_parent = parent_for_maya(QtWidgets, parent)
    if _WINDOW is None:
        _WINDOW = SmartMakerWindow(config_dir=config_dir, parent=window_parent)
    else:
        if window_parent is not None and _WINDOW.parent() is not window_parent:
            _WINDOW.setParent(window_parent)
        _WINDOW.project_config = ProjectConfig(config_dir or _default_config_dir())
        _WINDOW.asset_service = SmartCastingService(_WINDOW.project_config)
        _WINDOW.refresh()
    if window_parent is not None:
        _WINDOW.setWindowFlags(_WINDOW.windowFlags() | QtCore.Qt.Window)
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    return _WINDOW

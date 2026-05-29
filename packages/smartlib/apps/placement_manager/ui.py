from __future__ import annotations

import os
from pathlib import Path

from smartlib.core.config_loader import ProjectConfig


def _qt_modules():
    try:
        from PySide6 import QtCore, QtWidgets

        return QtCore, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtWidgets

        return QtCore, QtWidgets


QtCore, QtWidgets = _qt_modules()


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


class PlacementManagerWindow(QtWidgets.QMainWindow):
    def __init__(self, config_dir: str | os.PathLike[str] | None = None, parent=None):
        super().__init__(parent)
        self.project_config = ProjectConfig(config_dir or _default_config_dir())
        self.cast_members = []
        self._populating_tree = False
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        self.setWindowTitle(f"Placement Manager - {self.project_config.project_name}")
        self.resize(460, 650)
        central = QtWidgets.QWidget()
        root_layout = QtWidgets.QVBoxLayout(central)
        root_layout.setContentsMargins(6, 6, 6, 6)
        root_layout.setSpacing(6)
        self.setCentralWidget(central)

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

    def refresh(self) -> None:
        from smartlib.dcc.maya import placement

        try:
            self.cast_members = placement.list_cast_members(self.project_config)
            locators = placement.list_placement_locators()
        except Exception as exc:
            self.status_label.setText(str(exc))
            self.cast_members = []
            locators = []
        self._populate_cast_table()
        self._populate_placement_tree(locators)

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
            by_node = {row.node: row for row in locators}
            items = {}
            for row in locators:
                item = QtWidgets.QTreeWidgetItem([row.name, row.category, row.group])
                item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable | QtCore.Qt.ItemIsDragEnabled | QtCore.Qt.ItemIsDropEnabled)
                item.setData(0, QtCore.Qt.UserRole, row.node)
                items[row.node] = item
            for row in locators:
                item = items[row.node]
                parent_item = items.get(row.parent)
                if parent_item:
                    parent_item.addChild(item)
                else:
                    self.placement_tree.addTopLevelItem(item)
            self.placement_tree.expandAll()
        finally:
            self._populating_tree = False

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


_WINDOW = None


def show(config_dir: str | os.PathLike[str] | None = None):
    global _WINDOW
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    if _WINDOW is None:
        _WINDOW = PlacementManagerWindow(config_dir=config_dir)
    else:
        _WINDOW.project_config = ProjectConfig(config_dir or _default_config_dir())
        _WINDOW.refresh()
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    return _WINDOW

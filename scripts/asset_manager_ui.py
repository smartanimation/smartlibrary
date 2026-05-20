from __future__ import annotations

import os
import sys
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
    def __init__(self, manager: AssetManager | None = None, parent=None):
        super().__init__(parent)
        self.manager = manager or AssetManager()
        self.assets: list[Asset] = []
        self.published_work_icon = self.style().standardIcon(QtWidgets.QStyle.SP_DialogApplyButton)
        self.setWindowTitle(f"Asset Manager - {self.manager.project_name}")
        self.resize(760, 460)
        self._build_ui()
        self.refresh_assets()

    def _build_ui(self) -> None:
        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(4, 4, 4, 4)
        root_layout.setSpacing(4)

        splitter = QtWidgets.QSplitter()
        root_layout.addWidget(splitter, 1)

        self.asset_panel = QtWidgets.QWidget()
        asset_panel_layout = QtWidgets.QVBoxLayout(self.asset_panel)
        asset_panel_layout.setContentsMargins(2, 2, 2, 2)
        asset_panel_layout.setSpacing(4)
        asset_browser_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        asset_panel_layout.addWidget(asset_browser_splitter, 1)

        self.asset_filter_tree = QtWidgets.QTreeWidget()
        self.asset_filter_tree.setHeaderHidden(True)
        self.asset_filter_tree.setMinimumWidth(120)
        self.asset_filter_tree.setMaximumWidth(220)
        self.asset_filter_tree.setRootIsDecorated(True)
        self.asset_filter_tree.setIndentation(10)
        self.asset_filter_tree.setStyleSheet("QTreeWidget::item { height: 24px; }")
        asset_browser_splitter.addWidget(self.asset_filter_tree)

        asset_browser = QtWidgets.QWidget()
        asset_browser_layout = QtWidgets.QVBoxLayout(asset_browser)
        asset_browser_layout.setContentsMargins(2, 2, 2, 2)
        asset_browser_layout.setSpacing(4)
        asset_browser_splitter.addWidget(asset_browser)
        asset_browser_splitter.setStretchFactor(0, 0)
        asset_browser_splitter.setStretchFactor(1, 1)

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
        splitter.addWidget(self.asset_panel)

        right = QtWidgets.QWidget()
        self.detail_panel = right
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(2, 2, 2, 2)
        right_layout.setSpacing(4)
        splitter.addWidget(right)

        detail_header = QtWidgets.QHBoxLayout()
        detail_header.setContentsMargins(0, 0, 0, 0)
        detail_header.setSpacing(4)
        self.back_to_assets_btn = QtWidgets.QPushButton("Back")
        detail_header.addWidget(self.back_to_assets_btn)
        detail_header.addWidget(QtWidgets.QLabel("Variant"))
        self.asset_variant_combo = QtWidgets.QComboBox()
        self.asset_variant_combo.setMinimumWidth(120)
        detail_header.addWidget(self.asset_variant_combo)
        detail_header.addStretch(1)
        right_layout.addLayout(detail_header)

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
        asset_info_layout.addWidget(self.detail_thumbnail)
        asset_info_layout.addWidget(self.detail_info, 1)
        right_layout.addLayout(asset_info_layout)

        self.detail_tabs = QtWidgets.QTabWidget()
        right_layout.addWidget(self.detail_tabs, 1)

        work_tab = QtWidgets.QWidget()
        work_layout = QtWidgets.QVBoxLayout(work_tab)
        work_layout.setContentsMargins(4, 4, 4, 4)
        work_layout.setSpacing(4)
        self.dept_tabs = QtWidgets.QTabBar()
        self.dept_tabs.setExpanding(False)
        for dept in self.manager.asset_depts:
            self.dept_tabs.addTab(dept)
        if not self.manager.asset_depts:
            self.dept_tabs.addTab("model")
        work_layout.addWidget(self.dept_tabs)

        work_layout.addWidget(QtWidgets.QLabel("Subset"))
        self.variant_list = QtWidgets.QListWidget()
        self.variant_list.setMaximumHeight(72)
        work_layout.addWidget(self.variant_list)

        self.dependency_label = QtWidgets.QLabel("")
        work_layout.addWidget(self.dependency_label)

        self.work_list = QtWidgets.QTableWidget(0, 4)
        self.work_list.setHorizontalHeaderLabels(["Action", "File", "Updated", "Comment"])
        self.work_list.horizontalHeader().setStretchLastSection(True)
        self.work_list.verticalHeader().setStretchLastSection(False)
        self.work_list.verticalHeader().setVisible(False)
        self.work_list.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.work_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.work_list.setSortingEnabled(True)
        work_layout.addWidget(self.work_list)
        button_grid = QtWidgets.QGridLayout()
        self.open_scene_btn = QtWidgets.QPushButton("OPEN")
        self.reference_btn = QtWidgets.QPushButton("REFERENCE")
        self.save_scene_btn = QtWidgets.QPushButton("SAVE")
        self.publish_btn = QtWidgets.QPushButton("Publish")
        button_grid.addWidget(self.open_scene_btn, 0, 0)
        button_grid.addWidget(self.reference_btn, 0, 1, 1, 2)
        button_grid.addWidget(self.save_scene_btn, 1, 0)
        button_grid.addWidget(self.publish_btn, 1, 1, 1, 2)
        work_layout.addLayout(button_grid)
        self.detail_tabs.addTab(work_tab, "Work Scene")

        data_tab = QtWidgets.QWidget()
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

        self.publish_list = QtWidgets.QListWidget()
        self.publish_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)

        self.status_label = QtWidgets.QLabel("")
        root_layout.addWidget(self.status_label)

        self.search_edit.textChanged.connect(self._apply_filter)
        self.asset_filter_tree.currentItemChanged.connect(lambda _current, _previous: self._apply_filter())
        self.refresh_btn.clicked.connect(self.refresh_assets)
        self.asset_list.currentRowChanged.connect(self._show_current_asset)
        self.asset_list.itemDoubleClicked.connect(lambda _item: self._show_detail_mode())
        self.asset_card_btn.clicked.connect(self._set_asset_card_view)
        self.asset_table_btn.clicked.connect(self._set_asset_table_view)
        self.create_asset_btn.clicked.connect(self._create_asset)
        self.create_variant_btn.clicked.connect(self._create_variant)
        self.back_to_assets_btn.clicked.connect(self._show_asset_mode)
        self.asset_variant_combo.currentIndexChanged.connect(lambda _index: self._show_current_asset())
        self.dept_tabs.currentChanged.connect(self._on_department_changed)
        self.variant_list.currentRowChanged.connect(lambda _row: self._show_current_asset())
        self.asset_list.customContextMenuRequested.connect(self._show_asset_context_menu)
        self.work_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.work_list.customContextMenuRequested.connect(self._show_work_context_menu)
        self.work_list.itemChanged.connect(self._on_work_item_changed)
        self.data_list.customContextMenuRequested.connect(self._show_data_context_menu)
        self.publish_list.customContextMenuRequested.connect(self._show_publish_context_menu)
        self.open_scene_btn.clicked.connect(self._open_selected_scene)
        self.reference_btn.clicked.connect(self._reference_latest_publish)
        self.save_scene_btn.clicked.connect(self._save_scene)
        self.publish_btn.clicked.connect(self._publish_selected_work)
        self.export_mesh_btn.clicked.connect(lambda: self._show_export_data_menu("mesh"))
        self.export_guide_btn.clicked.connect(lambda: self._show_export_data_menu("guide"))
        self.export_skin_btn.clicked.connect(lambda: self._show_export_data_menu("skin"))
        self.import_data_btn.clicked.connect(self._import_selected_data)
        self._show_asset_mode()

    def refresh_assets(self, keep_selection: bool = True) -> None:
        selected_key = self._current_asset_key() if keep_selection else None
        if not keep_selection:
            self.asset_filter_tree.clear()
        self.assets = self.manager.list_assets_from_sheet(fallback_to_filesystem=True)
        self._populate_asset_filter_tree()
        self._apply_filter(selected_key=selected_key)
        self._populate_asset_variants()
        self._populate_variants()
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

    def _selected_asset_filter(self) -> tuple[str, str, str]:
        item = self.asset_filter_tree.currentItem()
        if not item:
            return "", "", ""
        data = item.data(0, QtCore.Qt.UserRole)
        if isinstance(data, tuple) and len(data) == 3:
            return str(data[0]), str(data[1]), str(data[2])
        return "", "", ""

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
            action_button = QtWidgets.QPushButton("+")
            action_button.setFixedWidth(42)
            action_button.clicked.connect(lambda _checked=False, row=row: self._show_work_row_action_menu(row))
            self.work_list.setCellWidget(row, 0, action_button)
            file_item = QtWidgets.QTableWidgetItem(path.name)
            if publish_record:
                file_item.setIcon(self.published_work_icon)
                file_item.setToolTip(
                    f"Published official version: v{int(publish_record['version']):03d}"
                )
            file_item.setData(QtCore.Qt.UserRole, str(path))
            file_item.setFlags(file_item.flags() & ~QtCore.Qt.ItemIsEditable)
            updated_item = QtWidgets.QTableWidgetItem(self._format_updated(path))
            updated_item.setFlags(updated_item.flags() & ~QtCore.Qt.ItemIsEditable)
            self.work_list.setItem(row, 1, file_item)
            self.work_list.setItem(row, 2, updated_item)
            comment_item = QtWidgets.QTableWidgetItem(self.manager.file_comment(path))
            self.work_list.setItem(row, 3, comment_item)
        self.work_list.blockSignals(False)
        self.work_list.setSortingEnabled(True)
        self.work_list.resizeColumnsToContents()

        self._populate_data_tree(asset)

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
        index = self.dept_tabs.currentIndex()
        if index < 0:
            return "model"
        return self.dept_tabs.tabText(index)

    def _current_variant(self) -> str:
        item = self.variant_list.currentItem()
        if item:
            return item.text()
        variants = self.manager.work_subsets(self._current_department())
        return variants[0] if variants else "main"

    def _current_asset_variant(self) -> str:
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

    def _populate_asset_variants(self) -> None:
        asset = self._current_asset()
        current = self._current_asset_variant()
        self.asset_variant_combo.blockSignals(True)
        self.asset_variant_combo.clear()
        variants = self.manager.asset_variants(asset) if asset else ["default"]
        selected = 0
        for index, variant in enumerate(variants):
            self.asset_variant_combo.addItem(variant)
            if variant == current:
                selected = index
        if variants:
            self.asset_variant_combo.setCurrentIndex(selected)
        self.asset_variant_combo.blockSignals(False)

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
        elif action == copy_root:
            self._copy_text(str(asset.root))
        elif action == copy_data:
            self._copy_text(str(asset.data_dir))
        elif action == copy_work:
            self._copy_text(str(asset.work_dir))
        elif action == copy_publish:
            self._copy_text(str(asset.publish_dir))

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
        copy_path = menu.addAction("Copy Path")
        action = menu.exec(self.work_list.mapToGlobal(pos))
        if action == open_scene:
            self._open_selected_scene()
        elif action == open_folder:
            self.manager.open_in_explorer(path.parent)
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
        elif action == open_folder:
            self.manager.open_in_explorer(path.parent)
        elif action == copy_path:
            self._copy_text(str(path))

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
                self.manager.update_file_metadata(
                    target,
                    comment=comment,
                    scene_info=collect_scene_info(),
                )
                self.status_label.setText(f"Saved: {target.name}")
                self.refresh_assets(keep_selection=True)
            except Exception as exc:
                QtWidgets.QMessageBox.critical(self, "Save Scene Failed", str(exc))
            return

        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("Save Scene")
        msg.setText("How do you want to save the current scene?")
        overwrite_btn = msg.addButton("Overwrite", QtWidgets.QMessageBox.AcceptRole)
        next_take_btn = msg.addButton("Next Take", QtWidgets.QMessageBox.ActionRole)
        next_version_btn = msg.addButton("Next Version", QtWidgets.QMessageBox.ActionRole)
        msg.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
        msg.exec()

        if msg.clickedButton() == overwrite_btn and selected_path:
            target = Path(selected_path)
        elif msg.clickedButton() == next_take_btn or msg.clickedButton() == overwrite_btn:
            target = self.manager.next_work_take_path(
                asset,
                current_path=selected_path,
                department=department,
                variant=self._work_variant_arg(asset),
                subset=self._work_subset_arg(asset),
            )
        elif msg.clickedButton() == next_version_btn:
            target = self.manager.next_work_version_path(
                asset,
                current_path=selected_path,
                department=department,
                variant=self._work_variant_arg(asset),
                subset=self._work_subset_arg(asset),
            )
        else:
            return

        comment = self._ask_comment("Save Scene Comment")
        if comment is None:
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            save_scene_in_current_dcc(target)
            self.manager.update_file_metadata(
                target,
                comment=comment,
                scene_info=collect_scene_info(),
            )
            self.status_label.setText(f"Saved: {target.name}")
            self.refresh_assets(keep_selection=True)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save Scene Failed", str(exc))

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

        target = self.manager.publish_file_path(
            asset,
            department=parsed["department"],
            variant=parsed["variant"],
            subset=self._work_subset_arg(asset),
            version=parsed["version"],
            ext=parsed["ext"],
        )
        overwrite = False
        if target.exists():
            result = QtWidgets.QMessageBox.question(
                self,
                "Publish Exists",
                f"{target.name} already exists. Overwrite it?",
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
            published = self.manager.publish_work_file(
                asset,
                source_path,
                overwrite=overwrite,
                comment=comment,
                subset=self._work_subset_arg(asset),
            )
            self.status_label.setText(f"Published: {published.name}")
            self._show_current_asset()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Publish Failed", str(exc))

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
        if layer != "defaultRenderLayer":
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
        job = f'-frameRange {frame} {frame} {roots} -file "{data_path}"'
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

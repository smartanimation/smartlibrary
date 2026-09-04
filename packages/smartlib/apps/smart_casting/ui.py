from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from smartlib.apps.smart_casting.service import CastingAsset, SmartCastingService
from smartlib.apps.common.asset_cards import (
    asset_card_text,
    asset_icon,
    asset_tooltip,
    configure_asset_card_list,
    set_label_thumbnail,
)
from smartlib.core.asset_categories import ASSET_CATEGORIES
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.icons import asset_category_icon_path, tool_ico_path


ASSET_HEADERS = ["", "Category", "Group", "Asset Name", "Variant", "Description"]
AVAILABLE_ASSET_HEADERS = ["", "Category", "Group", "Asset Name", "Variant", "FAST", "WORK", "FINAL", "Description"]
SEQUENCE_CAST_HEADERS = ["Category", "Group", "Asset Name", "Variant"]
CAST_DETAIL_KEYS = ["cast_key", "asset", "category", "variant", "namespace", "asset_publish", "required", "note"]
EDITABLE_CAST_DETAIL_KEYS = {"asset", "category", "variant", "namespace", "asset_publish", "required", "note"}


def _qt():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
        return QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets
        return QtCore, QtGui, QtWidgets


QtCore, QtGui, QtWidgets = _qt()


def _category_icon(category: str):
    path = asset_category_icon_path(category, size=20)
    return QtGui.QIcon(str(path)) if path else QtGui.QIcon()


def _default_config_dir() -> Path:
    env_path = os.environ.get("PROJECT_CONFIG_DIR")
    if env_path:
        return Path(env_path)
    root = Path(os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT") or Path.cwd())
    return root / "config" / "STKB"


class AssetDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, *, title: str = "Create Asset", asset: CastingAsset | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QtWidgets.QFormLayout(self)
        self.category = QtWidgets.QComboBox()
        self.category.setIconSize(QtCore.QSize(20, 20))
        for category in ASSET_CATEGORIES:
            self.category.addItem(_category_icon(category), category)
        if asset and self.category.findText(asset.category) >= 0:
            self.category.setCurrentText(asset.category)
        self.group = QtWidgets.QLineEdit(asset.group if asset else "hero")
        self.asset = QtWidgets.QLineEdit(asset.asset if asset else "")
        self.variant = QtWidgets.QLineEdit("default" if asset is None else "")
        self.description = QtWidgets.QLineEdit("")
        if asset:
            self.category.setEnabled(False)
            self.group.setReadOnly(True)
            self.asset.setReadOnly(True)
        for label, widget in (
            ("Category", self.category),
            ("Group", self.group),
            ("Asset", self.asset),
            ("Variant", self.variant),
            ("Description", self.description),
        ):
            layout.addRow(label, widget)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self) -> dict[str, str]:
        return {
            "category": self.category.currentText().strip(),
            "group": self.group.text().strip(),
            "asset": self.asset.text().strip(),
            "variant": self.variant.text().strip() or "default",
            "description": self.description.text().strip(),
        }


class SmartCastingWindow(QtWidgets.QMainWindow):
    def __init__(self, config_dir: str | os.PathLike[str] | None = None, parent=None):
        super().__init__(parent)
        icon_path = tool_ico_path("smart_casting")
        if icon_path:
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))
        self.project_config = ProjectConfig(config_dir or _default_config_dir())
        self.service = SmartCastingService(self.project_config)
        self.asset_rows: list[CastingAsset] = []
        self._detail_cast_item = None
        self._populating_cast_detail = False
        self._populating_asset_table = False
        self._populating_asset_cast_detail = False
        self._sequence_cast_drafts: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._sequence_cast_dirty: set[tuple[str, str]] = set()
        self.setWindowTitle("Smart Casting")
        self.resize(1180, 700)
        self._build()
        self.refresh()

    def focus_context(
        self,
        *,
        episode: str = "",
        sequence: str = "",
        shot: str = "",
        asset_names: list[str] | None = None,
    ) -> None:
        """Open the requested cast context without modifying cast data."""
        episode = str(episode or "").strip()
        sequence = str(sequence or "").strip()
        shot = str(shot or "").strip()
        if shot:
            self.tabs.setCurrentWidget(self.shots_tab)
            if episode:
                self.episode_combo.setCurrentText(episode)
            if sequence:
                self.sequence_combo.setCurrentText(sequence)
            for row in range(self.shot_list.count()):
                item = self.shot_list.item(row)
                if item.text() == shot:
                    self.shot_list.setCurrentRow(row)
                    break
        elif episode and sequence:
            self.tabs.setCurrentWidget(self.assets_tab)
            for ep_row in range(self.sequence_tree.topLevelItemCount()):
                ep_item = self.sequence_tree.topLevelItem(ep_row)
                for seq_row in range(ep_item.childCount()):
                    item = ep_item.child(seq_row)
                    if item.data(0, QtCore.Qt.UserRole) == (episode, sequence):
                        self.sequence_tree.setCurrentItem(item)
                        break

        names = {str(name).strip() for name in (asset_names or []) if str(name).strip()}
        if names and self.tabs.currentWidget() == self.assets_tab:
            self.asset_table.clearSelection()
            for row in range(self.asset_table.rowCount()):
                values = {
                    str(self.asset_table.item(row, column).text()).strip()
                    for column in range(self.asset_table.columnCount())
                    if self.asset_table.item(row, column)
                }
                if names & values:
                    self.asset_table.selectRow(row)

    def _build(self) -> None:
        self.tabs = QtWidgets.QTabWidget()
        self.setCentralWidget(self.tabs)
        self.assets_tab = QtWidgets.QWidget()
        self.shots_tab = QtWidgets.QWidget()
        self.tabs.addTab(self.assets_tab, "Assets")
        self.tabs.addTab(self.shots_tab, "Shots")
        self._build_assets_tab()
        self._build_shots_tab()

    def _build_assets_tab(self) -> None:
        layout = QtWidgets.QHBoxLayout(self.assets_tab)
        left = QtWidgets.QVBoxLayout()
        self.sequence_tree = QtWidgets.QTreeWidget()
        self.sequence_tree.setHeaderHidden(True)
        self.sequence_tree.setMinimumWidth(210)
        self.sequence_tree.setIndentation(12)
        self.sequence_tree.currentItemChanged.connect(lambda _current, _previous: self.on_sequence_tree_changed())
        self.category_filter = QtWidgets.QListWidget()
        self.category_filter.setIconSize(QtCore.QSize(20, 20))
        self.category_filter.setMaximumHeight(150)
        self.category_filter.itemChanged.connect(lambda _item: self.populate_asset_table())
        left.addWidget(QtWidgets.QLabel("Sequence"))
        left.addWidget(self.sequence_tree, 1)
        left.addWidget(QtWidgets.QLabel("Category Filter"))
        left.addWidget(self.category_filter)

        center = QtWidgets.QVBoxLayout()
        self.asset_search = QtWidgets.QLineEdit()
        self.asset_search.setPlaceholderText("Search asset")
        self.asset_search.textChanged.connect(lambda _text: self.populate_asset_table())
        buttons = QtWidgets.QHBoxLayout()
        self.create_asset_btn = QtWidgets.QPushButton("Create Asset")
        self.create_variant_btn = QtWidgets.QPushButton("Create Variant")
        self.set_thumbnail_btn = QtWidgets.QPushButton("Set Thumbnail")
        self.add_to_cast_btn = QtWidgets.QPushButton("Add Selected to Cast")
        self.save_asset_sequence_cast_btn = QtWidgets.QPushButton("Save Sequence Cast")
        self.publish_asset_sequence_cast_btn = QtWidgets.QPushButton("Publish Sequence Cast")
        self.remove_asset_sequence_cast_btn = QtWidgets.QPushButton("Remove Cast")
        for button in (
            self.create_asset_btn,
            self.create_variant_btn,
            self.set_thumbnail_btn,
            self.add_to_cast_btn,
            self.remove_asset_sequence_cast_btn,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        sequence_buttons = QtWidgets.QHBoxLayout()
        sequence_buttons.addStretch(1)
        sequence_buttons.addWidget(self.save_asset_sequence_cast_btn)
        sequence_buttons.addWidget(self.publish_asset_sequence_cast_btn)
        self.asset_table = QtWidgets.QTableWidget(0, len(ASSET_HEADERS))
        self.asset_table.setHorizontalHeaderLabels(ASSET_HEADERS)
        self.asset_table.setIconSize(QtCore.QSize(20, 20))
        self._hide_vertical_header(self.asset_table)
        self.asset_table.horizontalHeader().setStretchLastSection(True)
        self.asset_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.asset_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.asset_table.itemSelectionChanged.connect(self.populate_asset_detail)
        self.asset_table.itemChanged.connect(self._on_asset_table_item_changed)
        self.available_asset_label = QtWidgets.QLabel("Asset List")
        self.available_asset_table = QtWidgets.QTableWidget(0, len(AVAILABLE_ASSET_HEADERS))
        self.available_asset_table.setHorizontalHeaderLabels(AVAILABLE_ASSET_HEADERS)
        self.available_asset_table.setIconSize(QtCore.QSize(20, 20))
        self._hide_vertical_header(self.available_asset_table)
        self.available_asset_table.horizontalHeader().setStretchLastSection(True)
        self.available_asset_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.available_asset_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.available_asset_table.itemSelectionChanged.connect(self.populate_asset_detail)
        self.sequence_cast_label = QtWidgets.QLabel("Cast")
        center.addWidget(self.asset_search)
        center.addLayout(buttons)
        center.addWidget(self.available_asset_label)
        center.addWidget(self.available_asset_table, 1)
        center.addWidget(self.sequence_cast_label)
        center.addWidget(self.asset_table, 1)
        center.addLayout(sequence_buttons)

        right = QtWidgets.QVBoxLayout()
        self.asset_thumb = QtWidgets.QLabel("Thumbnail")
        self.asset_thumb.setFixedSize(190, 108)
        self.asset_thumb.setAlignment(QtCore.Qt.AlignCenter)
        self.asset_thumb.setStyleSheet("background:#30363d; border:1px solid #4a4a4a;")
        self.asset_info = QtWidgets.QLabel("")
        self.asset_info.setWordWrap(True)
        self.asset_cast_info_table = QtWidgets.QTableWidget(0, 2)
        self.asset_cast_info_table.setHorizontalHeaderLabels(["Key", "Value"])
        self._hide_vertical_header(self.asset_cast_info_table)
        self.asset_cast_info_table.horizontalHeader().setStretchLastSection(True)
        self.asset_cast_info_table.itemChanged.connect(self._on_asset_cast_detail_changed)
        self.metadata_table = QtWidgets.QTableWidget(0, 2)
        self.metadata_table.setHorizontalHeaderLabels(["Key", "Value"])
        self._hide_vertical_header(self.metadata_table)
        self.metadata_table.horizontalHeader().setStretchLastSection(True)
        meta_buttons = QtWidgets.QHBoxLayout()
        self.add_meta_btn = QtWidgets.QPushButton("+")
        self.remove_meta_btn = QtWidgets.QPushButton("-")
        self.save_meta_btn = QtWidgets.QPushButton("Save Metadata")
        meta_buttons.addWidget(self.add_meta_btn)
        meta_buttons.addWidget(self.remove_meta_btn)
        meta_buttons.addWidget(self.save_meta_btn)
        right.addWidget(self.asset_thumb, 0, QtCore.Qt.AlignHCenter)
        right.addWidget(self.asset_info)
        right.addWidget(QtWidgets.QLabel("Cast Data"))
        right.addWidget(self.asset_cast_info_table, 1)
        right.addWidget(QtWidgets.QLabel("Custom Metadata"))
        right.addWidget(self.metadata_table, 1)
        right.addLayout(meta_buttons)

        layout.addLayout(left, 1)
        layout.addLayout(center, 4)
        layout.addLayout(right, 2)

        self.create_asset_btn.clicked.connect(self.create_asset)
        self.create_variant_btn.clicked.connect(self.create_variant)
        self.set_thumbnail_btn.clicked.connect(self.set_thumbnail)
        self.add_to_cast_btn.clicked.connect(self.add_selected_to_sequence_cast)
        self.save_asset_sequence_cast_btn.clicked.connect(self.save_sequence_cast)
        self.publish_asset_sequence_cast_btn.clicked.connect(self.publish_sequence_cast)
        self.remove_asset_sequence_cast_btn.clicked.connect(self.remove_sequence_cast)
        self.add_meta_btn.clicked.connect(lambda: self.metadata_table.insertRow(self.metadata_table.rowCount()))
        self.remove_meta_btn.clicked.connect(self.remove_metadata_row)
        self.save_meta_btn.clicked.connect(self.save_metadata)

    def _build_shots_tab(self) -> None:
        layout = QtWidgets.QHBoxLayout(self.shots_tab)
        left = QtWidgets.QVBoxLayout()
        form = QtWidgets.QFormLayout()
        self.episode_combo = QtWidgets.QComboBox()
        self.sequence_combo = QtWidgets.QComboBox()
        form.addRow("Episode", self.episode_combo)
        form.addRow("Sequence", self.sequence_combo)
        self.shot_list = QtWidgets.QListWidget()
        left.addLayout(form)
        left.addWidget(QtWidgets.QLabel("Shot list"))
        left.addWidget(self.shot_list, 1)

        center = QtWidgets.QVBoxLayout()
        self.sequence_cast_list = QtWidgets.QListWidget()
        configure_asset_card_list(self.sequence_cast_list, QtCore, QtWidgets)
        self.sequence_cast_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.sequence_cast_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.save_sequence_cast_btn = QtWidgets.QPushButton("Save Sequence Cast")
        self.publish_sequence_cast_btn = QtWidgets.QPushButton("Publish Sequence Cast")
        self.remove_sequence_cast_btn = QtWidgets.QPushButton("Remove Sequence Cast")
        self.save_sequence_cast_btn.setVisible(False)
        self.publish_sequence_cast_btn.setVisible(False)
        self.remove_sequence_cast_btn.setVisible(False)
        shot_buttons = QtWidgets.QHBoxLayout()
        self.add_cast_btn = QtWidgets.QPushButton("Add Cast")
        self.remove_cast_btn = QtWidgets.QPushButton("Remove Cast")
        self.auto_selection_btn = QtWidgets.QPushButton("Auto Selection")
        shot_buttons.addWidget(self.add_cast_btn)
        shot_buttons.addWidget(self.remove_cast_btn)
        shot_buttons.addWidget(self.auto_selection_btn)
        shot_buttons.addStretch(1)
        self.shot_cast_list = QtWidgets.QListWidget()
        configure_asset_card_list(self.shot_cast_list, QtCore, QtWidgets)
        self.shot_cast_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        bottom = QtWidgets.QHBoxLayout()
        bottom.addStretch(1)
        self.save_shot_cast_btn = QtWidgets.QPushButton("Save")
        self.publish_shot_cast_btn = QtWidgets.QPushButton("Publish")
        bottom.addWidget(self.save_shot_cast_btn)
        bottom.addWidget(self.publish_shot_cast_btn)
        center.addWidget(QtWidgets.QLabel("Sequence Cast"))
        center.addWidget(self.sequence_cast_list, 1)
        center.addLayout(shot_buttons)
        center.addWidget(QtWidgets.QLabel("Shot Cast"))
        center.addWidget(self.shot_cast_list, 1)
        center.addLayout(bottom)

        right = QtWidgets.QVBoxLayout()
        self.cast_thumb = QtWidgets.QLabel("Thumbnail")
        self.cast_thumb.setFixedSize(190, 108)
        self.cast_thumb.setAlignment(QtCore.Qt.AlignCenter)
        self.cast_thumb.setStyleSheet("background:#30363d; border:1px solid #4a4a4a;")
        self.cast_info_table = QtWidgets.QTableWidget(0, 2)
        self.cast_info_table.setHorizontalHeaderLabels(["Key", "Value"])
        self._hide_vertical_header(self.cast_info_table)
        self.cast_info_table.horizontalHeader().setStretchLastSection(True)
        self.cast_info_table.itemChanged.connect(self._on_cast_detail_changed)
        right.addWidget(self.cast_thumb, 0, QtCore.Qt.AlignHCenter)
        right.addWidget(self.cast_info_table, 1)

        layout.addLayout(left, 1)
        layout.addLayout(center, 3)
        layout.addLayout(right, 1)

        self.episode_combo.currentTextChanged.connect(lambda _text: self.populate_sequence_combo())
        self.sequence_combo.currentTextChanged.connect(lambda _text: self.populate_shots_tab())
        self.shot_list.currentRowChanged.connect(lambda _row: self.populate_shot_cast())
        self.sequence_cast_list.itemSelectionChanged.connect(self.populate_cast_detail)
        self.sequence_cast_list.customContextMenuRequested.connect(self.show_sequence_cast_menu)
        self.shot_cast_list.itemSelectionChanged.connect(self.populate_cast_detail)
        self.add_cast_btn.clicked.connect(self.add_sequence_cast_to_shot)
        self.save_sequence_cast_btn.clicked.connect(self.save_sequence_cast)
        self.publish_sequence_cast_btn.clicked.connect(self.publish_sequence_cast)
        self.remove_sequence_cast_btn.clicked.connect(self.remove_sequence_cast)
        self.remove_cast_btn.clicked.connect(self.remove_shot_cast)
        self.save_shot_cast_btn.clicked.connect(self.save_shot_cast)
        self.publish_shot_cast_btn.clicked.connect(self.publish_shot_cast)
        self.auto_selection_btn.clicked.connect(self.auto_selection)

    @staticmethod
    def _hide_vertical_header(table) -> None:
        table.verticalHeader().setVisible(False)

    def refresh(self) -> None:
        self.asset_rows = self.service.list_assets()
        self.populate_sequence_tree()
        self.populate_categories()
        self.populate_asset_table()
        self.populate_episode_combo()

    def populate_sequence_tree(self) -> None:
        current = self.selected_sequence()
        self.sequence_tree.clear()
        episodes: dict[str, list[str]] = {}
        for seq in self.service.sequences():
            episodes.setdefault(seq.episode, []).append(seq.sequence)
        selected_item = None
        first_sequence_item = None
        for episode, sequences in sorted(episodes.items()):
            ep_item = QtWidgets.QTreeWidgetItem([episode])
            self.sequence_tree.addTopLevelItem(ep_item)
            for sequence in sorted(set(sequences)):
                item = QtWidgets.QTreeWidgetItem([sequence])
                item.setData(0, QtCore.Qt.UserRole, (episode, sequence))
                ep_item.addChild(item)
                if first_sequence_item is None:
                    first_sequence_item = item
                if current == (episode, sequence):
                    selected_item = item
        self.sequence_tree.expandAll()
        if selected_item or first_sequence_item:
            self.sequence_tree.setCurrentItem(selected_item or first_sequence_item)

    def on_sequence_tree_changed(self) -> None:
        self.populate_categories()
        self.populate_asset_table()

    def populate_categories(self) -> None:
        current = self.selected_categories()
        previous_categories = {
            self.category_filter.item(row).text()
            for row in range(self.category_filter.count())
        }
        categories = self._active_category_values()
        valid_current = current & set(categories)
        check_all = not current or not valid_current or current == previous_categories
        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        for category in categories:
            item = QtWidgets.QListWidgetItem(_category_icon(category), category)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if check_all or category in valid_current else QtCore.Qt.Unchecked)
            self.category_filter.addItem(item)
        self.category_filter.blockSignals(False)

    def _active_category_values(self) -> list[str]:
        sequence = self.selected_sequence()
        if sequence:
            values = {str(row.category or "").strip() for row in self.asset_rows}
            return sorted(value for value in values if value)
        return self.service.categories()

    def selected_categories(self) -> set[str]:
        result = set()
        for row in range(self.category_filter.count()):
            item = self.category_filter.item(row)
            if item.checkState() == QtCore.Qt.Checked:
                result.add(item.text())
        return result

    def populate_asset_table(self) -> None:
        sequence = self.selected_sequence()
        if sequence:
            self.populate_sequence_cast_table(sequence[0], sequence[1])
            return
        self._set_asset_table_headers(ASSET_HEADERS)
        self.asset_cast_info_table.setVisible(False)
        self.save_asset_sequence_cast_btn.setVisible(False)
        self.publish_asset_sequence_cast_btn.setVisible(False)
        self.remove_asset_sequence_cast_btn.setVisible(False)
        self.available_asset_label.setVisible(False)
        self.available_asset_table.setVisible(False)
        self.sequence_cast_label.setVisible(False)
        self.add_to_cast_btn.setVisible(True)
        selected_categories = self.selected_categories()
        query = self.asset_search.text().strip().lower()
        self._populating_asset_table = True
        self.asset_table.blockSignals(True)
        self.asset_table.setRowCount(0)
        for asset in self.asset_rows:
            if selected_categories and asset.category not in selected_categories:
                continue
            haystack = " ".join([asset.category, asset.group, asset.asset, asset.variant, asset.status, asset.description]).lower()
            if query and query not in haystack:
                continue
            row = self.asset_table.rowCount()
            self.asset_table.insertRow(row)
            values = ["", asset.category, asset.group, asset.asset, asset.variant, asset.description]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(str(value))
                item.setData(QtCore.Qt.UserRole, asset)
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                if column == 0 and asset.thumbnail:
                    item.setIcon(asset_icon(QtCore, QtGui, thumbnail=asset.thumbnail, label=asset.asset))
                elif column == 1:
                    item.setIcon(_category_icon(asset.category))
                self.asset_table.setItem(row, column, item)
        self.asset_table.blockSignals(False)
        self._populating_asset_table = False
        self.asset_table.resizeColumnsToContents()
        self.asset_table.horizontalHeader().setStretchLastSection(True)

    def populate_sequence_cast_table(self, episode: str, sequence: str) -> None:
        self._set_asset_table_headers(SEQUENCE_CAST_HEADERS)
        self.asset_cast_info_table.setVisible(True)
        self.save_asset_sequence_cast_btn.setVisible(True)
        self.publish_asset_sequence_cast_btn.setVisible(True)
        self.remove_asset_sequence_cast_btn.setVisible(True)
        self.add_to_cast_btn.setVisible(True)
        self.available_asset_label.setVisible(True)
        self.available_asset_table.setVisible(True)
        self.sequence_cast_label.setVisible(True)
        self._update_sequence_cast_save_label((episode, sequence))
        query = self.asset_search.text().strip().lower()
        rows = self._sequence_cast_draft((episode, sequence))
        self._populating_asset_table = True
        self.asset_table.blockSignals(True)
        self.asset_table.setRowCount(0)
        for data in rows:
            category = str(data.get("category") or "")
            group = str(data.get("group") or data.get("namespace") or "")
            haystack = " ".join(
                [
                    category,
                    group,
                    str(data.get("asset") or ""),
                    str(data.get("variant") or ""),
                    str(data.get("note") or ""),
                ]
            ).lower()
            if query and query not in haystack:
                continue
            row = self.asset_table.rowCount()
            self.asset_table.insertRow(row)
            values = [
                category,
                group,
                str(data.get("asset") or ""),
                str(data.get("variant") or "default"),
            ]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setData(QtCore.Qt.UserRole, data)
                item.setData(QtCore.Qt.UserRole + 1, self._sequence_cast_column_key(column))
                if not self._sequence_cast_column_key(column):
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                if column == 0:
                    item.setIcon(_category_icon(category))
                self.asset_table.setItem(row, column, item)
        self.asset_table.blockSignals(False)
        self._populating_asset_table = False
        self.asset_table.resizeColumnsToContents()
        self.asset_table.horizontalHeader().setStretchLastSection(True)
        self.populate_available_asset_table()

    def populate_available_asset_table(self) -> None:
        query = self.asset_search.text().strip().lower()
        selected_categories = self.selected_categories()
        if self.available_asset_table.columnCount() != len(AVAILABLE_ASSET_HEADERS):
            self.available_asset_table.setColumnCount(len(AVAILABLE_ASSET_HEADERS))
        self.available_asset_table.setHorizontalHeaderLabels(AVAILABLE_ASSET_HEADERS)
        self.available_asset_table.blockSignals(True)
        self.available_asset_table.setRowCount(0)
        for asset in self.asset_rows:
            if selected_categories and asset.category not in selected_categories:
                continue
            haystack = " ".join(
                [asset.category, asset.group, asset.asset, asset.variant, asset.status, asset.description]
            ).lower()
            if query and query not in haystack:
                continue
            row = self.available_asset_table.rowCount()
            self.available_asset_table.insertRow(row)
            contexts = self.service.cast_context_statuses(
                {"asset": asset.asset, "variant": asset.variant, "asset_publish": "approved"}
            )
            values = [
                "",
                asset.category,
                asset.group,
                asset.asset,
                asset.variant,
                str(contexts.get("FAST") or "Missing"),
                str(contexts.get("WORK") or "Missing"),
                str(contexts.get("FINAL") or "Missing"),
                asset.description,
            ]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(str(value))
                item.setData(QtCore.Qt.UserRole, asset)
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                if column == 0 and asset.thumbnail:
                    item.setIcon(asset_icon(QtCore, QtGui, thumbnail=asset.thumbnail, label=asset.asset))
                elif column == 1:
                    item.setIcon(_category_icon(asset.category))
                if column in {5, 6, 7}:
                    self._style_status_item(item, str(value))
                self.available_asset_table.setItem(row, column, item)
        self.available_asset_table.blockSignals(False)
        self.available_asset_table.resizeColumnsToContents()
        self.available_asset_table.horizontalHeader().setStretchLastSection(True)

    def _sequence_cast_draft(self, sequence: tuple[str, str]) -> list[dict[str, Any]]:
        if sequence not in self._sequence_cast_drafts:
            self._sequence_cast_drafts[sequence] = [
                dict(row) for row in self.service.sequence_cast_rows(sequence[0], sequence[1])
            ]
        return self._sequence_cast_drafts[sequence]

    def _mark_sequence_cast_dirty(self, sequence: tuple[str, str] | None = None) -> None:
        sequence = sequence or self.selected_sequence()
        if sequence:
            self._sequence_cast_dirty.add(sequence)
            self._update_sequence_cast_save_label(sequence)

    def _update_sequence_cast_save_label(self, sequence: tuple[str, str]) -> None:
        dirty = sequence in self._sequence_cast_dirty
        label = "Save Sequence Cast"
        self.save_asset_sequence_cast_btn.setText(f"{label} *" if dirty else label)
        self.save_sequence_cast_btn.setText(f"{label} *" if dirty else label)

    def _set_asset_table_headers(self, headers: list[str]) -> None:
        if self.asset_table.columnCount() != len(headers):
            self.asset_table.setColumnCount(len(headers))
        self.asset_table.setHorizontalHeaderLabels(headers)

    @staticmethod
    def _sequence_cast_column_key(column: int) -> str:
        return {
            2: "asset",
            3: "variant",
        }.get(column, "")

    def _style_status_item(self, item, value: str) -> None:
        text = str(value).strip().lower()
        if text == "ready":
            item.setForeground(QtGui.QBrush(QtGui.QColor("#00c853")))
        elif text == "wip":
            item.setForeground(QtGui.QBrush(QtGui.QColor("#ffc400")))
        elif text == "missing":
            item.setForeground(QtGui.QBrush(QtGui.QColor("#ff1744")))

    def current_asset(self) -> CastingAsset | None:
        if self.available_asset_table.isVisible() and self.available_asset_table.selectedIndexes():
            return self._current_asset_from_table(self.available_asset_table)
        return self._current_asset_from_table(self.asset_table)

    def _current_asset_from_table(self, table) -> CastingAsset | None:
        row = self.asset_table.currentRow()
        if table is not self.asset_table:
            row = table.currentRow()
        if row < 0:
            return None
        item = table.item(row, 0) or table.item(row, 1)
        data = item.data(QtCore.Qt.UserRole) if item else None
        if isinstance(data, CastingAsset):
            return data
        if isinstance(data, dict):
            asset = str(data.get("asset") or "")
            variant = str(data.get("variant") or "default")
            return next((row for row in self.asset_rows if row.asset == asset and row.variant == variant), None)
        return None

    def selected_assets(self) -> list[CastingAsset]:
        table = self.available_asset_table if self.available_asset_table.isVisible() else self.asset_table
        rows = sorted({index.row() for index in table.selectedIndexes()})
        result = []
        for row in rows:
            item = table.item(row, 0) or table.item(row, 1)
            data = item.data(QtCore.Qt.UserRole) if item else None
            if isinstance(data, CastingAsset):
                result.append(data)
            elif isinstance(data, dict):
                asset = str(data.get("asset") or "")
                variant = str(data.get("variant") or "default")
                match = next((row for row in self.asset_rows if row.asset == asset and row.variant == variant), None)
                if match:
                    result.append(match)
        return result

    def populate_asset_detail(self) -> None:
        if self.sender() is self.available_asset_table:
            asset = self._current_asset_from_table(self.available_asset_table)
            self.populate_plain_asset_detail(asset)
            return
        cast_data = self.current_asset_table_cast()
        if cast_data:
            self.populate_asset_cast_detail(cast_data)
            return
        asset = self.current_asset()
        self.populate_plain_asset_detail(asset)

    def populate_plain_asset_detail(self, asset: CastingAsset | None) -> None:
        self.metadata_table.setRowCount(0)
        self.asset_cast_info_table.setRowCount(0)
        if not asset:
            self.asset_thumb.clear()
            self.asset_thumb.setText("Thumbnail")
            self.asset_info.setText("")
            return
        self.asset_info.setText(
            f"{asset.asset}\nCategory: {asset.category}\nGroup: {asset.group}\nVariant: {asset.variant}\nStatus: {asset.status}"
        )
        self._set_label_pixmap(self.asset_thumb, asset.thumbnail)
        for key, value in sorted(self.service.custom_metadata(asset).items()):
            row = self.metadata_table.rowCount()
            self.metadata_table.insertRow(row)
            self.metadata_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(key)))
            self.metadata_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(value)))

    def current_asset_table_cast(self) -> dict[str, Any] | None:
        row = self.asset_table.currentRow()
        if row < 0:
            return None
        item = self.asset_table.item(row, 0) or self.asset_table.item(row, 1)
        data = item.data(QtCore.Qt.UserRole) if item else None
        return dict(data) if isinstance(data, dict) and data.get("cast_key") else None

    def populate_asset_cast_detail(self, data: dict[str, Any], *, refresh_metadata: bool = True) -> None:
        asset = self.current_asset()
        self.metadata_table.setRowCount(0)
        self.asset_info.setText(
            f"{data.get('asset', '')}\n"
            f"Category: {data.get('category') or ''}\n"
            f"Group: {data.get('group') or data.get('namespace') or ''}\n"
            f"Variant: {data.get('variant', 'default')}\n"
            f"Status: {data.get('asset_publish', 'approved')}"
        )
        self._set_label_pixmap(self.asset_thumb, str(data.get("thumbnail") or getattr(asset, "thumbnail", "")))
        self._populating_asset_cast_detail = True
        self.asset_cast_info_table.blockSignals(True)
        self.asset_cast_info_table.setRowCount(0)
        for key in CAST_DETAIL_KEYS:
            row = self.asset_cast_info_table.rowCount()
            self.asset_cast_info_table.insertRow(row)
            key_item = QtWidgets.QTableWidgetItem(key)
            key_item.setFlags(key_item.flags() & ~QtCore.Qt.ItemIsEditable)
            value_item = QtWidgets.QTableWidgetItem(str(data.get(key, "")))
            value_item.setData(QtCore.Qt.UserRole, key)
            if key not in EDITABLE_CAST_DETAIL_KEYS:
                value_item.setFlags(value_item.flags() & ~QtCore.Qt.ItemIsEditable)
            self.asset_cast_info_table.setItem(row, 0, key_item)
            self.asset_cast_info_table.setItem(row, 1, value_item)
        self.asset_cast_info_table.blockSignals(False)
        self._populating_asset_cast_detail = False
        if asset and refresh_metadata:
            for key, value in sorted(self.service.custom_metadata(asset).items()):
                row = self.metadata_table.rowCount()
                self.metadata_table.insertRow(row)
                self.metadata_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(key)))
                self.metadata_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(value)))

    def _on_asset_table_item_changed(self, item) -> None:
        if self._populating_asset_table:
            return
        data = item.data(QtCore.Qt.UserRole)
        key = str(item.data(QtCore.Qt.UserRole + 1) or "")
        if not isinstance(data, dict) or not key:
            return
        updated = dict(data)
        updated[key] = item.text().strip()
        if key in {"asset", "variant", "asset_publish"}:
            updated = self._sync_cast_asset_metadata(updated)
        self._replace_sequence_cast_draft_row(updated)
        self._mark_sequence_cast_dirty()
        self._update_asset_table_row_data(item.row(), updated)
        self._refresh_asset_table_row(item.row(), updated)
        self.populate_asset_cast_detail(updated, refresh_metadata=False)

    def _on_asset_cast_detail_changed(self, table_item) -> None:
        if self._populating_asset_cast_detail or table_item.column() != 1:
            return
        key = str(table_item.data(QtCore.Qt.UserRole) or "")
        if key not in EDITABLE_CAST_DETAIL_KEYS:
            return
        data = self.current_asset_table_cast()
        if not data:
            return
        data[key] = table_item.text().strip()
        if key in {"asset", "variant", "asset_publish"}:
            data = self._sync_cast_asset_metadata(data)
        self._replace_sequence_cast_draft_row(data)
        self._mark_sequence_cast_dirty()
        self._update_asset_table_row_data(self.asset_table.currentRow(), data)
        self._refresh_asset_table_row(self.asset_table.currentRow(), data)

    def _update_asset_table_row_data(self, row: int, data: dict[str, Any]) -> None:
        if row < 0:
            return
        for column in range(self.asset_table.columnCount()):
            item = self.asset_table.item(row, column)
            if item:
                item.setData(QtCore.Qt.UserRole, data)

    def _refresh_asset_table_row(self, row: int, data: dict[str, Any]) -> None:
        if row < 0:
            return
        values = [
            str(data.get("category") or ""),
            str(data.get("group") or data.get("namespace") or ""),
            str(data.get("asset") or ""),
            str(data.get("variant") or "default"),
        ]
        self._populating_asset_table = True
        self.asset_table.blockSignals(True)
        for column, value in enumerate(values):
            item = self.asset_table.item(row, column)
            if not item:
                item = QtWidgets.QTableWidgetItem(value)
                self.asset_table.setItem(row, column, item)
            item.setText(value)
            if column == 0:
                item.setIcon(_category_icon(value))
            item.setData(QtCore.Qt.UserRole, data)
            item.setData(QtCore.Qt.UserRole + 1, self._sequence_cast_column_key(column))
            if not self._sequence_cast_column_key(column):
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
        self.asset_table.blockSignals(False)
        self._populating_asset_table = False

    def _sync_cast_asset_metadata(self, data: dict[str, Any]) -> dict[str, Any]:
        updated = dict(data)
        asset_name = str(updated.get("asset") or "")
        variant = str(updated.get("variant") or "default")
        match = next(
            (asset for asset in self.asset_rows if asset.asset == asset_name and asset.variant == variant),
            None,
        )
        if match:
            updated.update(
                {
                    "category": match.category,
                    "group": match.group,
                    "thumbnail": match.thumbnail,
                    "description": match.description,
                }
            )
        updated["contexts"] = self.service.cast_context_statuses(updated)
        return updated

    def _replace_sequence_cast_draft_row(self, data: dict[str, Any]) -> None:
        sequence = self.selected_sequence()
        cast_key = str(data.get("cast_key") or "")
        if not sequence or not cast_key:
            return
        draft = self._sequence_cast_draft(sequence)
        for index, row in enumerate(draft):
            if str(row.get("cast_key") or "") == cast_key:
                draft[index] = dict(data)
                return
        draft.append(dict(data))

    def create_asset(self) -> None:
        dialog = AssetDialog(self, title="Create Asset")
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        values = dialog.values()
        if not values["category"] or not values["group"] or not values["asset"]:
            return
        try:
            self.service.create_asset(values["category"], values["group"], values["asset"], values["variant"], values["description"])
            self.refresh()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Create Asset Failed", str(exc))

    def create_variant(self) -> None:
        asset = self.current_asset()
        if not asset:
            return
        dialog = AssetDialog(self, title="Create Variant", asset=asset)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        values = dialog.values()
        try:
            self.service.create_variant(asset.category, asset.group, asset.asset, values["variant"], values["description"])
            self.refresh()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Create Variant Failed", str(exc))

    def set_thumbnail(self) -> None:
        asset = self.current_asset()
        if not asset:
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Set Thumbnail", "", "Images (*.jpg *.jpeg *.png);;All Files (*.*)")
        if not path:
            return
        try:
            self.service.set_thumbnail(asset, path)
            self.refresh()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Set Thumbnail Failed", str(exc))

    def save_metadata(self) -> None:
        asset = self.current_asset()
        if not asset:
            return
        data = {}
        for row in range(self.metadata_table.rowCount()):
            key_item = self.metadata_table.item(row, 0)
            value_item = self.metadata_table.item(row, 1)
            key = key_item.text().strip() if key_item else ""
            if key:
                data[key] = value_item.text().strip() if value_item else ""
        try:
            self.service.write_custom_metadata(asset, data)
            self.populate_asset_detail()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save Metadata Failed", str(exc))

    def remove_metadata_row(self) -> None:
        row = self.metadata_table.currentRow()
        if row >= 0:
            self.metadata_table.removeRow(row)

    def selected_sequence(self) -> tuple[str, str] | None:
        item = self.sequence_tree.currentItem()
        data = item.data(0, QtCore.Qt.UserRole) if item else None
        if isinstance(data, tuple) and len(data) == 2:
            return str(data[0]), str(data[1])
        return None

    def add_selected_to_sequence_cast(self) -> None:
        sequence = self.selected_sequence()
        if not sequence:
            QtWidgets.QMessageBox.warning(self, "Add Selected to Cast", "Select a sequence in the left tree.")
            return
        assets = self.selected_assets()
        if not assets:
            QtWidgets.QMessageBox.information(self, "Add Selected to Cast", "Select one or more assets in the Asset List.")
            return
        if self.tabs.currentWidget() == self.assets_tab and self.selected_sequence():
            self._add_assets_to_sequence_cast_draft(sequence, assets)
            self.populate_asset_table()
            return
        try:
            path, rows = self.service.add_assets_to_sequence_cast(sequence[0], sequence[1], assets)
            QtWidgets.QMessageBox.information(self, "Add Selected to Cast", f"Added {len(rows)} cast rows:\n{path}")
            self.populate_asset_table()
            self.populate_shots_tab()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Add Selected to Cast Failed", str(exc))

    def _add_assets_to_sequence_cast_draft(self, sequence: tuple[str, str], assets: list[CastingAsset]) -> None:
        draft = self._sequence_cast_draft(sequence)
        existing = {
            str(row.get("cast_key") or ""): row
            for row in draft
            if str(row.get("cast_key") or "")
        }
        for asset in assets:
            row = self.service.shot_service.asset_selection_cast_row(asset.cast_payload, existing_cast=existing)
            if not row:
                continue
            row.update(
                {
                    "category": asset.category,
                    "group": asset.group,
                    "thumbnail": asset.thumbnail,
                    "description": asset.description,
                    "contexts": self.service.cast_context_statuses(row),
                }
            )
            existing[str(row.get("cast_key") or "")] = row
            draft.append(row)
        self._mark_sequence_cast_dirty(sequence)

    def _pick_assets_for_sequence_cast(self) -> list[CastingAsset]:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Add Assets to Sequence Cast")
        layout = QtWidgets.QVBoxLayout(dialog)
        search = QtWidgets.QLineEdit()
        search.setPlaceholderText("Search asset")
        asset_list = QtWidgets.QListWidget()
        asset_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        layout.addWidget(search)
        layout.addWidget(asset_list, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        layout.addWidget(buttons)

        def populate() -> None:
            query = search.text().strip().lower()
            asset_list.clear()
            for asset in self.asset_rows:
                label = f"{asset.category}/{asset.group}/{asset.asset}/{asset.variant}"
                if query and query not in label.lower():
                    continue
                item = QtWidgets.QListWidgetItem(label)
                item.setData(QtCore.Qt.UserRole, asset)
                item.setIcon(asset_icon(QtCore, QtGui, thumbnail=asset.thumbnail, label=asset.asset))
                asset_list.addItem(item)

        search.textChanged.connect(lambda _text: populate())
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        populate()
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return []
        result = []
        for item in asset_list.selectedItems():
            data = item.data(QtCore.Qt.UserRole)
            if isinstance(data, CastingAsset):
                result.append(data)
        return result

    def populate_episode_combo(self) -> None:
        current = self.episode_combo.currentText()
        episodes = sorted({seq.episode for seq in self.service.sequences()}) or ["ep001"]
        self.episode_combo.blockSignals(True)
        self.episode_combo.clear()
        self.episode_combo.addItems(episodes)
        self.episode_combo.setCurrentText(current if current in episodes else episodes[0])
        self.episode_combo.blockSignals(False)
        self.populate_sequence_combo()

    def populate_sequence_combo(self) -> None:
        episode = self.episode_combo.currentText()
        current = self.sequence_combo.currentText()
        sequences = sorted({seq.sequence for seq in self.service.sequences() if seq.episode == episode}) or ["sq010"]
        self.sequence_combo.blockSignals(True)
        self.sequence_combo.clear()
        self.sequence_combo.addItems(sequences)
        self.sequence_combo.setCurrentText(current if current in sequences else sequences[0])
        self.sequence_combo.blockSignals(False)
        self.populate_shots_tab()

    def populate_shots_tab(self) -> None:
        episode = self.episode_combo.currentText()
        sequence = self.sequence_combo.currentText()
        self.shot_list.clear()
        for shot in self.service.shots_for_sequence(episode, sequence):
            item = QtWidgets.QListWidgetItem(shot.shot)
            item.setData(QtCore.Qt.UserRole, shot)
            self.shot_list.addItem(item)
        self.populate_sequence_cast()
        if self.shot_list.count():
            self.shot_list.setCurrentRow(0)
        else:
            self.populate_shot_cast()

    def populate_sequence_cast(self) -> None:
        self.sequence_cast_list.clear()
        data = self.service.load_sequence_cast(self.episode_combo.currentText(), self.sequence_combo.currentText())
        for key, entry in sorted((data.get("cast") or {}).items()):
            self.sequence_cast_list.addItem(self._cast_item(key, entry))

    def populate_shot_cast(self) -> None:
        self.shot_cast_list.clear()
        identity = self.current_shot_identity()
        if not identity:
            return
        data = self.service.load_shot_cast(identity)
        for key, entry in sorted((data.get("cast") or {}).items()):
            self.shot_cast_list.addItem(self._cast_item(key, entry))

    def current_shot_identity(self):
        item = self.shot_list.currentItem()
        data = item.data(QtCore.Qt.UserRole) if item else None
        return data

    def _cast_item(self, key: str, entry: dict[str, Any]) -> QtWidgets.QListWidgetItem:
        asset_name = str(entry.get("asset") or key)
        variant = str(entry.get("variant") or "default")
        asset_row = self._asset_for_cast(entry)
        category = str(entry.get("category") or getattr(asset_row, "category", ""))
        group = str(entry.get("group") or getattr(asset_row, "group", ""))
        label = asset_card_text(
            asset=asset_name,
            category=category,
            group=group,
            variant=variant,
            status=str(entry.get("status") or getattr(asset_row, "status", "")),
            asset_type=category,
            description=str(entry.get("note") or getattr(asset_row, "description", "")),
        )
        item = QtWidgets.QListWidgetItem(label)
        row = {"cast_key": key, **entry}
        item.setData(QtCore.Qt.UserRole, row)
        item.setToolTip(
            asset_tooltip(
                asset=asset_name,
                category=category,
                group=group,
                variant=variant,
                status=str(entry.get("status") or getattr(asset_row, "status", "")),
                description=str(entry.get("note") or getattr(asset_row, "description", "")),
            )
        )
        item.setIcon(asset_icon(QtCore, QtGui, thumbnail=getattr(asset_row, "thumbnail", ""), label=asset_name))
        return item

    def _asset_for_cast(self, entry: dict[str, Any]) -> CastingAsset | None:
        asset = str(entry.get("asset") or "")
        variant = str(entry.get("variant") or "default")
        return next((row for row in self.asset_rows if row.asset == asset and row.variant == variant), None)

    def populate_cast_detail(self) -> None:
        item = self.sender().currentItem() if isinstance(self.sender(), QtWidgets.QListWidget) else None
        if not item:
            item = self.shot_cast_list.currentItem() or self.sequence_cast_list.currentItem()
        self._detail_cast_item = item
        data = item.data(QtCore.Qt.UserRole) if item else {}
        if not isinstance(data, dict):
            data = {}
        asset_row = self._asset_for_cast(data)
        read_only = self._detail_cast_is_sequence_cast(item)
        self._set_label_pixmap(self.cast_thumb, asset_row.thumbnail if asset_row else "")
        self._populating_cast_detail = True
        self.cast_info_table.blockSignals(True)
        self.cast_info_table.setRowCount(0)
        for key, value in data.items():
            row = self.cast_info_table.rowCount()
            self.cast_info_table.insertRow(row)
            key_item = QtWidgets.QTableWidgetItem(str(key))
            key_item.setFlags(key_item.flags() & ~QtCore.Qt.ItemIsEditable)
            value_item = QtWidgets.QTableWidgetItem(str(value))
            value_item.setData(QtCore.Qt.UserRole, str(key))
            if read_only or str(key) not in EDITABLE_CAST_DETAIL_KEYS:
                value_item.setFlags(value_item.flags() & ~QtCore.Qt.ItemIsEditable)
            else:
                value_item.setToolTip("Editable. Save Shot Cast to persist.")
            self.cast_info_table.setItem(row, 0, key_item)
            self.cast_info_table.setItem(row, 1, value_item)
        self.cast_info_table.blockSignals(False)
        self._populating_cast_detail = False

    def _detail_cast_is_sequence_cast(self, item) -> bool:
        return bool(item and self.sequence_cast_list.row(item) >= 0)

    def _on_cast_detail_changed(self, table_item) -> None:
        if self._populating_cast_detail or table_item.column() != 1:
            return
        if self._detail_cast_is_sequence_cast(self._detail_cast_item):
            return
        key = str(table_item.data(QtCore.Qt.UserRole) or "")
        if key not in EDITABLE_CAST_DETAIL_KEYS or self._detail_cast_item is None:
            return
        data = dict(self._detail_cast_item.data(QtCore.Qt.UserRole) or {})
        data[key] = table_item.text().strip()
        self._detail_cast_item.setData(QtCore.Qt.UserRole, data)
        self._detail_cast_item.setText(self._cast_item(str(data.get("cast_key") or ""), data).text())

    def add_sequence_cast_to_shot(self) -> None:
        existing = self._shot_cast_rows()
        keys = {row["cast_key"] for row in existing}
        for item in self.sequence_cast_list.selectedItems():
            row = dict(item.data(QtCore.Qt.UserRole) or {})
            if row.get("cast_key") and row["cast_key"] not in keys:
                self.shot_cast_list.addItem(self._cast_item(row["cast_key"], row))
                keys.add(row["cast_key"])

    def remove_shot_cast(self) -> None:
        for item in self.shot_cast_list.selectedItems():
            self.shot_cast_list.takeItem(self.shot_cast_list.row(item))

    def show_sequence_cast_menu(self, pos) -> None:
        item = self.sequence_cast_list.itemAt(pos)
        if item:
            self.sequence_cast_list.setCurrentItem(item)
        menu = QtWidgets.QMenu(self)
        add_action = menu.addAction("Add to Shot Cast")
        add_action.setEnabled(bool(self.sequence_cast_list.selectedItems()))
        global_pos = self.sequence_cast_list.mapToGlobal(pos)
        action = menu.exec_(global_pos) if hasattr(menu, "exec_") else menu.exec(global_pos)
        if action == add_action:
            self.add_sequence_cast_to_shot()

    def remove_sequence_cast(self) -> None:
        episode, sequence = self._active_sequence_for_sequence_cast()
        keys = self._selected_sequence_cast_keys()
        if not keys:
            return
        accepted = QtWidgets.QMessageBox.question(
            self,
            "Remove Sequence Cast",
            "Remove selected cast from sequence cast?\n\n" + "\n".join(keys),
        )
        if accepted != QtWidgets.QMessageBox.Yes:
            return
        if self.tabs.currentWidget() == self.assets_tab and self.selected_sequence():
            draft = self._sequence_cast_draft((episode, sequence))
            key_set = set(keys)
            self._sequence_cast_drafts[(episode, sequence)] = [
                row for row in draft if str(row.get("cast_key") or "") not in key_set
            ]
            self._mark_sequence_cast_dirty((episode, sequence))
            self.populate_asset_table()
            self.populate_asset_detail()
            return
        try:
            self.service.remove_sequence_cast(episode, sequence, keys)
            self.populate_asset_table()
            self.populate_sequence_cast()
            self.populate_cast_detail()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Remove Sequence Cast Failed", str(exc))

    def save_sequence_cast(self) -> None:
        try:
            path = self._save_sequence_cast()
            self.populate_asset_table()
            self.populate_sequence_cast()
            QtWidgets.QMessageBox.information(self, "Save Sequence Cast", str(path))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save Sequence Cast Failed", str(exc))

    def _save_sequence_cast(self) -> Path:
        episode, sequence = self._active_sequence_for_sequence_cast()
        path = self.service.save_sequence_cast(episode, sequence, self._sequence_cast_rows())
        self._sequence_cast_drafts.pop((episode, sequence), None)
        self._sequence_cast_dirty.discard((episode, sequence))
        self._update_sequence_cast_save_label((episode, sequence))
        return path

    def publish_sequence_cast(self) -> None:
        episode, sequence = self._active_sequence_for_sequence_cast()
        comment, accepted = QtWidgets.QInputDialog.getText(self, "Publish Sequence Cast", "Comment")
        if not accepted:
            return
        try:
            self._save_sequence_cast()
            path = self.service.publish_sequence_cast(episode, sequence, comment=comment.strip())
            QtWidgets.QMessageBox.information(self, "Publish Sequence Cast", str(path))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Publish Sequence Cast Failed", str(exc))

    def save_shot_cast(self) -> None:
        identity = self.current_shot_identity()
        if not identity:
            return
        try:
            path = self.service.save_shot_cast(identity, self._shot_cast_rows())
            QtWidgets.QMessageBox.information(self, "Save Shot Cast", str(path))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save Shot Cast Failed", str(exc))

    def publish_shot_cast(self) -> None:
        identity = self.current_shot_identity()
        if not identity:
            return
        comment, accepted = QtWidgets.QInputDialog.getText(self, "Publish Shot Cast", "Comment")
        if not accepted:
            return
        try:
            self.save_shot_cast()
            path = self.service.publish_shot_cast(identity, comment=comment.strip())
            QtWidgets.QMessageBox.information(self, "Publish Shot Cast", str(path))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Publish Shot Cast Failed", str(exc))

    def _shot_cast_rows(self) -> list[dict[str, Any]]:
        rows = []
        identity = self.current_shot_identity()
        for index in range(self.shot_cast_list.count()):
            data = dict(self.shot_cast_list.item(index).data(QtCore.Qt.UserRole) or {})
            rows.append({"episode": identity.episode, "sequence": identity.sequence, "shot": identity.shot, **data})
        return rows

    def _sequence_cast_rows(self) -> list[dict[str, Any]]:
        rows = []
        episode, sequence = self._active_sequence_for_sequence_cast()
        sequence_key = (episode, sequence)
        if sequence_key in self._sequence_cast_dirty or (self.tabs.currentWidget() == self.assets_tab and self.selected_sequence()):
            for row in self._sequence_cast_draft(sequence_key):
                data = dict(row)
                data.pop("contexts", None)
                data.pop("thumbnail", None)
                data.pop("description", None)
                data.pop("group", None)
                rows.append({"episode": episode, "sequence": sequence, **data})
            return rows
        for index in range(self.sequence_cast_list.count()):
            data = dict(self.sequence_cast_list.item(index).data(QtCore.Qt.UserRole) or {})
            rows.append({"episode": episode, "sequence": sequence, **data})
        return rows

    def _active_sequence_for_sequence_cast(self) -> tuple[str, str]:
        if self.tabs.currentWidget() == self.assets_tab:
            selected = self.selected_sequence()
            if selected:
                return selected
        return self.episode_combo.currentText(), self.sequence_combo.currentText()

    def _selected_sequence_cast_keys(self) -> list[str]:
        if self.tabs.currentWidget() == self.assets_tab and self.selected_sequence():
            keys = []
            for row in sorted({index.row() for index in self.asset_table.selectedIndexes()}):
                item = self.asset_table.item(row, 0)
                data = item.data(QtCore.Qt.UserRole) if item else {}
                key = str(data.get("cast_key") or "") if isinstance(data, dict) else ""
                if key:
                    keys.append(key)
            return keys
        keys = []
        for item in self.sequence_cast_list.selectedItems():
            data = item.data(QtCore.Qt.UserRole) or {}
            key = str(data.get("cast_key") or "")
            if key:
                keys.append(key)
        return keys

    def auto_selection(self) -> None:
        try:
            import maya.cmds as cmds
        except Exception:
            QtWidgets.QMessageBox.information(self, "Auto Selection", "Auto Selection is available inside Maya.")
            return
        namespaces = {name.split(":", 1)[0] for name in cmds.ls(selection=True) or [] if ":" in name}
        if not namespaces:
            QtWidgets.QMessageBox.information(self, "Auto Selection", "Select referenced assets in Maya first.")
            return
        for index in range(self.sequence_cast_list.count()):
            item = self.sequence_cast_list.item(index)
            data = item.data(QtCore.Qt.UserRole) or {}
            item.setSelected(str(data.get("namespace") or data.get("cast_key")) in namespaces)

    def _set_label_pixmap(self, label: QtWidgets.QLabel, path: str) -> None:
        set_label_thumbnail(QtCore, QtGui, label, path)


_WINDOW = None


def show(
    config_dir: str | os.PathLike[str] | None = None,
    parent=None,
    *,
    episode: str = "",
    sequence: str = "",
    shot: str = "",
    asset_names: list[str] | None = None,
):
    global _WINDOW
    existing_app = QtWidgets.QApplication.instance()
    app = existing_app or QtWidgets.QApplication(sys.argv)
    from smartlib.core.qt import parent_for_maya

    window_parent = parent_for_maya(QtWidgets, parent)
    _WINDOW = SmartCastingWindow(config_dir=config_dir, parent=window_parent)
    if window_parent is not None:
        _WINDOW.setWindowFlags(_WINDOW.windowFlags() | QtCore.Qt.Window)
    _WINDOW.show()
    _WINDOW.focus_context(
        episode=episode,
        sequence=sequence,
        shot=shot,
        asset_names=asset_names,
    )
    if existing_app:
        return _WINDOW
    return app.exec()

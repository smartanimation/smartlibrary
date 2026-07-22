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
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json


def _qt():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
        return QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets
        return QtCore, QtGui, QtWidgets


QtCore, QtGui, QtWidgets = _qt()


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
        self.category = QtWidgets.QLineEdit(asset.category if asset else "characters")
        self.group = QtWidgets.QLineEdit(asset.group if asset else "hero")
        self.asset = QtWidgets.QLineEdit(asset.asset if asset else "")
        self.variant = QtWidgets.QLineEdit("default" if asset is None else "")
        self.description = QtWidgets.QLineEdit("")
        if asset:
            self.category.setReadOnly(True)
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
            "category": self.category.text().strip(),
            "group": self.group.text().strip(),
            "asset": self.asset.text().strip(),
            "variant": self.variant.text().strip() or "default",
            "description": self.description.text().strip(),
        }


class SmartCastingWindow(QtWidgets.QMainWindow):
    def __init__(self, config_dir: str | os.PathLike[str] | None = None, parent=None):
        super().__init__(parent)
        self.project_config = ProjectConfig(config_dir or _default_config_dir())
        self.service = SmartCastingService(self.project_config)
        self.asset_rows: list[CastingAsset] = []
        self.setWindowTitle("Smart Casting")
        self.resize(1180, 700)
        self._build()
        self.refresh()

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
        self.category_filter = QtWidgets.QListWidget()
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
        for button in (self.create_asset_btn, self.create_variant_btn, self.set_thumbnail_btn, self.add_to_cast_btn):
            buttons.addWidget(button)
        buttons.addStretch(1)
        self.asset_table = QtWidgets.QTableWidget(0, 6)
        self.asset_table.setHorizontalHeaderLabels(["", "Category", "Group", "Asset Name", "Variant", "Status"])
        self.asset_table.horizontalHeader().setStretchLastSection(True)
        self.asset_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.asset_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.asset_table.itemSelectionChanged.connect(self.populate_asset_detail)
        center.addWidget(self.asset_search)
        center.addLayout(buttons)
        center.addWidget(self.asset_table, 1)

        right = QtWidgets.QVBoxLayout()
        self.asset_thumb = QtWidgets.QLabel("Thumbnail")
        self.asset_thumb.setFixedSize(190, 108)
        self.asset_thumb.setAlignment(QtCore.Qt.AlignCenter)
        self.asset_thumb.setStyleSheet("background:#30363d; border:1px solid #4a4a4a;")
        self.asset_info = QtWidgets.QLabel("")
        self.asset_info.setWordWrap(True)
        self.metadata_table = QtWidgets.QTableWidget(0, 2)
        self.metadata_table.setHorizontalHeaderLabels(["Key", "Value"])
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
        sequence_buttons = QtWidgets.QHBoxLayout()
        self.save_sequence_cast_btn = QtWidgets.QPushButton("Save Sequence Cast")
        self.publish_sequence_cast_btn = QtWidgets.QPushButton("Publish Sequence Cast")
        self.remove_sequence_cast_btn = QtWidgets.QPushButton("Remove Sequence Cast")
        sequence_buttons.addStretch(1)
        sequence_buttons.addWidget(self.save_sequence_cast_btn)
        sequence_buttons.addWidget(self.publish_sequence_cast_btn)
        sequence_buttons.addWidget(self.remove_sequence_cast_btn)
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
        center.addLayout(sequence_buttons)
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
        self.cast_info_table.horizontalHeader().setStretchLastSection(True)
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

    def refresh(self) -> None:
        self.asset_rows = self.service.list_assets()
        self.populate_sequence_tree()
        self.populate_categories()
        self.populate_asset_table()
        self.populate_episode_combo()

    def populate_sequence_tree(self) -> None:
        self.sequence_tree.clear()
        episodes: dict[str, list[str]] = {}
        for seq in self.service.sequences():
            episodes.setdefault(seq.episode, []).append(seq.sequence)
        for episode, sequences in sorted(episodes.items()):
            ep_item = QtWidgets.QTreeWidgetItem([episode])
            self.sequence_tree.addTopLevelItem(ep_item)
            for sequence in sorted(set(sequences)):
                item = QtWidgets.QTreeWidgetItem([sequence])
                item.setData(0, QtCore.Qt.UserRole, (episode, sequence))
                ep_item.addChild(item)
        self.sequence_tree.expandAll()

    def populate_categories(self) -> None:
        current = self.selected_categories()
        self.category_filter.clear()
        for category in self.service.categories():
            item = QtWidgets.QListWidgetItem(category)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if not current or category in current else QtCore.Qt.Unchecked)
            self.category_filter.addItem(item)

    def selected_categories(self) -> set[str]:
        result = set()
        for row in range(self.category_filter.count()):
            item = self.category_filter.item(row)
            if item.checkState() == QtCore.Qt.Checked:
                result.add(item.text())
        return result

    def populate_asset_table(self) -> None:
        selected_categories = self.selected_categories()
        query = self.asset_search.text().strip().lower()
        self.asset_table.setRowCount(0)
        for asset in self.asset_rows:
            if selected_categories and asset.category not in selected_categories:
                continue
            haystack = " ".join([asset.category, asset.group, asset.asset, asset.variant, asset.status, asset.description]).lower()
            if query and query not in haystack:
                continue
            row = self.asset_table.rowCount()
            self.asset_table.insertRow(row)
            values = ["", asset.category, asset.group, asset.asset, asset.variant, asset.status]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(str(value))
                item.setData(QtCore.Qt.UserRole, asset)
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                if column == 0 and asset.thumbnail:
                    item.setIcon(asset_icon(QtCore, QtGui, thumbnail=asset.thumbnail, label=asset.asset))
                self.asset_table.setItem(row, column, item)
        self.asset_table.resizeColumnsToContents()
        self.asset_table.horizontalHeader().setStretchLastSection(True)

    def current_asset(self) -> CastingAsset | None:
        row = self.asset_table.currentRow()
        if row < 0:
            return None
        item = self.asset_table.item(row, 0) or self.asset_table.item(row, 1)
        data = item.data(QtCore.Qt.UserRole) if item else None
        return data if isinstance(data, CastingAsset) else None

    def selected_assets(self) -> list[CastingAsset]:
        rows = sorted({index.row() for index in self.asset_table.selectedIndexes()})
        result = []
        for row in rows:
            item = self.asset_table.item(row, 0) or self.asset_table.item(row, 1)
            data = item.data(QtCore.Qt.UserRole) if item else None
            if isinstance(data, CastingAsset):
                result.append(data)
        return result

    def populate_asset_detail(self) -> None:
        asset = self.current_asset()
        self.metadata_table.setRowCount(0)
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
            return
        try:
            path, rows = self.service.add_assets_to_sequence_cast(sequence[0], sequence[1], assets)
            QtWidgets.QMessageBox.information(self, "Add Selected to Cast", f"Added {len(rows)} cast rows:\n{path}")
            self.populate_shots_tab()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Add Selected to Cast Failed", str(exc))

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
            asset_type=str(entry.get("role") or category),
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
        data = item.data(QtCore.Qt.UserRole) if item else {}
        if not isinstance(data, dict):
            data = {}
        asset_row = self._asset_for_cast(data)
        self._set_label_pixmap(self.cast_thumb, asset_row.thumbnail if asset_row else "")
        self.cast_info_table.setRowCount(0)
        for key, value in data.items():
            row = self.cast_info_table.rowCount()
            self.cast_info_table.insertRow(row)
            self.cast_info_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(key)))
            self.cast_info_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(value)))

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
        menu.addSeparator()
        save_action = menu.addAction("Save Sequence Cast")
        publish_action = menu.addAction("Publish Sequence Cast")
        remove_action = menu.addAction("Remove from Sequence Cast")
        remove_action.setEnabled(bool(self.sequence_cast_list.selectedItems()))
        global_pos = self.sequence_cast_list.mapToGlobal(pos)
        action = menu.exec_(global_pos) if hasattr(menu, "exec_") else menu.exec(global_pos)
        if action == add_action:
            self.add_sequence_cast_to_shot()
        elif action == save_action:
            self.save_sequence_cast()
        elif action == publish_action:
            self.publish_sequence_cast()
        elif action == remove_action:
            self.remove_sequence_cast()

    def remove_sequence_cast(self) -> None:
        episode = self.episode_combo.currentText()
        sequence = self.sequence_combo.currentText()
        keys = []
        for item in self.sequence_cast_list.selectedItems():
            data = item.data(QtCore.Qt.UserRole) or {}
            key = str(data.get("cast_key") or "")
            if key:
                keys.append(key)
        if not keys:
            return
        accepted = QtWidgets.QMessageBox.question(
            self,
            "Remove Sequence Cast",
            "Remove selected cast from sequence cast?\n\n" + "\n".join(keys),
        )
        if accepted != QtWidgets.QMessageBox.Yes:
            return
        try:
            self.service.remove_sequence_cast(episode, sequence, keys)
            self.populate_sequence_cast()
            self.populate_cast_detail()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Remove Sequence Cast Failed", str(exc))

    def save_sequence_cast(self) -> None:
        try:
            path = self._save_sequence_cast()
            QtWidgets.QMessageBox.information(self, "Save Sequence Cast", str(path))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save Sequence Cast Failed", str(exc))

    def _save_sequence_cast(self) -> Path:
        episode = self.episode_combo.currentText()
        sequence = self.sequence_combo.currentText()
        return self.service.save_sequence_cast(episode, sequence, self._sequence_cast_rows())

    def publish_sequence_cast(self) -> None:
        episode = self.episode_combo.currentText()
        sequence = self.sequence_combo.currentText()
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
        episode = self.episode_combo.currentText()
        sequence = self.sequence_combo.currentText()
        for index in range(self.sequence_cast_list.count()):
            data = dict(self.sequence_cast_list.item(index).data(QtCore.Qt.UserRole) or {})
            rows.append({"episode": episode, "sequence": sequence, **data})
        return rows

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


def show(config_dir: str | os.PathLike[str] | None = None, parent=None):
    global _WINDOW
    existing_app = QtWidgets.QApplication.instance()
    app = existing_app or QtWidgets.QApplication(sys.argv)
    from smartlib.core.qt import parent_for_maya

    window_parent = parent_for_maya(QtWidgets, parent)
    _WINDOW = SmartCastingWindow(config_dir=config_dir, parent=window_parent)
    if window_parent is not None:
        _WINDOW.setWindowFlags(_WINDOW.windowFlags() | QtCore.Qt.Window)
    _WINDOW.show()
    if existing_app:
        return _WINDOW
    return app.exec()

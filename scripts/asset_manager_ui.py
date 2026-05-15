from __future__ import annotations

import os
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

        filter_layout = QtWidgets.QHBoxLayout()
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Search asset")
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        filter_layout.addWidget(self.search_edit)
        filter_layout.addWidget(self.refresh_btn)
        root_layout.addLayout(filter_layout)

        splitter = QtWidgets.QSplitter()
        root_layout.addWidget(splitter, 1)

        self.asset_list = QtWidgets.QListWidget()
        self.asset_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.asset_list.setViewMode(QtWidgets.QListView.IconMode)
        self.asset_list.setResizeMode(QtWidgets.QListView.Adjust)
        self.asset_list.setMovement(QtWidgets.QListView.Static)
        self.asset_list.setIconSize(QtCore.QSize(150, 84))
        self.asset_list.setGridSize(QtCore.QSize(190, 190))
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
                padding: 8px;
                margin: 6px;
            }
            QListWidget::item:selected {
                background: #4d6f86;
                border: 1px solid #7fa8c2;
            }
            QListWidget::item:hover {
                background: #424242;
            }
        """)
        splitter.addWidget(self.asset_list)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        splitter.addWidget(right)

        right_layout.addWidget(QtWidgets.QLabel("Work Scenes"))
        self.dept_tabs = QtWidgets.QTabBar()
        self.dept_tabs.setExpanding(False)
        for dept in self.manager.asset_depts:
            self.dept_tabs.addTab(dept)
        if not self.manager.asset_depts:
            self.dept_tabs.addTab("model")
        right_layout.addWidget(self.dept_tabs)

        self.variant_list = QtWidgets.QListWidget()
        self.variant_list.setMaximumHeight(72)
        right_layout.addWidget(self.variant_list)

        self.dependency_label = QtWidgets.QLabel("")
        right_layout.addWidget(self.dependency_label)

        self.work_list = QtWidgets.QTableWidget(0, 5)
        self.work_list.setHorizontalHeaderLabels(["File", "Version", "Take", "Published", "Comment"])
        self.work_list.horizontalHeader().setStretchLastSection(True)
        self.work_list.verticalHeader().setVisible(False)
        self.work_list.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.work_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        right_layout.addWidget(self.work_list, 1)

        right_layout.addWidget(QtWidgets.QLabel("Data Files"))
        self.data_list = QtWidgets.QListWidget()
        self.data_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        right_layout.addWidget(self.data_list, 1)

        action_layout = QtWidgets.QHBoxLayout()
        self.open_scene_btn = QtWidgets.QPushButton("Open Scene")
        self.save_scene_btn = QtWidgets.QPushButton("Save Scene")
        self.publish_btn = QtWidgets.QPushButton("Publish")
        self.export_data_btn = QtWidgets.QPushButton("Export Data")
        self.import_btn = QtWidgets.QPushButton("Import Latest")
        action_layout.addStretch(1)
        action_layout.addWidget(self.open_scene_btn)
        action_layout.addWidget(self.save_scene_btn)
        action_layout.addWidget(self.publish_btn)
        action_layout.addWidget(self.export_data_btn)
        action_layout.addWidget(self.import_btn)
        right_layout.addLayout(action_layout)

        self.status_label = QtWidgets.QLabel("")
        root_layout.addWidget(self.status_label)

        self.search_edit.textChanged.connect(self._apply_filter)
        self.refresh_btn.clicked.connect(self.refresh_assets)
        self.asset_list.currentRowChanged.connect(self._show_current_asset)
        self.dept_tabs.currentChanged.connect(self._on_department_changed)
        self.variant_list.currentRowChanged.connect(lambda _row: self._show_current_asset())
        self.asset_list.customContextMenuRequested.connect(self._show_asset_context_menu)
        self.work_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.work_list.customContextMenuRequested.connect(self._show_work_context_menu)
        self.work_list.itemChanged.connect(self._on_work_item_changed)
        self.data_list.customContextMenuRequested.connect(self._show_data_context_menu)
        self.open_scene_btn.clicked.connect(self._open_selected_scene)
        self.save_scene_btn.clicked.connect(self._save_scene)
        self.publish_btn.clicked.connect(self._publish_selected_work)
        self.export_data_btn.clicked.connect(self._show_export_data_menu)
        self.import_btn.clicked.connect(self._import_latest_publish)

    def refresh_assets(self, keep_selection: bool = True) -> None:
        selected_key = self._current_asset_key() if keep_selection else None
        self.assets = self.manager.list_assets()
        self._apply_filter(selected_key=selected_key)
        self._populate_variants()
        self._show_current_asset()
        self.status_label.setText(f"{len(self.assets)} assets")

    def _apply_filter(self, selected_key: tuple[str, str, str] | None = None) -> None:
        if selected_key is None:
            selected_key = self._current_asset_key()
        text = self.search_edit.text().strip().lower()
        self.asset_list.clear()
        row_to_select = -1
        for asset in self.assets:
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
                return QtGui.QIcon(self._thumbnail_canvas(pixmap, asset.name))

        pixmap = QtGui.QPixmap(150, 84)
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

    def _thumbnail_canvas(self, source: QtGui.QPixmap, label: str) -> QtGui.QPixmap:
        canvas = QtGui.QPixmap(150, 84)
        canvas.fill(QtGui.QColor("#2f343a"))
        scaled = source.scaled(
            142,
            76,
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
        painter = QtGui.QPainter(canvas)
        x = (canvas.width() - scaled.width()) // 2
        y = (canvas.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        painter.end()
        return canvas

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
        self.work_list.blockSignals(True)
        self.work_list.setRowCount(0)
        self.work_list.blockSignals(False)
        self.data_list.clear()
        self._update_dependency_label(asset)
        if not asset:
            return

        department = self._current_department()
        variant = self._current_variant()
        work_files = self.manager.list_work_files(
            asset,
            department=department,
            variant=variant,
            extensions=["ma", "mb", "hip", "hiplc", "hipnc"],
        )
        if not work_files:
            self.status_label.setText(f"No work scenes found under: {asset.work_dir / 'maya' / department / variant}")

        self.work_list.blockSignals(True)
        for path in work_files:
            row = self.work_list.rowCount()
            self.work_list.insertRow(row)
            parsed = self.manager.parse_work_file(path) or {}
            publish_record = self.manager.publish_record_for_work_file(asset, path)
            file_item = QtWidgets.QTableWidgetItem(path.relative_to(asset.root).as_posix())
            if publish_record:
                file_item.setIcon(self.published_work_icon)
                file_item.setToolTip(
                    f"Published official version: v{int(publish_record['version']):03d}"
                )
            file_item.setData(QtCore.Qt.UserRole, str(path))
            self.work_list.setItem(row, 0, file_item)
            self.work_list.setItem(row, 1, QtWidgets.QTableWidgetItem(f"v{parsed.get('version', 0):03d}" if parsed else ""))
            self.work_list.setItem(row, 2, QtWidgets.QTableWidgetItem(str(parsed.get("take", ""))))
            self.work_list.setItem(row, 3, QtWidgets.QTableWidgetItem("yes" if publish_record else ""))
            comment_item = QtWidgets.QTableWidgetItem(self.manager.file_comment(path))
            self.work_list.setItem(row, 4, comment_item)
        self.work_list.blockSignals(False)

        for path in self.manager.list_data_files(asset):
            item = QtWidgets.QListWidgetItem(path.relative_to(asset.root).as_posix())
            item.setData(QtCore.Qt.UserRole, str(path))
            self.data_list.addItem(item)

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
        variants = self.manager.work_variants(self._current_department())
        return variants[0] if variants else "main"

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

    def _latest_published_scene(self) -> Path | None:
        asset = self._current_asset()
        if not asset:
            return None
        latest = self.manager.latest_publish_info(
            asset,
            department=self._current_department(),
            variant=self._current_variant(),
            publish_format="ma",
        )
        if latest and latest.get("absolute_path"):
            return Path(latest["absolute_path"])
        return None

    def _on_department_changed(self, _index: int) -> None:
        self._populate_variants()
        self._show_current_asset()

    def _populate_variants(self) -> None:
        current = self._current_variant()
        self.variant_list.blockSignals(True)
        self.variant_list.clear()
        selected_row = 0
        for index, variant in enumerate(self.manager.work_variants(self._current_department())):
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
            variant="hires",
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
            text = self.data_list.currentItem().data(QtCore.Qt.UserRole)
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
        if not asset:
            return
        menu = QtWidgets.QMenu(self)
        open_root = menu.addAction("Open Asset Root")
        open_data = menu.addAction("Open Data")
        open_work = menu.addAction("Open Work")
        open_publish = menu.addAction("Open Publish")
        menu.addSeparator()
        create_folders = menu.addAction("Create Asset Folders")
        menu.addSeparator()
        copy_root = menu.addAction("Copy Asset Root")
        copy_data = menu.addAction("Copy Data Path")
        copy_work = menu.addAction("Copy Work Path")
        copy_publish = menu.addAction("Copy Publish Path")
        action = menu.exec(self.asset_list.mapToGlobal(pos))
        if action == open_root:
            self._open_current("root")
        elif action == open_data:
            self._open_current("data")
        elif action == open_work:
            self._open_current("work")
        elif action == open_publish:
            self._open_current("publish")
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

    def _show_data_context_menu(self, pos) -> None:
        item = self.data_list.itemAt(pos)
        if not item or not item.data(QtCore.Qt.UserRole):
            return
        path = Path(item.data(QtCore.Qt.UserRole))
        menu = QtWidgets.QMenu(self)
        open_folder = menu.addAction("Open Folder")
        copy_path = menu.addAction("Copy Path")
        action = menu.exec(self.data_list.mapToGlobal(pos))
        if action == open_folder:
            self.manager.open_in_explorer(path.parent)
        elif action == copy_path:
            self._copy_text(str(path))

    def _open_selected_scene(self) -> None:
        asset = self._current_asset()
        if not asset:
            self.status_label.setText("Select an asset first")
            return

        selected_path = self._selected_work_path()
        work_files = self.manager.list_work_files(
            asset,
            department=self._current_department(),
            variant=self._current_variant(),
            extensions=["ma", "mb", "hip", "hiplc", "hipnc"],
        )
        latest_work = self._latest_work_file(work_files)
        published_scene = self._latest_published_scene()

        menu = QtWidgets.QMenu(self)
        selected_action = menu.addAction("Selected Work Scene")
        selected_action.setEnabled(bool(selected_path))
        latest_action = menu.addAction("Latest Work Scene")
        latest_action.setEnabled(bool(latest_work))
        published_action = menu.addAction("Published Scene")
        published_action.setEnabled(bool(published_scene))
        action = menu.exec(QtGui.QCursor.pos())
        if not action:
            return
        if action == selected_action:
            path = selected_path
        elif action == latest_action:
            path = latest_work
        elif action == published_action:
            path = published_scene
        else:
            return

        try:
            open_scene_in_current_dcc(path)
            self.status_label.setText(f"Opened: {Path(path).name}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Open Scene Failed", str(exc))

    def _selected_work_path(self) -> str | None:
        row = self.work_list.currentRow()
        if row < 0:
            return None
        item = self.work_list.item(row, 0)
        if not item:
            return None
        return item.data(QtCore.Qt.UserRole)

    def _on_work_item_changed(self, item) -> None:
        if item.column() != 4:
            return
        path = self.work_list.item(item.row(), 0).data(QtCore.Qt.UserRole)
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
                variant=self._current_variant(),
            )
            comment = self._ask_comment("Save Scene Comment")
            if comment is None:
                return
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                save_scene_in_current_dcc(target)
                self.manager.set_file_comment(target, comment)
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
                variant=self._current_variant(),
            )
        elif msg.clickedButton() == next_version_btn:
            target = self.manager.next_work_version_path(
                asset,
                current_path=selected_path,
                department=department,
                variant=self._current_variant(),
            )
        else:
            return

        comment = self._ask_comment("Save Scene Comment")
        if comment is None:
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            save_scene_in_current_dcc(target)
            self.manager.set_file_comment(target, comment)
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
            )
            self.status_label.setText(f"Published: {published.name}")
            self._show_current_asset()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Publish Failed", str(exc))

    def _show_export_data_menu(self) -> None:
        asset = self._current_asset()
        if not asset:
            self.status_label.setText("Select an asset first")
            return

        menu = QtWidgets.QMenu(self)
        export_fbx = menu.addAction("Selected Mesh: .fbx")
        export_abc = menu.addAction("Selected Mesh: .abc")
        export_usd = menu.addAction("Selected Mesh: .usd")
        menu.addSeparator()
        export_guide = menu.addAction("mGear Guide")
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
                paths = export_selected_model_data(asset, self.manager, self._current_variant(), "fbx", comment)
            elif action == export_abc:
                paths = export_selected_model_data(asset, self.manager, self._current_variant(), "abc", comment)
            elif action == export_usd:
                paths = export_selected_model_data(asset, self.manager, self._current_variant(), "usd", comment)
            elif action == export_guide:
                paths = [export_mgear_guide(asset, self.manager)]
            elif action == export_skin_high:
                paths = [export_mgear_skin(asset, self.manager, "high")]
            elif action == export_skin_low:
                paths = [export_mgear_skin(asset, self.manager, "low")]
            else:
                return
            for path in paths:
                self.manager.set_file_comment(path, comment)
            self.status_label.setText("Exported: " + ", ".join(path.name for path in paths))
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


def export_selected_model_data(
    asset: Asset,
    manager: AssetManager,
    variant: str,
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

    variant = variant or "hires"
    clean_format = data_format.lower().lstrip(".")
    base_name = f"{asset.name}_model_{variant}"
    version = manager.next_data_version(
        asset,
        department="model",
        variant=variant,
    )
    data_path = manager.data_file_path(
        asset,
        department="model",
        variant=variant,
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
        version=version,
        files={clean_format: data_path.name},
        source_workfile=source_workfile,
        comment=comment,
    )
    return [data_path]


def export_mgear_guide(asset: Asset, manager: AssetManager) -> Path:
    try:
        import maya.cmds as cmds
    except ImportError:
        raise RuntimeError("mGear guide export is available inside Maya.")

    path = manager.next_data_version_path(
        asset,
        department="guide",
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
    return path


def export_mgear_skin(asset: Asset, manager: AssetManager, variant: str) -> Path:
    try:
        import maya.cmds as cmds
    except ImportError:
        raise RuntimeError("mGear skin export is available inside Maya.")

    path = asset.data_dir / "skin" / f"{asset.name}_{variant}.gSkinPack"
    path.parent.mkdir(parents=True, exist_ok=True)

    selection = cmds.ls(selection=True, long=True) or []
    if not selection:
        raise RuntimeError("Select skinned meshes to export.")

    try:
        from mgear.core import skin
    except ImportError:
        raise RuntimeError("mGear skin module was not found in this Maya session.")

    for candidate in ("exportSkinPack", "exportSkin", "exportSkinPackBinary"):
        exporter = getattr(skin, candidate, None)
        if exporter:
            try:
                exporter(str(path), selection)
            except TypeError:
                exporter(selection, str(path))
            return path

    raise RuntimeError("mGear skin export API was not found. Check your mGear version.")


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

from __future__ import annotations

import json

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets


class AssetMetadataPanel(QtWidgets.QWidget):
    saved = QtCore.Signal(object)

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.asset = None
        self.drafts = {}
        self._loading = False
        self.setMinimumWidth(250)
        layout = QtWidgets.QVBoxLayout(self)
        self.thumbnail = QtWidgets.QLabel("Thumbnail")
        self.thumbnail.setFixedSize(190, 108)
        self.thumbnail.setAlignment(QtCore.Qt.AlignCenter)
        self.info = QtWidgets.QLabel()
        self.info.setWordWrap(True)
        self.set_thumbnail_btn = QtWidgets.QPushButton("Set Thumbnail")
        self.table = QtWidgets.QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Key", "Value"])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.add_btn = QtWidgets.QPushButton("+")
        self.remove_btn = QtWidgets.QPushButton("-")
        self.add_btn.setToolTip("Add metadata")
        self.remove_btn.setToolTip("Remove selected metadata")
        self.save_btn = QtWidgets.QPushButton("Save Metadata")
        buttons = QtWidgets.QHBoxLayout()
        for button in (self.add_btn, self.remove_btn, self.save_btn):
            buttons.addWidget(button)
        layout.addWidget(self.thumbnail, 0, QtCore.Qt.AlignHCenter)
        layout.addWidget(self.info)
        layout.addWidget(self.set_thumbnail_btn)
        layout.addWidget(QtWidgets.QLabel("Custom Metadata"))
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)
        self.table.itemChanged.connect(self._remember)
        self.add_btn.clicked.connect(self._add)
        self.remove_btn.clicked.connect(self._remove)
        self.set_thumbnail_btn.clicked.connect(self._choose_thumbnail)
        self.save_btn.clicked.connect(self.save)
        self.set_asset(None)

    def _key(self):
        return (self.asset.category, self.asset.group, self.asset.name)

    def _rows(self):
        rows = []
        for row in range(self.table.rowCount()):
            key = self.table.item(row, 0)
            value = self.table.item(row, 1)
            rows.append((key.text() if key else "", value.text() if value else "",
                         value.data(QtCore.Qt.UserRole) if value else None))
        return rows

    def _remember(self, *_args):
        if self._loading or self.asset is None:
            return
        draft = self.drafts.setdefault(self._key(), {})
        draft["rows"] = self._rows()
        self.save_btn.setText("Save Metadata *")

    def set_asset(self, asset):
        self.asset = asset
        self._loading = True
        self.table.setRowCount(0)
        self.thumbnail.clear()
        self.thumbnail.setText("Thumbnail")
        self.info.clear()
        enabled = bool(asset and self.manager.is_asset_initialized(asset))
        for widget in (self.table, self.add_btn, self.remove_btn, self.save_btn, self.set_thumbnail_btn):
            widget.setEnabled(enabled)
        self.save_btn.setText("Save Metadata")
        if asset:
            data = self.manager.load_asset_metadata(asset)
            self.info.setText(
                f"{asset.name}\nCategory: {asset.category}\nGroup: {asset.group}\n"
                f"Status: {data.get('status') or 'Wait'}\n{data.get('description') or ''}"
            )
            custom = data.get("metadata") or {}
            rows = []
            for key, value in sorted(custom.items()):
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                rows.append((key, text, (text, value)))
            draft = self.drafts.get(self._key(), {})
            for key, text, original in draft.get("rows", rows):
                row = self.table.rowCount()
                self.table.insertRow(row)
                self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(key))
                item = QtWidgets.QTableWidgetItem(text)
                item.setData(QtCore.Qt.UserRole, original)
                self.table.setItem(row, 1, item)
            path = draft.get("thumbnail") or self.manager.find_asset_thumbnail(asset)
            self._show_thumbnail(path)
            self.save_btn.setText("Save Metadata *" if draft else "Save Metadata")
        self._loading = False

    def _show_thumbnail(self, path):
        if path:
            pixmap = QtGui.QPixmap(str(path))
            if not pixmap.isNull():
                self.thumbnail.setPixmap(pixmap.scaled(
                    self.thumbnail.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
                ))

    def _add(self):
        self.table.insertRow(self.table.rowCount())
        self._remember()

    def _remove(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)
        self._remember()

    def _choose_thumbnail(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Set Thumbnail", "", "Images (*.jpg *.jpeg *.png)")
        if not path:
            return
        if QtGui.QPixmap(path).isNull():
            QtWidgets.QMessageBox.warning(self, "Set Thumbnail", "The image could not be loaded.")
            return
        self._remember()
        self.drafts[self._key()]["thumbnail"] = path
        self._show_thumbnail(path)

    def save(self):
        if self.asset is None:
            return
        try:
            custom = {}
            for key, text, original in self._rows():
                key = key.strip()
                if not key:
                    raise ValueError("Metadata keys must not be empty.")
                if key in custom:
                    raise ValueError(f"Duplicate metadata key: {key}")
                # Keep existing JSON types when the displayed value was not edited.
                custom[key] = original[1] if original and original[0] == text else text
            draft = self.drafts.get(self._key(), {})
            self.manager.save_custom_metadata(self.asset, custom, draft.get("thumbnail", ""))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save Metadata Failed", str(exc))
            return
        self.drafts.pop(self._key(), None)
        self.set_asset(self.asset)
        self.saved.emit(self.asset)

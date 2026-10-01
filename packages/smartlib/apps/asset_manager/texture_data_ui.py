"""File-level Texture Data management embedded in Asset Manager."""
from pathlib import Path

from .texture_publish_ui import TexturePublishDialog, TextureDropTable, QtCore, QtGui, QtWidgets
from .texture_data import TextureDataService


class _Operation(QtCore.QThread):
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            self.completed.emit(self.operation())
        except Exception as exc:
            self.failed.emit(str(exc))


class TextureDataPanel(QtWidgets.QWidget):
    published = QtCore.Signal()

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.service = TextureDataService(config)
        self.identity = None
        self.worker = None
        layout = QtWidgets.QVBoxLayout(self)
        controls = QtWidgets.QHBoxLayout()
        self.subset = QtWidgets.QComboBox()
        from .subsets import subsets_for_asset
        self.subset.addItems(subsets_for_asset(config.load('templates_assets.yml'), 'texture') or ['low', 'high', 'proxy'])
        controls.addWidget(QtWidgets.QLabel('Subset'))
        controls.addWidget(self.subset)
        self.import_button = QtWidgets.QPushButton('Import textures…')
        self.publish_button = QtWidgets.QPushButton('Publish selected')
        self.remove_button = QtWidgets.QPushButton('Remove selected from Publish')
        self.refresh_button = QtWidgets.QPushButton('Refresh')
        for button in (self.import_button, self.publish_button, self.remove_button, self.refresh_button):
            controls.addWidget(button)
        layout.addLayout(controls)
        layout.addWidget(QtWidgets.QLabel('Drop files / folders to import. Select rows to publish only those textures.'))
        self.table = TextureDropTable()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(['Texture', 'Data', 'Published from', 'State', 'Usage', 'Color space'])
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        layout.addWidget(self.table)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.subset.currentTextChanged.connect(self.refresh)
        self.refresh_button.clicked.connect(self.refresh)
        self.import_button.clicked.connect(self.open_import)
        self.publish_button.clicked.connect(self.publish_selected)
        self.remove_button.clicked.connect(self.remove_selected)
        self.table.pathsDropped.connect(self.import_paths)

    def set_identity(self, identity):
        self.identity = identity
        self.refresh()

    def refresh(self, *_):
        self.table.setRowCount(0)
        if not self.identity:
            return
        try:
            catalog = self.service.catalog(self.identity, self.subset.currentText())
            published, _ = self.service.texture_snapshot(self.identity, self.subset.currentText())
            for key in sorted(set(catalog) | set(published)):
                data, official = catalog.get(key), published.get(key)
                path = Path(data['path'] if data else official['artifact']['path'])
                state = 'Not imported' if not data else ('New' if not official else
                    ('Published' if data['sha256'] == official['artifact']['sha256'] and
                     all(data[k] == official['metadata'].get(k, 'unspecified') for k in ('usage', 'color_space')) else 'Modified'))
                metadata = data or official['metadata']
                values = [path.name, data['version'] if data else '', official['version'] if official else '',
                          state, metadata.get('usage', 'unspecified'), metadata.get('color_space', 'unspecified')]
                row = self.table.rowCount()
                self.table.insertRow(row)
                for column, value in enumerate(values):
                    item = QtWidgets.QTableWidgetItem(value)
                    item.setToolTip(str(path))
                    self.table.setItem(row, column, item)
                self.table.item(row, 0).setData(QtCore.Qt.UserRole, key)
                reader = QtGui.QImageReader(str(path))
                reader.setScaledSize(QtCore.QSize(48, 48))
                preview = reader.read()
                if not preview.isNull():
                    self.table.item(row, 0).setIcon(QtGui.QIcon(QtGui.QPixmap.fromImage(preview)))
            self.status.setText(f'{len(catalog)} imported / {len(published)} published textures')
        except Exception as exc:
            self.status.setText(str(exc))

    def open_import(self):
        if not self.identity:
            return
        dialog = TexturePublishDialog(self.service, self.identity, self, ingest=True, subset=self.subset.currentText())
        (getattr(dialog, 'exec', None) or dialog.exec_)()
        if dialog.manifest:
            self.subset.setCurrentText(dialog.subset.currentText())
            self.refresh()

    def import_paths(self, paths):
        if not self.identity:
            return
        # Use the intake confirmation for metadata and excluded-file review.
        dialog = TexturePublishDialog(self.service, self.identity, self, ingest=True, subset=self.subset.currentText())
        dialog.add_paths(paths)
        (getattr(dialog, 'exec', None) or dialog.exec_)()
        if dialog.manifest:
            self.subset.setCurrentText(dialog.subset.currentText())
            self.refresh()

    def selected_names(self):
        return [self.table.item(row, 0).data(QtCore.Qt.UserRole)
                for row in sorted({index.row() for index in self.table.selectedIndexes()})]

    def publish_selected(self):
        names = self.selected_names()
        if not names:
            self.status.setText('Select textures to publish.')
            return
        identity, subset = self.identity, self.subset.currentText()
        self._start(lambda: self.service.publish_data(identity, names, subset))

    def remove_selected(self):
        names = self.selected_names()
        if not names:
            return
        identity, subset = self.identity, self.subset.currentText()
        self._start(lambda: self.service.publish_textures(identity, subset=subset, entries=[], removed=names))

    def _start(self, operation):
        self.setEnabled(False)
        self.status.setText('Publishing…')
        self.worker = _Operation(operation, self)
        self.worker.completed.connect(self._completed)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _completed(self, manifest):
        self.worker.wait()
        self.setEnabled(True)
        self.refresh()
        self.status.setText(f'Published: {manifest}')
        self.published.emit()

    def _failed(self, message):
        self.worker.wait()
        self.setEnabled(True)
        self.status.setText(f'Publish failed: {message}')

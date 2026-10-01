"""Standalone file intake and confirmation for Asset Manager Texture Publish."""
from pathlib import Path
import json

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets

from .texture_intake import (COLOR_SPACES, IMAGE_EXTENSIONS, TEXTURE_USAGES,
                             TextureInput, collect_textures)


class TextureDropTable(QtWidgets.QTableWidget):
    pathsDropped = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(0, 5, parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DropOnly)
        self.setHorizontalHeaderLabels(['Texture', 'Size', 'UDIM', 'Usage', 'Color space'])
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.setIconSize(QtCore.QSize(48, 48))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and all(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls and all(url.isLocalFile() for url in urls):
            self.pathsDropped.emit([url.toLocalFile() for url in urls])
            event.acceptProposedAction()
        else:
            event.ignore()


class _PublishWorker(QtCore.QThread):
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, service, identity, subset, entries, parent, ingest=False):
        super().__init__(parent)
        self.service, self.identity = service, identity
        self.subset, self.entries = subset, entries
        self.ingest = ingest

    def run(self):
        try:
            operation = self.service.ingest if self.ingest else self.service.publish_textures
            manifest = operation(
                self.identity, subset=self.subset, entries=self.entries)
            self.completed.emit(manifest)
        except Exception as exc:
            self.failed.emit(str(exc))


class TexturePublishDialog(QtWidgets.QDialog):
    SETTINGS_MIME = 'application/x-smartpipeline-texture-settings'

    def __init__(self, service, identity, parent=None, *, ingest=False, subset=None):
        super().__init__(parent)
        self.service, self.identity = service, identity
        self.manifest = None
        self.worker = None
        self.ingest = ingest
        self.setWindowTitle('Import Texture Data' if ingest else 'Publish Texture')
        self.resize(880, 540)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(
            f'{identity.category} / {identity.group} / {identity.name} / {identity.variant}'))
        self.controls = QtWidgets.QWidget()
        controls = QtWidgets.QHBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(QtWidgets.QLabel('Subset'))
        self.subset = QtWidgets.QComboBox()
        from .subsets import subsets_for_asset
        config = service.project_config.load('templates_assets.yml') or {}
        subsets = subsets_for_asset(config, 'texture', category=identity.category, group=identity.group)
        self.subset.addItems(subsets or ['low', 'high', 'proxy'])
        if subset:
            if self.subset.findText(subset) < 0:
                self.subset.addItem(subset)
            self.subset.setCurrentText(subset)
        controls.addWidget(self.subset)
        for label, callback in [('Add files', self._add_files), ('Add folder', self._add_folder),
                                ('Remove selected', self._remove_selected)]:
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(callback)
            controls.addWidget(button)
        layout.addWidget(self.controls)
        self.destination = QtWidgets.QLabel()
        self.destination.setWordWrap(True)
        layout.addWidget(self.destination)
        layout.addWidget(QtWidgets.QLabel(
            'Drop image files or folders below. Unselected published textures are retained.\n'
            'Names are preserved. Usage and color space are metadata; images are not converted.'))
        self.table = TextureDropTable()
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.table.pathsDropped.connect(self.add_paths)
        layout.addWidget(self.table, 1)
        settings_row = QtWidgets.QHBoxLayout()
        for label, callback in [('Copy settings', self.copy_settings), ('Paste settings', self.paste_settings)]:
            button = QtWidgets.QPushButton(label)
            button.setAutoDefault(False)
            button.clicked.connect(callback)
            settings_row.addWidget(button)
        settings_row.addWidget(QtWidgets.QLabel('Copy one row; select multiple rows with Ctrl / Shift to paste.'))
        layout.addLayout(settings_row)
        action_class = getattr(QtGui, 'QAction', None) or QtWidgets.QAction
        for label, key, callback in [('Copy settings', 'Ctrl+C', self.copy_settings),
                                     ('Paste settings', 'Ctrl+V', self.paste_settings)]:
            action = action_class(label, self.table)
            action.setShortcut(QtGui.QKeySequence(key))
            action.setShortcutContext(QtCore.Qt.WidgetWithChildrenShortcut)
            action.triggered.connect(callback)
            self.table.addAction(action)
        self.status = QtWidgets.QLabel('No textures selected. Unspecified metadata can be assigned later.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QtWidgets.QDialogButtonBox()
        self.publish_button = buttons.addButton('Import all' if ingest else 'Publish', QtWidgets.QDialogButtonBox.AcceptRole)
        self.cancel_button = buttons.addButton(QtWidgets.QDialogButtonBox.Cancel)
        self.publish_button.clicked.connect(self._publish)
        self.cancel_button.clicked.connect(self.reject)
        self.publish_button.setEnabled(False)
        layout.addWidget(buttons)
        self.subset.currentTextChanged.connect(self._destination)
        self._destination()

    def _destination(self):
        try:
            resolver = self.service.paths.asset_data_dir if self.ingest else self.service.paths.asset_publish_dir
            root = resolver(self.identity, 'texture', self.subset.currentText())
            self.destination.setText(f'Destination: {root}\nA new version will be allocated on Publish.')
        except Exception as exc:
            self.destination.setText(str(exc))

    def entries(self):
        return [TextureInput(Path(self.table.item(row, 0).data(QtCore.Qt.UserRole)),
                             self.table.cellWidget(row, 3).currentText(),
                             self.table.cellWidget(row, 4).currentText())
                for row in range(self.table.rowCount())]

    def copy_settings(self):
        if not self.table.isEnabled():
            return
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        if len(rows) != 1:
            self.status.setText('Select exactly one row to copy Usage and Color space.')
            return
        row = rows[0]
        settings = dict(usage=self.table.cellWidget(row, 3).currentText(),
                        color_space=self.table.cellWidget(row, 4).currentText())
        mime = QtCore.QMimeData()
        mime.setData(self.SETTINGS_MIME, json.dumps(settings).encode('utf-8'))
        mime.setText(settings['usage'] + '\t' + settings['color_space'])
        QtWidgets.QApplication.clipboard().setMimeData(mime)
        self.status.setText(f"Copied settings: {settings['usage']} / {settings['color_space']}")

    def paste_settings(self):
        if not self.table.isEnabled():
            return
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        if not rows:
            self.status.setText('Select destination rows first.')
            return
        mime = QtWidgets.QApplication.clipboard().mimeData()
        try:
            if mime is None or not mime.hasFormat(self.SETTINGS_MIME):
                raise ValueError()
            settings = json.loads(bytes(mime.data(self.SETTINGS_MIME)).decode('utf-8'))
            if settings['usage'] not in TEXTURE_USAGES or settings['color_space'] not in COLOR_SPACES:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            self.status.setText('Copy Texture settings first.')
            return
        for row in rows:
            self.table.cellWidget(row, 3).setCurrentText(settings['usage'])
            self.table.cellWidget(row, 4).setCurrentText(settings['color_space'])
        self.status.setText(f'Pasted Usage and Color space to {len(rows)} rows.')

    def add_paths(self, paths):
        previous = self.entries()
        try:
            entries, ignored = collect_textures([entry.path for entry in previous] + list(paths))
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))
            return
        metadata = {entry.path: entry for entry in previous}
        self.table.setRowCount(0)
        for entry in entries:
            entry = metadata.get(entry.path, entry)
            row = self.table.rowCount()
            self.table.insertRow(row)
            item = QtWidgets.QTableWidgetItem(entry.path.name)
            item.setData(QtCore.Qt.UserRole, str(entry.path))
            item.setToolTip(str(entry.path))
            # Qt may not decode EXR/TX; those remain valid publish inputs.
            reader = QtGui.QImageReader(str(entry.path))
            reader.setScaledSize(QtCore.QSize(48, 48))
            preview = reader.read()
            if not preview.isNull():
                item.setIcon(QtGui.QIcon(QtGui.QPixmap.fromImage(preview)))
            self.table.setItem(row, 0, item)
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(f'{entry.path.stat().st_size / 1024:.1f} KB'))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(entry.udim or '')))
            for column, choices, value in [(3, TEXTURE_USAGES, entry.usage), (4, COLOR_SPACES, entry.color_space)]:
                combo = QtWidgets.QComboBox()
                combo.addItems(choices)
                combo.setCurrentText(value)
                combo.activated.connect(lambda _index, r=row, c=column: self.table.setCurrentCell(r, c))
                self.table.setCellWidget(row, column, combo)
        message = f'{len(entries)} textures selected.'
        if ignored:
            message += '\nIgnored non-texture files: ' + ', '.join(str(path) for path in ignored)
        self.status.setText(message)
        self.publish_button.setEnabled(bool(entries))

    def _add_files(self):
        patterns = ' '.join('*' + extension for extension in sorted(IMAGE_EXTENSIONS))
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(self, 'Select textures', '', f'Textures ({patterns})')
        if files:
            self.add_paths(files)

    def _add_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, 'Select texture folder')
        if folder:
            self.add_paths([folder])

    def _remove_selected(self):
        for row in sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)
        self.publish_button.setEnabled(self.table.rowCount() > 0)
        self.status.setText(f'{self.table.rowCount()} textures selected.')

    def _publish(self):
        self.controls.setEnabled(False)
        self.table.setEnabled(False)
        self.publish_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.status.setText('Publishing textures…')
        self.worker = _PublishWorker(self.service, self.identity, self.subset.currentText(), self.entries(), self, self.ingest)
        self.worker.completed.connect(self._completed)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _completed(self, manifest):
        self.worker.wait()
        self.manifest = manifest
        self.accept()

    def _failed(self, message):
        self.worker.wait()
        self.controls.setEnabled(True)
        self.table.setEnabled(True)
        self.publish_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.status.setText(f'Publish failed: {message}')

    def reject(self):
        if self.worker is None or not self.worker.isRunning():
            super().reject()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            event.ignore()
        else:
            super().closeEvent(event)

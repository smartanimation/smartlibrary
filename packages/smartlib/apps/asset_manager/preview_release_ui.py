"""Published-input USD Release workspace, independent of Maya Context."""
from pathlib import Path
import tempfile
from .texture_publish_ui import QtCore, QtGui, QtWidgets
from .current_preview_ui import dependency_versions
from .preview_publish import PreviewPublishService
from .preview_release import release_preview
from smartlib.core.metadata import read_json


class PreviewReleasePanel(QtWidgets.QWidget):
    published = QtCore.Signal(str)
    previewReady = QtCore.Signal(str)

    def __init__(self, config, identity=None, subset='proxy', parent=None):
        super().__init__(parent)
        self.service = PreviewPublishService(config)
        self.identity = None
        self.manifest = None
        self.workspaces = []
        self.open_preview = None
        self._loading = False
        outer = QtWidgets.QHBoxLayout(self)
        nav = QtWidgets.QVBoxLayout()
        nav.addWidget(QtWidgets.QLabel('Quality'))
        self.quality = QtWidgets.QListWidget()
        self.quality.setMaximumWidth(120)
        self.quality.addItems(['proxy', 'render  (Future)'])
        self.quality.item(1).setFlags(QtCore.Qt.NoItemFlags)
        self.quality.item(1).setForeground(QtGui.QColor('#777777'))
        self.quality.item(1).setToolTip('Future: high silhouette preview')
        self.quality.setCurrentRow(0)
        nav.addWidget(self.quality)
        outer.addLayout(nav)
        main = QtWidgets.QVBoxLayout()
        outer.addLayout(main, 1)
        header = QtWidgets.QHBoxLayout()
        header.addWidget(QtWidgets.QLabel('Release inputs'), 1)
        self.refresh_button = QtWidgets.QPushButton('Refresh')
        self.check_button = QtWidgets.QPushButton('Check in usdview')
        self.release = QtWidgets.QPushButton('Release USD')
        self.release.setStyleSheet('QPushButton { background: #286b9a; color: white; padding: 6px 12px; } QPushButton:disabled { background: #444; color: #888; }')
        for button in (self.refresh_button, self.check_button, self.release):
            header.addWidget(button)
        main.addLayout(header)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(30)
        main.addWidget(self.status)
        self.inputs = QtWidgets.QTableWidget(3, 5)
        self.inputs.setHorizontalHeaderLabels(['Product', 'Subset', 'Selected version', 'Latest', 'State'])
        self.inputs.verticalHeader().hide()
        self.inputs.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.inputs.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.inputs.setMinimumHeight(142)
        self.inputs.setMaximumHeight(162)
        self.subset, self.look_subset = QtWidgets.QComboBox(), QtWidgets.QComboBox()
        self.geometry, self.look = QtWidgets.QComboBox(), QtWidgets.QComboBox()
        self.rig_subset, self.rig = QtWidgets.QComboBox(), QtWidgets.QComboBox()
        for row, label, subsets, versions in [(0, 'Geometry', self.subset, self.geometry), (1, 'Look', self.look_subset, self.look), (2, 'Rig (optional)', self.rig_subset, self.rig)]:
            subsets.addItems([] if row == 2 else ['low', 'proxy'])
            self.inputs.setItem(row, 0, QtWidgets.QTableWidgetItem(label))
            self.inputs.setCellWidget(row, 1, subsets)
            self.inputs.setCellWidget(row, 2, versions)
            self.inputs.setItem(row, 3, QtWidgets.QTableWidgetItem('-'))
            self.inputs.setItem(row, 4, QtWidgets.QTableWidgetItem('-'))
            subsets.currentTextChanged.connect(self.refresh)
            versions.currentIndexChanged.connect(self.update_selection)
        main.addWidget(self.inputs)
        main.addWidget(QtWidgets.QLabel('Texture dependencies are pinned by Look.'))
        comment_row = QtWidgets.QHBoxLayout()
        comment_row.addWidget(QtWidgets.QLabel('Comment'))
        self.comment = QtWidgets.QLineEdit()
        self.comment.setPlaceholderText('Release comment...')
        comment_row.addWidget(self.comment, 1)
        main.addLayout(comment_row)
        summary_box = QtWidgets.QGroupBox('Release summary')
        summary_layout = QtWidgets.QVBoxLayout(summary_box)
        self.asset_label = QtWidgets.QLabel()
        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        summary_layout.addWidget(self.asset_label)
        summary_layout.addWidget(self.summary)
        main.addWidget(summary_box)
        main.addWidget(QtWidgets.QLabel('Release history'))
        self.history = QtWidgets.QTreeWidget()
        self.history.setHeaderLabels(['Release', 'Quality', 'Inputs', 'Status', 'Comment'])
        self.history.setRootIsDecorated(True)
        self.history.setAlternatingRowColors(True)
        self.history.setMinimumHeight(160)
        self.history.setColumnWidth(2, 240)
        main.addWidget(self.history, 1)
        self.paths = {}
        for key, label in [('entry', 'Asset entry'), ('release', 'Release')]:
            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel(label))
            edit = QtWidgets.QLineEdit()
            edit.setReadOnly(True)
            edit.setPlaceholderText('Select a Release from history')
            row.addWidget(edit, 1)
            copy = QtWidgets.QPushButton('Copy path')
            folder = QtWidgets.QPushButton('Open folder')
            copy.clicked.connect(lambda _=False, e=edit: QtWidgets.QApplication.clipboard().setText(e.text()))
            folder.clicked.connect(lambda _=False, e=edit: self.open_folder(e.text()))
            row.addWidget(copy)
            row.addWidget(folder)
            self.paths[key] = (edit, copy, folder)
            main.addLayout(row)
        self.refresh_button.clicked.connect(self.refresh)
        self.check_button.clicked.connect(self.check_preview)
        self.release.clicked.connect(self.execute)
        self.history.currentItemChanged.connect(self.show_history_paths)
        self.configure(identity)

    def configure(self, identity):
        if identity != self.identity:
            self.manifest = None
            self.comment.clear()
            self._loading = True
            self.geometry.clear()
            self.look.clear()
            self.rig.clear()
            self._loading = False
        self.identity = identity
        self.asset_label.setText(f'{identity.name} / {identity.variant}' if identity else 'Select an Asset')
        self.refresh()

    def refresh(self, *_):
        if self._loading:
            return
        self._loading = True
        try:
            previous_subset = self.rig_subset.currentText()
            rig_subsets = []
            if self.identity:
                root = self.service.paths.asset_publish_dir(self.identity, 'rig', '')
                for directory in sorted(root.iterdir()) if root.exists() else []:
                    if not directory.is_dir():
                        continue
                    rows, _ = dependency_versions(self.service, self.identity, 'rig', directory.name)
                    if any(read_json(p, {}).get('usd_skel', {}).get('schema') == 'smartpipeline.usd_skel.v2'
                           for _, p in rows):
                        rig_subsets.append(directory.name)
            self.rig_subset.clear()
            self.rig_subset.addItems(rig_subsets)
            if previous_subset in rig_subsets:
                self.rig_subset.setCurrentText(previous_subset)
            for row, kind, subsets, combo in [(0, 'model', self.subset, self.geometry), (1, 'look', self.look_subset, self.look), (2, 'rig', self.rig_subset, self.rig)]:
                previous = combo.currentData()
                rows, latest = dependency_versions(self.service, self.identity, kind, subsets.currentText()) if self.identity else ([], '')
                combo.clear()
                if kind == 'rig':
                    combo.addItem('None (static)', None)
                    rows = [(v, p) for v, p in rows if read_json(p, {}).get('usd_skel', {}).get('schema') == 'smartpipeline.usd_skel.v2']
                    latest = latest if latest in {v for v, _ in rows} else ''
                for version, receipt in rows:
                    combo.addItem(version, receipt)
                target = previous if previous in [p for _, p in rows] else next((p for v, p in rows if v == latest), None)
                if kind == 'rig' and not previous:
                    target = None
                if combo.findData(target) >= 0:
                    combo.setCurrentIndex(combo.findData(target))
                self.inputs.item(row, 3).setText(latest or '-')
        except Exception as exc:
            self.set_status(str(exc), False)
        finally:
            self._loading = False
        self.update_selection()
        self.refresh_history()

    def set_status(self, message, ready):
        self.status.setText(message)
        self.status.setStyleSheet('padding: 6px; border: 1px solid ' + ('#527b50; background: #293c2a;' if ready else '#746448; background: #40382b;'))

    def update_selection(self, *_):
        if self._loading:
            return
        ready = bool(self.identity and self.geometry.currentData() and self.look.currentData())
        matching = self.subset.currentText() == self.look_subset.currentText()
        for row, combo in enumerate((self.geometry, self.look)):
            self.inputs.item(row, 4).setText('SELECTED' if combo.currentData() else 'MISSING')
        self.inputs.item(2, 4).setText('SELECTED' if self.rig.currentData() else 'OMITTED')
        self.release.setEnabled(ready and matching)
        self.check_button.setEnabled(ready and matching)
        message = 'READY - Geometry and Look selected. Compatibility is checked on Check / Release.' if ready else 'Select published Geometry and Look.'
        if ready and not matching:
            message = 'Geometry and Look subsets must match.'
        if self.rig.currentData():
            message += ' Rig bind shape is also checked.'
        self.set_status(message, ready and matching)
        self.summary.setText(f'Release target: proxy    |    Shading: UsdPreviewSurface\nGeometry: {self.subset.currentText()} / {self.geometry.currentText() or "-"}    |    Look: {self.look_subset.currentText()} / {self.look.currentText() or "-"}')

        self.summary.setText(self.summary.text() + f'\nRig: {self.rig_subset.currentText()} / {self.rig.currentText()}')

    def refresh_history(self):
        selected = self.history.currentItem()
        previous = selected.data(0, QtCore.Qt.UserRole) if selected else None
        self.history.clear()
        self.show_history_paths()
        if not self.identity:
            return
        root = self.service.paths.asset_publish_dir(self.identity, 'asset', 'proxy')
        latest = read_json(self.service.paths.artifact_file(root, 'latest.json'), {}) or {}
        chosen = None
        for directory in sorted(root.glob('v*'), key=lambda p: int(p.name[1:]) if p.name[1:].isdigit() else -1, reverse=True):
            if self.service.paths.artifact_file(directory, '_building').exists():
                continue
            record = read_json(self.service.paths.artifact_file(directory, 'publish.json'), {}) or {}
            if record.get('schema') != 'smartpipeline.preview_release.v1' or record.get('status') != 'complete':
                continue
            kinds = ['Geometry', 'Look'] + (['Rig'] if record.get('rig') else [])
            records = [read_json(record[key.lower()]['path'], {}) or {} for key in kinds]
            inputs = ' + '.join(f'{kind} {data.get("version", "-")}' for kind, data in zip(kinds, records))
            item = QtWidgets.QTreeWidgetItem([record['version'], 'proxy', inputs, 'LATEST' if record['version'] == latest.get('version') else 'RELEASED', record.get('comment', '')])
            item.setData(0, QtCore.Qt.UserRole, record)
            self.history.addTopLevelItem(item)
            for kind, data in zip(kinds, records):
                child = QtWidgets.QTreeWidgetItem([kind, data.get('subset', ''), data.get('version', ''), '', ''])
                child.setData(0, QtCore.Qt.UserRole, record)
                item.addChild(child)
            if previous and record['version'] == previous.get('version'):
                chosen = item
        if chosen is None and self.history.topLevelItemCount():
            chosen = self.history.topLevelItem(0)
        if chosen:
            chosen.setExpanded(True)
            self.history.setCurrentItem(chosen)

    def show_history_paths(self, *_):
        item = self.history.currentItem()
        record = item.data(0, QtCore.Qt.UserRole) if item else {}
        record = record or {}
        for key, path in [('entry', record.get('usd_entrypoint', '')), ('release', record.get('absolute_files', {}).get('usd', ''))]:
            edit, copy, folder = self.paths[key]
            edit.setText(path)
            edit.setCursorPosition(0)
            edit.setToolTip(path)
            copy.setEnabled(bool(path))
            folder.setEnabled(bool(path) and Path(path).parent.is_dir())

    def open_folder(self, path):
        if path and Path(path).parent.is_dir():
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Path(path).parent)))

    def check_preview(self):
        self.check_button.setEnabled(False)
        try:
            work = tempfile.TemporaryDirectory(prefix='smart-release-check-')
            self.workspaces.append(work)
            output = self.service.paths.artifact_file(Path(work.name), 'preview.usda')
            path = release_preview(self.service, self.identity, self.geometry.currentData(), self.look.currentData(), preview_output=output, rig_manifest=self.rig.currentData())
            if self.open_preview:
                self.open_preview(str(path))
            else:
                self.previewReady.emit(str(path))
            self.set_status('CHECK PASSED - Selected inputs validated for usdview.', True)
            for row in range(3 if self.rig.currentData() else 2):
                self.inputs.item(row, 4).setText('VALIDATED')
        except Exception as exc:
            self.set_status(str(exc), False)
        finally:
            self.check_button.setEnabled(bool(self.identity and self.geometry.currentData() and self.look.currentData() and self.subset.currentText() == self.look_subset.currentText()))

    def execute(self):
        self.release.setEnabled(False)
        try:
            self.manifest = release_preview(self.service, self.identity, self.geometry.currentData(), self.look.currentData(), subset='proxy', comment=self.comment.text(), rig_manifest=self.rig.currentData())
            self.refresh_history()
            for index in range(self.history.topLevelItemCount()):
                item = self.history.topLevelItem(index)
                if item.text(0) == self.manifest.parent.name:
                    self.history.setCurrentItem(item)
                    item.setExpanded(True)
            self.set_status(f'Released: {self.manifest}', True)
            self.published.emit(str(self.manifest))
        except Exception as exc:
            self.set_status(str(exc), False)
        finally:
            self.release.setEnabled(bool(self.identity and self.geometry.currentData() and self.look.currentData() and self.subset.currentText() == self.look_subset.currentText()))


class PreviewReleaseDialog(QtWidgets.QDialog):
    """Compatibility wrapper for older callers."""
    def __init__(self, config, identity, subset='proxy', parent=None):
        super().__init__(parent)
        self.setWindowTitle('Release USD')
        layout = QtWidgets.QVBoxLayout(self)
        self.panel = PreviewReleasePanel(config, identity, subset, self)
        layout.addWidget(self.panel)
        for name in ('release', 'subset', 'geometry', 'look', 'status', 'execute'):
            setattr(self, name, getattr(self.panel, name))
        self.panel.published.connect(self.accept)

    @property
    def manifest(self):
        return self.panel.manifest

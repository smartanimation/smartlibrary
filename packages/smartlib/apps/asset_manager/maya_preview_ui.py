"""Launch saved-scene Preview publishes without replacing the user's Maya scene."""
import json
import os
from pathlib import Path
import tempfile

from .texture_publish_ui import QtCore, QtWidgets
from .preview_publish import PreviewPublishService
from smartlib.core.maya_runtime import resolve_mayapy, process_environment
from smartlib.core.metadata import read_json


class MayaPreviewPublishDialog(QtWidgets.QDialog):
    def __init__(self, config, identity, kind, source='', subset='low', parent=None):
        super().__init__(parent)
        self.config, self.identity, self.kind = config, identity, kind
        self.service = PreviewPublishService(config)
        self.process = None
        self.manifest = None
        self.work = tempfile.TemporaryDirectory(prefix='smart-preview-')
        self.setWindowTitle('Publish geo.usd' if kind == 'geometry' else 'Publish Preview look.usd')
        self.resize(820, 480)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(f'{identity.name} / {identity.variant} — cache_geo_set'))
        layout.addWidget(QtWidgets.QLabel('Uses the saved scene on disk. Save Maya edits before publishing.'))
        self.controls = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(self.controls)
        self.source = QtWidgets.QLineEdit(str(source or ''))
        browse = QtWidgets.QPushButton('Browse…')
        browse.clicked.connect(self.browse)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.source)
        row.addWidget(browse)
        form.addRow('Maya scene', row)
        self.subset = QtWidgets.QComboBox()
        self.subset.addItems(list(dict.fromkeys([subset or 'low', 'low', 'proxy', 'high'])))
        form.addRow('Subset', self.subset)
        self.geometry = QtWidgets.QComboBox()
        self.textures = QtWidgets.QComboBox()
        if kind == 'look':
            form.addRow('Geometry version', self.geometry)
            form.addRow('Texture composition', self.textures)
        layout.addWidget(self.controls)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)
        self.publish = QtWidgets.QPushButton('Publish')
        self.publish.clicked.connect(self.start)
        layout.addWidget(self.publish)
        self.subset.currentTextChanged.connect(self.refresh)
        self.refresh()

    def browse(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Maya scene', self.source.text(), 'Maya (*.ma *.mb)')
        if path:
            self.source.setText(path)

    def refresh(self):
        for kind, combo in [('model', self.geometry), ('texture', self.textures)]:
            combo.clear()
            root = self.service.paths.asset_publish_dir(self.identity, kind, self.subset.currentText())
            if root.exists():
                for directory in sorted(root.iterdir(), reverse=True):
                    if not directory.is_dir():
                        continue
                    receipt = self.service.paths.artifact_file(directory, 'publish.json')
                    record = read_json(receipt, {}) or {}
                    if record.get('status') == 'published' and (kind == 'texture' or 'usd' in record.get('artifacts', {})):
                        combo.addItem(directory.name, str(receipt))
            if kind == 'texture':
                combo.addItem('None (solid colors only)', None)

    def start(self):
        try:
            if not Path(self.source.text()).is_file():
                raise ValueError('Choose a saved Maya scene')
            if self.kind == 'look' and not self.geometry.currentData():
                raise ValueError('Publish Geometry before Look')
            work = Path(self.work.name)
            job = dict(config=str(self.config.config_dir), identity=dict(category=self.identity.category,
                group=self.identity.group, name=self.identity.name, variant=self.identity.variant),
                kind=self.kind, scene=self.source.text(), subset=self.subset.currentText(), staging=str(work),
                geometry_manifest=self.geometry.currentData(), texture_manifest=self.textures.currentData(),
                result=str(work / 'result.json'))
            request = work / 'job.json'
            request.write_text(json.dumps(job), encoding='utf-8')
            mayapy = resolve_mayapy(self.config)
            values, paths = process_environment(self.config)
            env = QtCore.QProcessEnvironment.systemEnvironment()
            env.remove('PYTHONHOME')
            env.remove('PYTHONPATH')
            for key, value in values.items():
                env.insert(key, value)
            for key, entries in paths.items():
                env.insert(key, os.pathsep.join([*entries, env.value(key)]).rstrip(os.pathsep))
            self.process = QtCore.QProcess(self)
            self.process.setProcessEnvironment(env)
            self.process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
            self.process.readyReadStandardOutput.connect(self.read_log)
            self.process.finished.connect(self.finished)
            self.process.errorOccurred.connect(self.error)
            self.controls.setEnabled(False)
            self.publish.setEnabled(False)
            script = Path(__file__).resolve().parents[4] / 'scripts' / 'publish_maya_preview.py'
            self.process.start(str(mayapy), [str(script), str(request)])
        except Exception as exc:
            self.log.appendPlainText(str(exc))

    def read_log(self):
        self.log.appendPlainText(bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace'))

    def error(self, error):
        if error == QtCore.QProcess.FailedToStart:
            self.log.appendPlainText(self.process.errorString())
            self.controls.setEnabled(True)
            self.publish.setEnabled(True)

    def finished(self, code, *_):
        self.read_log()
        self.controls.setEnabled(True)
        self.publish.setEnabled(True)
        result = Path(self.work.name) / 'result.json'
        if code == 0 and result.exists():
            self.manifest = json.loads(result.read_text(encoding='utf-8'))['manifest']
            self.accept()
        else:
            self.log.appendPlainText(f'Publish failed (exit {code}). See log above.')

    def reject(self):
        if self.process is None or self.process.state() == QtCore.QProcess.NotRunning:
            super().reject()

    def closeEvent(self, event):
        if self.process is not None and self.process.state() != QtCore.QProcess.NotRunning:
            event.ignore()
        else:
            super().closeEvent(event)

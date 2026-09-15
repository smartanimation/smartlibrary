"""Explicit USD source selection; generation runs in isolated mayapy."""
import json
import os
from pathlib import Path

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets

from smartlib.apps.shot_manager.usd_handoff import UsdHandoffService
from smartlib.core.metadata import read_json, write_json


class UsdPublishDialog(QtWidgets.QDialog):
    def __init__(self, shots, identity, parent=None):
        super().__init__(parent)
        self.service = UsdHandoffService(shots)
        self.identity = identity
        self.process = None
        self.setWindowTitle('USD Publish — fixed Data inputs (Submit not required)')
        self.resize(1050, 600)
        layout = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel('Select only the targets needed downstream. Animation: ATOM Data + matching Rig; '
            'Sculpt is optional. Assets/Camera: fixed USD Publish. Layout: published Set Dress JSON. '
            'No current scene is used. Published does not mean reviewed/approved.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['Type', 'Target / Instance ID', 'Data / USD Version', 'Rig Version (Animation)', 'Sculpt Data (optional)'])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        actions = QtWidgets.QHBoxLayout()
        for label, callback in [('Add Target', self.add_row), ('Remove Target', self.remove_row),
                                ('Select File…', self.browse), ('Data Versions…', self.data_versions),
                                ('Create Sculpt Data…', self.sculpt_data)]:
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)
        layout.addWidget(QtWidgets.QLabel('Set Dress mapping: recorded node ID → /Shot/Assets/<instance>/<prim> (JSON object)'))
        self.mapping = QtWidgets.QPlainTextEdit('{}')
        self.mapping.setMaximumHeight(75)
        layout.addWidget(self.mapping)
        self.status = QtWidgets.QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setMaximumHeight(110)
        layout.addWidget(self.status)
        bottom = QtWidgets.QHBoxLayout()
        self.publish_btn = QtWidgets.QPushButton('Publish Selected + Compose Shot')
        self.publish_btn.clicked.connect(self.publish)
        bottom.addWidget(self.publish_btn)
        self.compose_btn = QtWidgets.QPushButton('Compose Existing Products…')
        self.compose_btn.clicked.connect(self.compose)
        bottom.addWidget(self.compose_btn)
        layout.addLayout(bottom)
        self.add_row()

    def add_row(self):
        row = self.table.rowCount()
        self.table.insertRow(row)
        combo = QtWidgets.QComboBox()
        combo.addItems(['animation', 'camera', 'assets', 'layout'])
        self.table.setCellWidget(row, 0, combo)
        for col in range(1, 5):
            self.table.setItem(row, col, QtWidgets.QTableWidgetItem(''))

    def remove_row(self):
        self.table.removeRow(self.table.currentRow())

    def browse(self):
        row, col = self.table.currentRow(), self.table.currentColumn()
        if row < 0 or col < 2:
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Select fixed Data/Publish Version', '',
            'Data / Publish (*.json *.usd *.usda *.usdc *.ma *.mb)')
        if path:
            self.table.item(row, col).setText(path)

    def data_versions(self):
        row = self.table.currentRow()
        if row < 0:
            return
        kind = self.table.cellWidget(row, 0).currentText()
        shots = self.service.shots
        if kind == 'animation':
            target = self.table.item(row, 1).text().strip()
            versions = shots.list_animation_curve_versions(self.identity, target=target)
        elif kind == 'layout':
            versions = shots.list_set_dress_publish_versions(self.identity)
        else:
            self.status.setPlainText('Use Select File to choose the fixed Proxy USD / Primary Camera USD.')
            return
        labels = [f'{v.name} | {v.version} | {v.path}' for v in versions]
        if not labels:
            self.status.setPlainText('No fixed Data versions found for this target.')
            return
        label, ok = QtWidgets.QInputDialog.getItem(self, 'Data Version', 'Select version', labels, 0, False)
        if ok:
            path = versions[labels.index(label)].path
            self.table.item(row, 2).setText(str(path))
            if kind == 'animation':
                deps = read_json(path, {}).get('rig_dependencies', [])
                if len(deps) == 1:
                    self.table.item(row, 3).setText(deps[0]['path'])

    def publish(self):
        try:
            rows = []
            mapping = json.loads(self.mapping.toPlainText())
            if not isinstance(mapping, dict):
                raise ValueError('Set Dress mapping must be a JSON object')
            for i in range(self.table.rowCount()):
                text = lambda col: self.table.item(i, col).text().strip()
                kind = self.table.cellWidget(i, 0).currentText()
                row = dict(kind=kind, target=text(1), source=text(2))
                if kind == 'animation':
                    row.update(rig=text(3), sculpt=text(4) or None)
                elif kind == 'layout':
                    row['node_map'] = mapping
                rows.append(row)
            plan = self.service.plan(self.identity, rows)
            self.start_worker(plan)
        except Exception as exc:
            self.status.setPlainText(str(exc))

    def sculpt_data(self):
        row = self.table.currentRow()
        if row < 0 or self.table.cellWidget(row, 0).currentText() != 'animation':
            self.status.setPlainText('Select an Animation row with a fixed Curve Data version first.')
            return
        files = []
        for title, file_filter in (
            ('Select unsculpted Animation USD product manifest', 'Product manifest (*.json)'),
            ('Select sculpted final-deform USD (matching hierarchy)', 'USD (*.usd *.usda *.usdc)')):
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, title, '', file_filter)
            if not path:
                return
            files.append(path)
        try:
            path = self.service.publish_sculpt_data(self.identity, self.table.item(row, 1).text().strip(),
                *files, self.table.item(row, 2).text().strip())
            self.table.item(row, 4).setText(str(path))
            self.status.setPlainText('Created optional Shot Sculpt Data: ' + str(path))
        except Exception as exc:
            self.status.setPlainText(str(exc))

    def start_worker(self, plan):
        from smartlib.apps.review_build_manager.service import ReviewBuildManagerService
        runtime = ReviewBuildManagerService(self.service.config)
        mayapy = runtime.resolve_mayapy()
        _, directory = self.service._reserve(self.service.paths.usd_handoff_build_dir(*self.service._identity(self.identity)))
        selection = self.service._file(directory, 'plan.json')
        self.result = self.service._file(directory, 'result.json')
        write_json(selection, plan)
        process = QtCore.QProcess(self)
        env = QtCore.QProcessEnvironment.systemEnvironment()
        env_vars, path_vars = runtime.maya_process_environment()
        for key, value in env_vars.items():
            env.insert(key, os.path.expandvars(value))
        for key, values in path_vars.items():
            env.insert(key, os.pathsep.join([os.path.expandvars(v) for v in values if v] + [env.value(key)]))
        env.insert('PROJECT_CONFIG_DIR', str(self.service.config.config_dir))
        env.insert('PYTHONPATH', str(Path(__file__).resolve().parents[3]) + os.pathsep + env.value('PYTHONPATH'))
        process.setProcessEnvironment(env)
        process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        process.readyReadStandardOutput.connect(lambda: self.status.appendPlainText(bytes(process.readAllStandardOutput()).decode('utf-8', 'replace')))
        process.finished.connect(self.finished_worker)
        def failed(error):
            self.status.appendPlainText(process.errorString())
            if error == QtCore.QProcess.FailedToStart:
                self.publish_btn.setEnabled(True)
                self.compose_btn.setEnabled(True)
        process.errorOccurred.connect(failed)
        self.process = process
        self.publish_btn.setEnabled(False)
        self.compose_btn.setEnabled(False)
        self.status.setPlainText('Building from fixed Data in an isolated Maya process…')
        process.start(str(mayapy), ['-m', 'smartlib.dcc.maya.usd_handoff', '--config',
            str(self.service.config.config_dir), '--plan', str(selection), '--result', str(self.result)])

    def finished_worker(self, code, _status):
        self.publish_btn.setEnabled(True)
        self.compose_btn.setEnabled(True)
        result = read_json(self.result, {})
        if code == 0 and result.get('ok'):
            self.accept_result(result['manifest'])
        else:
            self.status.appendPlainText(result.get('error', 'USD Publish failed; inspect worker output above.'))

    def compose(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, 'Select product manifests (no re-export)', '', 'Product manifests (*.json)')
        if paths:
            try:
                self.accept_result(self.service.compose_products(self.identity, paths))
            except Exception as exc:
                self.status.setPlainText(str(exc))

    def accept_result(self, manifest):
        data = self.service.load_handoff(manifest)
        self.entrypoint = data['entrypoint']['path']
        self.status.appendPlainText('Published (not reviewed): ' + str(manifest))

    def reject(self):
        if self.process and self.process.state() != QtCore.QProcess.NotRunning:
            self.status.appendPlainText('USD generation is running; wait for completion before closing.')
            return
        super().reject()

    def closeEvent(self, event):
        if self.process and self.process.state() != QtCore.QProcess.NotRunning:
            event.ignore()
            self.status.appendPlainText('USD generation is running; wait for completion before closing.')
            return
        super().closeEvent(event)

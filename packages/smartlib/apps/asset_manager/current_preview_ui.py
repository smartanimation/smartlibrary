"""Inline current-Maya-scene publishing and disposable pre-publish USD checks."""
from pathlib import Path
import re
import tempfile

from .texture_publish_ui import QtCore, QtWidgets
from .preview_publish import PreviewPublishService
from smartlib.core.metadata import read_json


def dependency_versions(service, identity, kind, subset):
    root = service.paths.asset_publish_dir(identity, kind, subset)
    pointer = read_json(service.paths.artifact_file(root, 'latest.json'), {}) or {}
    rows = []
    if root.exists():
        for directory in root.iterdir():
            if not directory.is_dir() or not re.fullmatch(r'v\d+', directory.name):
                continue
            receipt = service.paths.artifact_file(directory, 'publish.json')
            record = read_json(receipt, {}) or {}
            if record.get('status') == 'published' and (kind == 'texture' or 'usd' in record.get('artifacts', {})
                    or (kind == 'rig' and record.get('usd_skel', {}).get('schema') == 'smartpipeline.usd_skel.v2')):
                rows.append((directory.name, str(receipt)))
    rows.sort(key=lambda row: int(row[0][1:]), reverse=True)
    latest = str(pointer.get('version', ''))
    # Never silently claim a directory is Latest if the publication pointer is absent/broken.
    return rows, latest if latest in {row[0] for row in rows} else ''


class CurrentScenePublishPanel(QtWidgets.QWidget):
    published = QtCore.Signal(str)
    previewReady = QtCore.Signal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.service = PreviewPublishService(config)
        self.identity = None
        self.kind = 'geometry'
        self.workspaces = []  # Keep checks alive while usdview may be reading them.
        layout = QtWidgets.QVBoxLayout(self)
        self.scene = QtWidgets.QLabel()
        self.scene.setWordWrap(True)
        layout.addWidget(self.scene)
        layout.addWidget(QtWidgets.QLabel('Current Maya scene, including unsaved edits'))
        form = QtWidgets.QFormLayout()
        self.form = form
        self.subset = QtWidgets.QComboBox()
        self.subset.addItems(['low', 'proxy', 'high'])
        form.addRow('Subset', self.subset)
        self.geometry = QtWidgets.QComboBox()
        self.textures = QtWidgets.QComboBox()
        form.addRow('Geometry', self.geometry)
        form.addRow('Texture', self.textures)
        layout.addLayout(form)
        self.latest = QtWidgets.QLabel()
        self.latest.setWordWrap(True)
        layout.addWidget(self.latest)
        refresh = QtWidgets.QPushButton('Refresh dependencies')
        refresh.clicked.connect(self.refresh)
        layout.addWidget(refresh)
        self.publish_button = QtWidgets.QPushButton('Publish current scene')
        self.publish_button.clicked.connect(lambda: self.execute(False))
        layout.addWidget(self.publish_button)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch(1)
        self.subset.currentTextChanged.connect(self.refresh)

    def configure(self, identity, kind, subset=None):
        changed = identity != self.identity or kind != self.kind
        self.identity, self.kind = identity, kind
        if kind == 'rig':
            subset = 'anim'
        if subset and changed:
            self.subset.blockSignals(True)
            if self.subset.findText(subset) < 0:
                self.subset.addItem(subset)
            self.subset.setCurrentText(subset)
            self.subset.blockSignals(False)
        self.refresh(reset=changed)
        self.publish_button.setText('Publish static Rig USD' if kind == 'rig' else 'Publish current scene')
        if kind == 'rig':
            self.latest.setText('geo.usd + rig.usd → usdSkel.usd\nJoint/Skin basis. Shot Publish checks every frame; unsupported deformation uses a deform cache.')

    def refresh(self, *_args, reset=False):
        try:
            import maya.cmds as cmds
            self.scene.setText(cmds.file(query=True, sceneName=True) or 'Untitled scene')
        except ImportError:
            self.scene.setText('Open Asset Manager inside Maya to use the current scene.')
        if not self.identity:
            return
        latest_labels = []
        for kind, combo in [('model', self.geometry), ('texture', self.textures)]:
            combo.setVisible(self.kind == 'look')
            self.form.labelForField(combo).setVisible(self.kind == 'look')
            previous = combo.currentData()
            had_selection = combo.count() > 0
            rows, latest = dependency_versions(self.service, self.identity, kind, self.subset.currentText())
            combo.clear()
            for version, receipt in rows:
                combo.addItem(version + (' (Latest)' if version == latest else ''), receipt)
            if kind == 'texture':
                combo.addItem('None — solid colors only', None)
            target = previous if had_selection and not reset else next((p for v, p in rows if v == latest), None)
            index = combo.findData(target)
            if index >= 0:
                combo.setCurrentIndex(index)
            combo.setEnabled(self.kind == 'look')
            latest_labels.append(f'{"Geometry" if kind == "model" else "Texture"} Latest: {latest or "unavailable"}')
        self.latest.setText('\n'.join(latest_labels))
        if self.kind == 'rig':
            self.latest.setText('geo.usd + rig.usd → usdSkel.usd\nJoint/Skin basis. Shot Publish verifies every frame and uses a deform cache when needed.')

    def execute(self, preview=False):
        self.setEnabled(False)
        try:
            if not self.identity:
                raise ValueError('Select an Asset')
            import maya.cmds as cmds
            from smartlib.dcc.maya.current_preview import publish_scene as run
            active = cmds.file(query=True, sceneName=True)
            if not active:
                raise ValueError('Name and save the scene once before publishing')
            self.scene.setText(active)
            if self.kind == 'look' and not self.geometry.currentData():
                raise ValueError('Choose a published Geometry version')
            work = tempfile.TemporaryDirectory(prefix='smart-current-preview-')
            self.workspaces.append(work)
            job = dict(config=str(self.config.config_dir), identity=dict(category=self.identity.category,
                group=self.identity.group, name=self.identity.name, variant=self.identity.variant),
                scene=active, kind=self.kind, subset=self.subset.currentText(), staging=work.name,
                geometry_manifest=self.geometry.currentData(), texture_manifest=self.textures.currentData())
            path = str(run(job, current_scene=True, preview=preview))
            self.status.setText(('Temporary USD check: ' if preview else 'Published: ') + path)
            if preview:
                self.previewReady.emit(path)
            else:
                self.published.emit(path)
                self.refresh()
        except Exception as exc:
            message = f'{"USD check" if preview else "Publish"} failed: {exc}'
            self.status.setText(message)
            self.status.setToolTip(message)
            print(message)
        finally:
            self.setEnabled(True)

"""Qt standalone UI using the USD runtime's own Qt bindings and Hydra view."""
import argparse
import os
import sys
from pathlib import Path

from pxr import Usd, UsdGeom
from pxr.Usdviewq.qt import QtCore, QtWidgets, QtGui
from pxr.Usdviewq.stageView import StageView
from pxr.Usdviewq.common import CameraMaskModes
from .viewport import ViewportHUD, camera_info, camera_name
from smartlib.apps.shot_manager import ShotManagerService, ShotIdentity
from smartlib.core.config_loader import ProjectConfig
from .service import CompositionSession
from .browser import composition_versions
from .references import reference_text
from smartlib.core.metadata import read_json


class Job(QtCore.QThread):
    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation
        self.value = None
        self.error = None

    def run(self):
        try:
            self.value = self.operation()
        except Exception as exc:
            self.error = str(exc)


class CompositionWindow(QtWidgets.QMainWindow):
    def __init__(self, config='', source=''):
        super().__init__()
        self.setWindowTitle('Smart Composition')
        self.resize(1380, 820)
        self.export_process = None
        self.last_export = None
        self.session = None
        self.job = None
        self.preview_layers = None
        self.saved_selection = ()
        self.preview_selection = None
        self.rows = []
        self.section_choices = {}
        self.section_rows = {}
        container = QtWidgets.QWidget()
        self.setCentralWidget(container)
        outer = QtWidgets.QVBoxLayout(container)
        self.config_path = str(Path(config).resolve()) if config else ''
        self.shot_service = None
        self.shot_ids = []
        header = QtWidgets.QHBoxLayout()
        header.addWidget(QtWidgets.QLabel('Project'))
        self.project_label = QtWidgets.QLabel('Loading Launcher project…')
        self.project_label.setToolTip(self.config_path)
        header.addWidget(self.project_label, 1)
        self.reload_btn = QtWidgets.QPushButton('Refresh Shots')
        self.reload_btn.clicked.connect(lambda: self.initialize_browser())
        header.addWidget(self.reload_btn)
        outer.addLayout(header)
        source_row = QtWidgets.QHBoxLayout()
        self.episode_combo = QtWidgets.QComboBox()
        self.sequence_combo = QtWidgets.QComboBox()
        self.shot_combo = QtWidgets.QComboBox()
        self.composition_combo = QtWidgets.QComboBox()
        for label, combo in [('Episode', self.episode_combo), ('Sequence', self.sequence_combo),
                             ('Shot', self.shot_combo), ('Composition', self.composition_combo)]:
            source_row.addWidget(QtWidgets.QLabel(label))
            combo.setMinimumWidth(100)
            source_row.addWidget(combo, 1)
        self.episode_combo.currentIndexChanged.connect(self.episode_changed)
        self.sequence_combo.currentIndexChanged.connect(self.sequence_changed)
        self.shot_combo.currentIndexChanged.connect(self.shot_changed)
        self.composition_combo.currentIndexChanged.connect(self.sync_browser_controls)
        self.open_btn = QtWidgets.QPushButton('Open')
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self.open_input)
        source_row.addWidget(self.open_btn)
        outer.addLayout(source_row)
        self.source_label = QtWidgets.QLabel('Open a Shot Manager USD composition to begin.')
        self.source_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        outer.addWidget(self.source_label)
        split = QtWidgets.QSplitter()
        outer.addWidget(split, 1)
        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.addWidget(QtWidgets.QLabel('Layers / Published product versions'))
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(['Layer / Target', 'Version', 'Latest'])
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.tree.setColumnWidth(1, 95)
        self.tree.setColumnWidth(2, 65)
        self.tree.itemChanged.connect(self.schedule_preview)
        left_layout.addWidget(self.tree)
        self.tree.currentItemChanged.connect(self.show_reference_details)
        left_layout.addWidget(QtWidgets.QLabel('Selected reference — paths can be copied'))
        self.reference_details = QtWidgets.QPlainTextEdit()
        self.reference_details.setReadOnly(True)
        self.reference_details.setMaximumHeight(170)
        self.reference_details.setPlaceholderText('Select a section or target to inspect its USD and source files.')
        left_layout.addWidget(self.reference_details)
        tools = QtWidgets.QHBoxLayout()
        self.refresh_btn = QtWidgets.QPushButton('Refresh Versions')
        self.refresh_btn.clicked.connect(self.refresh)
        self.reset_btn = QtWidgets.QPushButton('Reset')
        self.reset_btn.clicked.connect(self.reset)
        tools.addWidget(self.refresh_btn)
        tools.addWidget(self.reset_btn)
        left_layout.addLayout(tools)
        split.addWidget(left)
        right = QtWidgets.QWidget()
        viewport_layout = QtWidgets.QVBoxLayout(right)
        self.model = StageView.DefaultDataModel()
        self.model.viewSettings.showHUD = False
        self.model.viewSettings.showBBoxes = False
        self.view = StageView(dataModel=self.model)
        viewport_layout.addWidget(self.view, 1)
        self.hud = ViewportHUD(self.view)
        display_controls = QtWidgets.QHBoxLayout()
        self.mask_toggle = QtWidgets.QCheckBox('Camera Mask')
        self.mask_toggle.setChecked(True)
        self.mask_toggle.setEnabled(False)
        self.mask_toggle.setToolTip('Dim the area outside the selected camera gate. Free camera has no mask.')
        self.mask_toggle.toggled.connect(self.update_overlays)
        display_controls.addWidget(self.mask_toggle)
        self.info_toggle = QtWidgets.QCheckBox('Shot / Camera Info')
        self.info_toggle.setChecked(True)
        self.info_toggle.toggled.connect(self.update_overlays)
        display_controls.addWidget(self.info_toggle)
        display_controls.addStretch()
        viewport_layout.addLayout(display_controls)
        timeline = QtWidgets.QHBoxLayout()
        self.play = QtWidgets.QPushButton('Play')
        self.play.setCheckable(True)
        self.play.toggled.connect(self.toggle_play)
        timeline.addWidget(self.play)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.valueChanged.connect(self.set_frame)
        timeline.addWidget(self.slider, 1)
        self.frame = QtWidgets.QSpinBox()
        self.frame.setMinimumWidth(90)
        self.frame.valueChanged.connect(self.slider.setValue)
        timeline.addWidget(self.frame)
        self.camera = QtWidgets.QComboBox()
        self.camera.addItem('Free camera', '')
        self.camera.currentIndexChanged.connect(self.set_camera)
        self.view.signalSwitchedToFreeCam.connect(lambda: self.camera.setCurrentIndex(0))
        timeline.addWidget(self.camera)
        fit = QtWidgets.QPushButton('Frame All')
        fit.clicked.connect(lambda: self.view.updateView(resetCam=True, forceComputeBBox=True))
        timeline.addWidget(fit)
        viewport_layout.addLayout(timeline)
        split.addWidget(right)
        split.setSizes([430, 930])
        bottom = QtWidgets.QHBoxLayout()
        self.status = QtWidgets.QPlainTextEdit('Ready')
        self.status.setReadOnly(True)
        self.status.setFixedHeight(90)
        self.status.setMinimumWidth(0)
        self.status.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.status.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.status.setAccessibleName('Status and error messages')
        bottom.addWidget(self.status, 1)
        self.save_btn = QtWidgets.QPushButton('Save New Composition')
        self.save_btn.clicked.connect(self.save)
        self.save_btn.setEnabled(False)
        bottom.addWidget(self.save_btn)
        self.movie_btn = QtWidgets.QPushButton('Create Review Movie + PDF')
        self.movie_btn.clicked.connect(self.export_movie)
        bottom.addWidget(self.movie_btn)
        self.output_btn = QtWidgets.QPushButton('Open Output')
        self.output_btn.setEnabled(False)
        self.output_btn.clicked.connect(self.open_export_output)
        bottom.addWidget(self.output_btn)
        outer.addLayout(bottom)
        self.debounce = QtCore.QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(180)
        self.debounce.timeout.connect(self.preview)
        self.playback = QtCore.QTimer(self)
        self.playback.timeout.connect(self.advance)
        self.setStyleSheet('QWidget {background:#292d32;color:#e3e6ea;} '
            'QTreeWidget,QLineEdit,QComboBox,QSpinBox {background:#202328;} '
            'QPushButton {background:#3a424b;padding:7px;border-radius:3px;} '
            'QPushButton:disabled {color:#77818c;} QHeaderView::section {background:#343b43;padding:6px;}')
        QtCore.QTimer.singleShot(0, lambda: self.initialize_browser(source))

    @staticmethod
    def fill_combo(combo, values, preferred=''):
        blocked = combo.blockSignals(True)
        combo.clear()
        combo.addItems(values)
        if preferred in values:
            combo.setCurrentText(preferred)
        combo.blockSignals(blocked)

    def initialize_browser(self, source=''):
        preferred = (self.episode_combo.currentText() or os.environ.get('SMART_EPISODE', ''),
                     self.sequence_combo.currentText() or os.environ.get('SMART_SEQUENCE', ''),
                     self.shot_combo.currentText() or os.environ.get('SMART_SHOT', ''))
        def load():
            if not self.config_path:
                raise ValueError('Select a project in Launcher and launch Smart Composition again.')
            config = ProjectConfig(self.config_path)
            if not config.project_root:
                raise ValueError('The Launcher project has no project_root setting.')
            shots = ShotManagerService(config)
            return shots, shots.list_shots()
        def loaded(result):
            self.shot_service, self.shot_ids = result
            self.project_label.setText(self.shot_service.project_config.project_name)
            self.fill_combo(self.episode_combo, sorted({i.episode for i in self.shot_ids}), preferred[0])
            self.episode_changed(preferred_sequence=preferred[1], preferred_shot=preferred[2])
            if source:
                self.open_source(source)
            elif not self.shot_ids:
                self.status.setPlainText('No shots registered in this project.')
            else:
                self.status.setPlainText('Select Episode / Sequence / Shot / Composition, then Open.')
        self.run_job(load, loaded, 'Loading project shots…')

    def episode_changed(self, *args, preferred_sequence='', preferred_shot=''):
        episode = self.episode_combo.currentText()
        self.fill_combo(self.sequence_combo, sorted({i.sequence for i in self.shot_ids if i.episode == episode}), preferred_sequence)
        self.sequence_changed(preferred_shot=preferred_shot)

    def sequence_changed(self, *args, preferred_shot=''):
        episode, sequence = self.episode_combo.currentText(), self.sequence_combo.currentText()
        self.fill_combo(self.shot_combo, sorted({i.shot for i in self.shot_ids
            if i.episode == episode and i.sequence == sequence}), preferred_shot)
        self.shot_changed()

    def shot_changed(self, *args, preferred_path=''):
        combo = self.composition_combo
        blocked = combo.blockSignals(True)
        combo.clear()
        identity = ShotIdentity(self.episode_combo.currentText(), self.sequence_combo.currentText(), self.shot_combo.currentText())
        try:
            if self.shot_service and all((identity.episode, identity.sequence, identity.shot)):
                for version, path in composition_versions(self.shot_service, identity):
                    combo.addItem(version, path)
                    combo.setItemData(combo.count() - 1, path, QtCore.Qt.ToolTipRole)
                if preferred_path:
                    index = next((i for i in range(combo.count())
                        if Path(combo.itemData(i)).resolve() == Path(preferred_path).resolve()), -1)
                    if index >= 0:
                        combo.setCurrentIndex(index)
            if not combo.count():
                combo.addItem('No published composition', None)
        except Exception as exc:
            self.status.setPlainText(str(exc))
        finally:
            combo.blockSignals(blocked)
        self.sync_browser_controls()

    def sync_browser_controls(self, *args):
        self.open_btn.setEnabled(self.job is None and bool(self.composition_combo.currentData()))

    def sync_loaded_shot(self):
        identity = self.session.identity
        if identity not in self.shot_ids:
            self.shot_ids.append(identity)
        self.fill_combo(self.episode_combo, sorted({i.episode for i in self.shot_ids}), identity.episode)
        self.episode_changed(preferred_sequence=identity.sequence, preferred_shot=identity.shot)
        self.shot_changed(preferred_path=self.session.source)

    def open_input(self):
        source = self.composition_combo.currentData()
        if source and self.discard_ok():
            self.open_source(source)

    def open_source(self, source):
        config = self.config_path
        if not config or not Path(config).is_dir():
            self.status.setPlainText('Select a project in Launcher and launch Smart Composition again.')
            return
        try:
            project_config = ProjectConfig(config)
            if not project_config.project_root:
                raise ValueError('The Launcher project configuration is invalid. Select a configured project and relaunch.')
        except Exception as exc:
            self.status.setPlainText(str(exc))
            return
        self.debounce.stop()
        def load():
            session = CompositionSession(ShotManagerService(project_config), source)
            return session, self.available_versions(session)
        def loaded(result):
            self.session, versions = result
            self.section_choices = {kind: ref['path'] for kind, ref in self.session.snapshot.get('sections', {}).items()}
            self.saved_selection = tuple(sorted(self.session.initial.values()))
            self.populate(versions, self.session.initial)
            self.source_label.setText(str(self.session.source))
            self.sync_loaded_shot()
            self.preview_selection = None
            self.preview()
        self.run_job(load, loaded, 'Opening composition…')

    def selection(self):
        return tuple(sorted(combo.currentData() for item, combo, key in self.rows
            if item.checkState(0) == QtCore.Qt.Checked))

    def selected_map(self):
        return {key: combo.currentData() for item, combo, key in self.rows
            if item.checkState(0) == QtCore.Qt.Checked}

    @staticmethod
    def available_versions(session):
        return {'products': session.versions(),
                'sections': session.handoff.section_versions(session.identity)}

    def populate(self, versions, selected):
        self.available = versions
        blocked = self.tree.blockSignals(True)
        try:
            self.tree.clear()
            self.rows = []
            self.section_rows = {}
            kinds = sorted(set(k[0] for k in versions['products']) | set(versions['sections']))
            for kind in kinds:
                parent = QtWidgets.QTreeWidgetItem(self.tree, [kind.title()])
                parent.setFlags(parent.flags() | QtCore.Qt.ItemIsAutoTristate | QtCore.Qt.ItemIsUserCheckable)
                choices = versions['sections'].get(kind, [])
                self.set_latest(parent, choices)
                section_path = self.section_choices.get(kind)
                if choices:
                    combo = self.version_combo(choices, section_path)
                    # Absent categories remain unchecked; a candidate only supplies the child list.
                    section_path = combo.currentData()
                    self.section_choices[kind] = section_path
                    self.tree.setItemWidget(parent, 1, combo)
                    self.section_rows[kind] = (parent, combo)
                    combo.currentIndexChanged.connect(lambda _index, k=kind, c=combo: self.change_section(k, c.currentData()))
                    data = read_json(self.session.paths.project_dependency(section_path), {})
                    detail = reference_text(data, section_path)
                    parent.setData(0, QtCore.Qt.UserRole, detail)
                    parent.setToolTip(0, detail)
                    members = (data.get('definition') or {}).get('members', [])
                    targets = {m['target'] for m in members}
                else:
                    targets = {key[1] for key in versions['products'] if key[0] == kind}
                targets.update(key[1] for key in selected if key[0] == kind)
                for target in sorted(targets):
                    key = (kind, target)
                    product_choices = versions['products'].get(key, [])
                    if not product_choices:
                        raise ValueError(f'No published product versions: {kind}/{target}')
                    pinned = selected.get(key)
                    if not pinned and choices:
                        for ref in data.get('products', []):
                            product = read_json(self.session.paths.project_dependency(ref['path']), {})
                            if product.get('target') == target:
                                pinned = ref['path']
                                break
                    child = QtWidgets.QTreeWidgetItem(parent, [target])
                    child.setCheckState(0, QtCore.Qt.Checked if key in selected else QtCore.Qt.Unchecked)
                    self.set_latest(child, product_choices)
                    child_combo = self.version_combo(product_choices, pinned)
                    self.tree.setItemWidget(child, 1, child_combo)
                    self.rows.append((child, child_combo, key))
                    self.update_reference_row(child, child_combo)
                    child_combo.currentIndexChanged.connect(lambda _index, row=child, c=child_combo: self.reference_changed(row, c))
            self.tree.expandAll()
            self.update_section_labels()
        finally:
            self.tree.blockSignals(blocked)
        if self.rows:
            self.tree.setCurrentItem(self.rows[0][0])

    @staticmethod
    def set_latest(item, choices):
        if not choices:
            item.setText(2, '—')
            return
        version, path = max(choices, key=lambda row: int(row[0][1:]))
        item.setText(2, version)
        item.setToolTip(2, 'Latest published version\n' + str(path))

    def version_combo(self, choices, selected):
        combo = QtWidgets.QComboBox()
        for version, path in choices:
            combo.addItem(version, path)
            combo.setItemData(combo.count() - 1, path, QtCore.Qt.ToolTipRole)
        if selected:
            index = next((i for i, (_, path) in enumerate(choices)
                if Path(path).resolve() == Path(selected).resolve()), -1)
            if index < 0:
                raise ValueError(f'Selected version is no longer available: {selected}')
            combo.setCurrentIndex(index)
        return combo

    def change_section(self, kind, path):
        selected = self.selected_map()
        def load():
            _, products = self.session.handoff.section_products(self.session.identity, [path])
            return { (kind, read_json(p, {})['target']): p for p in products }
        def apply(members):
            selected.update(members)
            selected_for_kind = {key: value for key, value in selected.items() if key[0] != kind or key in members}
            self.section_choices[kind] = path
            self.populate(self.available, selected_for_kind)
            self.schedule_preview()
        def restore():
            _, combo = self.section_rows[kind]
            blocked = combo.blockSignals(True)
            combo.setCurrentIndex(combo.findData(self.section_choices[kind]))
            combo.blockSignals(blocked)
        self.run_job(load, apply, 'Loading section products…', on_error=restore)

    def update_section_labels(self):
        selected = self.selected_map()
        blocked = self.tree.blockSignals(True)
        try:
            for kind, (item, combo) in self.section_rows.items():
                data = read_json(self.session.paths.project_dependency(combo.currentData()), {})
                expected = {str(Path(ref['path']).resolve()) for ref in data.get('products', [])}
                actual = {str(Path(path).resolve()) for key, path in selected.items() if key[0] == kind}
                modified = bool(actual) and actual != expected
                item.setText(0, kind.title() + (' *' if modified else ''))
                combo.setToolTip(str(combo.currentData()) +
                    ('\nModified product selection; Save creates a matching Section.' if modified else ''))
        finally:
            self.tree.blockSignals(blocked)

    def show_reference_details(self, item, previous=None):
        self.reference_details.setPlainText((item.data(0, QtCore.Qt.UserRole) or '') if item else '')

    def update_reference_row(self, item, combo):
        blocked = self.tree.blockSignals(True)
        try:
            path = combo.currentData()
            data = read_json(self.session.paths.project_dependency(path), {})
            usd = (data.get('entrypoint') or {}).get('path', '')
            filename = Path(usd).name if usd else data.get('target', '')
            if data.get('kind') == 'camera' or filename in {'deform.usdc', 'deform.usda'}:
                filename = f"{data['target']} ({filename})"
            item.setText(0, filename)
            details = reference_text(data, path)
            item.setData(0, QtCore.Qt.UserRole, details)
            item.setToolTip(0, details)
            combo.setToolTip(str(path))
        finally:
            self.tree.blockSignals(blocked)

    def reference_changed(self, item, combo):
        self.update_reference_row(item, combo)
        self.tree.setCurrentItem(item)
        self.show_reference_details(item)
        self.schedule_preview()

    def refresh(self):
        if self.session:
            selection = self.selected_map()
            self.run_job(lambda: self.available_versions(self.session),
                lambda versions: self.populate(versions, selection), 'Refreshing available versions…')

    def reset(self):
        if self.session:
            def reset_rows(versions):
                self.section_choices = {kind: ref['path'] for kind, ref in self.session.snapshot.get('sections', {}).items()}
                self.populate(versions, self.session.initial)
                self.schedule_preview()
            self.run_job(lambda: self.available_versions(self.session), reset_rows, 'Restoring saved selection…')

    def schedule_preview(self, *args):
        self.update_section_labels()
        self.save_btn.setEnabled(False)
        self.status.setPlainText('Selection changed — updating preview…')
        self.debounce.start()

    def preview(self):
        if not self.session:
            return
        if self.job:
            self.debounce.start()
            return
        selected = self.selection()
        if not selected:
            self.preview_selection = None
            self.view.makeCurrent()
            self.view.closeRenderer()
            self.model.stage = None
            self.update_overlays()
            self.view.update()
            self.status.setPlainText('Select at least one published product.')
            return
        def display(result):
            if selected != self.selection():
                self.schedule_preview()
                return
            stage, self.preview_layers = result
            first = self.model.stage is None
            previous_camera = self.camera.currentData()
            start, end = int(stage.GetStartTimeCode()), int(stage.GetEndTimeCode())
            current = start if first else max(start, min(end, self.frame.value()))
            # Rebuild Hydra's scene index when replacing the anonymous stage.
            # Retaining the renderer can leave unchecked targets on screen.
            self.view.makeCurrent()
            self.view.closeRenderer()
            self.model.currentFrame = Usd.TimeCode(current)
            self.model.stage = stage
            self.model.selection.clear()
            self.slider.setRange(start, end)
            self.frame.setRange(start, end)
            self.slider.setValue(current)
            self.set_frame(current)
            self.camera.blockSignals(True)
            self.camera.clear()
            self.camera.addItem('Free camera', '')
            for prim in stage.Traverse():
                if prim.IsA(UsdGeom.Camera):
                    self.camera.addItem(camera_name(prim), str(prim.GetPath()))
                    self.camera.setItemData(self.camera.count() - 1, str(prim.GetPath()), QtCore.Qt.ToolTipRole)
            self.camera.setCurrentIndex(max(0, self.camera.findData(previous_camera)))
            self.camera.blockSignals(False)
            self.set_camera()
            self.view.updateView(resetCam=first, forceComputeBBox=True)
            self.preview_selection = selected
            self.save_btn.setEnabled(True)
            self.status.setPlainText('Preview updated. Save creates a new composition version.')
        self.run_job(lambda: self.session.preview(selected), display, 'Composing preview…')

    def run_job(self, operation, callback, message, on_error=None):
        if self.job:
            return
        self.play.setChecked(False)
        self.status.setPlainText(message)
        self.save_btn.setEnabled(False)
        controls = (self.tree, self.open_btn, self.refresh_btn, self.reset_btn, self.reload_btn, self.movie_btn,
                    self.episode_combo, self.sequence_combo, self.shot_combo, self.composition_combo)
        for control in controls:
            control.setEnabled(False)
        job = Job(operation, self)
        self.job = job
        def done():
            self.job = None
            for control in controls:
                control.setEnabled(True)
            try:
                if job.error:
                    if on_error:
                        on_error()
                    self.preview_selection = None
                    self.status.setPlainText('Error: ' + job.error + ' (Preview may show the previous selection.)')
                else:
                    callback(job.value)
            except Exception as exc:
                self.preview_selection = None
                self.status.setPlainText('Error: ' + str(exc))
            if not self.job:
                self.save_btn.setEnabled(self.preview_selection == self.selection() and bool(self.selection()))
            self.sync_browser_controls()
            job.deleteLater()
        job.finished.connect(done)
        job.start()

    def save(self):
        selected = self.selection()
        if self.preview_selection != selected or not selected:
            return
        def publish():
            manifest = self.session.save(selected)
            session = CompositionSession(self.session.handoff.shots, manifest)
            session.preview_refs = self.session.preview_refs
            return session, self.available_versions(session)
        def saved(result):
            self.session, versions = result
            self.section_choices = {kind: ref['path'] for kind, ref in self.session.snapshot.get('sections', {}).items()}
            self.populate(versions, self.session.initial)
            self.saved_selection = self.selection()
            self.source_label.setText(str(self.session.source))
            self.sync_loaded_shot()
            self.status.setPlainText('Saved new composition: ' + str(self.session.source))
        self.run_job(publish, saved, 'Saving new composition…')

    def export_movie(self):
        if self.job or self.export_process:
            return
        if not self.session or not self.model.stage or self.preview_selection != self.selection():
            self.status.setPlainText('Open a composition and wait for its preview before exporting.')
            return
        if self.selection() != self.saved_selection:
            self.status.setPlainText('Save New Composition before exporting the modified selection.')
            return
        camera = self.camera.currentData()
        if not camera:
            self.status.setPlainText('Select a published camera from the camera list before exporting.')
            return
        from .movie_export import prepare_export
        self.run_job(lambda: prepare_export(self.session, camera), self.start_export, 'Preparing USD review…')

    def start_export(self, job_file):
        job = read_json(job_file, {})
        process = QtCore.QProcess(self)
        self.export_process = process
        self.export_log = ''
        self.export_buffer = ''
        dialog = QtWidgets.QProgressDialog('Rendering saved USD composition…', 'Cancel', 0,
            job['frame_range'][1] - job['frame_range'][0] + 3, self)
        dialog.setWindowTitle('USD Review Movie + PDF')
        dialog.setWindowModality(QtCore.Qt.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.canceled.connect(lambda: Path(job['cancel']).touch())
        dialog.show()
        self.export_dialog = dialog
        def output():
            chunk = bytes(process.readAllStandardOutput()).decode('utf8', errors='replace')
            self.export_log = (self.export_log + chunk)[-12000:]
            self.export_buffer += chunk
            while '\n' in self.export_buffer:
                line, self.export_buffer = self.export_buffer.split('\n', 1)
                if line.startswith('FRAME '):
                    _, count, total = line.split()
                    dialog.setLabelText(f'Rendering frame {count} / {total}')
                    dialog.setValue(int(count))
                elif line.strip() in ('ENCODING', 'REPORT'):
                    dialog.setLabelText('Encoding movie…' if line.strip() == 'ENCODING' else 'Creating reference report…')
        def finish(code, status):
            output()
            dialog.hide()
            dialog.deleteLater()
            self.export_process = None
            if code == 0 and Path(job['files']['receipt']).is_file():
                self.last_export = job['files']
                self.output_btn.setEnabled(True)
                self.status.setPlainText('Created movie + PDF: ' + job['files']['movie'])
            else:
                self.status.setPlainText('Export cancelled.' if Path(job['cancel']).exists() else
                    'Export failed: ' + self.export_log)
            process.deleteLater()
        process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        process.readyReadStandardOutput.connect(output)
        process.finished.connect(finish)
        def failed(error):
            if error == QtCore.QProcess.FailedToStart:
                self.export_log += process.errorString()
                Path(job['lock']).unlink(missing_ok=True)
                finish(1, None)
        process.errorOccurred.connect(failed)
        env = QtCore.QProcessEnvironment.systemEnvironment()
        root = Path(__file__).resolve().parents[4]
        env.insert('PYTHONPATH', str(root / 'packages') + os.pathsep + env.value('PYTHONPATH'))
        env.insert('PYTHONIOENCODING', 'utf-8')
        env.insert('QT_SCALE_FACTOR', '1')
        process.setProcessEnvironment(env)
        process.start(sys.executable, [str(root / 'tools' / 'usd' / 'nvidia_usdpython_launcher.py'),
            '-m', 'smartlib.apps.smart_composition.movie_worker', str(job_file)])

    def open_export_output(self):
        if self.last_export:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Path(self.last_export['movie']).parent)))

    def set_frame(self, value):
        self.frame.blockSignals(True)
        self.frame.setValue(value)
        self.frame.blockSignals(False)
        if self.model.stage:
            self.model.currentFrame = Usd.TimeCode(value)
            self.update_overlays()
            self.view.updateView()

    def set_camera(self, *args):
        if self.model.stage:
            path = self.camera.currentData()
            self.model.viewSettings.cameraPrim = self.model.stage.GetPrimAtPath(path) if path else None
            self.update_overlays()
            self.view.updateView()

    def update_overlays(self, *args):
        stage = self.model.stage
        prim = self.model.viewSettings.cameraPrim if stage else None
        has_camera = bool(prim and prim.IsA(UsdGeom.Camera))
        settings = self.model.viewSettings
        settings.cameraMaskColor = (0.0, 0.0, 0.0, 1.0)
        settings.cameraMaskMode = (CameraMaskModes.PARTIAL
            if has_camera and self.mask_toggle.isChecked() else CameraMaskModes.NONE)
        # Keep the gate outlined even with the dimming off, so toggling does not
        # change the camera fit or the image framing.
        settings.showMask_Outline = has_camera
        self.mask_toggle.setEnabled(has_camera)
        self.hud.setVisible(bool(stage) and self.info_toggle.isChecked())
        if stage:
            identity = self.session.identity if self.session else None
            name = ' / '.join((identity.episode, identity.sequence, identity.shot)) if identity else 'Shot'
            anchors = (self.session.handoff.config.base.get('anchors') or {}) if self.session else {}
            resolution = anchors.get('resolution')
            resolution_text = 'Project resolution: --'
            if isinstance(resolution, (tuple, list)) and len(resolution) >= 2:
                try:
                    width, height = int(resolution[0]), int(resolution[1])
                    if width > 0 and height > 0:
                        resolution_text = f'Project resolution: {width} x {height} ({width / height:.3f}:1)'
                except (ValueError, TypeError):
                    pass
            self.hud.shot.setText(f'{name}\n{resolution_text}\nFrame: {self.frame.value()}\nFPS: {stage.GetFramesPerSecond():g}')
            self.hud.camera.setText(camera_info(stage, prim, self.model.currentFrame))
            self.hud.raise_()
        self.view.update()

    def toggle_play(self, playing):
        self.play.setText('Pause' if playing else 'Play')
        if playing and self.model.stage:
            self.playback.start(max(1, round(1000 / self.model.stage.GetFramesPerSecond())))
        else:
            self.playback.stop()

    def advance(self):
        value = self.slider.value() + 1
        self.slider.setValue(self.slider.minimum() if value > self.slider.maximum() else value)

    def discard_ok(self):
        if self.session and self.selection() != self.saved_selection:
            return QtWidgets.QMessageBox.question(self, 'Unsaved selection',
                'Discard the unsaved layer selection?', QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No) == QtWidgets.QMessageBox.Yes
        return True

    def closeEvent(self, event):
        if self.export_process:
            self.status.setPlainText("Cancel the export and wait for it to finish before closing.")
            event.ignore()
            return
        if self.job:
            self.status.setPlainText('Wait for the current operation to finish before closing.')
            event.ignore()
            return
        if not self.discard_ok():
            event.ignore()
            return
        self.playback.stop()
        self.debounce.stop()
        self.view.closeRenderer()
        event.accept()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Smart Composition — standalone USD layer manager')
    parser.add_argument('composition', nargs='?', default='')
    parser.add_argument('--config', default=os.environ.get('PROJECT_CONFIG_DIR', ''))
    args = parser.parse_args(argv)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    window = CompositionWindow(args.config, args.composition)
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())

from __future__ import annotations

import os
from copy import copy
from collections import Counter
from pathlib import Path

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets

from smartlib.core.asset_publish_resolver import AssetPublishResolver
from smartlib.core.config_loader import ProjectConfig, default_config_dir
from smartlib.apps.smart_reference_editor.service import ReferenceEditorService, compare_cast, suggest_target, identity_scope
from smartlib.dcc.maya.reference_editor import Operation, snapshot, apply


class ReferenceEditorWindow(QtWidgets.QMainWindow):
    def __init__(self, config_dir=None, parent=None, *, service=None, cmds=None):
        super().__init__(parent)
        if cmds is None:
            from maya import cmds
        self.cmds = cmds
        config = ProjectConfig(Path(config_dir or os.environ.get('PROJECT_CONFIG_DIR') or default_config_dir()))
        self.service = service or ReferenceEditorService(config)
        self.scanned = None
        self.cast_items = []
        self.cast_choices = {}
        self.cast_overrides = {}
        self.differences, self.references, self.targets = [], [], []
        self.setWindowTitle('Smart Reference Editor')
        self.resize(1180, 720)
        self.setStyleSheet("""
            QWidget { background: #292e33; color: #dbe1e6; font-family: 'Segoe UI'; font-size: 12px; }
            QTableWidget, QPlainTextEdit { background: #23272b; border: 1px solid #49525b; selection-background-color: #235b61; }
            QHeaderView::section { background: #343c43; border: 0; padding: 7px; }
            QComboBox, QPushButton { background: #39434c; border: 1px solid #58636d; padding: 6px; border-radius: 3px; }
            QPushButton:hover { background: #465662; }
            QWidget:disabled { color: #828b94; }
            QTabBar::tab { background: #343c43; padding: 9px 20px; }
            QTabBar::tab:selected { background: #235b61; color: white; }
            QTabWidget::pane { border: 1px solid #49525b; }
        """)
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        context = QtWidgets.QHBoxLayout()
        context.addWidget(QtWidgets.QLabel(f'Project: {config.project_name}'))
        self.scope = QtWidgets.QComboBox()
        self.scope.addItems(['Shot', 'Sequence'])
        self.sequence = QtWidgets.QComboBox()
        self.shot = QtWidgets.QComboBox()
        self.department = QtWidgets.QComboBox()
        self.department.addItems(['anim', 'layout', 'fx', 'lighting', 'default'])
        for label, widget in [('Scope', self.scope), ('Episode / Sequence', self.sequence), ('Shot', self.shot), ('Department', self.department)]:
            context.addWidget(QtWidgets.QLabel(label))
            context.addWidget(widget, 1)
        self.add_button(context, 'Use Current Scene', self.select_scene_context)
        layout.addLayout(context)
        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.cast_table, cast_layout = self.make_tab('Casting Sync', ['Select', 'Status', 'Asset / Variant / Namespace', 'Context', 'Version'])
        cast_controls = QtWidgets.QHBoxLayout()
        self.changes_only = QtWidgets.QCheckBox('Changes only')
        self.changes_only.setChecked(False)
        self.changes_only.toggled.connect(self.filter_cast)
        cast_controls.addWidget(self.changes_only)
        self.show_other_references = QtWidgets.QCheckBox('Show references outside Casting')
        self.show_other_references.setToolTip('Include cameras and other scene references not registered in this Casting.')
        self.show_other_references.toggled.connect(self.filter_cast)
        cast_controls.addWidget(self.show_other_references)
        self.add_button(cast_controls, 'Select added only', self.select_added)
        self.add_button(cast_controls, 'Refresh Comparison', self.refresh_cast)
        cast_layout.insertLayout(0, cast_controls)
        self.relink_table, relink_layout = self.make_tab('Production Relink', ['Select', 'Reference / Namespace', 'Client reference', 'Production asset', 'Status'])
        relink_controls = QtWidgets.QHBoxLayout()
        relink_controls.addWidget(QtWidgets.QLabel('Map client references to Production assets. Nested references are read-only.'), 1)
        self.add_button(relink_controls, 'Scan References', self.refresh_relink)
        relink_layout.insertLayout(0, relink_controls)
        self.info_tabs = QtWidgets.QTabWidget()
        self.info_tabs.setMaximumHeight(180)
        self.detail = QtWidgets.QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.scan_warning = QtWidgets.QPlainTextEdit()
        self.scan_warning.setReadOnly(True)
        self.scan_warning.setStyleSheet('color: #e1b965;')
        self.info_tabs.addTab(self.detail, 'Details')
        self.info_tabs.addTab(self.scan_warning, 'Warnings (0)')
        layout.addWidget(self.info_tabs)
        layout.addWidget(QtWidgets.QLabel('Namespace and reference edits are retained. Compatibility depends on the replacement asset. Save a copy before applying.'))
        footer = QtWidgets.QHBoxLayout()
        self.status = QtWidgets.QLabel('Choose a sequence / shot, then refresh or scan references.')
        footer.addWidget(self.status, 1)
        self.add_button(footer, 'Preview Changes', self.preview)
        self.apply_button = self.add_button(footer, 'Apply Selected', self.apply_selected)
        self.apply_button.setStyleSheet('QPushButton { background: #197b83; color: white; padding: 8px 20px; }')
        self.apply_button.setEnabled(False)
        layout.addLayout(footer)
        self.setCentralWidget(central)
        self.cast_table.itemSelectionChanged.connect(self.show_detail)
        self.relink_table.itemSelectionChanged.connect(self.show_detail)
        for identity in self.service.casting.sequences():
            self.sequence.addItem(identity.code, identity)
        self.sequence.currentIndexChanged.connect(self.update_shots)
        self.scope.currentIndexChanged.connect(self.invalidate)
        self.shot.currentIndexChanged.connect(self.invalidate)
        self.department.currentIndexChanged.connect(self.invalidate)
        self.tabs.currentChanged.connect(self.invalidate)
        self.update_shots()
        self.select_scene_context()

    @staticmethod
    def add_button(layout, text, callback):
        button = QtWidgets.QPushButton(text)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def make_tab(self, name, headers):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        table = QtWidgets.QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().hide()
        table.setWordWrap(False)
        layout.addWidget(table)
        self.tabs.addTab(page, name)
        return table, layout

    def update_shots(self, *_):
        self.shot.clear()
        seq = self.sequence.currentData()
        if seq:
            for identity in self.service.casting.shots_for_sequence(seq.episode, seq.sequence):
                self.shot.addItem(identity.shot, identity)
        self.invalidate()

    def invalidate(self, *_):
        self.scanned = None
        self.cast_items = []
        self.cast_choices = {}
        self.cast_overrides = {}
        self.cast_table.setRowCount(0)
        self.relink_table.setRowCount(0)
        self.differences, self.references, self.targets = [], [], []
        self.apply_button.setEnabled(False)
        self.shot.setEnabled(self.scope.currentText() == 'Shot')
        self.department.setEnabled(self.scope.currentText() == 'Shot')
        self.detail.clear()
        self.scan_warning.clear()
        self.info_tabs.setTabText(1, 'Warnings (0)')
        self.info_tabs.setCurrentIndex(0)
        self.status.setText('Context changed. Refresh or scan before applying.')

    def select_scene_context(self):
        try:
            scene = str(self.cmds.file(query=True, sceneName=True) or '')
            identity = self.service.scene_identity(scene)
            self.sequence.setCurrentIndex(-1)
            if identity is None:
                self.status.setText('Scene context could not be resolved. Select the Casting scope explicitly.')
                return
            is_sequence = identity_scope(identity) == 'sequence'
            self.scope.setCurrentText('Sequence' if is_sequence else 'Shot')
            if is_sequence:
                self.department.setCurrentText('layout')
            for index in range(self.sequence.count()):
                seq = self.sequence.itemData(index)
                if (seq.episode, seq.sequence) == (identity.episode, identity.sequence):
                    self.sequence.setCurrentIndex(index)
                    break
            if not is_sequence:
                self.shot.setCurrentIndex(-1)
                for index in range(self.shot.count()):
                    candidate = self.shot.itemData(index)
                    if (candidate.episode, candidate.sequence, candidate.shot) == (identity.episode, identity.sequence, identity.shot):
                        self.shot.setCurrentIndex(index)
                        break
            self.refresh_current_tab()
        except Exception as exc:
            self.sequence.setCurrentIndex(-1)
            self.status.setText(f'Scene context unavailable: {exc}')

    def refresh_current_tab(self):
        if self.tabs.currentIndex() == 0:
            self.refresh_cast()
        else:
            self.refresh_relink()

    def identity(self):
        value = self.sequence.currentData() if self.scope.currentText() == 'Sequence' else self.shot.currentData()
        if value is None:
            raise RuntimeError('Select an existing sequence / shot.')
        return value

    def fail(self, exc):
        self.apply_button.setEnabled(False)
        self.scanned = None
        QtWidgets.QMessageBox.warning(self, 'Smart Reference Editor', str(exc))

    def cell(self, table, row, column, text):
        item = QtWidgets.QTableWidgetItem(str(text))
        item.setToolTip(str(text))
        table.setItem(row, column, item)
        return item

    def checkbox(self, table, row, enabled, checked=False):
        item = self.cell(table, row, 0, '')
        item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable if enabled else QtCore.Qt.NoItemFlags)
        item.setCheckState(QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)

    def show_scan_warnings(self):
        warnings = [f'{ref.node}: {ref.error}' for ref in self.scanned[1] if ref.error]
        self.scan_warning.setPlainText('Unreadable references (kept unchanged):\n' + '\n'.join(warnings) if warnings else '')
        self.info_tabs.setTabText(1, f'Warnings ({len(warnings)})')

    def refresh_cast(self):
        saved_overrides = dict(self.cast_overrides)
        self.invalidate()
        try:
            items = self.service.preview(self.identity(), self.department.currentText())
            self.scanned = snapshot(self.cmds)
            self.show_scan_warnings()
            occupied = [str(ns).strip(':') for ns in (self.cmds.namespaceInfo(':', listOnlyNamespaces=True, recurse=True) or [])]
            self.cast_items = list(items)
            self.cast_occupied = occupied
            for item in self.cast_items:
                key = self.cast_item_key(item)
                choices = self.service.cast_target_choices(item)
                self.cast_choices[key] = choices
                target = saved_overrides.get(key)
                if target and any(target == entry['path'] for entries in choices.values() for entry in entries):
                    self.cast_overrides[key] = target
            self.differences = compare_cast(self.selected_cast_items(), self.scanned[1], occupied)
            self.cast_table.setRowCount(len(self.differences))
            colors = {'Added': '#65c798', 'Changed': '#e1b965', 'Conflict': '#ed8f86', 'Unresolved': '#e1b965'}
            for row, diff in enumerate(self.differences):
                self.checkbox(self.cast_table, row, diff.status in {'Added', 'Changed'}, diff.status == 'Added')
                status = self.cell(self.cast_table, row, 1, diff.status)
                status.setForeground(QtGui.QColor(colors.get(diff.status, '#aab4bf')))
                self.cell(self.cast_table, row, 2, f'{diff.asset} / {diff.variant} / {diff.namespace}' if diff.cast_key else diff.namespace)
                if row < len(self.cast_items):
                    self.create_target_combos(row, diff)
                else:
                    self.cell(self.cast_table, row, 3, '—')
                    self.cell(self.cast_table, row, 4, '—')
            for column, width in [(0, 55), (1, 120), (2, 400), (3, 160), (4, 140)]:
                self.cast_table.setColumnWidth(column, width)
            self.filter_cast()
            self.apply_button.setEnabled(True)
        except Exception as exc:
            self.fail(exc)

    @staticmethod
    def cast_item_key(item):
        return tuple(str(getattr(item, name, '') or '') for name in
                     ('cast_key', 'asset', 'variant', 'namespace', 'variant_root'))

    def selected_cast_items(self, items=None):
        result = []
        for item in (self.cast_items if items is None else items):
            key = self.cast_item_key(item)
            target = self.cast_overrides.get(key)
            if key in self.cast_overrides:
                item = copy(item)
                # BuildPreviewItem is frozen; copy without modifying the source snapshot.
                object.__setattr__(item, 'publish_path', target)
                object.__setattr__(item, 'status', 'resolved' if target else 'missing')
                object.__setattr__(item, 'message', '')
            result.append(item)
        return result

    def create_target_combos(self, row, diff):
        choices = self.cast_choices[self.cast_item_key(self.cast_items[row])]
        context_combo = QtWidgets.QComboBox()
        version_combo = QtWidgets.QComboBox()
        for context in choices:
            context_combo.addItem(context)
        selected_context = next((context for context, entries in choices.items()
                                 if any(entry['path'] == diff.target for entry in entries)), None)
        if selected_context is None:
            context_combo.insertItem(0, 'Choose context…')
            context_combo.setCurrentIndex(0)
        else:
            context_combo.setCurrentText(selected_context)
        enabled = bool(choices) and diff.status not in {'Conflict', 'Unreadable'}
        context_combo.setEnabled(enabled)
        version_combo.setEnabled(enabled)
        self.cast_table.setCellWidget(row, 3, context_combo)
        self.cast_table.setCellWidget(row, 4, version_combo)
        self.populate_versions(row, diff.target)
        context_combo.currentIndexChanged.connect(lambda _, r=row: self.context_changed(r))
        version_combo.currentIndexChanged.connect(lambda _, r=row: self.target_changed(r))

    def populate_versions(self, row, selected_path=''):
        context = self.cast_table.cellWidget(row, 3).currentText()
        choices = self.cast_choices[self.cast_item_key(self.cast_items[row])]
        combo = self.cast_table.cellWidget(row, 4)
        combo.blockSignals(True)
        combo.clear()
        for entry in choices.get(context, []):
            combo.addItem(entry['version'], entry['path'])
            combo.setItemData(combo.count() - 1, entry['path'], QtCore.Qt.ToolTipRole)
        index = combo.findData(selected_path)
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def context_changed(self, row):
        self.populate_versions(row)
        self.target_changed(row)

    def target_changed(self, row):
        target = self.cast_table.cellWidget(row, 4).currentData()
        key = self.cast_item_key(self.cast_items[row])
        # Empty selections are explicit unresolved choices, never the previous target.
        self.cast_overrides[key] = target or ''
        self.differences = compare_cast(self.selected_cast_items(), self.scanned[1], self.cast_occupied)
        diff = self.differences[row]
        if not target:
            from dataclasses import replace
            diff = replace(diff, target='', status='Unresolved', message='Choose a published Context and Version.')
            self.differences[row] = diff
        self.cell(self.cast_table, row, 1, diff.status)
        self.checkbox(self.cast_table, row, diff.status in {'Added', 'Changed'}, diff.status in {'Added', 'Changed'})
        self.cast_table.selectRow(row)
        self.filter_cast()
        self.show_detail()

    def filter_cast(self, *_):
        visible = []
        for row, diff in enumerate(self.differences):
            hidden = (not diff.cast_key and not self.show_other_references.isChecked()) or (
                self.changes_only.isChecked() and diff.status == 'In sync')
            self.cast_table.setRowHidden(row, hidden)
            if not hidden:
                visible.append(diff)
        counts = ' · '.join(f'{key}: {count}' for key, count in Counter(d.status for d in visible).items())
        total = sum(bool(diff.cast_key) for diff in self.differences)
        self.status.setText(f'Casting assets: {total} · ' + (counts or 'No matching rows.'))

    def select_added(self):
        for row, diff in enumerate(self.differences):
            self.cast_table.item(row, 0).setCheckState(QtCore.Qt.Checked if diff.status == 'Added' else QtCore.Qt.Unchecked)

    def refresh_relink(self):
        self.invalidate()
        try:
            consumer = self.scope.currentText().lower()
            department = 'layout' if consumer == 'sequence' else self.department.currentText()
            self.targets = self.service.targets(consumer, department)
            self.scanned = snapshot(self.cmds)
            self.show_scan_warnings()
            self.references = list(self.scanned[1])
            self.relink_table.setRowCount(len(self.references))
            for row, ref in enumerate(self.references):
                self.checkbox(self.relink_table, row, not ref.nested and not ref.error)
                self.cell(self.relink_table, row, 1, f'{ref.node} / {ref.namespace}')
                self.cell(self.relink_table, row, 2, ref.path)
                combo = QtWidgets.QComboBox()
                combo.addItem('Choose asset…', None)
                for target in self.targets:
                    combo.addItem(target.label, target)
                    combo.setItemData(combo.count() - 1, target.path, QtCore.Qt.ToolTipRole)
                suggested = suggest_target(ref, self.targets)
                if suggested is not None:
                    combo.setCurrentIndex(suggested + 1)
                combo.setEnabled(not ref.nested and not ref.error)
                self.relink_table.setCellWidget(row, 3, combo)
                self.cell(self.relink_table, row, 4, 'Unreadable — kept' if ref.error else ('Nested — kept' if ref.nested else ('Suggested — review' if suggested is not None else 'Unresolved')))
                combo.currentIndexChanged.connect(lambda _, r=row: self.mapping_changed(r))
            for column, width in [(0, 55), (1, 210), (2, 280), (3, 380)]:
                self.relink_table.setColumnWidth(column, width)
            self.status.setText(f'{len(self.references)} references · {len(self.targets)} resolved Production assets. Check rows to include them.')
            self.apply_button.setEnabled(True)
        except Exception as exc:
            self.fail(exc)

    def mapping_changed(self, row):
        target = self.relink_table.cellWidget(row, 3).currentData()
        self.relink_table.item(row, 4).setText('Mapped — review' if target else 'Unresolved')
        self.show_detail()

    def operations(self):
        if self.scanned is None:
            raise RuntimeError('Refresh or scan references first.')
        result = []
        if self.tabs.currentIndex() == 0:
            for row, diff in enumerate(self.differences):
                if self.cast_table.item(row, 0).checkState() == QtCore.Qt.Checked:
                    if diff.status not in {'Added', 'Changed'}:
                        raise RuntimeError(f'Cannot apply {diff.status}: {diff.namespace}')
                    result.append(Operation('add' if diff.status == 'Added' else 'replace', diff.namespace, diff.target, diff.reference))
        else:
            for row, ref in enumerate(self.references):
                if self.relink_table.item(row, 0).checkState() != QtCore.Qt.Checked:
                    continue
                target = self.relink_table.cellWidget(row, 3).currentData()
                if target is None:
                    raise RuntimeError(f'Choose a Production asset for {ref.node}.')
                result.append(Operation('replace', ref.namespace, target.path, ref))
        if not result:
            raise RuntimeError('Select at least one change.')
        return result

    @staticmethod
    def plan_text(operations):
        return '\n\n'.join(f'{op.kind.upper()}  {op.namespace}\n{op.reference.path if op.reference else "Missing"}\n→ {op.target}' for op in operations)

    def preview(self):
        try:
            self.detail.setPlainText(self.plan_text(self.operations()))
        except Exception as exc:
            QtWidgets.QMessageBox.information(self, 'Preview', str(exc))

    def show_detail(self):
        if self.tabs.currentIndex() == 0:
            row = self.cast_table.currentRow()
            if 0 <= row < len(self.differences):
                diff = self.differences[row]
                self.detail.setPlainText(f'{diff.status}: {diff.namespace}\nCurrent: {diff.reference.path if diff.reference else "Missing"}\nTarget: {diff.target}\n{diff.message}')
        else:
            row = self.relink_table.currentRow()
            if 0 <= row < len(self.references):
                ref = self.references[row]
                target = self.relink_table.cellWidget(row, 3).currentData()
                self.detail.setPlainText(f'{ref.node} / {ref.namespace}\nCurrent: {ref.path}\nTarget: {target.path if target else "Unresolved"}\nLoaded: {ref.loaded}. Namespace retained. Reference edit compatibility must be checked.')

    def apply_selected(self):
        try:
            operations = self.operations()
            review = QtWidgets.QMessageBox(self)
            review.setWindowTitle('Apply reference changes')
            review.setText(f'Apply {len(operations)} selected changes to the current scene?')
            review.setInformativeText('Namespace and reference nodes are retained on replacement. Failed edits stop the batch. Changes are not automatically rolled back or saved.')
            review.setDetailedText(self.plan_text(operations))
            review.setStandardButtons(QtWidgets.QMessageBox.Apply | QtWidgets.QMessageBox.Cancel)
            review.setDefaultButton(QtWidgets.QMessageBox.Cancel)
            if review.exec_() != QtWidgets.QMessageBox.Apply:
                return
            if self.tabs.currentIndex() == 0:
                fresh_items = list(self.service.preview(self.identity(), self.department.currentText()))
                original = {self.cast_item_key(item): item for item in self.cast_items}
                for item in fresh_items:
                    key = self.cast_item_key(item)
                    if key in self.cast_overrides:
                        if key not in original or item.publish_path != original[key].publish_path:
                            raise RuntimeError('Casting changed after comparison. Refresh before applying.')
                        choices = self.service.cast_target_choices(item)
                        if not any(entry['path'] == self.cast_overrides[key] for entries in choices.values() for entry in entries):
                            raise RuntimeError('Selected publish is no longer available. Refresh before applying.')
                fresh = compare_cast(self.selected_cast_items(fresh_items), self.scanned[1], self.cast_occupied)
                for op in operations:
                    if not any(d.namespace == op.namespace and d.target == op.target and d.status == ('Added' if op.kind == 'add' else 'Changed') for d in fresh):
                        raise RuntimeError('Casting changed after comparison. Refresh before applying.')
            result = apply(self.cmds, self.scanned, operations)
            self.refresh_current_tab()
            self.detail.setPlainText('\n'.join(result.completed + result.errors))
            summary = f'{len(result.completed)} completed · {len(result.errors)} errors.'
            self.status.setText(summary + (' Refreshed.' if self.scanned is not None else ' Refresh failed; scan again.'))
            if result.errors:
                QtWidgets.QMessageBox.warning(self, 'Batch stopped', '\n'.join(result.errors))
        except Exception as exc:
            self.fail(exc)


_window = None


def show(config_dir=None, parent=None):
    global _window
    if parent is None:
        from maya import OpenMayaUI
        try:
            from shiboken6 import wrapInstance
        except ImportError:
            from shiboken2 import wrapInstance
        parent = wrapInstance(int(OpenMayaUI.MQtUtil.mainWindow()), QtWidgets.QWidget)
    if _window is not None:
        _window.close()
        _window.deleteLater()
    _window = ReferenceEditorWindow(config_dir, parent)
    _window.show()
    _window.raise_()
    return _window

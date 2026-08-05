from __future__ import annotations

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets

from smartlib.preflight import PreflightContext, PreflightEngine, Severity, profile_for_context


COLORS = {
    Severity.PASS: "#73c94f",
    Severity.WARNING: "#e7b52f",
    Severity.ERROR: "#ef5a50",
    Severity.RUNNING: "#38bde8",
    Severity.WAITING: "#888888",
}


class SmartPreflightWindow(QtWidgets.QMainWindow):
    def __init__(self, *, adapter, context: PreflightContext, publisher=None, parent=None):
        super().__init__(parent)
        self.adapter = adapter
        self.context = context
        self.publisher = publisher
        self.profile = profile_for_context(context)
        self.engine = PreflightEngine(adapter, self.profile)
        self.report = None
        self.results = {}
        self.output_checks = {}
        self.setWindowFlag(QtCore.Qt.Tool, True)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self.setWindowTitle(f"Smart Preflight - {self.profile.label}")
        self.resize(520, 820)
        self._build_ui()
        self._populate()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)

        title_row = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Smart Preflight")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.profile_combo = QtWidgets.QComboBox()
        self.profile_combo.addItem("Asset", "asset")
        self.profile_combo.addItem("Shot", "shot")
        self.profile_combo.setCurrentIndex(0 if self.context.kind == "asset" else 1)
        self.profile_combo.currentIndexChanged.connect(self._change_profile)
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self.profile_combo)
        root.addLayout(title_row)
        self.context_label = QtWidgets.QLabel()
        self.context_label.setStyleSheet("color: #b8c0c8;")
        root.addWidget(self.context_label)

        self.outputs_group = QtWidgets.QGroupBox("Publish Outputs")
        self.outputs_layout = QtWidgets.QVBoxLayout(self.outputs_group)
        root.addWidget(self.outputs_group)

        check_header = QtWidgets.QHBoxLayout()
        check_header.addWidget(QtWidgets.QLabel("Preflight Checks"))
        check_header.addStretch()
        self.summary_label = QtWidgets.QLabel("Not run")
        check_header.addWidget(self.summary_label)
        root.addLayout(check_header)
        self.check_tree = QtWidgets.QTreeWidget()
        self.check_tree.setHeaderLabels(["Check", "Result", "Time"])
        self.check_tree.setRootIsDecorated(False)
        self.check_tree.currentItemChanged.connect(self._show_details)
        header = self.check_tree.header()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        root.addWidget(self.check_tree, 1)

        details = QtWidgets.QGroupBox("Details")
        details_layout = QtWidgets.QVBoxLayout(details)
        self.detail_message = QtWidgets.QLabel("Select a check to view details.")
        self.detail_message.setWordWrap(True)
        self.node_list = QtWidgets.QListWidget()
        self.node_list.setMaximumHeight(100)
        details_layout.addWidget(self.detail_message)
        details_layout.addWidget(self.node_list)
        detail_actions = QtWidgets.QHBoxLayout()
        self.select_btn = QtWidgets.QPushButton("Select in Maya")
        self.recheck_btn = QtWidgets.QPushButton("Recheck Item")
        self.copy_btn = QtWidgets.QPushButton("Copy Details")
        self.select_btn.clicked.connect(self._select_nodes)
        self.recheck_btn.clicked.connect(self._recheck_selected)
        self.copy_btn.clicked.connect(self._copy_details)
        detail_actions.addWidget(self.select_btn)
        detail_actions.addWidget(self.recheck_btn)
        detail_actions.addWidget(self.copy_btn)
        details_layout.addLayout(detail_actions)
        root.addWidget(details)

        self.footer_label = QtWidgets.QLabel()
        root.addWidget(self.footer_label)
        actions = QtWidgets.QHBoxLayout()
        self.run_btn = QtWidgets.QPushButton("Run All Checks")
        self.publish_btn = QtWidgets.QPushButton()
        self.run_btn.clicked.connect(self.run_all)
        self.publish_btn.clicked.connect(self._publish)
        actions.addWidget(self.run_btn)
        actions.addWidget(self.publish_btn)
        root.addLayout(actions)

    def _populate(self):
        self.context_label.setText(self.context.label or self.context.scene_path or "Unsaved scene")
        self._clear_layout(self.outputs_layout)
        self.output_checks.clear()
        for output in self.profile.outputs:
            check = QtWidgets.QCheckBox(f"{output.label}   —   {output.summary}")
            check.setChecked(output.required or output.selected)
            check.setEnabled(not output.required)
            check.toggled.connect(self._outputs_changed)
            self.outputs_layout.addWidget(check)
            self.output_checks[output.key] = check
        self.check_tree.clear()
        self.results.clear()
        for definition in self.profile.checks:
            item = QtWidgets.QTreeWidgetItem([definition.label, "WAITING", "—"])
            item.setData(0, QtCore.Qt.UserRole, definition.key)
            self._set_item_state(item, Severity.WAITING)
            self.check_tree.addTopLevelItem(item)
        self.publish_btn.setText(self.profile.publish_label)
        self._update_footer()
        self._update_publish_state()

    def selected_outputs(self):
        return [key for key, check in self.output_checks.items() if check.isChecked()]

    def run_all(self):
        self.run_btn.setEnabled(False)
        QtWidgets.QApplication.processEvents()
        self.report = self.engine.run(
            self.context,
            selected_outputs=self.selected_outputs(),
            on_progress=self._on_progress,
        )
        self.run_btn.setEnabled(True)
        self._refresh_summary()
        self._update_publish_state()

    def _on_progress(self, index, total, result):
        self.results[result.key] = result
        item = self._item_for_key(result.key)
        if item:
            item.setText(1, result.severity.value)
            item.setText(2, f"{result.duration:.2f}s")
            item.setToolTip(0, result.message)
            self._set_item_state(item, result.severity)
        self.summary_label.setText(f"Validating {index} / {total}")
        QtWidgets.QApplication.processEvents()

    def _refresh_summary(self):
        if not self.report:
            self.summary_label.setText("Not run")
            return
        counts = self.report.counts
        self.summary_label.setText(
            f"{counts['PASS']} Passed   {counts['WARNING']} Warnings   {counts['ERROR']} Errors"
        )

    def _show_details(self, item, _previous):
        key = str(item.data(0, QtCore.Qt.UserRole)) if item else ""
        result = self.results.get(key)
        self.node_list.clear()
        if not result:
            self.detail_message.setText("This check has not run yet.")
            self.select_btn.setEnabled(False)
            self.recheck_btn.setEnabled(bool(key))
            self.copy_btn.setEnabled(False)
            return
        self.detail_message.setText(result.message or result.severity.value)
        self.node_list.addItems(result.nodes)
        self.select_btn.setEnabled(bool(result.nodes))
        self.recheck_btn.setEnabled(True)
        self.copy_btn.setEnabled(True)

    def _select_nodes(self):
        item = self.check_tree.currentItem()
        result = self.results.get(str(item.data(0, QtCore.Qt.UserRole))) if item else None
        if result:
            self.adapter.select_nodes(result.nodes)

    def _copy_details(self):
        item = self.check_tree.currentItem()
        result = self.results.get(str(item.data(0, QtCore.Qt.UserRole))) if item else None
        if not result:
            return
        lines = [
            f"Check: {result.label}",
            f"Result: {result.severity.value}",
            f"Message: {result.message}",
            f"Duration: {result.duration:.2f}s",
        ]
        if result.nodes:
            lines.extend(("Nodes:", *(f"- {node}" for node in result.nodes)))
        QtWidgets.QApplication.clipboard().setText("\n".join(lines))
        self.copy_btn.setText("Copied")
        QtCore.QTimer.singleShot(1200, lambda: self.copy_btn.setText("Copy Details"))

    def _recheck_selected(self):
        item = self.check_tree.currentItem()
        if not item:
            return
        key = str(item.data(0, QtCore.Qt.UserRole))
        report = self.engine.run(
            self.context,
            selected_outputs=self.selected_outputs(),
            only=(key,),
            attempt_id=self.report.attempt_id if self.report else None,
            on_progress=self._on_progress,
        )
        if self.report:
            self.report.results = [row for row in self.report.results if row.key != key] + report.results
        else:
            self.report = report
        self._refresh_summary()
        self._show_details(item, None)
        self._update_publish_state()

    def _change_profile(self):
        kind = str(self.profile_combo.currentData())
        self.context = PreflightContext(
            kind=kind,
            project=self.context.project,
            entity=self.context.entity,
            task=self.context.task,
            subset=self.context.subset,
            version=self.context.version,
            scene_path=self.context.scene_path,
            metadata=self.context.metadata,
        )
        self.profile = profile_for_context(self.context)
        self.engine = PreflightEngine(self.adapter, self.profile)
        self.report = None
        self.setWindowTitle(f"Smart Preflight - {self.profile.label}")
        self._populate()

    def _update_footer(self):
        count = sum(check.isChecked() for check in self.output_checks.values())
        self.footer_label.setText(f"{count} outputs selected")

    def _outputs_changed(self):
        self.report = None
        self.summary_label.setText("Outputs changed — run checks again")
        self._update_footer()
        self._update_publish_state()

    def _update_publish_state(self):
        ready = bool(self.report) and not self.report.blocked and callable(self.publisher)
        self.publish_btn.setEnabled(ready)
        if not callable(self.publisher):
            self.publish_btn.setToolTip("No publish service is connected yet.")
        elif self.report and self.report.blocked:
            self.publish_btn.setToolTip("Resolve all preflight errors before publishing.")
        else:
            self.publish_btn.setToolTip("")

    def _publish(self):
        if self.report and not self.report.blocked and callable(self.publisher):
            self.publisher(self.report)

    def _item_for_key(self, key):
        for index in range(self.check_tree.topLevelItemCount()):
            item = self.check_tree.topLevelItem(index)
            if item.data(0, QtCore.Qt.UserRole) == key:
                return item
        return None

    @staticmethod
    def _set_item_state(item, severity):
        item.setForeground(1, QtGui.QBrush(QtGui.QColor(COLORS[severity])))

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

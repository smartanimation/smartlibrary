from __future__ import annotations

from pathlib import Path

try:
    from PySide6 import QtCore, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtWidgets

from smartlib.apps.shot_manager import ShotIdentity
from smartlib.review.playblast_package import find_ffmpeg

from .service import SmartDeliveryService


class SmartDeliveryWindow(QtWidgets.QMainWindow):
    def __init__(self, config_dir: str | Path, parent=None):
        super().__init__(parent)
        self.service = SmartDeliveryService(config_dir)
        self._plan = None
        self.setWindowTitle("Smart Delivery")
        self.resize(1100, 720)
        self._build_ui()
        self._populate_shots()
        self._apply_style()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        form = QtWidgets.QGridLayout()
        self.episode_combo = QtWidgets.QComboBox()
        self.sequence_combo = QtWidgets.QComboBox()
        self.shot_list = QtWidgets.QListWidget()
        self.shot_list.setMaximumHeight(110)
        self.profile_label = QtWidgets.QLabel("DandeLione_v003")
        self.task_edit = QtWidgets.QLineEdit("preComp")
        self.version_spin = QtWidgets.QSpinBox(); self.version_spin.setRange(1, 9999); self.version_spin.setValue(1)
        self.output_edit = QtWidgets.QLineEdit()
        browse_output = QtWidgets.QPushButton("Browse…")
        browse_output.clicked.connect(self._browse_output)
        form.addWidget(QtWidgets.QLabel("Episode"), 0, 0); form.addWidget(self.episode_combo, 0, 1)
        form.addWidget(QtWidgets.QLabel("Sequence"), 0, 2); form.addWidget(self.sequence_combo, 0, 3)
        form.addWidget(QtWidgets.QLabel("Client Profile"), 1, 0); form.addWidget(self.profile_label, 1, 1)
        form.addWidget(QtWidgets.QLabel("Task"), 1, 2); form.addWidget(self.task_edit, 1, 3)
        form.addWidget(QtWidgets.QLabel("Version"), 1, 4); form.addWidget(self.version_spin, 1, 5)
        form.addWidget(QtWidgets.QLabel("Package Output"), 2, 0); form.addWidget(self.output_edit, 2, 1, 1, 4); form.addWidget(browse_output, 2, 5)
        layout.addLayout(form)
        layout.addWidget(QtWidgets.QLabel("Review Shots (Internal Review version / state)"))
        layout.addWidget(self.shot_list)

        self.inputs = QtWidgets.QTableWidget(0, 4)
        self.inputs.setHorizontalHeaderLabels(["Enabled", "Type / Review Layer", "Source", "Status"])
        self.inputs.horizontalHeader().setStretchLastSection(False)
        self.inputs.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.inputs, 2)
        buttons = QtWidgets.QHBoxLayout()
        refresh = QtWidgets.QPushButton("Auto Resolve")
        dry = QtWidgets.QPushButton("Dry Run")
        build = QtWidgets.QPushButton("Build Delivery"); build.setProperty("primary", True)
        refresh.clicked.connect(self._resolve); dry.clicked.connect(self._dry_run); build.clicked.connect(self._build)
        buttons.addWidget(refresh); buttons.addStretch(1); buttons.addWidget(dry); buttons.addWidget(build)
        layout.addLayout(buttons)
        self.report = QtWidgets.QPlainTextEdit(); self.report.setReadOnly(True)
        layout.addWidget(self.report, 1)
        self.episode_combo.currentIndexChanged.connect(self._populate_sequences)
        self.sequence_combo.currentIndexChanged.connect(self._populate_shot_list)
        self.shot_list.currentItemChanged.connect(self._resolve)

    def _populate_shots(self):
        self._shots = self.service.list_shots()
        self.episode_combo.blockSignals(True)
        self.episode_combo.clear()
        self.episode_combo.addItems(sorted({row.episode for row in self._shots}, key=str.lower))
        self.episode_combo.blockSignals(False)
        self._populate_sequences()

    def _populate_sequences(self):
        episode = self.episode_combo.currentText()
        current = self.sequence_combo.currentText()
        values = sorted({row.sequence for row in self._shots if row.episode == episode}, key=str.lower)
        self.sequence_combo.blockSignals(True)
        self.sequence_combo.clear(); self.sequence_combo.addItems(values)
        if current in values:
            self.sequence_combo.setCurrentText(current)
        self.sequence_combo.blockSignals(False)
        self._populate_shot_list()

    def _populate_shot_list(self):
        episode = self.episode_combo.currentText(); sequence = self.sequence_combo.currentText()
        self.shot_list.blockSignals(True)
        self.shot_list.clear()
        for identity in self._shots:
            if identity.episode != episode or identity.sequence != sequence:
                continue
            status = self.service.review_status(identity)
            item = QtWidgets.QListWidgetItem(f"{identity.shot}    {status}")
            item.setData(QtCore.Qt.UserRole, identity)
            self.shot_list.addItem(item)
        if self.shot_list.count():
            self.shot_list.setCurrentRow(0)
        self.shot_list.blockSignals(False)
        self._resolve()

    def _identity(self) -> ShotIdentity | None:
        item = self.shot_list.currentItem()
        return item.data(QtCore.Qt.UserRole) if item else None

    def _resolve(self):
        identity = self._identity()
        if not identity:
            return
        sources = self.service.suggested_sources(identity)
        sequences = self.service.suggested_image_sequences(identity)
        review_source = self.service.review_source(identity)
        review_version = str(review_source.get("version") or "")
        if review_version.lower().startswith("v") and review_version[1:].isdigit():
            self.version_spin.setValue(int(review_version[1:]))
        layers = self.service.review_layers(identity)
        self.inputs.setRowCount(0)
        for kind in ("maya", "aep", "review"):
            self._add_row(kind, sources.get(kind, ""))
        for layer in layers:
            self._add_row(f"image_sequence:{layer}", sequences.get(layer, ""))
        project_root = self.service.config.project_root or Path.cwd()
        self.output_edit.setText(str(Path(project_root)))
        manifest = str(review_source.get("manifest") or "not found")
        if review_source.get("approved"):
            approval = review_source.get("approval") or {}
            self.report.setPlainText(
                f"APPROVED {review_version} by {approval.get('author') or '-'}\n"
                f"Resolved from source manifest:\n{manifest}\n\nConfirm every source, then run Dry Run."
            )
        else:
            self.report.setPlainText(
                f"BLOCKED: No approved Internal Review. Latest submission is "
                f"{review_source.get('latest_version') or '-'}.\nApprove a version in Smart Review first."
            )

    def _add_row(self, label: str, source: str):
        row = self.inputs.rowCount(); self.inputs.insertRow(row)
        enabled = QtWidgets.QTableWidgetItem(); enabled.setFlags(enabled.flags() | QtCore.Qt.ItemIsUserCheckable); enabled.setCheckState(QtCore.Qt.Checked if source else QtCore.Qt.Unchecked)
        self.inputs.setItem(row, 0, enabled); self.inputs.setItem(row, 1, QtWidgets.QTableWidgetItem(label)); self.inputs.setItem(row, 2, QtWidgets.QTableWidgetItem(source)); self.inputs.setItem(row, 3, QtWidgets.QTableWidgetItem("FOUND" if source else "MISSING"))

    def _browse_output(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Package parent directory", self.output_edit.text())
        if path:
            self.output_edit.setText(str(Path(path)))

    def _values(self):
        sources, sequences = {}, {}
        for row in range(self.inputs.rowCount()):
            if self.inputs.item(row, 0).checkState() != QtCore.Qt.Checked:
                continue
            label = self.inputs.item(row, 1).text(); value = self.inputs.item(row, 2).text().strip()
            if label.startswith("image_sequence:"):
                sequences[label.split(":", 1)[1]] = value
            else:
                sources[label] = value
        return sources, sequences

    def _make_plan(self):
        identity = self._identity()
        if not identity:
            raise RuntimeError("Select a shot.")
        sources, sequences = self._values()
        return self.service.build_plan(identity, task=self.task_edit.text().strip(), version=self.version_spin.value(), package_root=self.output_edit.text().strip(), sources=sources, layer_sequences=sequences)

    def _dry_run(self):
        try:
            self._plan = self._make_plan()
            lines = [f"Job: {self._plan.job_id}", f"Package: {self._plan.package_root}", f"Files: {len(self._plan.items)}", ""]
            lines.extend(self._plan_lines(self._plan))
            self.report.setPlainText("\n".join(lines))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Dry Run", str(exc))

    def _build(self):
        try:
            plan = self._make_plan()
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            result = self.service.execute(plan, ffmpeg=find_ffmpeg(self.service.config))
            lines = [f"Blocked: {result.blocked}", f"Package: {result.package_root}", f"Manifest: {result.manifest}", f"ZIP: {result.archive or '-'}", f"Contact Sheet: {result.contact_sheet or '-'}", ""]
            sequence_results = [row for row in result.results if row.item_id.startswith("image_sequence.")]
            regular_results = [row for row in result.results if not row.item_id.startswith("image_sequence.")]
            lines.extend(f"[{row.severity}] {row.code}: {row.message}" for row in regular_results)
            if sequence_results:
                counts = {state: sum(row.severity == state for row in sequence_results) for state in ("PASS", "WARNING", "ERROR")}
                lines.append(
                    f"[IMAGE SEQUENCES] {len(sequence_results)} checks: "
                    f"{counts['PASS']} pass, {counts['WARNING']} warning, {counts['ERROR']} error"
                )
            self.report.setPlainText("\n".join(lines))
            title = "Delivery blocked" if result.blocked else "Delivery complete"
            QtWidgets.QMessageBox.information(self, title, "\n".join(lines[:5]))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Smart Delivery", str(exc))
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    @staticmethod
    def _plan_lines(plan):
        lines = []
        sequences = {}
        for item in plan.items:
            if item.kind != "image_sequence":
                lines.append(f"{item.kind}: {item.source} -> {item.destination.as_posix()}")
                continue
            layer = str(item.metadata.get("review_layer") or "sequence")
            sequences.setdefault(layer, []).append(item)
        for layer, items in sequences.items():
            frames = sorted(str(item.metadata.get("frame") or "") for item in items if item.metadata.get("frame"))
            source_pattern = str(items[0].source)
            destination_pattern = items[0].destination.as_posix()
            if frames:
                source_pattern = source_pattern.replace(frames[0], "####")
                destination_pattern = destination_pattern.replace(frames[0].zfill(4), "####")
            frame_range = f"{frames[0]}-{frames[-1]}" if frames else "-"
            lines.append(
                f"image_sequence:{layer}: {source_pattern} -> {destination_pattern} "
                f"({len(items)} frames, {frame_range})"
            )
        return lines

    def _apply_style(self):
        self.setStyleSheet("""
        QMainWindow,QWidget { background:#252728; color:#e5e5e5; }
        QLineEdit,QComboBox,QSpinBox,QTableWidget,QPlainTextEdit { background:#202223; border:1px solid #3a3d3f; }
        QPushButton { min-height:28px; padding:3px 12px; background:#3a3d3f; border:1px solid #4a4d4f; border-radius:4px; }
        QPushButton[primary="true"] { background:#296eaa; }
        QHeaderView::section { background:#333638; padding:6px; }
        """)

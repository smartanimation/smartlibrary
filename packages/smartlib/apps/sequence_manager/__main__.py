from __future__ import annotations

import argparse
import sys
from pathlib import Path

from smartlib.apps.sequence_manager import SequenceManagerService, SequenceSummary
from smartlib.core.config_loader import ProjectConfig


def _qt():
    try:
        from PySide6 import QtWidgets
    except ImportError:
        from PySide2 import QtWidgets
    return QtWidgets


QtWidgets = _qt()


_WINDOW = None


class SequenceManagerWindow(QtWidgets.QMainWindow):
    def __init__(self, config_dir: Path):
        super().__init__(); self.service = SequenceManagerService(ProjectConfig(config_dir)); self.current = None
        self.setWindowTitle("Sequence Manager"); self.resize(1050, 680)
        root = QtWidgets.QWidget(); self.setCentralWidget(root); layout = QtWidgets.QVBoxLayout(root)
        top = QtWidgets.QHBoxLayout(); self.sequences = QtWidgets.QComboBox(); top.addWidget(self.sequences, 1)
        for label, slot in (("Refresh", self.refresh), ("Save", self.save)):
            button = QtWidgets.QPushButton(label); button.clicked.connect(slot); top.addWidget(button)
        layout.addLayout(top)
        split = QtWidgets.QSplitter(); layout.addWidget(split, 1)
        shot_panel = QtWidgets.QWidget(); shot_layout = QtWidgets.QVBoxLayout(shot_panel); shot_layout.addWidget(QtWidgets.QLabel("Shots / Editorial Order"))
        self.shots = QtWidgets.QTableWidget(0, 4); self.shots.setHorizontalHeaderLabels(["Order", "Shot", "Cut In", "Cut Out"]); self.shots.horizontalHeader().setStretchLastSection(True); shot_layout.addWidget(self.shots)
        assembly_panel = QtWidgets.QWidget(); assembly_layout = QtWidgets.QVBoxLayout(assembly_panel); assembly_layout.addWidget(QtWidgets.QLabel("Recommended Assemblies (each Shot pins its actual version)"))
        self.assemblies = QtWidgets.QTableWidget(0, 3); self.assemblies.setHorizontalHeaderLabels(["Entity ID", "Variant", "Version"]); self.assemblies.horizontalHeader().setStretchLastSection(True); assembly_layout.addWidget(self.assemblies)
        row = QtWidgets.QHBoxLayout(); add = QtWidgets.QPushButton("Add"); remove = QtWidgets.QPushButton("Remove"); add.clicked.connect(self.add_assembly); remove.clicked.connect(self.remove_assembly); row.addWidget(add); row.addWidget(remove); row.addStretch(1); assembly_layout.addLayout(row)
        split.addWidget(shot_panel); split.addWidget(assembly_panel)
        self.status = QtWidgets.QLabel(); layout.addWidget(self.status)
        self.sequences.currentIndexChanged.connect(self.load); self.refresh()

    def refresh(self):
        self.sequences.blockSignals(True); self.sequences.clear()
        for identity in self.service.list_sequences(): self.sequences.addItem(f"{identity.episode} / {identity.sequence}", identity)
        self.sequences.blockSignals(False)
        if self.sequences.count(): self.sequences.setCurrentIndex(0); self.load()

    def load(self):
        identity = self.sequences.currentData()
        if not identity: return
        self.current = self.service.load(identity); self.shots.setRowCount(0); self.assemblies.setRowCount(0)
        for data in self.current.shots:
            row = self.shots.rowCount(); self.shots.insertRow(row)
            for col, key in enumerate(("order", "shot", "cut_in", "cut_out")): self.shots.setItem(row, col, QtWidgets.QTableWidgetItem(str(data.get(key) if data.get(key) is not None else "")))
        for data in self.current.default_assemblies: self.add_assembly(data)
        self.status.setText(f"{len(self.current.shots)} Shots")

    def add_assembly(self, data=None):
        data = data if isinstance(data, dict) else {}
        row = self.assemblies.rowCount(); self.assemblies.insertRow(row)
        for col, key in enumerate(("entity_id", "variant", "version")): self.assemblies.setItem(row, col, QtWidgets.QTableWidgetItem(str(data.get(key) or ("default" if key == "variant" else ""))))

    def remove_assembly(self):
        for row in sorted({x.row() for x in self.assemblies.selectedItems()}, reverse=True): self.assemblies.removeRow(row)

    def save(self):
        if not self.current: return
        shots = []
        for row in range(self.shots.rowCount()):
            values = [(self.shots.item(row, col).text().strip() if self.shots.item(row, col) else "") for col in range(4)]
            shots.append({"order": int(values[0] or 0), "shot": values[1], "cut_in": int(values[2]) if values[2] else None, "cut_out": int(values[3]) if values[3] else None})
        assemblies = []
        for row in range(self.assemblies.rowCount()):
            values = [(self.assemblies.item(row, col).text().strip() if self.assemblies.item(row, col) else "") for col in range(3)]
            if values[0]: assemblies.append(dict(zip(("entity_id", "variant", "version"), values)))
        path = self.service.save(SequenceSummary(self.current.identity, shots, assemblies)); self.status.setText(f"Saved: {path}")


def show(config_dir: str | Path):
    """Show Sequence Manager in the current Qt process (including Maya)."""
    global _WINDOW
    if _WINDOW is not None:
        try:
            _WINDOW.close()
            _WINDOW.deleteLater()
        except RuntimeError:
            pass
    _WINDOW = SequenceManagerWindow(Path(config_dir))
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    return _WINDOW


def main(argv=None):
    parser = argparse.ArgumentParser(); parser.add_argument("--config-dir", required=True); args = parser.parse_args(argv)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv); show(args.config_dir); return app.exec()


if __name__ == "__main__": raise SystemExit(main())

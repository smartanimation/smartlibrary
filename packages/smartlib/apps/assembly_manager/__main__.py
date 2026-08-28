from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from smartlib.apps.assembly_manager import AssemblyManagerService, AssemblyMember
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.path_resolver import AssemblyIdentity


def _qt():
    try:
        from PySide6 import QtCore, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtWidgets
    return QtCore, QtWidgets


QtCore, QtWidgets = _qt()


class AssemblyManagerWindow(QtWidgets.QMainWindow):
    def __init__(self, config_dir: Path):
        super().__init__()
        self.service = AssemblyManagerService(ProjectConfig(config_dir))
        self.current: AssemblyIdentity | None = None
        self.setWindowTitle("Assembly Manager")
        self.resize(1120, 700)
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)
        top = QtWidgets.QHBoxLayout()
        self.assemblies = QtWidgets.QComboBox()
        self.purpose = QtWidgets.QComboBox(); self.purpose.addItems(["blockout", "layout", "render"])
        for label, slot in (("New", self.create_assembly), ("Refresh", self.refresh), ("Save Draft", self.save), ("Validate", self.validate), ("Construct Maya", self.construct), ("Publish", self.publish)):
            button = QtWidgets.QPushButton(label); button.clicked.connect(slot); top.addWidget(button)
        top.insertWidget(0, self.assemblies, 1); top.insertWidget(1, self.purpose)
        layout.addLayout(top)
        self.table = QtWidgets.QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["UID", "Type", "Entity ID", "Variant", "Version", "Namespace", "Purpose", "Transform JSON"])
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        row = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("Add Member"); add.clicked.connect(self.add_member)
        remove = QtWidgets.QPushButton("Remove Member"); remove.clicked.connect(self.remove_member)
        row.addWidget(add); row.addWidget(remove); row.addStretch(1); layout.addLayout(row)
        self.log = QtWidgets.QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(150); layout.addWidget(self.log)
        self.assemblies.currentIndexChanged.connect(self.load)
        self.refresh()

    def refresh(self):
        selected = self.assemblies.currentData()
        self.assemblies.blockSignals(True); self.assemblies.clear()
        for identity in self.service.list_assemblies():
            self.assemblies.addItem(f"{identity.category} / {identity.group} / {identity.name} / {identity.variant}", identity)
        self.assemblies.blockSignals(False)
        index = self.assemblies.findData(selected) if selected else 0
        if self.assemblies.count(): self.assemblies.setCurrentIndex(max(0, index)); self.load()

    def create_assembly(self):
        values = []
        for title, default in (("Category", "environment"), ("Group", "main"), ("Assembly", ""), ("Variant", "default")):
            value, ok = QtWidgets.QInputDialog.getText(self, "New Assembly", title, text=default)
            if not ok: return
            values.append(value.strip())
        if not all(values): return
        identity = AssemblyIdentity(*values)
        self.service.create_assembly(identity); self.refresh()

    def load(self):
        self.current = self.assemblies.currentData()
        self.table.setRowCount(0)
        if not self.current: return
        data = self.service.load_composition(self.current)
        self.purpose.setCurrentText(str(data.get("purpose") or "layout"))
        for member in data.get("members") or []: self.add_member(member)

    def add_member(self, data=None):
        data = data if isinstance(data, dict) else {}
        row = self.table.rowCount(); self.table.insertRow(row)
        values = [data.get(k, "") for k in ("uid", "entity_type", "entity_id", "variant", "version", "namespace", "purpose")]
        values.append(json.dumps(data.get("transform") or {}, ensure_ascii=False))
        values[1] = values[1] or "asset"; values[3] = values[3] or "default"; values[6] = values[6] or "render"
        for column, value in enumerate(values): self.table.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))

    def remove_member(self):
        rows = sorted({item.row() for item in self.table.selectedItems()}, reverse=True)
        for row in rows: self.table.removeRow(row)

    def members(self):
        keys = ("uid", "entity_type", "entity_id", "variant", "version", "namespace", "purpose")
        members = []
        for row in range(self.table.rowCount()):
            values = {key: (self.table.item(row, col).text().strip() if self.table.item(row, col) else "") for col, key in enumerate(keys)}
            transform_text = self.table.item(row, 7).text().strip() if self.table.item(row, 7) else "{}"
            values["transform"] = json.loads(transform_text or "{}")
            members.append(AssemblyMember(**values))
        return members

    def save(self):
        if not self.current: return
        try:
            path = self.service.save_composition(self.current, self.members(), purpose=self.purpose.currentText()); self.log.appendPlainText(f"Saved: {path}")
        except Exception as exc: QtWidgets.QMessageBox.critical(self, "Save failed", str(exc))

    def validate(self):
        if not self.current: return
        self.save(); issues = self.service.validate(self.current)
        self.log.appendPlainText("Validation OK" if not issues else "\n".join(f"{x.severity} {x.code}: {x.message}" for x in issues))

    def publish(self):
        if not self.current: return
        try:
            self.save(); path = self.service.publish(self.current); self.log.appendPlainText(f"Published: {path}")
        except Exception as exc: QtWidgets.QMessageBox.critical(self, "Publish failed", str(exc))

    def construct(self):
        if not self.current: return
        try:
            self.save(); path = self.service.construct_maya(self.current); self.log.appendPlainText(f"Constructed: {path}")
        except Exception as exc: QtWidgets.QMessageBox.critical(self, "Construct failed", str(exc))


def main(argv=None):
    parser = argparse.ArgumentParser(); parser.add_argument("--config-dir", required=True); args = parser.parse_args(argv)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    window = AssemblyManagerWindow(Path(args.config_dir)); window.show(); return app.exec()


if __name__ == "__main__": raise SystemExit(main())

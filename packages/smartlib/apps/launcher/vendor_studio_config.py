from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from PySide6 import QtCore, QtWidgets


class ConfigCreatorApp(QtWidgets.QMainWindow):
    config_saved = QtCore.Signal()

    def __init__(self, target_project=None, config_mode=None):
        super().__init__()
        self.setWindowTitle("Vendor Studio Settings")
        self._path = Path(
            os.environ.get("SMARTPIPELINE_STUDIO_CONFIG")
            or Path(os.environ.get("SMARTPIPELINE_STUDIO_CONFIG_DIR", ".")) / "studio.yml"
        )
        self._build_ui()
        self._load()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        form = QtWidgets.QFormLayout(central)
        self.studio_id_input = QtWidgets.QLineEdit()
        self.studio_name_input = QtWidgets.QLineEdit()
        self.google_credentials_path_edit = QtWidgets.QLineEdit()
        self.google_credentials_path_edit.setPlaceholderText("%APPDATA%/credentials.json")
        credentials = QtWidgets.QHBoxLayout()
        credentials.addWidget(self.google_credentials_path_edit, 1)
        browse = QtWidgets.QPushButton("Browse")
        browse.clicked.connect(self._browse)
        credentials.addWidget(browse)
        form.addRow("Studio ID:", self.studio_id_input)
        form.addRow("Studio Name:", self.studio_name_input)
        form.addRow("Role:", QtWidgets.QLabel("Vendor"))
        form.addRow("Google Credentials File:", credentials)
        note = QtWidgets.QLabel(
            "Role is fixed to Vendor. Only the credential path is saved; the file is not copied."
        )
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch()
        save = QtWidgets.QPushButton("Save")
        save.clicked.connect(self.save_config)
        buttons.addWidget(save)
        form.addRow(buttons)

    def _load_data(self):
        if not self._path.is_file():
            return {}
        with self._path.open("r", encoding="utf-8") as stream:
            return yaml.safe_load(stream) or {}

    def _load(self):
        data = self._load_data()
        studio = data.get("studio") or {}
        self.studio_id_input.setText(str(studio.get("id") or ""))
        self.studio_name_input.setText(str(studio.get("name") or ""))
        self.google_credentials_path_edit.setText(
            str((data.get("google_sheets") or {}).get("credentials_path") or "")
        )

    def _browse(self):
        current = os.path.expandvars(self.google_credentials_path_edit.text().strip())
        path, _selected = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select Google Credentials File", current, "JSON Files (*.json)"
        )
        if path:
            self.google_credentials_path_edit.setText(path.replace("\\", "/"))

    def save_config(self):
        studio_id = self.studio_id_input.text().strip()
        studio_name = self.studio_name_input.text().strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", studio_id):
            QtWidgets.QMessageBox.warning(
                self, "Invalid Studio ID", "Use lowercase letters, numbers, '_' or '-'."
            )
            return
        if not studio_name:
            QtWidgets.QMessageBox.warning(self, "Invalid Studio Name", "Studio Name is required.")
            return
        data = self._load_data()
        data.setdefault("schema", "smartpipeline.studio.v1")
        data["studio"] = {"id": studio_id, "name": studio_name, "role": "vendor"}
        google = dict(data.get("google_sheets") or {})
        credential = self.google_credentials_path_edit.text().strip()
        if credential:
            google["credentials_path"] = credential
        else:
            google.pop("credentials_path", None)
        if google:
            data["google_sheets"] = google
        else:
            data.pop("google_sheets", None)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8", newline="\n") as stream:
            yaml.safe_dump(data, stream, sort_keys=False, allow_unicode=True)
        self.config_saved.emit()
        QtWidgets.QMessageBox.information(self, "Saved", "Vendor studio settings saved.")
        self.close()

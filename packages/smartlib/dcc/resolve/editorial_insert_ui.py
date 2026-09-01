from __future__ import annotations

from typing import Any

from smartlib.core.config_loader import ProjectConfig
from smartlib.dcc.resolve.editorial_insert import InsertRequest, export_editorial_insert


_WINDOW = None


def show(*, config_dir: str, resolve_app: Any) -> Any:
    global _WINDOW
    try:
        from PySide6 import QtWidgets
    except ImportError:
        from PySide2 import QtWidgets
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    timeline = resolve_app.GetProjectManager().GetCurrentProject().GetCurrentTimeline()
    default_episode = _episode_from_timeline(timeline.GetName() if timeline else "")
    dialog = QtWidgets.QDialog()
    dialog.setWindowTitle("Editorial Insert Export")
    form = QtWidgets.QFormLayout(dialog)
    episode = QtWidgets.QLineEdit(default_episode)
    sequence = QtWidgets.QLineEdit(f"{default_episode}01" if default_episode else "op01")
    head = QtWidgets.QSpinBox(); head.setRange(0, 999); head.setValue(8)
    tail = QtWidgets.QSpinBox(); tail.setRange(0, 999); tail.setValue(8)
    form.addRow("Episode / Unit", episode)
    form.addRow("Production Sequence", sequence)
    form.addRow("Head Handle", head)
    form.addRow("Tail Handle", tail)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)
    _WINDOW = dialog
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return None
    result = export_editorial_insert(
        resolve_app=resolve_app,
        project_config=ProjectConfig(config_dir),
        request=InsertRequest(
            episode=episode.text().strip().lower(),
            production_sequence=sequence.text().strip().lower(),
            head_handle=head.value(), tail_handle=tail.value(),
        ),
    )
    QtWidgets.QMessageBox.information(dialog, "Editorial Insert Export", f"Completed:\n{result}")
    return result


def _episode_from_timeline(name: str) -> str:
    value = str(name or "").strip().lower()
    return value.rsplit("_", 1)[-1] if "_" in value else value

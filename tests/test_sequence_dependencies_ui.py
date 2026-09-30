"""Exercise the real Dependencies tab with Qt and disposable project data."""
import ast
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtCore, QtGui, QtWidgets

from smartlib.apps.shot_manager.service import SequenceIdentity, ShotIdentity, ShotManagerService
from test_shot_dependencies import _ProjectConfig


def test_sequence_assignment_versions_clear_and_shot_isolation(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    source = Path(__file__).parents[1] / "scripts/shot_manager_ui.py"
    cls = next(n for n in ast.parse(source.read_text(encoding="utf-8-sig")).body
               if isinstance(n, ast.ClassDef) and n.name == "ShotManagerWindow")
    names = {
        "_setup_dependencies_tab", "_dependency_identity", "populate_dependencies",
        "_selected_dependency_assignment", "_dependency_scope_label", "_dependency_role_label",
        "show_add_assignment_menu", "populate_dependency_candidates", "_selected_dependency_candidate",
        "_update_dependency_inspector", "assign_selected_candidate", "remove_dependency", "preview_dependency",
    }
    scope = dict(Path=Path, QtWidgets=QtWidgets, QtCore=QtCore, QtGui=QtGui,
                 _ensure_smartlib_on_path=lambda: None)
    exec(compile(ast.Module(body=[n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names],
                            type_ignores=[]), str(source), "exec"), scope)
    Harness = type("Harness", (QtWidgets.QWidget,), {name: scope[name] for name in names})
    widget = Harness()
    widget.tabs = QtWidgets.QTabWidget(widget)
    widget.dependencies_tab = QtWidgets.QWidget()
    widget.dependencies_tree = QtWidgets.QTreeWidget()
    widget.status_label = QtWidgets.QLabel()
    widget.active_sequence_identity = sequence = SequenceIdentity("ep02", "s027")
    widget.active_shot_identity = None
    shot = ShotIdentity("ep02", "s027", "c001")
    service = widget.service = ShotManagerService(_ProjectConfig(tmp_path))
    service.sequence_shot_identities = lambda _: [shot]
    service.load_sequence_cast = lambda *_: {"cast": {"DLI": {"asset": "DLI", "category": "character"}}}
    for data_type, representation, subset in [("mocap", "fbx", "dli"), ("virtual_camera", "fbx", "take06")]:
        for version in ("v001", "v002"):
            folder = service.paths.sequence_data_version_dir("ep02", "s027", data_type, representation, subset, version)
            folder.mkdir(parents=True)
            (folder / "motion.fbx").write_bytes(b"fbx")
    widget._setup_dependencies_tab()
    widget.populate_dependencies()
    assert widget.dependencies_tree.topLevelItemCount() == 2
    assert widget.dependencies_tree.topLevelItem(0).text(0) == "Sequence ep02 / s027"

    def assign(scope_index, slot_index, version):
        widget.dependencies_tree.setCurrentItem(widget.dependencies_tree.topLevelItem(scope_index).child(slot_index))
        assert widget.dependency_candidates.rowCount() == 2
        row = next(row for row in range(2) if widget.dependency_candidates.item(row, 6).text() == version)
        widget.dependency_candidates.selectRow(row)
        widget.assign_selected_candidate()

    errors = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", lambda *args: errors.append(args))
    assign(0, 0, "v001")
    assign(0, 0, "v002")
    assign(0, 1, "v002")
    assign(1, 0, "v001")
    assert not errors
    entries = service.load_dependencies(sequence)["dependencies"]
    selected = [row for row in entries if row["status"] == "selected"]
    assert {(row["type"], row["version"]) for row in selected} == {("mocap", "v002"), ("virtual_camera", "v002")}
    assert len({row["id"] for row in entries}) == 3
    assert service.load_dependencies(shot)["dependencies"][0]["version"] == "v001"
    widget.populate_dependencies()
    assert "v002" in widget.dependencies_tree.topLevelItem(0).child(0).text(3)
    widget.dependencies_tree.setCurrentItem(widget.dependencies_tree.topLevelItem(0).child(0))
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *args: QtWidgets.QMessageBox.Yes)
    widget.remove_dependency()
    assert not errors
    assert not any(row["type"] == "mocap" and row["status"] == "selected"
                   for row in service.load_dependencies(sequence)["dependencies"])
    assert service.load_dependencies(shot)["dependencies"][0]["status"] == "selected"
    widget.active_shot_identity = shot
    widget.populate_dependencies()
    assert widget.dependencies_tree.topLevelItemCount() == 1
    assert widget.dependencies_tree.topLevelItem(0).text(0) == "c001"
    widget.close()

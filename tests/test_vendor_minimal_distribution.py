import argparse
from pathlib import Path

import yaml
from PySide6 import QtWidgets

from scripts.build_vendor_distribution import build_distribution, copy_vendor_library


ROOT = Path(__file__).resolve().parents[1]


def test_vendor_distribution_contains_only_required_source(tmp_path: Path):
    source_library = tmp_path / "source-library"
    copy_vendor_library(ROOT, source_library)
    projects = tmp_path / "source-projects"
    projects.mkdir()
    (projects / "studio.yml").write_text(
        yaml.safe_dump({"studio": {"id": "vendor_a", "name": "Vendor A", "role": "vendor"}}),
        encoding="utf-8",
    )
    output = tmp_path / "output"
    args = argparse.Namespace(
        source_library=source_library,
        source_projects=projects,
        source_tools=tmp_path / "unused-tools",
        output_root=output,
        tools_mode="external",
        projects="studio",
        studio_id="vendor_a",
        studio_name="Vendor A",
        studio_role="vendor",
        replace=False,
    )
    build_distribution(args)
    library = output / "smartlibrary"

    assert (library / "packages/smartlib/apps/launcher/main.py").is_file()
    assert (library / "packages/smartlib/apps/launcher/vendor_studio_config.py").is_file()
    assert (library / "packages/smartlib/apps/smart_delivery/service.py").is_file()
    assert (library / "packages/smartlib/dcc/maya/preflight.py").is_file()
    assert not (library / "scripts").exists()
    assert not (library / "packages/smartlib/apps/asset_manager").exists()
    assert not (library / "packages/smartlib/dcc/houdini").exists()
    assert not (library / "packages/smartlib/dcc/resolve").exists()


def test_vendor_studio_role_is_forced_when_overlaying(tmp_path: Path):
    from scripts.build_vendor_distribution import _overlay_studio_identity

    path = tmp_path / "studio.yml"
    path.write_text("studio:\n  id: internal\n  name: Internal\n  role: internal\n", encoding="utf-8")
    _overlay_studio_identity(path, "vendor_a", "Vendor A", "vendor")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["studio"]["role"] == "vendor"


def test_vendor_settings_has_no_role_switch_and_always_saves_vendor(
    tmp_path: Path, monkeypatch
):
    from smartlib.apps.launcher.vendor_studio_config import ConfigCreatorApp

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = tmp_path / "studio.yml"
    monkeypatch.setenv("SMARTPIPELINE_STUDIO_CONFIG", str(path))
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *args: None)
    window = ConfigCreatorApp(config_mode="internal")
    assert not hasattr(window, "studio_role_combo")
    window.studio_id_input.setText("vendor_a")
    window.studio_name_input.setText("Vendor A")
    window.save_config()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["studio"]["role"] == "vendor"
    window.deleteLater()
    app.processEvents()

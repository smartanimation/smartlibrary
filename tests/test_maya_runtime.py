from pathlib import Path
from types import SimpleNamespace

import pytest

from smartlib.core import maya_runtime


def test_review_build_selection_is_shared_with_camera_worker(tmp_path):
    (tmp_path / "templates_base.yml").write_text(
        "enabled_softwares: [maya2024, maya2026]\nreview_build:\n  maya_software: maya2026\n",
        encoding="utf-8",
    )
    (tmp_path / "software_maya2024.yml").write_text("path: C:/Maya2024/bin/maya.exe\n", encoding="utf-8")
    (tmp_path / "software_maya2026.yml").write_text("path: C:/Maya2026/bin/maya.exe\n", encoding="utf-8")
    config = SimpleNamespace(
        config_dir=tmp_path,
        load=lambda name: __import__("yaml").safe_load((tmp_path / name).read_text(encoding="utf-8")),
    )

    assert maya_runtime.software_config_name(config) == "software_maya2026.yml"


def test_older_review_build_maya_is_rejected_for_camera_bake():
    with pytest.raises(ValueError, match="older than authoring Maya"):
        maya_runtime.validate_worker_version(
            Path("C:/Program Files/Autodesk/Maya2024/bin/mayapy.exe"), "2026"
        )


def test_same_or_newer_review_build_maya_is_allowed():
    maya_runtime.validate_worker_version(
        Path("C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe"), "Maya 2026"
    )

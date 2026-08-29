from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

import pytest

from scripts import build_vendor_distribution
from scripts.smarttools_runtime import iter_runtime_files, validate_zip_members


def test_runtime_file_selection_omits_python_cache(tmp_path):
    runtime = tmp_path / "smarttools"
    (runtime / "python" / "__pycache__").mkdir(parents=True)
    (runtime / "python" / "python.exe").write_bytes(b"python")
    (runtime / "python" / "__pycache__" / "module.pyc").write_bytes(b"cache")
    (runtime / "third_party").mkdir()
    (runtime / "third_party" / "module.py").write_text("value = 1\n", encoding="utf-8")

    selected = {
        relative.as_posix()
        for _, relative in iter_runtime_files(runtime, ["python", "third_party"])
    }

    assert selected == {
        "smarttools/python/python.exe",
        "smarttools/third_party/module.py",
    }


def test_runtime_archive_rejects_parent_traversal(tmp_path):
    artifact = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("smarttools/../outside.txt", "unsafe")

    with zipfile.ZipFile(artifact) as archive:
        with pytest.raises(ValueError, match="Unsafe runtime archive member"):
            validate_zip_members(archive)


def test_vendor_distribution_external_mode_preserves_smarttools(tmp_path, monkeypatch):
    library = tmp_path / "source" / "smartlibrary"
    projects = tmp_path / "source" / "smartprojects"
    output = tmp_path / "output"
    for name in build_vendor_distribution.LIBRARY_DIRS:
        (library / name).mkdir(parents=True)
    for name in build_vendor_distribution.LIBRARY_FILES:
        (library / name).write_text("test\n", encoding="utf-8")
    definition = library / "config" / "distribution" / "smarttools-runtime.yml"
    definition.parent.mkdir(parents=True, exist_ok=True)
    definition.write_text(
        "schema: smartpipeline.smarttools-runtime.v1\n"
        "version: 1.0.0\nplatform: windows-x64\n"
        "artifact: runtime.zip\n",
        encoding="utf-8",
    )
    projects.mkdir(parents=True)
    output.mkdir()
    existing_tools = output / "smarttools"
    existing_tools.mkdir()
    (existing_tools / "keep.txt").write_text("keep\n", encoding="utf-8")
    monkeypatch.setattr(build_vendor_distribution, "_git_version", lambda _root: "test")

    args = argparse.Namespace(
        source_library=library,
        source_projects=projects,
        source_tools=tmp_path / "missing-smarttools",
        output_root=output,
        tools_mode="external",
        projects="none",
        studio_id="vendor_test",
        studio_name="Vendor Test",
        studio_role="vendor",
        replace=False,
    )
    build_vendor_distribution.build_distribution(args)

    assert (existing_tools / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert not (output / "distribution.json").read_text(encoding="utf-8").find(
        '"smarttools": {'
    ) >= 0

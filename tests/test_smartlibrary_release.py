from __future__ import annotations

import zipfile
from pathlib import Path

from scripts.build_smartlibrary_release import build
from scripts.build_vendor_distribution import copy_vendor_library
from scripts.verify_smartlibrary_release import verify


def test_smartlibrary_release_is_small_and_references_external_runtime(tmp_path):
    source = tmp_path / "source"
    copy_vendor_library(Path(__file__).resolve().parents[1], source)
    (source / "tests").mkdir()
    (source / "tests" / "exclude.txt").write_text("exclude\n", encoding="utf-8")
    definition = source / "config" / "distribution" / "smarttools-runtime.yml"
    definition.parent.mkdir(parents=True, exist_ok=True)
    definition.write_text(
        "version: 1.0.0\nplatform: windows-x64\n"
        "artifact: smarttools-runtime-windows-x64-1.0.0.zip\n",
        encoding="utf-8",
    )

    artifact = build(source, tmp_path / "dist", "v1.2.3")
    manifest = verify(artifact, artifact.with_suffix(".zip.sha256"))

    assert manifest["version"] == "v1.2.3"
    assert manifest["smarttools_runtime"]["version"] == "1.0.0"
    with zipfile.ZipFile(artifact) as archive:
        assert "smartlibrary/tests/exclude.txt" not in archive.namelist()
        assert not any(name.startswith("smarttools/") for name in archive.namelist())

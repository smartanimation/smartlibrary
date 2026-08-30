import zipfile
from pathlib import Path

import pytest

from smartlib.apps.launcher.project_config_transfer import (
    export_project_config,
    import_project_config,
    inspect_project_config_archive,
)


def test_project_config_round_trip(tmp_path: Path):
    source = tmp_path / "internal" / "STKB"
    source.mkdir(parents=True)
    (source / "templates_base.yml").write_text("anchors: {}\n", encoding="utf-8")
    (source / "contexts").mkdir()
    (source / "contexts" / "shot.yml").write_text("name: shot\n", encoding="utf-8")
    archive = export_project_config(source, tmp_path / "STKB-smartproject.zip")

    assert inspect_project_config_archive(archive)["project"] == "STKB"
    installed = import_project_config(archive, tmp_path / "vendor" / "smartprojects" / "config")
    assert (installed / "templates_base.yml").read_text(encoding="utf-8") == "anchors: {}\n"
    assert (installed / "contexts" / "shot.yml").is_file()


def test_export_excludes_credentials_and_cache(tmp_path: Path):
    source = tmp_path / "STKB"
    source.mkdir()
    (source / "templates_base.yml").write_text("anchors: {}\n", encoding="utf-8")
    (source / "credentials.json").write_text("secret", encoding="utf-8")
    (source / ".cache").mkdir()
    (source / ".cache" / "data.yml").write_text("cached", encoding="utf-8")
    archive = export_project_config(source, tmp_path / "project.zip")

    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
    assert not any("credentials.json" in name or "/.cache/" in name for name in names)


def test_export_rejects_a_config_with_only_sensitive_files(tmp_path: Path):
    source = tmp_path / "STKB"
    source.mkdir()
    (source / "secrets.yml").write_text("secret", encoding="utf-8")
    archive = tmp_path / "project.zip"

    with pytest.raises(ValueError, match="exportable"):
        export_project_config(source, archive)
    assert not archive.exists()


def test_import_refuses_to_replace_existing_project(tmp_path: Path):
    source = tmp_path / "source" / "STKB"
    source.mkdir(parents=True)
    (source / "templates_base.yml").write_text("anchors: {}\n", encoding="utf-8")
    archive = export_project_config(source, tmp_path / "project.zip")
    projects = tmp_path / "smartprojects" / "config"
    (projects / "STKB").mkdir(parents=True)

    with pytest.raises(FileExistsError):
        import_project_config(archive, projects)


def test_import_rejects_unsafe_archive_member(tmp_path: Path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(
            "smartproject-manifest.json",
            '{"schema":"smartpipeline.project-config.v1","project":"STKB"}',
        )
        bundle.writestr("smartprojects/config/STKB/../../escape.yml", "bad")

    with pytest.raises(ValueError, match="Unsafe"):
        inspect_project_config_archive(archive)

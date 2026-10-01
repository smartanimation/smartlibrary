from pathlib import Path

import pytest

from smartlib.apps.launcher.after_effects_compat import (
    _validated_rename,
    detect_camera_raw_issue,
    is_after_effects_2024,
)


def _adobe_files(tmp_path: Path):
    directory = tmp_path / "Common Files" / "Adobe" / "Plug-Ins" / "CC" / "File Formats"
    directory.mkdir(parents=True)
    plugin = directory / "Camera Raw.8bi"
    machine_learning = directory / "Microsoft.AI.MachineLearning.dll"
    plugin.write_bytes(b"plugin")
    machine_learning.write_bytes(b"ml")
    return plugin, machine_learning


def test_identifies_after_effects_2024_only():
    assert is_after_effects_2024(
        "AfterEffects2024",
        r"C:\Program Files\Adobe\Adobe After Effects 2024\Support Files\AfterFX.exe",
    )
    assert not is_after_effects_2024(
        "AfterEffects2025",
        r"C:\Program Files\Adobe\Adobe After Effects 2025\Support Files\AfterFX.exe",
    )


def test_detects_known_camera_raw_crash_component(tmp_path):
    plugin, machine_learning = _adobe_files(tmp_path)

    def version_reader(path):
        if Path(path) == machine_learning:
            return (1, 24, 4, 0)
        return (18, 7, 0, 0)

    issue = detect_camera_raw_issue(
        "AfterEffects2024",
        r"C:\Adobe\After Effects 2024\AfterFX.exe",
        environ={"ProgramFiles": str(tmp_path)},
        version_reader=version_reader,
    )

    assert issue is not None
    assert issue.plugin == plugin
    assert issue.backup.name == "Camera Raw.8bi.disabled-18.7"


def test_ignores_future_machine_learning_component(tmp_path):
    _adobe_files(tmp_path)
    issue = detect_camera_raw_issue(
        "AfterEffects2024",
        r"C:\Adobe\After Effects 2024\AfterFX.exe",
        environ={"ProgramFiles": str(tmp_path)},
        version_reader=lambda _path: (1, 25, 0, 0),
    )
    assert issue is None


def test_validated_rename_preserves_plugin(tmp_path):
    plugin, _machine_learning = _adobe_files(tmp_path)
    backup = plugin.with_name("Camera Raw.8bi.disabled-18.7")

    _validated_rename(plugin, backup)

    assert not plugin.exists()
    assert backup.read_bytes() == b"plugin"


def test_validated_rename_rejects_unrelated_file(tmp_path):
    source = tmp_path / "unrelated.dll"
    source.write_bytes(b"data")
    with pytest.raises(ValueError):
        _validated_rename(source, tmp_path / "unrelated.dll.disabled-1")

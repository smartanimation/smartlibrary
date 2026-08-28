from pathlib import Path

import pytest

from smartlib.apps.shot_manager.service import (
    SequenceIdentity,
    ShotIdentity,
    ShotManagerService,
    validate_dependencies_data,
)


class _ProjectConfig:
    def __init__(self, root: Path):
        self.project_root = root
        self.project_name = "TEST"
        self.templates = {}
        self.config_dir = root / "settings"
        self.base = {"anchors": {"project_name": "TEST", "fps": 24}}


def _dependencies():
    return {
        "dependencies": [
            {
                "id": "take_001", "type": "mocap", "role": "body_motion",
                "asset": "DLI", "target": "DLI", "source": "package://mocap/DLI/take_001",
                "representation": "fbx", "status": "selected",
            },
            {
                "id": "take_004", "type": "mocap", "role": "body_motion",
                "asset": "DLI", "target": "DLI", "source": "package://mocap/DLI/take_004",
                "representation": "fbx", "status": "alternate",
            },
        ]
    }


def test_shot_and_sequence_dependencies_use_separate_files(tmp_path):
    service = ShotManagerService(_ProjectConfig(tmp_path))
    shot = ShotIdentity("ep02", "s027", "c001")
    sequence = SequenceIdentity("ep02", "s027")

    shot_path = service.write_dependencies(shot, _dependencies())
    sequence_path = service.write_dependencies(sequence, {"dependencies": []})

    assert shot_path == service.shot_root(shot) / "dependencies.json"
    assert sequence_path == service.sequence_workspace_root("ep02", "s027") / "dependencies.json"
    assert service.load_dependencies(shot)["shot"] == "c001"
    assert service.load_dependencies(sequence)["shot"] == "s027"


def test_set_selected_demotes_same_type_and_role(tmp_path):
    service = ShotManagerService(_ProjectConfig(tmp_path))
    shot = ShotIdentity("ep02", "s027", "c001")
    service.write_dependencies(shot, _dependencies())

    service.set_selected_dependency(shot, "take_004")

    statuses = {item["id"]: item["status"] for item in service.load_dependencies(shot)["dependencies"]}
    assert statuses == {"take_001": "alternate", "take_004": "selected"}


def test_dependencies_validation_rejects_duplicate_ids_and_selected_group():
    data = _dependencies()
    data["schema_version"] = 1
    data["dependencies"][1]["id"] = "take_001"
    data["dependencies"][1]["status"] = "selected"

    issues = validate_dependencies_data(data)

    assert "duplicate dependency id: take_001" in issues
    assert "multiple selected dependencies for target/type/role: DLI/mocap/body_motion" in issues


def test_write_dependencies_rejects_unknown_type(tmp_path):
    service = ShotManagerService(_ProjectConfig(tmp_path))
    shot = ShotIdentity("ep02", "s027", "c001")
    data = _dependencies()
    data["dependencies"][0]["type"] = "unknown"

    with pytest.raises(ValueError, match="unsupported dependency type"):
        service.write_dependencies(shot, data)


def test_selected_is_independent_per_target(tmp_path):
    service = ShotManagerService(_ProjectConfig(tmp_path))
    shot = ShotIdentity("ep02", "s027", "c001")
    data = _dependencies()
    data["dependencies"][1].update({"id": "jin_take", "target": "JIN", "asset": "JIN", "status": "selected"})

    service.write_dependencies(shot, data)

    assert len([item for item in service.load_dependencies(shot)["dependencies"] if item["status"] == "selected"]) == 2


def test_sequence_input_candidates_discovers_cast_target_and_vcam_take(tmp_path):
    service = ShotManagerService(_ProjectConfig(tmp_path))
    root = service.sequence_workspace_root("ep02", "s027") / "data"
    mocap = root / "mocap" / "fbx" / "DLI" / "v001" / "body.fbx"
    vcam = root / "virtual_camera" / "take30" / "v001" / "camera.fbx"
    vcam_mov = root / "virtual_camera" / "take30" / "v001" / "camera.mov"
    mocap.parent.mkdir(parents=True)
    vcam.parent.mkdir(parents=True)
    mocap.write_bytes(b"fbx")
    vcam.write_bytes(b"fbx")
    vcam_mov.write_bytes(b"mov")

    rows = service.sequence_input_candidates(SequenceIdentity("ep02", "s027"))

    assert any(row["type"] == "mocap" and row["target"] == "DLI" for row in rows)
    virtual_camera = next(row for row in rows if row["type"] == "virtual_camera" and row["name"] == "take30")
    assert virtual_camera["representation"] == "fbx"
    assert virtual_camera["role"] == "import_fbx"
    assert virtual_camera["mode"] == "import"
    assert not any(row["type"] == "virtual_camera" and row["representation"] == "mov" for row in rows)


def test_selected_virtual_camera_dependency_resolves_fbx_only(tmp_path):
    service = ShotManagerService(_ProjectConfig(tmp_path))
    shot = ShotIdentity("ep02", "s027", "c001")
    selected_fbx = tmp_path / "shots" / "camera" / "take30" / "v001" / "camera.fbx"
    alternate_fbx = selected_fbx.with_name("alternate.fbx")
    selected_mov = selected_fbx.with_name("camera.mov")
    selected_fbx.parent.mkdir(parents=True)
    selected_fbx.write_bytes(b"fbx")
    alternate_fbx.write_bytes(b"fbx")
    selected_mov.write_bytes(b"mov")
    service.write_dependencies(
        shot,
        {
            "dependencies": [
                {
                    "id": "vcam_selected", "name": "take30", "type": "virtual_camera",
                    "role": "import_fbx", "source": str(selected_fbx),
                    "representation": "fbx", "status": "selected", "target": "Camera",
                },
                {
                    "id": "vcam_alternate", "name": "take31", "type": "virtual_camera",
                    "role": "preview", "source": str(alternate_fbx),
                    "representation": "fbx", "status": "alternate", "target": "Camera",
                },
                {
                    "id": "vcam_mov", "name": "take32", "type": "virtual_camera",
                    "role": "preview", "source": str(selected_mov),
                    "representation": "mov", "status": "selected", "target": "Camera",
                },
            ]
        },
    )

    resolved = service.selected_virtual_camera_dependencies(shot)

    assert [item["id"] for item in resolved] == ["vcam_selected"]
    assert resolved[0]["path"] == str(selected_fbx)

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from smartlib.dcc.maya import shot_builder


class CameraCmds:
    def __init__(self) -> None:
        self.attrs = []
        self.frames = []
        self.matrices = []

    def objExists(self, _name):
        return True

    def delete(self, _name):
        return None

    def camera(self, name):
        return name, f"{name}Shape"

    def setAttr(self, plug, value, **_kwargs):
        self.attrs.append((plug, value))

    def currentTime(self, frame, edit=False):
        self.frames.append(frame)

    def xform(self, node, **kwargs):
        self.matrices.append((node, kwargs["matrix"]))

    def setKeyframe(self, *_args, **_kwargs):
        return None


def test_create_camera_supports_maya_camera_v1_samples(tmp_path: Path) -> None:
    camera_json = tmp_path / "camera.json"
    camera_json.write_text(
        json.dumps(
            {
                "schema": "maya_camera/v1",
                "camera": "cam_CHA_baked",
                "shape_attributes": {"focalLength": 35.0},
                "samples": [{"frame": 10, "world_matrix": list(range(16))}],
            }
        ),
        encoding="utf-8",
    )
    cmds = CameraCmds()

    camera = shot_builder._create_camera_from_json(
        cmds, camera_json, {"shot": "c001"}, 5.0
    )

    assert camera == "c001_anim_cam"
    assert ("c001_anim_camShape.focalLength", 35.0) in cmds.attrs
    assert 15.0 in cmds.frames
    assert cmds.matrices == [("c001_anim_cam", list(range(16)))]


def test_camera_scene_resolver_prefers_registered_maya_file(tmp_path: Path) -> None:
    camera_json = tmp_path / "camera.json"
    camera_ma = tmp_path / "cam_main.ma"
    camera_ma.write_text("// maya", encoding="utf-8")
    camera_json.write_text(
        json.dumps({"files": {"ma": camera_ma.name}}), encoding="utf-8"
    )

    assert shot_builder._camera_scene_from_publish(camera_json) == camera_ma


def test_stage_anim_does_not_report_reconstructed_camera_as_reference(
    tmp_path: Path, monkeypatch
) -> None:
    camera_json = tmp_path / "camera.json"
    camera_json.write_text("{}", encoding="utf-8")
    anim_input = tmp_path / "anim_input.json"
    anim_input.write_text(
        json.dumps({"shot": "c001", "camera": "camera.json"}),
        encoding="utf-8",
    )
    fake_cmds = SimpleNamespace(
        file=lambda *_args, **_kwargs: None,
        parent=lambda *_args, **_kwargs: None,
    )
    maya = ModuleType("maya")
    maya.cmds = fake_cmds
    monkeypatch.setitem(sys.modules, "maya", maya)
    monkeypatch.setitem(sys.modules, "maya.cmds", fake_cmds)
    monkeypatch.setattr(shot_builder, "resolve_shot_work_template", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(shot_builder, "_apply_scene_policy", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(shot_builder, "_apply_shot_timing", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(shot_builder, "build_shot_from_preview", lambda *_args, **_kwargs: ["rig.ma"])
    monkeypatch.setattr(shot_builder, "_create_camera_from_json", lambda *_args, **_kwargs: "camera")
    monkeypatch.setattr(shot_builder, "_ensure_group", lambda *_args, **_kwargs: "camera_grp")
    monkeypatch.setattr(shot_builder, "_apply_anim_placements", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(shot_builder, "_load_layout_overlay_usd", lambda *_args, **_kwargs: "")

    references = shot_builder.stage_anim_from_input(
        [], anim_input, {}, project_root=tmp_path
    )

    assert references == ["rig.ma"]
    assert str(camera_json) not in references


def test_stage_shot_applies_construct_cameras_for_layout(
    tmp_path: Path, monkeypatch
) -> None:
    fake_cmds = SimpleNamespace(file=lambda *_args, **_kwargs: None)
    maya = ModuleType("maya")
    maya.cmds = fake_cmds
    monkeypatch.setitem(sys.modules, "maya", maya)
    monkeypatch.setitem(sys.modules, "maya.cmds", fake_cmds)
    monkeypatch.setattr(
        shot_builder, "resolve_shot_work_template", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(shot_builder, "_apply_scene_policy", lambda *_args: None)
    monkeypatch.setattr(
        shot_builder, "build_shot_from_preview", lambda *_args: ["rig.ma"]
    )
    applied = []
    monkeypatch.setattr(
        shot_builder,
        "_apply_construct_cameras",
        lambda cmds, root, shot_data, construct, offset: applied.append(
            (cmds, root, shot_data, construct, offset)
        ),
    )
    curve_applied = []
    monkeypatch.setattr(
        shot_builder,
        "_apply_construct_animation_curves",
        lambda root, value: curve_applied.append((root, value)),
    )
    construct = {
        "components": [
            {
                "component_type": "camera",
                "name": "take30",
                "source": {"kind": "shot_dependency"},
            }
        ]
    }
    shot_data = {"shot": "c001"}

    references = shot_builder.stage_shot_from_preview(
        [],
        shot_data,
        department="layout",
        project_root=tmp_path,
        construct_data=construct,
    )

    assert references == ["rig.ma"]
    assert applied == [(fake_cmds, tmp_path, shot_data, construct, 0.0)]
    assert curve_applied == [(tmp_path, construct)]


def test_enabled_direct_cameras_are_reconstructed_when_primary_is_disabled(
    tmp_path: Path, monkeypatch
) -> None:
    camera_a = tmp_path / "cam_A" / "camera.json"
    camera_b = tmp_path / "cam_B" / "camera.json"
    camera_a.parent.mkdir(parents=True)
    camera_b.parent.mkdir(parents=True)
    camera_a.write_text("{}", encoding="utf-8")
    camera_b.write_text("{}", encoding="utf-8")
    created = []
    renamed = []
    cmds = SimpleNamespace(
        ls=lambda *_args, **_kwargs: [],
        delete=lambda *_args, **_kwargs: None,
        parent=lambda camera, group: [f"|{group}|{camera}1"],
        rename=lambda camera, name: renamed.append((camera, name)) or name,
    )
    monkeypatch.setattr(
        shot_builder,
        "_create_camera_from_json",
        lambda _cmds, path, _input, _offset, camera_name="": created.append(
            (path, camera_name)
        ) or camera_name,
    )
    monkeypatch.setattr(shot_builder, "_ensure_group", lambda *_args: "camera_grp")
    construct = {
        "components": [
            {
                "component_type": "camera",
                "name": "camera",
                "path": str(camera_a),
                "enabled": False,
                "source": {"field": "camera"},
            },
            {
                "component_type": "camera",
                "name": "cam_A",
                "path": str(camera_a),
                "enabled": True,
                "source": {"kind": "published_camera"},
            },
            {
                "component_type": "camera",
                "name": "cam_B",
                "path": str(camera_b),
                "enabled": True,
                "source": {"kind": "published_camera"},
            },
        ]
    }

    result = shot_builder._apply_construct_cameras(
        cmds,
        tmp_path,
        {"camera": str(camera_a)},
        construct,
        0.0,
    )

    assert result == ["cam_A", "cam_B"]
    assert created == [(camera_a, "cam_A"), (camera_b, "cam_B")]
    assert renamed == [
        ("|camera_grp|cam_A1", "cam_A"),
        ("|camera_grp|cam_B1", "cam_B"),
    ]


def test_animation_package_curve_dependencies_are_applied(tmp_path: Path, monkeypatch) -> None:
    curve_path = tmp_path / "curve.json"
    curve_path.write_text("{}", encoding="utf-8")
    manifest_path = tmp_path / "animation_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "casts": {
                    "DLI_main": {
                        "namespace": "DLI",
                        "curve_dependency": {"path": "curve.json"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    applied = []
    from smartlib.dcc.maya import animation_curves

    monkeypatch.setattr(
        animation_curves,
        "apply_animation_curves_from_file",
        lambda path, **kwargs: applied.append((Path(path), kwargs)) or {"ok": True},
    )
    construct = {
        "components": [
            {
                "component_type": "animation",
                "name": "animation_package",
                "path": str(manifest_path),
                "enabled": True,
                "source": {"kind": "published_animation_package"},
            }
        ]
    }

    reports = shot_builder._apply_construct_animation_curves(tmp_path, construct)

    assert reports == [{"ok": True}]
    assert applied == [(
        curve_path,
        {
            "namespace": "DLI",
            "clear_existing": True,
            "strict_destinations": False,
        },
    )]


def test_direct_animation_curve_data_takes_precedence_over_cache_package(
    tmp_path: Path, monkeypatch
) -> None:
    curve_path = tmp_path / "DLI_main" / "animation_curve.json"
    curve_path.parent.mkdir(parents=True)
    curve_path.write_text("{}", encoding="utf-8")
    applied = []
    from smartlib.dcc.maya import animation_curves

    monkeypatch.setattr(
        animation_curves,
        "apply_animation_curves_from_file",
        lambda path, **kwargs: applied.append((Path(path), kwargs)) or {"ok": True},
    )
    construct = {
        "components": [
            {
                "component_type": "animation_curve",
                "name": "DLI_main",
                "path": str(curve_path),
                "enabled": True,
                "source": {
                    "kind": "animation_curve_data",
                    "namespace": "DLI",
                },
            },
            {
                "component_type": "animation",
                "name": "animation_package",
                "path": str(tmp_path / "missing_manifest.json"),
                "enabled": True,
                "source": {"kind": "published_animation_package"},
            },
        ]
    }

    reports = shot_builder._apply_construct_animation_curves(tmp_path, construct)

    assert reports == [{"ok": True}]
    assert applied == [(
        curve_path,
        {
            "namespace": "DLI",
            "clear_existing": True,
            "strict_destinations": False,
        },
    )]


def test_animation_curve_component_fails_when_all_destinations_are_missing(
    tmp_path: Path, monkeypatch
) -> None:
    curve_path = tmp_path / "DLI_main" / "animation_curve.json"
    curve_path.parent.mkdir(parents=True)
    curve_path.write_text("{}", encoding="utf-8")
    from smartlib.dcc.maya import animation_curves

    monkeypatch.setattr(
        animation_curves,
        "apply_animation_curves_from_file",
        lambda *_args, **_kwargs: {
            "applied_destinations": 0,
            "applied_static_values": 0,
            "skipped_destinations": 0,
            "missing_destinations": 209,
        },
    )
    construct = {
        "components": [
            {
                "component_type": "animation_curve",
                "name": "DLI_main",
                "path": str(curve_path),
                "enabled": True,
                "source": {"namespace": "DLI"},
            }
        ]
    }

    with pytest.raises(RuntimeError, match="209 destinations missing"):
        shot_builder._apply_construct_animation_curves(tmp_path, construct)


def test_construct_uses_usd_only_for_enabled_usd_components():
    assert not shot_builder._construct_uses_usd(None)
    assert not shot_builder._construct_uses_usd(
        {"components": [{"component_type": "rig", "enabled": True}]}
    )
    assert not shot_builder._construct_uses_usd(
        {"components": [{"component_type": "usd", "enabled": False}]}
    )
    assert shot_builder._construct_uses_usd(
        {"components": [{"component_type": "usd", "enabled": True}]}
    )

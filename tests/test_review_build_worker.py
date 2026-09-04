from pathlib import Path
from types import SimpleNamespace

from smartlib.apps.review_build_manager import worker
from smartlib.apps.shot_manager.service import BuildPreviewItem
from smartlib.core.config_loader import ProjectConfig
from smartlib.review import ae


def test_review_render_backend_keeps_hardware_and_gui_explicit() -> None:
    assert worker._review_render_backend({}) == "maya_hardware2"
    assert worker._review_render_backend({"renderer": "maya_hardware2"}) == "maya_hardware2"
    assert worker._review_render_backend({"renderer": "maya_playblast"}) == "maya_playblast"


def test_review_render_backend_rejects_unknown_renderer() -> None:
    import pytest

    with pytest.raises(ValueError, match="Unsupported Review renderer"):
        worker._review_render_backend({"renderer": "viewport_magic"})


def test_review_relink_creates_queue_from_configured_final_comp(
    tmp_path: Path, monkeypatch
) -> None:
    published = tmp_path / "published.aep"
    published.write_bytes(b"fake aep")
    working = tmp_path / "working.aep"
    executable = tmp_path / "AfterFX.exe"
    executable.write_bytes(b"")
    monkeypatch.setattr(ae, "find_after_effects_executable", lambda _config: str(executable))

    def fake_popen(*_args, **_kwargs):
        working.with_suffix(".relinked").write_text("ok", encoding="utf-8")
        return SimpleNamespace()

    monkeypatch.setattr(worker.subprocess, "Popen", fake_popen)
    config = SimpleNamespace(
        load=lambda _name: {
            "final_comp": {"name": "final", "fallback_names": "render_final,anim_final"},
            "render_queue": {},
        }
    )

    result = worker._prepare_review_project_copy(
        published, working, {}, config
    )

    script = working.with_suffix(".relink.jsx").read_text(encoding="utf-8")
    assert result == working
    assert 'var finalCompNames = ["final", "render_final", "anim_final"]' in script
    assert "projectItem instanceof CompItem" in script
    assert "queue.items.add(outputComp)" in script
    assert "while (queue.numItems > 0)" in script
    assert "currentName === normalizedName(requested[n])" in script
    assert "Published project has no Render Queue item" not in script


def test_camera_for_layer_matches_full_dag_path_leaf() -> None:
    cameras = ["|camera_grp|cam_CHA_FIX", "|camera_grp|cam"]

    assert worker._camera_for_layer(cameras, "BGA", "cam") == "|camera_grp|cam"
    assert (
        worker._camera_for_layer(cameras, "CHA", "cam_CHA_FIX")
        == "|camera_grp|cam_CHA_FIX"
    )


def test_camera_for_layer_accepts_namespace_and_maya_numeric_suffix() -> None:
    cameras = ["|camera_grp|shotCamera:cam1"]

    assert (
        worker._camera_for_layer(cameras, "BGA", "cam")
        == "|camera_grp|shotCamera:cam1"
    )


def test_camera_for_layer_does_not_fall_back_to_unrelated_camera() -> None:
    cameras = ["|camera_grp|unrelatedCamera"]

    assert worker._camera_for_layer(cameras, "CHA", "publishedCam") == ""


def test_review_layer_contract_joins_definition_and_render_manifest() -> None:
    contracts = worker._resolved_review_layer_contracts(
        layer_definition={
            "layers": [
                {
                    "review_layer_id": "CHA",
                    "display_layer": "CHA_display",
                    "members": ["dli"],
                    "enabled": True,
                }
            ]
        },
        render_manifest={
            "layer_order": ["CHA"],
            "rows": [
                {
                    "layer": "CHA",
                    "display_layer": "CHA_display",
                    "camera": "cam_CHA_FIX",
                    "width": 1766,
                    "height": 1836,
                    "start": 278,
                    "end": 411,
                    "overscan": 1.1,
                    "preset": "layout_lighting",
                    "output_format": "png",
                    "ae_placeholder": "CHA_INPUT",
                }
            ],
        },
        assembly_by_uid={"dli": {"uid": "dli", "name": "DLI_main"}},
    )

    assert contracts["CHA"]["members"] == ["DLI_main"]
    assert contracts["CHA"]["camera"] == {"name": "cam_CHA_FIX"}
    assert contracts["CHA"]["resolution"] == {"width": 1766, "height": 1836}
    assert contracts["CHA"]["export_frame_range"] == [278, 411]
    assert contracts["CHA"]["precomp_placeholder"] == "CHA_INPUT"
    assert contracts["CHA"]["overscan"] == 1.1


def test_review_layer_contract_does_not_take_output_settings_from_definition() -> None:
    contracts = worker._resolved_review_layer_contracts(
        layer_definition={
            "layers": [{
                "name": "BGA", "members": ["room"],
                "camera": {"name": "stale_camera"},
                "resolution": {"width": 1, "height": 1},
            }]
        },
        render_manifest={
            "rows": [{
                "layer": "BGA", "display_layer": "BGA", "camera": "cam",
                "width": 1280, "height": 720, "start": 278, "end": 411,
            }]
        },
        assembly_by_uid={"room": {"name": "Room_main"}},
    )

    assert contracts["BGA"]["camera"] == {"name": "cam"}
    assert contracts["BGA"]["resolution"] == {"width": 1280, "height": 720}


def test_construct_snapshot_rig_path_overrides_worker_reresolution(
    tmp_path: Path,
) -> None:
    resolved_proxy = tmp_path / "proxy" / "asset.mb"
    pinned_anim = tmp_path / "anim" / "asset.mb"
    resolved_proxy.parent.mkdir()
    pinned_anim.parent.mkdir()
    resolved_proxy.write_bytes(b"proxy")
    pinned_anim.write_bytes(b"anim")
    preview = [
        BuildPreviewItem(
            cast_key="JIN_main", asset="JIN", variant="default",
            namespace="JIN", category="character", review_layer="CHA",
            asset_publish="approved", required=True, status="resolved",
            publish_path=str(resolved_proxy),
        )
    ]
    snapshot = {
        "components": [{
            "component_type": "rig", "name": "JIN_main",
            "namespace": "JIN", "path": str(pinned_anim),
            "required": True, "enabled": True,
        }]
    }

    locked = worker._lock_preview_to_construct_rigs(preview, snapshot)

    assert locked[0].publish_path == str(pinned_anim)
    assert locked[0].status == "resolved"


def test_after_effects_resolver_prefers_explicit_review_build_registration(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config" / "SHOW"
    config_dir.mkdir(parents=True)
    ae_2024 = tmp_path / "Adobe After Effects 2024" / "AfterFX.exe"
    ae_2025 = tmp_path / "Adobe After Effects 2025" / "AfterFX.exe"
    ae_2024.parent.mkdir()
    ae_2025.parent.mkdir()
    ae_2024.write_bytes(b"")
    ae_2025.write_bytes(b"")
    (config_dir / "software_AfterEffects2024.yml").write_text(
        f"path: '{ae_2024.as_posix()}'\n", encoding="utf-8"
    )
    (config_dir / "software_AfterEffects2025.yml").write_text(
        f"path: '{ae_2025.as_posix()}'\n", encoding="utf-8"
    )
    (config_dir / "templates_base.yml").write_text(
        "enabled_softwares:\n"
        "  - AfterEffects2025\n"
        "review_build:\n"
        "  after_effects_software: AfterEffects2024\n",
        encoding="utf-8",
    )

    resolved = ae.find_after_effects_executable(ProjectConfig(config_dir))

    assert Path(resolved) == ae_2024


def test_review_thumbnail_uses_imgcvt_output_format_flag(
    tmp_path: Path, monkeypatch
) -> None:
    rendered = tmp_path / "ogs.png"
    rendered.write_bytes(b"\x89PNG\r\n\x1a\n")
    maya_root = tmp_path / "Maya2024"
    imgcvt = maya_root / "bin" / "imgcvt.exe"
    imgcvt.parent.mkdir(parents=True)
    imgcvt.write_bytes(b"")
    target = tmp_path / "thumbnail.jpg"

    class FakeCmds:
        @staticmethod
        def currentTime(_frame, edit=True):
            return edit

        @staticmethod
        def ogsRender(**_kwargs):
            return str(rendered)

        @staticmethod
        def about(installDirectory=True):
            return str(maya_root) if installDirectory else ""

    def fake_run(command, **_kwargs):
        assert command[1:5] == ["-t", "jpg", "-q", "90"]
        Path(command[-1]).write_bytes(b"\xff\xd8fake-jpeg\xff\xd9")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(worker.subprocess, "run", fake_run)

    result = worker._render_review_thumbnail(
        FakeCmds(), camera="camera1", frame=1001,
        width=640, height=360, target=target,
    )

    assert result == target
    assert target.read_bytes()[:2] == b"\xff\xd8"

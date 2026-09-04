from __future__ import annotations

from smartlib.core.icons import build_content_icon_path


def test_generated_build_content_icons_resolve_at_small_size() -> None:
    for component_type in (
        "editorial_timing",
        "audio",
        "placement",
        "layout_overlay",
        "rig",
    ):
        path = build_content_icon_path(component_type, size=24)
        assert path is not None
        assert path.name == f"{component_type}.png"
        assert path.parent.name == "24"


def test_build_contents_reuses_existing_shot_data_icons() -> None:
    expected_names = {
        "animation_curve": "animation_curves.png",
        "camera": "camera.png",
        "virtual_camera": "camera.png",
        "light": "light.png",
        "set_dress": "set_dress_work_data.png",
    }
    for component_type, filename in expected_names.items():
        path = build_content_icon_path(component_type, size=24)
        assert path is not None
        assert path.name == filename
        assert path.parent.name == "28"


def test_unknown_build_content_icon_is_not_resolved() -> None:
    assert build_content_icon_path("unknown") is None

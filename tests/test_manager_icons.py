from __future__ import annotations

from smartlib.core.icons import asset_category_icon_path, shot_data_icon_path


def test_asset_manager_category_icons_exist() -> None:
    for category in ("character", "characters", "environment", "env", "prop", "vehicle", "asset"):
        path = asset_category_icon_path(category, 20)
        assert path is not None, category
        assert path.parent.name == "20"


def test_shot_manager_data_icons_exist() -> None:
    for data_type in (
        "animation_curve",
        "camera",
        "light",
        "render_manifest",
        "review_layers",
        "set_dress_data",
    ):
        path = shot_data_icon_path(data_type, 28)
        assert path is not None, data_type
        assert path.parent.name == "28"


def test_unknown_manager_icons_are_absent() -> None:
    assert asset_category_icon_path("unknown") is None
    assert shot_data_icon_path("unknown") is None

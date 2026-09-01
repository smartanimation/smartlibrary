from __future__ import annotations

from smartlib.core.icons import tool_icon_path


TOOL_IDS = (
    "smart_launcher",
    "smart_ingest",
    "smart_casting",
    "asset_manager",
    "shot_manager",
    "review_build_manager",
    "smart_ae_browser",
    "smart_editorial",
    "smart_delivery",
)


def test_all_tool_icon_variants_exist() -> None:
    for tool_id in TOOL_IDS:
        for size in (16, 20, 40):
            path = tool_icon_path(tool_id, size)
            assert path is not None, (tool_id, size)
            assert path.name.endswith(".png")


def test_unknown_tool_has_no_icon() -> None:
    assert tool_icon_path("unknown_tool", 20) is None


def test_non_menu_size_uses_master() -> None:
    path = tool_icon_path("smart_launcher", 64)
    assert path is not None
    assert path.parent.name == "master"

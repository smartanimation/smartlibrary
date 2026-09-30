from smartlib.core.icons import shot_publish_type_icon_path


def test_configured_shot_publish_type_icons_resolve():
    for publish_type in (
        "camera",
        "animation_cache",
        "assets",
        "placements",
        "set_dress",
        "preview_render",
    ):
        path = shot_publish_type_icon_path(publish_type, 28)
        assert path is not None, publish_type
        assert path.is_file()


def test_cast_publish_type_reuses_smart_casting_icon():
    assert shot_publish_type_icon_path("assets", 28) == shot_publish_type_icon_path("cast", 28)

from smartlib.core.icons import asset_publish_type_icon_path, build_content_icon_path


def test_all_asset_publish_type_icons_resolve():
    for publish_type in ("geometry", "rig", "look", "texture", "groom"):
        path = asset_publish_type_icon_path(publish_type, 24)
        assert path is not None, publish_type
        assert path.is_file()


def test_asset_rig_reuses_build_manager_rig_icon():
    assert asset_publish_type_icon_path("rig", 24) == build_content_icon_path("rig", 24)


def test_unknown_asset_publish_type_has_no_icon():
    assert asset_publish_type_icon_path("unknown", 24) is None

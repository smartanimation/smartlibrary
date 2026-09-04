from smartlib.core.icons import build_content_icon_path, shot_data_icon_path


def test_shot_placement_reuses_map_master():
    path = shot_data_icon_path("placement", 28)
    assert path is not None
    assert path.is_file()
    assert path.name == "placement.png"
    assert path.parent.name == "master"
    assert path == build_content_icon_path("placement", 28)
    assert shot_data_icon_path(" Placements ", 28) == path

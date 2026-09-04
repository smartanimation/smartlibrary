from smartlib.core.icons import sequence_input_icon_path


def test_sequence_recipe_input_icons_resolve():
    expected = {
        "editorial": "editorial_timing.png",
        "mocap": "motion_capture.png",
        "motion_capture": "motion_capture.png",
        "virtual_camera": "virtual_camera.png",
        "cast": "smart_casting.png",
        "storyreel": "smart_editorial.png",
        "audio": "audio.png",
        "light": "light.png",
    }
    for input_type, filename in expected.items():
        path = sequence_input_icon_path(input_type, 24)
        assert path is not None, input_type
        assert path.is_file()
        assert path.name == filename


def test_unknown_sequence_input_icon_is_absent():
    assert sequence_input_icon_path("unknown") is None

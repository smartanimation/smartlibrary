from smartlib.core.icons import tool_ico_path


def test_all_tool_ico_files_resolve():
    for tool_id in (
        "smart_launcher",
        "smart_ingest",
        "smart_casting",
        "asset_manager",
        "shot_manager",
        "build_manager",
        "review_build_manager",
        "smart_ae_browser",
        "smart_editorial",
        "smart_delivery",
        "smart_review",
    ):
        path = tool_ico_path(tool_id)
        assert path is not None, tool_id
        assert path.is_file()
        assert path.suffix == ".ico"


def test_unknown_tool_ico_is_absent():
    assert tool_ico_path("unknown") is None

from smartlib.dcc.maya import smart_menu


def test_internal_maya_defaults_to_all_features():
    assert smart_menu.allowed_maya_features({"studio": {"role": "internal"}}) is None


def test_vendor_maya_defaults_to_smart_preflight_only():
    assert smart_menu.allowed_maya_features({"studio": {"role": "vendor"}}) == {"smart_preflight"}


def test_maya_uses_explicit_feature_allowlist():
    assert smart_menu.allowed_maya_features({
        "studio": {"role": "vendor"},
        "maya": {"allowed_features": ["smart_preflight", "texture_path_repair"]},
    }) == {"smart_preflight", "texture_path_repair"}


def test_maya_menu_items_are_filtered_by_feature():
    items = [
        {"label": "Smart Preflight", "command": "smartlib.dcc.maya.smart_menu.show_smart_preflight"},
        {"label": "Asset Manager", "command": "smartlib.dcc.maya.smart_menu.show_asset_manager"},
    ]
    assert smart_menu._filter_allowed_items(items, {"smart_preflight"}) == [items[0]]


def test_disabled_maya_command_is_not_resolved(monkeypatch):
    monkeypatch.setattr(smart_menu, "_studio_maya_features", lambda: {"smart_preflight"})
    resolved = []
    monkeypatch.setattr(smart_menu, "_resolve_command", lambda path: resolved.append(path))
    smart_menu._run_command("smartlib.dcc.maya.smart_menu.show_asset_manager")
    assert resolved == []

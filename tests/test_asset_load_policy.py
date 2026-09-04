from smartlib.core.asset_load_policy import resolve_asset_load_policy


def test_context_layer_uses_older_default_for_newer_project_version(tmp_path, monkeypatch):
    from smartlib.apps.asset_manager.context import AssetContextService

    context_dir = tmp_path / "contexts" / "asset"
    context_dir.mkdir(parents=True)
    (context_dir / "v001.yml").write_text("name: asset\n", encoding="utf-8")

    assert AssetContextService._context_layer_path(context_dir, "v004") == context_dir / "v001.yml"


def test_rigged_prop_uses_maya_reference() -> None:
    result = resolve_asset_load_policy(
        {"category": "prop", "capabilities": {"rigged": True}},
    )
    assert result.mode == "reference"
    assert result.component_type == "rig"


def test_static_prop_uses_usd_payload_from_project_policy() -> None:
    result = resolve_asset_load_policy(
        {"category": "prop"},
        policy={
            "defaults": {"prop": {"mode": "payload", "context": "WORK"}},
            "category_classes": {"prop": ["prop"]},
        },
    )
    assert result.mode == "payload"
    assert result.context == "WORK"


def test_metadata_override_has_highest_priority() -> None:
    result = resolve_asset_load_policy(
        {
            "category": "env",
            "workspace_representation": {"mode": "maya_reference", "context": "ANIM"},
        },
    )
    assert result.mode == "reference"
    assert result.context == "ANIM"

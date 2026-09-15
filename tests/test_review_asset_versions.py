from pathlib import Path
from types import SimpleNamespace

from smartlib.core.asset_publish_resolver import AssetPublishResolver
from smartlib.apps.review_build_manager.window import ReviewBuildManagerWindow, QtWidgets


def test_versions_are_concrete_and_numerically_sorted(tmp_path):
    resolver = AssetPublishResolver(None)
    root = tmp_path / "default"
    for version in ("v001", "v003", "v010"):
        directory = root / "publish" / "asset" / "anim" / version
        directory.mkdir(parents=True)
        (directory / "DLI.mb").write_bytes(b"fixture")
    resolver._asset_context_for_stage = lambda root, context: "anim"
    options = resolver.list_context_versions(root, "ANIM")
    assert [v["version"] for v in options] == ["v010", "v003", "v001"]
    assert all(Path(v["path"]).is_file() for v in options)


def test_policy_alias_is_replaced_by_version_of_selected_file():
    row = {"build_version": "approved", "component": {"path": "asset/v001/DLI.mb", "version": "approved"},
           "asset_versions": [{"path": "asset/v003/DLI.mb", "version": "v003"},
                              {"path": "asset/v001/DLI.mb", "version": "v001"}]}
    ReviewBuildManagerWindow._concrete_asset_version(row)
    assert row["build_version"] == "v001"
    assert row["component"]["version"] == "v001"
    assert row["component"]["path"] == "asset/v001/DLI.mb"


def test_version_selection_is_saved_and_applied_to_construct(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    chosen = tmp_path / "v003.mb"
    chosen.write_bytes(b"fixture")
    row = {"type": "rig", "cast_key": "DLI_main", "enabled": True,
           "component": {"path": "old.mb", "version": "approved"},
           "asset_versions": [{"path": str(chosen), "version": "v003"}]}
    combo = QtWidgets.QComboBox()
    combo.addItem("v003", str(chosen))
    combo.setProperty("content_row", 0)
    saved = {}
    def save():
        saved.update(inputs=[{"type": "rig", "name": "DLI_main", "enabled": row["enabled"],
                              "path": row["component"]["path"], "version": row["build_version"]}])
    window = SimpleNamespace(_selected_status=lambda: object(), sender=lambda: combo,
        current_build_content_rows=[row], _local_content_state=lambda *args: "READY",
        _planned_controls_changed=save, _populate_build_contents=lambda *args: None,
        _populate_planned_snapshot=lambda *args: None)
    ReviewBuildManagerWindow._planned_asset_version_changed(window)
    result = ReviewBuildManagerWindow._apply_planned_snapshot_to_construct(
        {"components": [{"component_type": "rig", "name": "DLI_main", "path": "old.mb"}]}, saved)
    assert result["components"][0]["path"] == str(chosen)
    assert result["components"][0]["version"] == "v003"
    assert "approved" not in str(saved)

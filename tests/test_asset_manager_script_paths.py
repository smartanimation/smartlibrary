import json
from pathlib import Path

from scripts.asset_manager import AssetManager


def test_asset_manager_resolves_assets_root_used_by_sheet_cache(tmp_path: Path):
    project_root = tmp_path / "project"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_dir.joinpath("templates_base.yml").write_text(
        f"anchors:\n  project_name: TEST\n  project_root: '{project_root.as_posix()}'\n"
        "templates:\n  production_root: '{project_root}/production'\n"
        "  assets_root: '{production_root}/assets'\n  workspace_root: '{project_root}/workspace'\n",
        encoding="utf-8",
    )
    config_dir.joinpath("templates_assets.yml").write_text(
        "templates:\n  asset_root: '{assets_root}/{category}/{group}/{asset_name}'\n"
        "  asset_work_root: '{workspace_root}/{workspace_partition}/assets/{category}/{group}/{asset_name}/{variant}/work'\n",
        encoding="utf-8",
    )
    cache = config_dir / ".cache" / "asset_list.json"
    cache.parent.mkdir()
    cache.write_text(json.dumps([{"category": "character", "group": "main", "asset": "YOU"}]), encoding="utf-8")

    manager = AssetManager(config_dir)
    assets = manager.list_assets_from_sheet(fallback_to_filesystem=True)

    assert manager.assets_root == project_root / "production" / "assets"
    assert assets[0].root == project_root / "production" / "assets" / "character" / "main" / "YOU"

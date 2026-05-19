from __future__ import annotations

import json
import os
import sys
import argparse
from pathlib import Path

try:
    from scripts.asset_manager import AssetManager
except ImportError:
    from asset_manager import AssetManager


def row_value(row: dict, *names: str):
    normalized = {str(key).replace(" ", "").lower(): value for key, value in row.items()}
    for name in names:
        key = name.replace(" ", "").lower()
        if key in normalized:
            return normalized[key]
    return None


def normalize_rows(rows: list[dict]) -> list[dict]:
    assets = []
    for row in rows:
        category = str(row_value(row, "Category", "category") or "").strip()
        group = str(row_value(row, "Group", "group") or "").strip()
        asset_name = str(row_value(row, "AssetName", "Asset Name", "asset", "name") or "").strip()
        if not category or not group or not asset_name:
            continue
        assets.append(
            {
                "asset": asset_name,
                "name": asset_name,
                "category": category,
                "group": group,
                "asset_type": row_value(row, "AssetType", "Asset Type", "Type") or category,
                "status": row_value(row, "Status", "status") or "",
                "description": row_value(row, "Description", "description") or "",
                "published_by": row_value(row, "PublishedBy", "Published By") or "",
                "thumbnail": row_value(row, "Thumbnail", "thumbnail") or "thumbnail.jpg",
            }
        )
    return assets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", help="Project config directory, for example P:/dev/smartlibrary/config/STKB")
    parser.add_argument("--credentials", help="Path to credentials.json")
    args = parser.parse_args()

    if args.config_dir:
        os.environ["PROJECT_CONFIG_DIR"] = args.config_dir
    if args.credentials:
        os.environ["CREDENTIALS_PATH"] = args.credentials

    manager = AssetManager()
    print(f"Config: {manager.config_dir}")
    print(f"Base config: {manager.config_dir / 'templates_base.yml'}")
    sheet_id = manager._asset_sheet_id()
    print(f"Sheet ID: {sheet_id or '<missing>'}")
    credentials_path = manager._credentials_path()
    if not sheet_id:
        print("ERROR: google_sheets.asset_list_id is not set in templates_base.yml")
        return 1
    if not credentials_path or not credentials_path.exists():
        print("ERROR: credentials file was not found")
        print("Set CREDENTIALS_PATH, CREDENTIALS_DIR, or %APPDATA%/credentials.json.")
        return 1

    try:
        import gspread
    except ImportError as exc:
        print(f"ERROR: gspread is not installed for this Python: {exc}")
        return 1

    gc = gspread.service_account(filename=str(credentials_path))
    rows = gc.open_by_key(sheet_id).sheet1.get_all_records()
    assets = normalize_rows(rows)

    cache_path = manager._sheet_cache_path()
    old_assets = None
    if cache_path.exists():
        try:
            with cache_path.open("r", encoding="utf-8") as f:
                old_assets = json.load(f)
        except Exception:
            old_assets = None

    if old_assets == assets:
        print(f"Up to date: {cache_path}")
        return 0

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("w", encoding="utf-8") as f:
        json.dump(assets, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"Synced {len(assets)} assets to {cache_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

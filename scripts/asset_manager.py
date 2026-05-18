from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    import yaml
except ImportError:
    yaml = None


CONFIG_ENV_VAR = "PROJECT_CONFIG_DIR"
ROOT_ENV_VAR = "SMARTLIBRARY_ROOT"
CREDENTIALS_ENV_VARS = ("CREDENTIALS_PATH", "GOOGLE_APPLICATION_CREDENTIALS", "CREDENTIALS_DIR")
WORK_SCENE_RE = re.compile(
    r"^(?:(?P<prefix>.+?)_)?(?P<asset>.+?)_(?P<department>[^_]+)_(?P<variant>[^_]+)_v(?P<version>\d+)_(?P<take>\d+)\.(?P<ext>[^.]+)$"
)
WORK_DCC_LAYOUT = {
    "maya": {
        "model": ["hires", "proxy", "render"],
        "rig": ["layout", "anim"],
        "groom": [],
    },
    "substance": [],
    "mari": [],
    "zbrush": [],
    "houdini": [],
}
DATA_LAYOUT = {
    "model": ["hires", "proxy", "render"],
    "rig": ["skin", "guide", "build"],
    "guide": [],
    "skin": [],
    "groom": [],
    "sim": [],
}
PUBLISH_LAYOUT = {
    "model": {"hires": ["ma", "fbx", "abc", "usd"]},
    "rig": {"layout": ["ma"], "anim": ["ma"]},
    "groom": {},
    "surfacing": {},
    "texture": {"": ["hi", "lo"]},
    "usd": {"model": [], "look": [], "rig": []},
}
REFERENCE_LAYOUT = ["concept", "scan", "photo"]


def _norm(path: str | os.PathLike[str]) -> Path:
    return Path(str(path).replace("\\", "/")).expanduser()


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    if yaml is None:
        return _load_simple_project_yaml(path)
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_simple_project_yaml(path: Path) -> dict:
    data: dict = {}
    section: str | None = None

    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.split("#", 1)[0].rstrip()
            if not line.strip():
                continue

            indent = len(line) - len(line.lstrip(" "))
            stripped = line.strip()

            if indent == 0 and stripped.endswith(":"):
                section = stripped[:-1]
                data[section] = [] if section.endswith("_depts") else {}
                continue

            if section is None:
                continue

            if isinstance(data.get(section), list) and stripped.startswith("- "):
                data[section].append(_parse_scalar(stripped[2:].strip()))
                continue

            if isinstance(data.get(section), dict) and ":" in stripped:
                key, value = stripped.split(":", 1)
                data[section][key.strip()] = _parse_scalar(value.strip())

    return data


def _parse_scalar(value: str):
    if value == "":
        return ""
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        items = [item.strip() for item in value[1:-1].split(",") if item.strip()]
        return [_parse_scalar(item) for item in items]
    if value.isdigit():
        return int(value)
    return value


def _version_label(version: int | str) -> str:
    if isinstance(version, int):
        return f"v{version:03d}"
    text = str(version)
    if text.lower().startswith("v"):
        return text.lower()
    if text.isdigit():
        return f"v{int(text):03d}"
    return text


def _version_number(version: int | str) -> int:
    if isinstance(version, int):
        return version
    text = str(version).lower().lstrip("v")
    return int(text)


def _relative_to_asset(path: Path, asset: "Asset") -> str:
    try:
        return path.relative_to(asset.root).as_posix()
    except ValueError:
        return path.as_posix()


def _read_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _sidecar_json_path(path: str | os.PathLike[str]) -> Path:
    file_path = _norm(path)
    return file_path.parent / f"{file_path.name}.json"


def _resolve_templates(raw_templates: dict[str, str], project_root: str) -> dict[str, str]:
    resolved = {
        name: pattern.replace("{project_root}", project_root)
        for name, pattern in raw_templates.items()
    }
    for _ in range(5):
        changed = False
        for name, pattern in list(resolved.items()):
            next_pattern = pattern
            for other_name, other_pattern in resolved.items():
                next_pattern = next_pattern.replace("{" + other_name + "}", other_pattern)
            if next_pattern != pattern:
                resolved[name] = next_pattern
                changed = True
        if not changed:
            break
    return resolved


@dataclass(frozen=True)
class Asset:
    category: str
    group: str
    name: str
    root: Path

    @property
    def code(self) -> str:
        return self.name

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def publish_dir(self) -> Path:
        return self.root / "publish"

    @property
    def work_dir(self) -> Path:
        return self.root / "work"

    @property
    def reference_dir(self) -> Path:
        return self.root / "reference"

    def paths(self) -> dict[str, Path]:
        return {
            "root": self.root,
            "data": self.data_dir,
            "publish": self.publish_dir,
            "work": self.work_dir,
            "reference": self.reference_dir,
        }

    def variant_root(self, variant: str = "default") -> Path:
        return self.root / variant

    def uses_variant_structure(self, variant: str = "default") -> bool:
        return (self.variant_root(variant) / "variant.json").exists()


class AssetManager:
    def __init__(self, config_dir: str | os.PathLike[str] | None = None):
        self.config_dir = self._resolve_config_dir(config_dir)
        base_cfg = _load_yaml(self.config_dir / "templates_base.yml")
        asset_cfg = _load_yaml(self.config_dir / "templates_assets.yml")
        self.base_config = base_cfg
        self._sheet_metadata: dict[tuple[str, str, str], dict] = {}
        self.last_asset_source = "filesystem"
        self.last_asset_source_error = ""

        anchors = base_cfg.get("anchors", {})
        self.project_name = anchors.get("project_name", self.config_dir.name)
        self.project_root = _norm(anchors.get("project_root", ""))
        self.asset_depts = list(base_cfg.get("asset_depts", []))
        self.templates = _resolve_templates(
            asset_cfg.get("templates", {}),
            str(self.project_root).replace("\\", "/"),
        )

    @staticmethod
    def _resolve_config_dir(config_dir: str | os.PathLike[str] | None) -> Path:
        if config_dir:
            return _norm(config_dir)
        env_path = os.environ.get(CONFIG_ENV_VAR)
        if env_path:
            return _norm(env_path)
        raise RuntimeError(
            f"{CONFIG_ENV_VAR} is not set. Launch the DCC from Smart Launcher "
            "or pass config_dir to AssetManager."
        )

    @property
    def assets_root(self) -> Path:
        template = self.templates.get("asset_root")
        if template:
            before_category = template.split("{category}", 1)[0].rstrip("/\\")
            return _norm(before_category)
        return self.project_root / "assets"

    def list_assets(
        self,
        *,
        category: str | None = None,
        group: str | None = None,
    ) -> list[Asset]:
        root = self.assets_root
        if not root.exists():
            return []

        assets: list[Asset] = []
        category_dirs = [root / category] if category else self._iter_dirs(root)
        for category_dir in category_dirs:
            if not category_dir.exists():
                continue
            group_dirs = [category_dir / group] if group else self._iter_dirs(category_dir)
            for group_dir in group_dirs:
                if not group_dir.exists():
                    continue
                for asset_dir in self._iter_dirs(group_dir):
                    assets.append(
                        Asset(
                            category=category_dir.name,
                            group=group_dir.name,
                            name=asset_dir.name,
                            root=asset_dir,
                        )
                    )
        return sorted(assets, key=lambda a: (a.category.lower(), a.group.lower(), a.name.lower()))

    def list_assets_from_sheet(self, *, fallback_to_filesystem: bool = True) -> list[Asset]:
        self.last_asset_source = "filesystem"
        self.last_asset_source_error = ""
        sheet_id = self._asset_sheet_id()
        credentials_path = self._credentials_path()
        cached_assets = self._list_assets_from_sheet_cache()
        if cached_assets:
            self.last_asset_source = "spreadsheet cache"
            if fallback_to_filesystem:
                merged_assets = self._merge_assets(cached_assets, self.list_assets())
                if len(merged_assets) > len(cached_assets):
                    self.last_asset_source = "spreadsheet cache + folders"
                return merged_assets
            return cached_assets
        if not sheet_id:
            self.last_asset_source_error = "google_sheets.asset_list_id is not set"
            return self.list_assets() if fallback_to_filesystem else []
        if not credentials_path:
            self.last_asset_source_error = (
                "Credentials path is not set. Use CREDENTIALS_PATH, "
                "GOOGLE_APPLICATION_CREDENTIALS, or CREDENTIALS_DIR."
            )
            return self.list_assets() if fallback_to_filesystem else []
        if not credentials_path.exists():
            self.last_asset_source_error = f"Credentials file does not exist: {credentials_path}"
            return self.list_assets() if fallback_to_filesystem else []

        try:
            import gspread
        except ImportError as exc:
            self.last_asset_source_error = f"gspread import failed: {exc}"
            return self.list_assets() if fallback_to_filesystem else []

        try:
            gc = gspread.service_account(filename=str(credentials_path))
            rows = gc.open_by_key(sheet_id).sheet1.get_all_records()
        except Exception as exc:
            self.last_asset_source_error = f"Spreadsheet read failed: {exc}"
            return self.list_assets() if fallback_to_filesystem else []

        assets: list[Asset] = []
        self._sheet_metadata.clear()
        for row in rows:
            category = str(self._row_value(row, "Category", "category") or "").strip()
            group = str(self._row_value(row, "Group", "group") or "").strip()
            asset_name = str(
                self._row_value(row, "AssetName", "Asset Name", "asset", "name") or ""
            ).strip()
            if not category or not group or not asset_name:
                continue
            asset = self.get_asset(category, group, asset_name)
            assets.append(asset)
            metadata = {
                "asset": asset.name,
                "name": asset.name,
                "category": category,
                "group": group,
                "asset_type": self._row_value(row, "AssetType", "Asset Type", "Type") or category,
                "status": self._row_value(row, "Status", "status") or "",
                "description": self._row_value(row, "Description", "description") or "",
                "published_by": self._row_value(row, "PublishedBy", "Published By") or "",
            }
            self._sheet_metadata[(category, group, asset_name)] = metadata

        self.last_asset_source = "spreadsheet"
        return sorted(assets, key=lambda a: (a.category.lower(), a.group.lower(), a.name.lower()))

    def _sheet_cache_path(self) -> Path:
        return self.config_dir / ".cache" / "asset_list.json"

    def _list_assets_from_sheet_cache(self) -> list[Asset]:
        cache_path = self._sheet_cache_path()
        data = _read_json(cache_path, None)
        if not isinstance(data, list):
            return []

        assets: list[Asset] = []
        self._sheet_metadata.clear()
        for row in data:
            if not isinstance(row, dict):
                continue
            category = str(row.get("category", "")).strip()
            group = str(row.get("group", "")).strip()
            asset_name = str(row.get("asset", "") or row.get("name", "")).strip()
            if not category or not group or not asset_name:
                continue
            asset = self.get_asset(category, group, asset_name)
            assets.append(asset)
            self._sheet_metadata[(category, group, asset_name)] = row
        return sorted(assets, key=lambda a: (a.category.lower(), a.group.lower(), a.name.lower()))

    @staticmethod
    def _merge_assets(primary: list[Asset], secondary: list[Asset]) -> list[Asset]:
        merged: dict[tuple[str, str, str], Asset] = {}
        for asset in secondary:
            merged[(asset.category, asset.group, asset.name)] = asset
        for asset in primary:
            merged[(asset.category, asset.group, asset.name)] = asset
        return sorted(merged.values(), key=lambda a: (a.category.lower(), a.group.lower(), a.name.lower()))

    def _asset_sheet_id(self) -> str:
        google_sheets = self.base_config.get("google_sheets", {})
        if isinstance(google_sheets, dict):
            return str(google_sheets.get("asset_list_id", "")).strip()
        return ""

    @staticmethod
    def _credentials_path() -> Path | None:
        for name in CREDENTIALS_ENV_VARS:
            value = os.environ.get(name)
            if not value:
                continue
            path = _norm(value.strip().strip('"'))
            if path.is_dir():
                path = path / "credentials.json"
            return path
        return None

    @staticmethod
    def _row_value(row: dict, *names: str):
        normalized = {str(key).replace(" ", "").lower(): value for key, value in row.items()}
        for name in names:
            key = name.replace(" ", "").lower()
            if key in normalized:
                return normalized[key]
        return None

    def get_asset(self, category: str, group: str, asset_name: str) -> Asset:
        data = {"category": category, "group": group, "asset_name": asset_name}
        pattern = self.templates.get("asset_root", "{project_root}/assets/{category}/{group}/{asset_name}")
        root = _norm(pattern.format(project_root=self.project_root, **data))
        return Asset(category=category, group=group, name=asset_name, root=root)

    def asset_metadata_paths(self, asset: Asset) -> list[Path]:
        return [
            asset.root / "asset.json",
            asset.root / "asset.yml",
            asset.root / "asset.yaml",
        ]

    def load_asset_metadata(self, asset: Asset) -> dict:
        metadata = {
            "asset": asset.name,
            "name": asset.name,
            "category": asset.category,
            "group": asset.group,
            "asset_type": asset.category,
            "status": "",
            "description": "",
        }
        sheet_metadata = self._sheet_metadata.get((asset.category, asset.group, asset.name))
        if sheet_metadata:
            metadata.update(sheet_metadata)
        for path in self.asset_metadata_paths(asset):
            if not path.exists():
                continue
            if path.suffix.lower() == ".json":
                data = _read_json(path, {})
            else:
                data = _load_yaml(path)
            if isinstance(data, dict):
                metadata.update(data)
            break
        return metadata

    def thumbnail_path_for_asset(self, asset: Asset) -> Path:
        return asset.root / "thumbnail.jpg"

    def find_asset_thumbnail(self, asset: Asset) -> Path | None:
        metadata = self.load_asset_metadata(asset)
        thumbnail = metadata.get("thumbnail")
        if thumbnail:
            path = asset.root / thumbnail
            if path.exists():
                return path

        direct = self.thumbnail_path_for_asset(asset)
        if direct.exists():
            return direct

        publish_thumbnails = sorted(asset.publish_dir.rglob("thumbnail.jpg")) if asset.publish_dir.exists() else []
        if publish_thumbnails:
            return publish_thumbnails[-1]

        work_thumbnails = sorted(asset.work_dir.rglob(".thumbnails/*.jpg")) if asset.work_dir.exists() else []
        if work_thumbnails:
            return work_thumbnails[-1]

        return None

    def work_variants(self, department: str, *, dcc: str = "maya") -> list[str]:
        dcc_layout = WORK_DCC_LAYOUT.get(dcc, {})
        if isinstance(dcc_layout, dict):
            variants = dcc_layout.get(department, [])
            if variants:
                return list(variants)
        defaults = {
            "model": ["hires", "proxy", "render"],
            "rig": ["layout", "anim"],
            "look": ["main"],
        }
        return defaults.get(department, ["main"])

    def asset_variants(self, asset: Asset) -> list[str]:
        variants = []
        if asset.root.exists():
            for path in asset.root.iterdir():
                if path.is_dir() and (path / "variant.json").exists():
                    variants.append(path.name)
        return sorted(variants) or ["default"]

    def work_subsets(self, department: str, *, dcc: str = "maya") -> list[str]:
        return self.work_variants(department, dcc=dcc)

    def work_root_dir(
        self,
        asset: Asset,
        *,
        dcc: str = "maya",
        department: str,
        variant: str = "default",
        subset: str = "",
    ) -> Path:
        if asset.uses_variant_structure(variant):
            path = asset.variant_root(variant) / department / "work"
            if subset:
                path = path / subset
            return path
        path = asset.work_dir / dcc / department
        if subset:
            path = path / subset
        return path

    def work_subset_for_path(
        self,
        asset: Asset,
        path: str | os.PathLike[str],
        department: str,
        variant: str,
    ) -> str:
        source = _norm(path)
        if asset.uses_variant_structure(variant):
            work_root = asset.variant_root(variant) / department / "work"
            try:
                relative = source.parent.relative_to(work_root)
                if relative.parts:
                    return relative.parts[0]
            except ValueError:
                pass
        return variant

    def ensure_asset_dirs(self, asset: Asset) -> None:
        self.ensure_asset_structure(asset)

    def ensure_asset_structure(self, asset: Asset) -> None:
        for path in self.asset_structure_paths(asset):
            path.mkdir(parents=True, exist_ok=True)
        self.ensure_asset_metadata(asset)

    def ensure_asset_metadata(self, asset: Asset) -> Path:
        path = asset.root / "asset.json"
        if path.exists():
            return path
        metadata = {
            "asset": asset.name,
            "category": asset.category,
            "group": asset.group,
            "asset_type": asset.category,
            "status": "wip",
            "thumbnail": "thumbnail.jpg",
            "description": "",
        }
        _write_json(path, metadata)
        return path

    def asset_structure_paths(self, asset: Asset) -> list[Path]:
        paths = [asset.work_dir, asset.data_dir, asset.publish_dir, asset.reference_dir]

        for dcc, layout in WORK_DCC_LAYOUT.items():
            dcc_root = asset.work_dir / dcc
            paths.append(dcc_root)
            if dcc == "maya":
                for department in self.asset_depts:
                    paths.append(dcc_root / department)
            if isinstance(layout, dict):
                for department, variants in layout.items():
                    if dcc == "maya" and self.asset_depts and department not in self.asset_depts:
                        continue
                    dept_root = dcc_root / department
                    paths.append(dept_root)
                    for variant in variants:
                        paths.append(dept_root / variant)

        for department, folders in DATA_LAYOUT.items():
            dept_root = asset.data_dir / department
            paths.append(dept_root)
            for folder in folders:
                paths.append(dept_root / folder)

        for department in self.asset_depts:
            paths.append(asset.data_dir / department)
            paths.append(asset.publish_dir / department)

        for department, variants in PUBLISH_LAYOUT.items():
            dept_root = asset.publish_dir / department
            paths.append(dept_root)
            for variant, formats in variants.items():
                variant_root = dept_root / variant if variant else dept_root
                paths.append(variant_root)
                for publish_format in formats:
                    paths.append(variant_root / publish_format)

        for folder in REFERENCE_LAYOUT:
            paths.append(asset.reference_dir / folder)

        unique_paths = []
        seen = set()
        for path in paths:
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            unique_paths.append(path)
        return unique_paths

    def work_file_path(
        self,
        asset: Asset,
        *,
        dcc: str = "maya",
        department: str,
        variant: str,
        subset: str | None = None,
        version: int | str,
        take: int | str,
        ext: str = "ma",
    ) -> Path:
        subset = subset if subset is not None else variant
        version_label = _version_label(version)
        take_label = str(take).zfill(2)
        clean_ext = ext.lstrip(".")
        data = {
            "project_name": self.project_name,
            "asset_name": asset.name,
            "category": asset.category,
            "group": asset.group,
            "dcc": dcc,
            "department": department,
            "variant": variant,
            "subset": subset,
            "version": version_label,
            "take": take_label,
            "ext": clean_ext,
        }
        if asset.uses_variant_structure(variant):
            file_pattern = self.templates.get(
                "work_scene_file",
                "{project_name}_{asset_name}_{department}_{variant}_{version}_{take}.{ext}",
            )
            data["asset_root"] = str(asset.root).replace("\\", "/")
            return self.work_root_dir(
                asset,
                dcc=dcc,
                department=department,
                variant=variant,
                subset=subset,
            ) / file_pattern.format(**data)
        dir_pattern = self.templates.get(
            "work_scene_dir",
            "{asset_root}/work/{dcc}/{department}/{variant}",
        )
        file_pattern = self.templates.get(
            "work_scene_file",
            "{asset_name}_{department}_{variant}_{version}_{take}.{ext}",
        )
        data["asset_root"] = str(asset.root).replace("\\", "/")
        return _norm(dir_pattern.format(**data)) / file_pattern.format(**data)

    def parse_work_file(self, path: str | os.PathLike[str]) -> dict | None:
        match = WORK_SCENE_RE.match(Path(path).name)
        if not match:
            return None
        data = match.groupdict()
        data["version"] = int(data["version"])
        data["take"] = int(data["take"])
        return data

    def next_work_take_path(
        self,
        asset: Asset,
        *,
        current_path: str | os.PathLike[str] | None = None,
        dcc: str = "maya",
        department: str = "model",
        variant: str = "hires",
        subset: str | None = None,
        version: int | str = 1,
        ext: str = "ma",
    ) -> Path:
        parsed = self.parse_work_file(current_path) if current_path else None
        if parsed:
            department = parsed["department"]
            variant = parsed["variant"]
            version = parsed["version"]
            ext = parsed["ext"]
            search_dir = Path(current_path).parent
        else:
            search_dir = self.work_file_path(
                asset,
                dcc=dcc,
                department=department,
                variant=variant,
                subset=subset,
                version=version,
                take=1,
                ext=ext,
            ).parent

        max_take = 0
        if search_dir.exists():
            for path in search_dir.iterdir():
                item = self.parse_work_file(path)
                if not item:
                    continue
                if (
                    item["asset"] == asset.name
                    and item["department"] == department
                    and item["variant"] == variant
                    and item["version"] == _version_number(version)
                    and item["ext"].lower() == ext.lower().lstrip(".")
                ):
                    max_take = max(max_take, item["take"])

        return self.work_file_path(
            asset,
            dcc=dcc,
            department=department,
            variant=variant,
            subset=subset,
            version=version,
            take=max_take + 1,
            ext=ext,
        )

    def next_work_version_path(
        self,
        asset: Asset,
        *,
        current_path: str | os.PathLike[str] | None = None,
        dcc: str = "maya",
        department: str = "model",
        variant: str = "hires",
        subset: str | None = None,
        ext: str = "ma",
    ) -> Path:
        parsed = self.parse_work_file(current_path) if current_path else None
        if parsed:
            department = parsed["department"]
            variant = parsed["variant"]
            ext = parsed["ext"]
            next_version = parsed["version"] + 1
        else:
            next_version = 1
        return self.work_file_path(
            asset,
            dcc=dcc,
            department=department,
            variant=variant,
            subset=subset,
            version=next_version,
            take=1,
            ext=ext,
        )

    def publish_base_dir(
        self,
        asset: Asset,
        *,
        department: str,
        variant: str = "",
        subset: str | None = None,
        publish_format: str = "",
    ) -> Path:
        if variant and asset.uses_variant_structure(variant):
            return asset.variant_root(variant) / "publish" / department / (subset or variant)
        path = asset.publish_dir / department
        if variant:
            path = path / variant
        if publish_format:
            path = path / publish_format.lstrip(".")
        return path

    def publish_version_dir(
        self,
        asset: Asset,
        *,
        department: str,
        variant: str = "",
        subset: str | None = None,
        publish_format: str,
        version: int | str,
    ) -> Path:
        return self.publish_base_dir(
            asset,
            department=department,
            variant=variant,
            subset=subset,
            publish_format=publish_format,
        ) / _version_label(version)

    def publish_file_path(
        self,
        asset: Asset,
        *,
        department: str,
        variant: str,
        subset: str | None = None,
        version: int | str,
        ext: str,
    ) -> Path:
        clean_ext = ext.lstrip(".")
        version_label = _version_label(version)
        data = {
            "project_name": self.project_name,
            "asset_name": asset.name,
            "category": asset.category,
            "group": asset.group,
            "department": department,
            "variant": variant,
            "subset": subset or variant,
            "version": version_label,
            "ext": clean_ext,
        }
        if asset.uses_variant_structure(variant):
            filename = f"{department}.{clean_ext}"
        else:
            file_pattern = self.templates.get(
                "publish_scene_file",
                "{asset_name}_{variant}_{version}.{ext}",
            )
            filename = file_pattern.format(**data)
        return self.publish_version_dir(
            asset,
            department=department,
            variant=variant,
            subset=subset,
            publish_format=clean_ext,
            version=version,
        ) / filename

    def publish_work_file(
        self,
        asset: Asset,
        source_workfile: str | os.PathLike[str],
        *,
        overwrite: bool = False,
        comment: str = "",
        subset: str | None = None,
    ) -> Path:
        source = _norm(source_workfile)
        parsed = self.parse_work_file(source)
        if not parsed:
            raise ValueError(f"Work filename does not match the asset rule: {source.name}")

        target = self.publish_file_path(
            asset,
            department=parsed["department"],
            variant=parsed["variant"],
            subset=subset or self.work_subset_for_path(asset, source, parsed["department"], parsed["variant"]),
            version=parsed["version"],
            ext=parsed["ext"],
        )
        if target.exists() and not overwrite:
            raise FileExistsError(f"Publish already exists: {target}")

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        self.register_publish(
            asset,
            department=parsed["department"],
            variant=parsed["variant"],
            subset=subset or self.work_subset_for_path(asset, source, parsed["department"], parsed["variant"]),
            version=parsed["version"],
            files={parsed["ext"]: target.name},
            source_workfile=_relative_to_asset(source, asset),
            comment=comment,
        )
        return target

    def publish_metadata_path(
        self,
        asset: Asset,
        *,
        department: str,
        variant: str = "",
        subset: str | None = None,
        publish_format: str,
    ) -> dict[str, Path]:
        base_dir = self.publish_base_dir(
            asset,
            department=department,
            variant=variant,
            subset=subset,
            publish_format=publish_format,
        )
        return {
            "latest": base_dir / "latest.json",
            "versions": base_dir / "versions.json",
        }

    def register_publish(
        self,
        asset: Asset,
        *,
        department: str,
        variant: str,
        subset: str | None = None,
        version: int | str,
        files: dict[str, str | os.PathLike[str]],
        source_workfile: str | os.PathLike[str],
        comment: str = "",
        status: str = "latest",
    ) -> dict:
        version_label = _version_label(version)
        version_num = _version_number(version)
        record = {
            "asset": asset.name,
            "department": department,
            "variant": variant,
            "publish_type": department,
            "subset": subset or variant,
            "version": version_num,
            "files": {key: str(value).replace("\\", "/") for key, value in files.items()},
            "source_workfile": str(source_workfile).replace("\\", "/"),
            "comment": comment,
        }

        for publish_format, filename in record["files"].items():
            version_dir = self.publish_version_dir(
                asset,
                department=department,
                variant=variant,
                subset=subset,
                publish_format=publish_format,
                version=version,
            )
            _write_json(version_dir / "publish.json", record)

            metadata_paths = self.publish_metadata_path(
                asset,
                department=department,
                variant=variant,
                subset=subset,
                publish_format=publish_format,
            )
            publish_path = version_dir / Path(filename).name
            _write_json(
                metadata_paths["latest"],
                {
                    "version": version_label,
                    "path": f"{version_label}/{publish_path.name}",
                },
            )
            self._update_versions(metadata_paths["versions"], version_label, status)

        return record

    def latest_publish_from_metadata(
        self,
        asset: Asset,
        *,
        department: str,
        variant: str,
        subset: str | None = None,
        publish_format: str,
    ) -> Path | None:
        latest_path = self.publish_metadata_path(
            asset,
            department=department,
            variant=variant,
            subset=subset,
            publish_format=publish_format,
        )["latest"]
        latest = _read_json(latest_path, None)
        if not latest:
            return None
        path = latest.get("path")
        if not path:
            return None
        return latest_path.parent / path

    def latest_publish_info(
        self,
        asset: Asset,
        *,
        department: str,
        variant: str,
        subset: str | None = None,
        publish_format: str,
    ) -> dict | None:
        latest_path = self.publish_metadata_path(
            asset,
            department=department,
            variant=variant,
            subset=subset,
            publish_format=publish_format,
        )["latest"]
        latest = _read_json(latest_path, None)
        if not latest:
            return None
        result = dict(latest)
        if latest.get("path"):
            result["absolute_path"] = str(latest_path.parent / latest["path"])
        return result

    def publish_record_for_work_file(
        self,
        asset: Asset,
        source_workfile: str | os.PathLike[str],
    ) -> dict | None:
        source = _norm(source_workfile)
        parsed = self.parse_work_file(source)
        if not parsed:
            return None

        version_dir = self.publish_version_dir(
            asset,
            department=parsed["department"],
            variant=parsed["variant"],
            subset=self.work_subset_for_path(asset, source, parsed["department"], parsed["variant"]),
            publish_format=parsed["ext"],
            version=parsed["version"],
        )
        record = _read_json(version_dir / "publish.json", None)
        if not record:
            return None

        expected = _relative_to_asset(source, asset).replace("\\", "/")
        recorded = str(record.get("source_workfile", "")).replace("\\", "/")
        if recorded != expected:
            return None

        latest = self.latest_publish_info(
            asset,
            department=parsed["department"],
            variant=parsed["variant"],
            subset=self.work_subset_for_path(asset, source, parsed["department"], parsed["variant"]),
            publish_format=parsed["ext"],
        )
        if not latest:
            return None
        if _version_number(latest.get("version", 0)) != parsed["version"]:
            return None
        return record

    @staticmethod
    def _update_versions(path: Path, version_label: str, status: str) -> None:
        versions = _read_json(path, [])
        next_versions = []
        found = False
        for item in versions:
            if item.get("version") == version_label:
                next_versions.append({"version": version_label, "status": status})
                found = True
            else:
                old_status = "approved" if item.get("status") == "latest" else item.get("status", "")
                next_versions.append({"version": item.get("version"), "status": old_status})
        if not found:
            next_versions.append({"version": version_label, "status": status})
        _write_json(path, next_versions)

    def list_data_files(self, asset: Asset) -> list[Path]:
        files = self._list_files(asset.data_dir)
        for variant in self.asset_variants(asset):
            files.extend(self._list_files(asset.variant_root(variant) / "data"))
        return sorted(set(files), key=lambda path: path.as_posix().lower())

    def file_comment(self, path: str | os.PathLike[str]) -> str:
        metadata = _read_json(_sidecar_json_path(path), {})
        if isinstance(metadata, dict):
            return str(metadata.get("comment", ""))
        return ""

    def set_file_comment(self, path: str | os.PathLike[str], comment: str) -> None:
        sidecar = _sidecar_json_path(path)
        metadata = _read_json(sidecar, {})
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["comment"] = comment
        _write_json(sidecar, metadata)

    def update_file_metadata(self, path: str | os.PathLike[str], **values) -> None:
        sidecar = _sidecar_json_path(path)
        metadata = _read_json(sidecar, {})
        if not isinstance(metadata, dict):
            metadata = {}
        metadata.update(values)
        _write_json(sidecar, metadata)

    def list_work_files(
        self,
        asset: Asset,
        *,
        department: str | None = None,
        variant: str | None = None,
        subset: str | None = None,
        extensions: Iterable[str] | None = None,
    ) -> list[Path]:
        files = self._list_files(asset.work_dir)
        if department:
            if variant and asset.uses_variant_structure(variant):
                dept_root = self.work_root_dir(
                    asset,
                    department=department,
                    variant=variant,
                    subset=subset or "",
                )
            else:
                dept_root = asset.work_dir / "maya" / department
                if variant:
                    dept_root = dept_root / variant
            files = [
                path for path in files
                if path == dept_root or dept_root in path.parents
            ]
            if asset.uses_variant_structure(variant or "default"):
                variant_files = self._list_files(asset.variant_root(variant or "default") / department / "work")
                if subset:
                    subset_root = self.work_root_dir(
                        asset,
                        department=department,
                        variant=variant or "default",
                        subset=subset,
                    )
                    variant_files = [
                        path for path in variant_files
                        if path == subset_root or subset_root in path.parents
                    ]
                files.extend(variant_files)
        if extensions:
            wanted = {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions}
            files = [path for path in files if path.suffix.lower() in wanted]
        return files

    def thumbnail_path_for_workfile(self, workfile: str | os.PathLike[str]) -> Path:
        path = _norm(workfile)
        return path.parent / ".thumbnails" / f"{path.stem}.jpg"

    def thumbnail_path_for_publish(self, publish_file: str | os.PathLike[str]) -> Path:
        path = _norm(publish_file)
        return path.parent / "thumbnail.jpg"

    def next_data_version_path(
        self,
        asset: Asset,
        *,
        department: str,
        variant: str = "",
        subset: str | None = None,
        ext: str,
        name: str | None = None,
    ) -> Path:
        clean_ext = ext.lstrip(".")
        base_dir = self.data_base_dir(asset, department=department, variant=variant, subset=subset)
        stem = name or f"{asset.name}_{department}_{variant}".rstrip("_")

        max_version = 0
        if base_dir.exists():
            pattern = re.compile(rf"^{re.escape(stem)}_v(?P<version>\d+)\.{re.escape(clean_ext)}$")
            for path in base_dir.iterdir():
                if path.is_dir() and path.name.lower().startswith("v") and path.name[1:].isdigit():
                    max_version = max(max_version, int(path.name[1:]))
                    continue
                match = pattern.match(path.name)
                if match:
                    max_version = max(max_version, int(match.group("version")))

        return self.data_file_path(
            asset,
            department=department,
            variant=variant,
            subset=subset,
            version=max_version + 1,
            ext=clean_ext,
            name=stem,
        )

    def data_base_dir(
        self,
        asset: Asset,
        *,
        department: str,
        variant: str = "",
        subset: str | None = None,
    ) -> Path:
        if variant and asset.uses_variant_structure(variant):
            return asset.variant_root(variant) / "data" / department / (subset or variant)
        path = asset.data_dir / department
        if variant:
            path = path / variant
        return path

    def data_version_dir(
        self,
        asset: Asset,
        *,
        department: str,
        variant: str = "",
        subset: str | None = None,
        version: int | str,
    ) -> Path:
        return self.data_base_dir(asset, department=department, variant=variant, subset=subset) / _version_label(version)

    def data_file_path(
        self,
        asset: Asset,
        *,
        department: str,
        variant: str,
        subset: str | None = None,
        version: int | str,
        ext: str,
        name: str | None = None,
    ) -> Path:
        clean_ext = ext.lstrip(".")
        version_label = _version_label(version)
        stem = name or f"{asset.name}_{department}_{variant}".rstrip("_")
        return self.data_version_dir(
            asset,
            department=department,
            variant=variant,
            subset=subset,
            version=version,
        ) / f"{stem}_{version_label}.{clean_ext}"

    def next_data_version(
        self,
        asset: Asset,
        *,
        department: str,
        variant: str = "",
        subset: str | None = None,
    ) -> int:
        base_dir = self.data_base_dir(asset, department=department, variant=variant, subset=subset)
        max_version = 0
        if base_dir.exists():
            for path in base_dir.iterdir():
                if not path.is_dir():
                    continue
                name = path.name.lower()
                if name.startswith("v") and name[1:].isdigit():
                    max_version = max(max_version, int(name[1:]))
        return max_version + 1

    def data_metadata_path(
        self,
        asset: Asset,
        *,
        department: str,
        variant: str = "",
        subset: str | None = None,
    ) -> dict[str, Path]:
        base_dir = self.data_base_dir(asset, department=department, variant=variant, subset=subset)
        return {
            "latest": base_dir / "latest.json",
            "versions": base_dir / "versions.json",
        }

    def register_data_export(
        self,
        asset: Asset,
        *,
        department: str,
        variant: str,
        subset: str | None = None,
        version: int | str,
        files: dict[str, str | os.PathLike[str]],
        source_workfile: str | os.PathLike[str],
        comment: str = "",
        status: str = "latest",
    ) -> dict:
        version_label = _version_label(version)
        version_num = _version_number(version)
        record = {
            "asset": asset.name,
            "department": department,
            "variant": variant,
            "publish_type": department,
            "subset": subset or variant,
            "version": version_num,
            "files": {key: str(value).replace("\\", "/") for key, value in files.items()},
            "source_workfile": str(source_workfile).replace("\\", "/"),
            "comment": comment,
        }
        version_dir = self.data_version_dir(
            asset,
            department=department,
            variant=variant,
            subset=subset,
            version=version,
        )
        _write_json(version_dir / "publish.json", record)

        first_file = next(iter(record["files"].values()), "")
        metadata_paths = self.data_metadata_path(asset, department=department, variant=variant, subset=subset)
        _write_json(
            metadata_paths["latest"],
            {
                "version": version_label,
                "path": f"{version_label}/{Path(first_file).name}",
            },
        )
        self._update_versions(metadata_paths["versions"], version_label, status)
        return record

    def list_publish_files(self, asset: Asset) -> list[Path]:
        files = self._list_files(asset.publish_dir)
        for variant in self.asset_variants(asset):
            files.extend(self._list_files(asset.variant_root(variant) / "publish"))
        return sorted(set(files), key=lambda path: path.as_posix().lower())

    def list_latest_publishes(self, asset: Asset) -> list[Path]:
        latest_files = []
        latest_json_paths = list(asset.publish_dir.rglob("latest.json")) if asset.publish_dir.exists() else []
        for variant in self.asset_variants(asset):
            root = asset.variant_root(variant) / "publish"
            if root.exists():
                latest_json_paths.extend(root.rglob("latest.json"))
        for latest_json in latest_json_paths:
            latest = _read_json(latest_json, None)
            if not latest or not latest.get("path"):
                continue
            latest_files.append(latest_json.parent / latest["path"])
        return sorted(latest_files, key=lambda path: path.as_posix().lower())

    def latest_publish(
        self,
        asset: Asset,
        *,
        extensions: Iterable[str] | None = None,
    ) -> Path | None:
        if extensions:
            wanted = {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions}
        else:
            wanted = set()

        metadata_files = self.list_latest_publishes(asset)
        if wanted:
            metadata_files = [path for path in metadata_files if path.suffix.lower() in wanted]
        if metadata_files:
            return metadata_files[0]

        files = self.list_publish_files(asset)
        if wanted:
            files = [path for path in files if path.suffix.lower() in wanted]
        if not files:
            return None
        return max(files, key=lambda path: path.stat().st_mtime)

    @staticmethod
    def open_in_explorer(path: str | os.PathLike[str]) -> None:
        target = _norm(path)
        if os.name == "nt":
            subprocess.Popen(["explorer", str(target)])
        elif sys_platform() == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])

    @staticmethod
    def _iter_dirs(path: Path) -> list[Path]:
        return sorted([child for child in path.iterdir() if child.is_dir()], key=lambda p: p.name.lower())

    @staticmethod
    def _list_files(path: Path) -> list[Path]:
        if not path.exists():
            return []
        return sorted([child for child in path.rglob("*") if child.is_file()], key=lambda p: p.name.lower())


def sys_platform() -> str:
    import sys

    return sys.platform


def current() -> AssetManager:
    return AssetManager()

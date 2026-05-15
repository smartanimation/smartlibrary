from __future__ import annotations

import os
import json
import re
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
WORK_SCENE_RE = re.compile(
    r"^(?:(?P<prefix>.+?)_)?(?P<asset>.+?)_(?P<department>[^_]+)_(?P<variant>[^_]+)_v(?P<version>\d+)_(?P<take>\d+)\.(?P<ext>[^.]+)$"
)
WORK_DCC_LAYOUT = {
    "maya": {
        "model": ["hires", "hires/proxy", "hires/render"],
        "rig": ["layout", "anim"],
        "groom": [],
    },
    "substance": [],
    "mari": [],
    "zbrush": [],
    "houdini": [],
}
DATA_LAYOUT = {
    "rig": ["skin", "guide", "build"],
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


class AssetManager:
    def __init__(self, config_dir: str | os.PathLike[str] | None = None):
        self.config_dir = self._resolve_config_dir(config_dir)
        base_cfg = _load_yaml(self.config_dir / "templates_base.yml")
        asset_cfg = _load_yaml(self.config_dir / "templates_assets.yml")

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

    def get_asset(self, category: str, group: str, asset_name: str) -> Asset:
        data = {"category": category, "group": group, "asset_name": asset_name}
        pattern = self.templates.get("asset_root", "{project_root}/assets/{category}/{group}/{asset_name}")
        root = _norm(pattern.format(project_root=self.project_root, **data))
        return Asset(category=category, group=group, name=asset_name, root=root)

    def ensure_asset_dirs(self, asset: Asset) -> None:
        self.ensure_asset_structure(asset)

    def ensure_asset_structure(self, asset: Asset) -> None:
        for path in self.asset_structure_paths(asset):
            path.mkdir(parents=True, exist_ok=True)

    def asset_structure_paths(self, asset: Asset) -> list[Path]:
        paths = [asset.work_dir, asset.data_dir, asset.publish_dir, asset.reference_dir]

        for dcc, layout in WORK_DCC_LAYOUT.items():
            dcc_root = asset.work_dir / dcc
            paths.append(dcc_root)
            if isinstance(layout, dict):
                for department, variants in layout.items():
                    dept_root = dcc_root / department
                    paths.append(dept_root)
                    for variant in variants:
                        paths.append(dept_root / variant)

        for department, folders in DATA_LAYOUT.items():
            dept_root = asset.data_dir / department
            paths.append(dept_root)
            for folder in folders:
                paths.append(dept_root / folder)

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

        return paths

    def work_file_path(
        self,
        asset: Asset,
        *,
        dcc: str = "maya",
        department: str,
        variant: str,
        version: int | str,
        take: int | str,
        ext: str = "ma",
    ) -> Path:
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
            "version": version_label,
            "take": take_label,
            "ext": clean_ext,
        }
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
            version=version,
            take=max_take + 1,
            ext=ext,
        )

    def publish_base_dir(
        self,
        asset: Asset,
        *,
        department: str,
        variant: str = "",
        publish_format: str = "",
    ) -> Path:
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
        publish_format: str,
        version: int | str,
    ) -> Path:
        return self.publish_base_dir(
            asset,
            department=department,
            variant=variant,
            publish_format=publish_format,
        ) / _version_label(version)

    def publish_file_path(
        self,
        asset: Asset,
        *,
        department: str,
        variant: str,
        version: int | str,
        ext: str,
    ) -> Path:
        clean_ext = ext.lstrip(".")
        version_label = _version_label(version)
        filename = f"{asset.name}_{department}_{variant}_{version_label}.{clean_ext}"
        return self.publish_version_dir(
            asset,
            department=department,
            variant=variant,
            publish_format=clean_ext,
            version=version,
        ) / filename

    def publish_metadata_path(
        self,
        asset: Asset,
        *,
        department: str,
        variant: str = "",
        publish_format: str,
    ) -> dict[str, Path]:
        base_dir = self.publish_base_dir(
            asset,
            department=department,
            variant=variant,
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
        version: int | str,
        files: dict[str, str | os.PathLike[str]],
        source_workfile: str | os.PathLike[str],
        status: str = "latest",
    ) -> dict:
        version_label = _version_label(version)
        version_num = _version_number(version)
        record = {
            "asset": asset.name,
            "department": department,
            "variant": variant,
            "version": version_num,
            "files": {key: str(value).replace("\\", "/") for key, value in files.items()},
            "source_workfile": str(source_workfile).replace("\\", "/"),
        }

        for publish_format, filename in record["files"].items():
            version_dir = self.publish_version_dir(
                asset,
                department=department,
                variant=variant,
                publish_format=publish_format,
                version=version,
            )
            _write_json(version_dir / "publish.json", record)

            metadata_paths = self.publish_metadata_path(
                asset,
                department=department,
                variant=variant,
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
        publish_format: str,
    ) -> Path | None:
        latest_path = self.publish_metadata_path(
            asset,
            department=department,
            variant=variant,
            publish_format=publish_format,
        )["latest"]
        latest = _read_json(latest_path, None)
        if not latest:
            return None
        path = latest.get("path")
        if not path:
            return None
        return latest_path.parent / path

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
        return self._list_files(asset.data_dir)

    def list_work_files(
        self,
        asset: Asset,
        *,
        extensions: Iterable[str] | None = None,
    ) -> list[Path]:
        files = self._list_files(asset.work_dir)
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

    def list_publish_files(self, asset: Asset) -> list[Path]:
        return self._list_files(asset.publish_dir)

    def list_latest_publishes(self, asset: Asset) -> list[Path]:
        latest_files = []
        latest_json_paths = asset.publish_dir.rglob("latest.json") if asset.publish_dir.exists() else []
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

from __future__ import annotations

import fnmatch
import json
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from smartlib.core.config_loader import load_config


@dataclass(frozen=True)
class PackageProfile:
    """Profile for packages exchanged with vendors or clients."""

    id: str
    source: str
    received_from: str
    asset_subset: str
    asset_root: str
    shot_root: str
    layouts: dict[str, dict[str, str]]
    include: dict[str, tuple[str, ...]]

    @classmethod
    def load(cls, path: str | Path) -> "PackageProfile":
        data = load_config(path)
        profile = dict(data.get("profile") or {})
        return cls(
            id=str(profile.get("id") or Path(path).stem),
            source=str(profile.get("source") or "vendor"),
            received_from=str(profile.get("received_from") or "vendor"),
            asset_subset=str(profile.get("asset_subset") or "vendor"),
            asset_root=str(profile.get("asset_root") or "production/assets/{category}/{group}/{asset}/{variant}/data/assembly/{subset}/v###"),
            shot_root=str(profile.get("shot_root") or "production/shots/{episode}/{sequence}/{shot}/data/{department}/{subset}/v###"),
            layouts={str(k): {str(a): str(b) for a, b in dict(v or {}).items()} for k, v in dict(data.get("layouts") or {}).items()},
            include={str(k): tuple(str(v) for v in values or []) for k, values in dict(data.get("include") or {}).items()},
        )


@dataclass(frozen=True)
class PackageResult:
    archive: Path
    manifest: dict[str, Any]
    files: tuple[str, ...]


class VendorPackageBuilder:
    """Build deterministic Smart Ingest ZIPs without modifying source files."""

    def __init__(self, profile: PackageProfile):
        self.profile = profile

    def build_asset(self, *, scene: str | Path, texture_root: str | Path | None, output: str | Path,
                    project: str, category: str, group: str, asset: str, variant: str = "default",
                    subset: str | None = None, comment: str = "", assembly: bool = False) -> PackageResult:
        scene_path = Path(scene)
        if not scene_path.is_file():
            raise FileNotFoundError(f"Source scene was not found: {scene_path}")
        layout = self.profile.layouts.get("asset") or {"scene": "scene/{name}", "textures": "sourceimages/{relative}"}
        scene_member = _safe_member(layout.get("scene", "scene/{name}").format(name=scene_path.name))
        rows: list[tuple[Path, str]] = [(scene_path, scene_member)]
        files: list[dict[str, Any]] = [{"role": "scene", "path": scene_member, "source": scene_path.as_posix(), "required": True}]
        texture_path = Path(texture_root) if texture_root else None
        texture_patterns = self.profile.include.get("texture", ("**/*",))
        if texture_path and texture_path.is_dir():
            for source in _matching_files(texture_path, texture_patterns):
                relative = source.relative_to(texture_path).as_posix()
                rows.append((source, _safe_member(layout.get("textures", "sourceimages/{relative}").format(relative=relative))))
            files.append({"role": "texture_root", "path": _member_root(layout.get("textures", "sourceimages/{relative}")),
                          "source": texture_path.as_posix(), "required": False, "include_patterns": list(texture_patterns)})
        target = {"target_type": "Asset", "project": project, "category": category, "group": group,
                  "asset": asset, "variant": variant, "department": "assembly",
                  "subset": subset or self.profile.asset_subset, "format": scene_path.suffix.lstrip(".").lower()}
        manifest = {
            "schema": "smart_ingest.asset_package.v1", "package_type": "asset", "profile": self.profile.id,
            "delivery": {"source": self.profile.source, "received_from": self.profile.received_from,
                         "delivery_date": date.today().strftime("%Y%m%d"), "comment": comment},
            "target": target, "main_file": scene_member,
            "source_inputs": {"scene": scene_path.as_posix(), "texture_root": texture_path.as_posix() if texture_path else ""},
            "files": files,
            "ingest": {"auto_plan": True, "copy_mode": "expand_package", "preserve_relative_paths": True,
                       "version_policy": "next", "expected_target_root": self.profile.asset_root.format(**target),
                       "open_scene_file": scene_member, "write_indexes": ["manifest.json", "latest.json", "versions.json"]},
        }
        if assembly:
            if scene_path.suffix.lower() != ".ma":
                raise ValueError("Asset Assembly requires a reference-preserving .ma scene")
            manifest["assembly"] = {
                "scene": scene_member,
                "entity": "asset",
                "reference_policy": {
                    "preserve_maya_references": True,
                    "vendor_absolute_paths_are_production_truth": False,
                    "production_resolution_order": ["asset_metadata", "placement_manifest", "source_reference_evidence"],
                },
                "placements": [],
                "placement_schema": "smart_delivery.asset_placement/v1",
            }
        return self._write(output, rows, manifest)

    def build_shot(self, *, sources: Iterable[str | Path], output: str | Path,
                   target: dict[str, str], comment: str = "") -> PackageResult:
        layout = self.profile.layouts.get("shot") or {"default": "files/{name}"}
        rows: list[tuple[Path, str]] = []
        manifest_files: list[dict[str, Any]] = []
        for value in sources:
            source = Path(value)
            if not source.is_file():
                raise FileNotFoundError(f"Shot source was not found: {source}")
            role = _shot_role(source)
            member = _safe_member(layout.get(role, layout.get("default", "files/{name}")).format(name=source.name, relative=source.name))
            rows.append((source, member))
            manifest_files.append({"role": role, "path": member, "source": source.as_posix(), "required": True})
        manifest = {"schema": "smart_ingest.shot_package.v1", "package_type": "shot", "profile": self.profile.id,
                    "delivery": {"source": self.profile.source, "received_from": self.profile.received_from,
                                 "delivery_date": date.today().strftime("%Y%m%d"), "comment": comment},
                    "target": dict(target), "files": manifest_files,
                    "ingest": {"auto_plan": True, "copy_mode": "expand_package", "version_policy": "next",
                               "expected_target_root": self.profile.shot_root.format(**target)}}
        return self._write(output, rows, manifest)

    @staticmethod
    def _write(output: str | Path, rows: list[tuple[Path, str]], manifest: dict[str, Any]) -> PackageResult:
        archive = Path(output)
        archive.parent.mkdir(parents=True, exist_ok=True)
        if archive.exists():
            raise FileExistsError(f"Output ZIP already exists: {archive}")
        members = [member for _source, member in rows]
        if len(members) != len(set(members)):
            raise ValueError("Package contains duplicate member paths")
        with tempfile.TemporaryDirectory(prefix="smart-delivery-") as temp:
            manifest_path = Path(temp) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as handle:
                handle.write(manifest_path, "manifest.json")
                for source, member in rows:
                    handle.write(source, member)
        return PackageResult(archive=archive, manifest=manifest, files=tuple(["manifest.json", *members]))


def _matching_files(root: Path, patterns: tuple[str, ...]) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file() and any(
        fnmatch.fnmatch(path.relative_to(root).as_posix(), pattern)
        or fnmatch.fnmatch(path.name, pattern.removeprefix("**/")) for pattern in patterns))


def _safe_member(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"Unsafe ZIP member path: {value}")
    return path.as_posix()


def _member_root(template: str) -> str:
    return _safe_member(template.split("{relative}", 1)[0].rstrip("/"))


def _shot_role(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".ma", ".mb"}: return "maya"
    if suffix == ".aep": return "after_effects"
    if suffix in {".abc", ".fbx"}: return "cache"
    if suffix in {".usd", ".usda", ".usdc", ".usdz"}: return "usd"
    if suffix in {".exr", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".tga"}: return "image_sequence"
    return "file"

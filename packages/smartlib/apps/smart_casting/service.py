from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smartlib.apps.asset_manager import AssetCreateRequest, AssetManagerService
from smartlib.apps.shot_manager import SequenceIdentity, ShotIdentity, ShotManagerService
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json, write_json
from smartlib.core.path_resolver import AssetIdentity, ProjectPaths

CAST_CONTEXTS = ("FAST", "WORK", "FINAL")


@dataclass(frozen=True)
class CastingAsset:
    category: str
    group: str
    asset: str
    variant: str
    status: str = ""
    description: str = ""
    thumbnail: str = ""
    path: str = ""

    @property
    def cast_payload(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "group": self.group,
            "asset": self.asset,
            "variant": self.variant or "default",
        }


class SmartCastingService:
    def __init__(self, project_config: ProjectConfig):
        self.project_config = project_config
        project_root = project_config.project_root
        if project_root is None:
            raise RuntimeError("project_root is not set in templates_base.yml")
        self.paths = ProjectPaths(
            project_root,
            templates=project_config.templates,
            project_name=project_config.project_name,
        )
        self.asset_service = AssetManagerService(project_config)
        self.shot_service = ShotManagerService(project_config)

    def list_assets(self) -> list[CastingAsset]:
        root = self.paths.assets_root()
        rows: list[CastingAsset] = []
        if not root.exists():
            return rows
        for asset_json in root.glob("**/asset.json"):
            asset_root = asset_json.parent
            metadata = read_json(asset_json, {}) or {}
            category = str(metadata.get("category") or asset_root.parent.parent.name)
            group = str(metadata.get("group") or asset_root.parent.name)
            asset = str(metadata.get("asset") or metadata.get("name") or asset_root.name)
            description = str(metadata.get("description") or "")
            status = str(metadata.get("status") or "Wait")
            variants = self._variant_names(asset_root)
            if not variants:
                variants = ["default"]
            for variant in variants:
                variant_data = read_json(asset_root / variant / "variant.json", {}) or {}
                rows.append(
                    CastingAsset(
                        category=category,
                        group=group,
                        asset=asset,
                        variant=variant,
                        status=str(variant_data.get("status") or status),
                        description=str(variant_data.get("description") or description),
                        thumbnail=str(self._asset_thumbnail_path(asset_root) or ""),
                        path=str(asset_root),
                    )
                )
        return sorted(rows, key=lambda row: (row.category.lower(), row.group.lower(), row.asset.lower(), row.variant.lower()))

    def list_variants(self, category: str, group: str, asset: str) -> list[str]:
        asset_root = self.paths.asset_root(AssetIdentity(category, group, asset))
        if not asset_root.exists():
            return []
        return self._variant_names(asset_root)

    def categories(self) -> list[str]:
        return sorted({row.category for row in self.list_assets()})

    def sequences(self) -> list[SequenceIdentity]:
        return self.shot_service.list_sequences()

    def shots_for_sequence(self, episode: str, sequence: str) -> list[ShotIdentity]:
        return [shot for shot in self.shot_service.list_shots() if shot.episode == episode and shot.sequence == sequence]

    def create_asset(self, category: str, group: str, asset: str, variant: str = "default", description: str = ""):
        return self.asset_service.create_asset(AssetCreateRequest(category, group, asset, variant or "default", description))

    def create_variant(self, category: str, group: str, asset: str, variant: str, description: str = ""):
        return self.asset_service.create_variant(AssetCreateRequest(category, group, asset, variant, description))

    def asset_root(self, row: CastingAsset) -> Path:
        if row.path:
            path = Path(row.path)
            if path.exists():
                return path
        return self.paths.asset_root(AssetIdentity(row.category, row.group, row.asset))

    def asset_json_path(self, row: CastingAsset) -> Path:
        return self.asset_root(row) / "asset.json"

    def asset_metadata(self, row: CastingAsset) -> dict[str, Any]:
        return read_json(self.asset_json_path(row), {}) or {}

    def custom_metadata(self, row: CastingAsset) -> dict[str, Any]:
        data = self.asset_metadata(row)
        custom = data.get("metadata") or {}
        return dict(custom) if isinstance(custom, dict) else {}

    def write_custom_metadata(self, row: CastingAsset, custom: dict[str, Any]) -> Path:
        path = self.asset_json_path(row)
        data = read_json(path, {}) or {}
        data["metadata"] = custom
        return write_json(path, data)

    def asset_thumbnail_path(self, category: str, group: str, asset: str) -> Path | None:
        asset_root = self.paths.asset_root(AssetIdentity(category, group, asset))
        return self._asset_thumbnail_path(asset_root)

    @staticmethod
    def _variant_names(asset_root: Path) -> list[str]:
        if not asset_root.exists():
            return []
        variants = [path.name for path in asset_root.iterdir() if path.is_dir() and (path / "variant.json").exists()]
        return sorted(variants, key=lambda name: (name != "default", name.lower()))

    @staticmethod
    def _asset_thumbnail_path(asset_root: Path) -> Path | None:
        data = read_json(asset_root / "asset.json", {}) or {}
        thumb = str(data.get("thumbnail") or "").strip()
        candidates = []
        if thumb:
            candidates.extend([Path(thumb), asset_root / thumb])
        candidates.extend(asset_root / name for name in ("thumbnail.jpg", "thumbnail.jpeg", "thumbnail.png"))
        return next((path for path in candidates if path.exists()), None)

    def set_thumbnail(self, row: CastingAsset, source: str | Path) -> Path:
        source_path = Path(source)
        if not source_path.exists():
            raise FileNotFoundError(f"Thumbnail source was not found: {source_path}")
        asset_root = self.asset_root(row)
        suffix = source_path.suffix.lower() or ".jpg"
        target = asset_root / f"thumbnail{suffix}"
        if source_path.resolve() != target.resolve():
            shutil.copy2(source_path, target)
        data = read_json(asset_root / "asset.json", {}) or {}
        data["thumbnail"] = target.name
        write_json(asset_root / "asset.json", data)
        return target

    def add_assets_to_sequence_cast(self, episode: str, sequence: str, assets: list[CastingAsset]):
        selections = [row.cast_payload for row in assets]
        return self.shot_service.add_asset_selections_to_sequence_cast(episode, sequence, selections)

    def sequence_cast_rows(self, episode: str, sequence: str) -> list[dict[str, Any]]:
        cast_data = self.load_sequence_cast(episode, sequence)
        rows = []
        asset_rows = self.list_assets()
        for cast_key, entry in sorted((cast_data.get("cast") or {}).items()):
            row = {"cast_key": str(cast_key), **dict(entry)}
            asset_row = self._asset_for_cast_entry(row, asset_rows)
            if asset_row:
                row.setdefault("category", asset_row.category)
                row.setdefault("group", asset_row.group)
                row.setdefault("thumbnail", asset_row.thumbnail)
                row.setdefault("description", asset_row.description)
            row["contexts"] = self.cast_context_statuses(row)
            rows.append(row)
        return rows

    def cast_context_statuses(self, entry: dict[str, Any]) -> dict[str, str]:
        asset_name = str(entry.get("asset") or "")
        variant = str(entry.get("variant") or "default")
        asset_publish = str(entry.get("asset_publish") or "approved")
        asset_root = self.shot_service.find_asset_root(asset_name)
        if not asset_root:
            return {context: "Missing" for context in CAST_CONTEXTS}
        variant_root = asset_root / variant
        if not variant_root.exists():
            return {context: "Missing" for context in CAST_CONTEXTS}
        return {
            context: self._context_status(variant_root, context, asset_publish)
            for context in CAST_CONTEXTS
        }

    def _asset_for_cast_entry(
        self,
        entry: dict[str, Any],
        asset_rows: list[CastingAsset] | None = None,
    ) -> CastingAsset | None:
        asset = str(entry.get("asset") or "")
        variant = str(entry.get("variant") or "default")
        rows = asset_rows if asset_rows is not None else self.list_assets()
        return next((row for row in rows if row.asset == asset and row.variant == variant), None)

    def _context_status(self, variant_root: Path, context: str, asset_publish: str) -> str:
        context_root = variant_root / "publish" / "asset" / context.lower()
        requested = str(asset_publish or "approved").strip().lower()
        if not context_root.exists():
            return "Missing"
        if requested.startswith("v") and requested[1:].isdigit():
            return "Ready" if _preferred_publish_file(context_root / requested) else self._wip_or_missing(context_root)
        if requested in {"approved", "released", "stable"}:
            versions = read_json(context_root / "versions.json", []) or []
            for row in versions if isinstance(versions, list) else []:
                if not isinstance(row, dict):
                    continue
                status = str(row.get("status") or "").strip().lower()
                if status != requested:
                    continue
                if _preferred_publish_file(context_root / str(row.get("version") or "")):
                    return "Ready"
            return self._wip_or_missing(context_root)
        resolved = self.shot_service.asset_publish_resolver.resolve_context(
            variant_root,
            context,
            version=requested or "latest",
        )
        return "Ready" if resolved else self._wip_or_missing(context_root)

    @staticmethod
    def _wip_or_missing(context_root: Path) -> str:
        latest = read_json(context_root / "latest.json", {}) or {}
        latest_path = context_root / str(latest.get("path") or "")
        if latest_path.is_file():
            return "WIP"
        latest_version = str(latest.get("version") or "")
        if latest_version and _preferred_publish_file(context_root / latest_version):
            return "WIP"
        versions = read_json(context_root / "versions.json", []) or []
        for row in versions if isinstance(versions, list) else []:
            if isinstance(row, dict) and _preferred_publish_file(context_root / str(row.get("version") or "")):
                return "WIP"
        return "Missing"

    def remove_sequence_cast(self, episode: str, sequence: str, cast_keys: list[str]) -> Path:
        cast_data = self.load_sequence_cast(episode, sequence)
        cast = dict(cast_data.get("cast") or {})
        for key in cast_keys:
            cast.pop(key, None)
        review_layers = cast_data.get("review_layers") or {}
        for layer in review_layers.values():
            members = list(layer.get("members") or [])
            layer["members"] = [member for member in members if member not in cast_keys]
        return self.shot_service.write_sequence_cast(episode, sequence, {"cast": cast, "review_layers": review_layers})

    def load_sequence_cast(self, episode: str, sequence: str) -> dict[str, Any]:
        return self.shot_service.load_sequence_cast(episode, sequence)

    def save_sequence_cast(self, episode: str, sequence: str, rows: list[dict[str, Any]]) -> Path:
        existing = self.load_sequence_cast(episode, sequence)
        return self.shot_service.write_sequence_cast(
            episode,
            sequence,
            self.shot_service.build_cast_data(rows, existing=existing),
        )

    def publish_sequence_cast(self, episode: str, sequence: str, comment: str = "") -> Path:
        return self.shot_service.publish_sequence_cast(episode, sequence, comment=comment)

    def load_shot_cast(self, identity: ShotIdentity) -> dict[str, Any]:
        return self.shot_service.load_cast(identity)

    def save_shot_cast(self, identity: ShotIdentity, rows: list[dict[str, Any]]) -> Path:
        return self.shot_service.write_cast(identity, self.shot_service.build_cast_data(rows, existing=self.load_shot_cast(identity)))

    def publish_shot_cast(self, identity: ShotIdentity, comment: str = "") -> Path:
        return self.shot_service.publish_shot_cast_from_sequence(identity, comment=comment)


def _preferred_publish_file(version_dir: Path) -> Path | None:
    if not version_dir.is_dir():
        return None
    files = [path for path in version_dir.iterdir() if path.is_file()]
    for suffix in (".ma", ".mb", ".usd", ".usda", ".usdc", ".fbx", ".abc"):
        matches = sorted(path for path in files if path.suffix.lower() == suffix)
        if matches:
            return matches[0]
    return files[0] if files else None

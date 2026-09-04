from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json
from smartlib.core.path_resolver import AssetIdentity


VERSION_RE = re.compile(r"^v\d+$", re.IGNORECASE)


@dataclass(frozen=True)
class AssetResolverRule:
    consumer: str
    department: str
    context: str
    version: str = "approved"
    formats: tuple[str, ...] = ("ma", "mb")
    fallback_contexts: tuple[str, ...] = ()
    fallback_version: str = "latest"


class AssetPublishResolver:
    """Resolve an official packed asset for a configured pipeline consumer."""

    def __init__(self, project_config: ProjectConfig):
        self.project_config = project_config

    def rules(self) -> list[AssetResolverRule]:
        rows = self.project_config.load("resolvers.yml").get("asset_resolvers") or []
        result = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            formats = row.get("formats") or ["ma", "mb"]
            fallbacks = row.get("fallback_contexts") or []
            if isinstance(formats, str):
                formats = [formats]
            if isinstance(fallbacks, str):
                fallbacks = [fallbacks]
            result.append(
                AssetResolverRule(
                    consumer=str(row.get("consumer") or "shot").strip().lower(),
                    department=str(row.get("department") or "default").strip().lower(),
                    context=str(row.get("context") or "work").strip().lower(),
                    version=str(row.get("version") or "approved").strip().lower(),
                    formats=tuple(str(value).strip().lower().lstrip(".") for value in formats if str(value).strip()),
                    fallback_contexts=tuple(str(value).strip().lower() for value in fallbacks if str(value).strip()),
                    fallback_version=str(row.get("fallback_version") or "latest").strip().lower(),
                )
            )
        return result

    def rule_for(self, consumer: str, department: str) -> AssetResolverRule | None:
        consumer = str(consumer or "shot").strip().lower()
        department = str(department or "default").strip().lower()
        rows = self.rules()
        for row in rows:
            if row.consumer == consumer and row.department == department:
                return row
        for row in rows:
            if row.consumer == consumer and row.department == "default":
                return row
        return None

    def resolve(
        self,
        variant_root: str | Path,
        *,
        consumer: str = "shot",
        department: str = "default",
        version: str | None = None,
    ) -> Path | None:
        rule = self.rule_for(consumer, department)
        if rule is None:
            return None
        publish_root = Path(variant_root) / "publish" / "asset"
        requested_version = str(version or rule.version).strip().lower()
        contexts = [
            self._asset_context_for_stage(Path(variant_root), value)
            for value in (rule.context, *rule.fallback_contexts)
        ]
        for index, context in enumerate(dict.fromkeys(contexts)):
            alias = requested_version if index == 0 else rule.fallback_version
            path = self._resolve_context(publish_root / context, alias, rule.formats)
            if path:
                return path
        return None

    def identity_from_publish_path(self, publish_path: str | Path) -> AssetIdentity | None:
        """Resolve an Asset identity from its canonical asset.json ancestor."""
        path = Path(publish_path)
        for parent in (path.parent, *path.parents):
            metadata_path = parent / "asset.json"
            if not metadata_path.is_file():
                continue
            metadata = read_json(metadata_path, {}) or {}
            category = str(metadata.get("category") or "").strip()
            group = str(metadata.get("group") or "").strip()
            asset = str(metadata.get("asset") or metadata.get("name") or "").strip()
            if not category or not group or not asset:
                return None
            variant = str(metadata.get("default_variant") or "default").strip() or "default"
            for candidate in path.parents:
                if candidate.parent == parent and candidate.name:
                    variant = candidate.name
                    break
            return AssetIdentity(category, group, asset, variant)
        return None

    def _asset_context_for_stage(self, variant_root: Path, value: str) -> str:
        """Translate a Stage Profile to the appropriate per-asset Context."""

        requested = str(value or "").strip()
        if requested.upper() not in {"FAST", "WORK", "REND", "FINAL"}:
            return requested.lower()
        try:
            from smartlib.apps.asset_manager.context import AssetContextService
            from smartlib.core.path_resolver import AssetIdentity

            asset_root = variant_root.parent
            metadata = read_json(asset_root / "asset.json", {}) or {}
            identity = AssetIdentity(
                str(metadata.get("category") or asset_root.parents[1].name),
                str(metadata.get("group") or asset_root.parent.name),
                str(metadata.get("asset") or metadata.get("name") or asset_root.name),
                variant_root.name,
            )
            return AssetContextService(self.project_config).stage_context_for_asset(
                identity, requested
            ).lower()
        except (FileNotFoundError, KeyError, TypeError, ValueError, IndexError):
            return requested.lower()

    def resolve_context(
        self,
        variant_root: str | Path,
        context: str,
        *,
        version: str = "approved",
        formats: tuple[str, ...] = ("ma", "mb"),
    ) -> Path | None:
        """Resolve an explicitly selected packed-asset context."""

        context = str(context or "work").strip().lower()
        version = str(version or "approved").strip().lower()
        normalized_formats = tuple(
            str(value).strip().lower().lstrip(".")
            for value in formats
            if str(value).strip()
        ) or ("ma", "mb")
        publish_root = Path(variant_root) / "publish" / "asset"
        return self._resolve_context(
            publish_root / context,
            version,
            normalized_formats,
        )

    def _resolve_context(self, context_root: Path, version: str, formats: tuple[str, ...]) -> Path | None:
        if not context_root.exists():
            return None
        if VERSION_RE.match(version):
            return _preferred_file(context_root / version, formats)
        if version in {"approved", "released", "stable"}:
            versions = _read_json(context_root / "versions.json", [])
            accepted = {version, "latest"} if version != "latest" else {"latest"}
            candidates = []
            for row in versions if isinstance(versions, list) else []:
                if not isinstance(row, dict) or str(row.get("status") or "").lower() not in accepted:
                    continue
                candidate = _preferred_file(context_root / str(row.get("version") or ""), formats)
                if candidate:
                    candidates.append(candidate)
            if candidates:
                return candidates[-1]
        latest = _read_json(context_root / "latest.json", {})
        if isinstance(latest, dict):
            relative = str(latest.get("path") or "").strip()
            if relative:
                candidate = context_root / relative
                if candidate.is_file() and candidate.suffix.lower().lstrip(".") in formats:
                    return candidate
            latest_version = str(latest.get("version") or "").strip()
            if latest_version:
                candidate = _preferred_file(context_root / latest_version, formats)
                if candidate:
                    return candidate
        versions = sorted(path for path in context_root.glob("v[0-9]*") if path.is_dir())
        return _preferred_file(versions[-1], formats) if versions else None


def _preferred_file(version_dir: Path, formats: tuple[str, ...]) -> Path | None:
    if not version_dir.is_dir():
        return None
    files = [path for path in version_dir.iterdir() if path.is_file()]
    for extension in formats:
        matches = sorted(path for path in files if path.suffix.lower() == f".{extension}")
        if matches:
            return matches[0]
    return None


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return default

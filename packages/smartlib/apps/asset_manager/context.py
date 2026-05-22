from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smartlib.core.config_loader import ProjectConfig, load_config
from smartlib.core.metadata import read_json, write_json
from smartlib.core.path_resolver import AssetIdentity, ProjectPaths
from smartlib.core.versioning import format_version, next_version, parse_version


@dataclass(frozen=True)
class AssetContextEntry:
    publish_type: str
    requested_subset: str
    resolved_subset: str
    version: str
    status: str
    path: str
    files: dict[str, str]
    latest_version: str = ""
    comment: str = ""
    message: str = ""


@dataclass(frozen=True)
class AssetContextAssembly:
    identity: AssetIdentity
    context_name: str
    context_version: str
    quality_profile: str
    entries: list[AssetContextEntry]
    errors: list[str]
    manifest: dict[str, Any]


@dataclass(frozen=True)
class PackedAssetContext:
    version_dir: Path
    manifest_path: Path
    publish_json: Path


class AssetContextService:
    """Resolve and snapshot asset context assemblies without importing DCC APIs."""

    def __init__(self, project_config: ProjectConfig):
        self.project_config = project_config
        project_root = project_config.project_root
        if project_root is None:
            raise RuntimeError("project_root is not set in templates_base.yml")
        self.paths = ProjectPaths(project_root)

    def context_versions(self, context_name: str = "asset") -> list[str]:
        context_dir = self.context_dir(context_name)
        if not context_dir.exists():
            return []
        return sorted(path.stem for path in context_dir.glob("v*.yml") if path.is_file())

    def active_context_version(self, context_name: str = "asset") -> str:
        project_settings = self.project_config.load("project_settings.yml")
        active = (project_settings.get("active_contexts") or {}).get(context_name)
        if active:
            return str(active)
        versions = self.context_versions(context_name)
        if not versions:
            raise FileNotFoundError(f"No context versions found: {self.context_dir(context_name)}")
        return versions[-1]

    def context_dir(self, context_name: str) -> Path:
        return self.project_config.config_dir / "contexts" / context_name

    def load_context(self, context_name: str = "asset", version: str | None = None) -> dict[str, Any]:
        resolved_version = version or self.active_context_version(context_name)
        path = self.context_dir(context_name) / f"{resolved_version}.yml"
        data = load_config(path)
        if not data:
            raise FileNotFoundError(f"Context config was not found or empty: {path}")
        data.setdefault("name", context_name)
        data["_version_label"] = resolved_version
        data["_path"] = str(path)
        return data

    def quality_profiles(self, context_name: str = "asset", version: str | None = None) -> list[str]:
        context = self.load_context(context_name, version)
        return list((context.get("quality_profiles") or {}).keys())

    def assemble(
        self,
        identity: AssetIdentity,
        *,
        quality_profile: str,
        context_name: str = "asset",
        context_version: str | None = None,
    ) -> AssetContextAssembly:
        context = self.load_context(context_name, context_version)
        version_label = str(context.get("_version_label"))
        profiles = context.get("quality_profiles") or {}
        profile = profiles.get(quality_profile)
        if not isinstance(profile, dict):
            raise KeyError(f"Quality profile was not found: {context_name}/{version_label}/{quality_profile}")

        entries = []
        errors = []
        for publish_type, requested_subset in profile.items():
            requested = str(requested_subset)
            entry = self._resolve_representation(identity, context, str(publish_type), requested)
            entries.append(entry)
            if entry.status == "MISSING":
                errors.append(entry.message or f"Missing {entry.publish_type}/{entry.requested_subset}")

        manifest = {
            "asset": identity.name,
            "category": identity.category,
            "group": identity.group,
            "variant": identity.variant,
            "context": {
                "name": str(context.get("name") or context_name),
                "version": version_label,
                "quality_profile": quality_profile,
                "config": str(context.get("_path")),
            },
            "resolved_representations": [
                {
                    "publish_type": entry.publish_type,
                    "requested_subset": entry.requested_subset,
                    "resolved_subset": entry.resolved_subset,
                    "version": entry.version,
                    "latest_version": entry.latest_version,
                    "status": entry.status,
                    "path": entry.path,
                    "files": entry.files,
                    "comment": entry.comment,
                    "message": entry.message,
                }
                for entry in entries
            ],
            "validation": {
                "status": "ERROR" if errors else "OK",
                "errors": errors,
            },
        }
        return AssetContextAssembly(
            identity=identity,
            context_name=context_name,
            context_version=version_label,
            quality_profile=quality_profile,
            entries=entries,
            errors=errors,
            manifest=manifest,
        )

    def pack(self, assembly: AssetContextAssembly) -> PackedAssetContext:
        if assembly.errors:
            raise RuntimeError("Context pack is blocked by unresolved representations.")
        if not self.has_pack_changes(assembly):
            raise RuntimeError("Context pack is unchanged from the latest pack.")
        subset = f"{assembly.context_name}_{assembly.quality_profile.lower()}"
        base_dir = self.paths.asset_publish_dir(assembly.identity, "asset", subset)
        versions = [
            parse_version(path.name)
            for path in base_dir.glob("v*")
            if path.is_dir() and parse_version(path.name) is not None
        ]
        version_label = format_version(next_version([version for version in versions if version]))
        version_dir = base_dir / version_label
        manifest_path = write_json(version_dir / "build_manifest.json", assembly.manifest)
        publish_data = {
            "asset": assembly.identity.name,
            "publish_type": "asset",
            "subset": subset,
            "variant": assembly.identity.variant,
            "version": version_label,
            "files": {
                "build_manifest": manifest_path.name,
            },
            "context": assembly.manifest["context"],
        }
        publish_json = write_json(version_dir / "publish.json", publish_data)
        write_json(base_dir / "latest.json", {"version": version_label, "path": f"{version_label}/build_manifest.json"})
        self._update_versions(base_dir / "versions.json", version_label)
        return PackedAssetContext(version_dir=version_dir, manifest_path=manifest_path, publish_json=publish_json)

    def has_pack_changes(self, assembly: AssetContextAssembly) -> bool:
        packs = self.list_packs(
            assembly.identity,
            quality_profile=assembly.quality_profile,
            context_name=assembly.context_name,
        )
        if not packs:
            return True
        latest_manifest = packs[0]["manifest"]
        return self._representation_signature(assembly.manifest) != self._representation_signature(latest_manifest)

    def list_packs(self, identity: AssetIdentity, *, quality_profile: str, context_name: str = "asset") -> list[dict]:
        subset = f"{context_name}_{quality_profile.lower()}"
        base_dir = self.paths.asset_publish_dir(identity, "asset", subset)
        packs = []
        for version_dir in sorted(base_dir.glob("v*"), reverse=True):
            manifest_path = version_dir / "build_manifest.json"
            manifest = read_json(manifest_path, {})
            if not version_dir.is_dir() or not isinstance(manifest, dict) or not manifest:
                continue
            packs.append(
                {
                    "version": version_dir.name,
                    "manifest": manifest,
                    "manifest_path": str(manifest_path),
                    "comment": str((read_json(version_dir / "publish.json", {}) or {}).get("comment") or ""),
                }
            )
        return packs

    def entries_from_manifest(self, identity: AssetIdentity, manifest: dict[str, Any]) -> list[AssetContextEntry]:
        entries = []
        for data in manifest.get("resolved_representations") or []:
            if not isinstance(data, dict):
                continue
            publish_type = str(data.get("publish_type") or "")
            resolved_subset = str(data.get("resolved_subset") or data.get("requested_subset") or "")
            latest = self._latest_publish(identity, publish_type, resolved_subset) if publish_type and resolved_subset else None
            entries.append(
                AssetContextEntry(
                    publish_type=publish_type,
                    requested_subset=str(data.get("requested_subset") or ""),
                    resolved_subset=resolved_subset,
                    version=str(data.get("version") or ""),
                    status=str(data.get("status") or "PACKED"),
                    path=str(data.get("path") or ""),
                    files=dict(data.get("files") or {}),
                    latest_version=str((latest or {}).get("version") or ""),
                    comment=str(data.get("comment") or ""),
                    message=str(data.get("message") or ""),
                )
            )
        return entries

    def _resolve_representation(
        self,
        identity: AssetIdentity,
        context: dict[str, Any],
        publish_type: str,
        requested_subset: str,
    ) -> AssetContextEntry:
        resolved = self._latest_publish(identity, publish_type, requested_subset)
        if resolved:
            return self._entry_from_publish(publish_type, requested_subset, requested_subset, "RESOLVED", resolved)

        fallback_subset = self._fallback_subset(context, publish_type, requested_subset)
        if fallback_subset:
            fallback = self._latest_publish(identity, publish_type, fallback_subset)
            if fallback:
                return self._entry_from_publish(publish_type, requested_subset, fallback_subset, "FALLBACK", fallback)

        return AssetContextEntry(
            publish_type=publish_type,
            requested_subset=requested_subset,
            resolved_subset="",
            version="",
            status="MISSING",
            path="",
            files={},
            message=f"Missing publish: {publish_type}/{requested_subset}",
        )

    def _latest_publish(self, identity: AssetIdentity, publish_type: str, subset: str) -> dict[str, Any] | None:
        base_dir = self.paths.asset_publish_dir(identity, publish_type, subset)
        latest = read_json(base_dir / "latest.json", {})
        version = str((latest or {}).get("version") or "")
        if not version:
            return None
        version_dir = base_dir / version
        record = read_json(version_dir / "publish.json", {})
        if not isinstance(record, dict) or not record:
            return None
        files = {
            str(name): str((version_dir / Path(path).name).as_posix())
            for name, path in (record.get("files") or {}).items()
        }
        return {
            "version": version,
            "path": str(version_dir.as_posix()),
            "files": files,
            "comment": str(record.get("comment") or ""),
        }

    @staticmethod
    def _entry_from_publish(
        publish_type: str,
        requested_subset: str,
        resolved_subset: str,
        status: str,
        publish: dict[str, Any],
    ) -> AssetContextEntry:
        return AssetContextEntry(
            publish_type=publish_type,
            requested_subset=requested_subset,
            resolved_subset=resolved_subset,
            version=str(publish.get("version") or ""),
            status=status,
            path=str(publish.get("path") or ""),
            files=dict(publish.get("files") or {}),
            latest_version=str(publish.get("version") or ""),
            comment=str(publish.get("comment") or ""),
        )

    @staticmethod
    def _fallback_subset(context: dict[str, Any], publish_type: str, subset: str) -> str:
        fallbacks = context.get("fallbacks") or {}
        by_type = fallbacks.get(publish_type) or {}
        if isinstance(by_type, dict):
            return str(by_type.get(subset) or "")
        return ""

    @staticmethod
    def _representation_signature(manifest: dict[str, Any]) -> tuple[tuple[str, str, str, str], ...]:
        rows = []
        for entry in manifest.get("resolved_representations") or []:
            if not isinstance(entry, dict):
                continue
            rows.append(
                (
                    str(entry.get("publish_type") or ""),
                    str(entry.get("requested_subset") or ""),
                    str(entry.get("resolved_subset") or ""),
                    str(entry.get("version") or ""),
                )
            )
        return tuple(sorted(rows))

    @staticmethod
    def _update_versions(path: Path, version_label: str) -> Path:
        versions = read_json(path, [])
        next_rows = []
        for row in versions if isinstance(versions, list) else []:
            if not isinstance(row, dict):
                continue
            status = "approved" if row.get("status") == "latest" else row.get("status", "")
            next_rows.append({"version": row.get("version"), "status": status})
        next_rows.append({"version": version_label, "status": "latest"})
        return write_json(path, next_rows)

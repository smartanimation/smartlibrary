from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from smartlib.core.config_loader import (
    ProjectConfig,
    deep_merge,
    default_config_dir,
    load_config,
    studio_config_dir,
)
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
    scene_path: Path
    usd_path: Path
    publish_json: Path


@dataclass(frozen=True)
class AssembledAssetContext:
    assembly_dir: Path
    manifest_path: Path
    scene_path: Path
    assembly_json: Path


class AssetContextService:
    """Resolve and snapshot asset context assemblies without importing DCC APIs."""

    MAYA_SNAPSHOT_REVISION = 5
    USD_PACK_REVISION = 3

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

    def context_versions(self, context_name: str = "asset") -> list[str]:
        versions = {
            path.stem
            for context_dir in self.context_dirs(context_name)
            if context_dir.exists()
            for path in context_dir.glob("v*.yml")
            if path.is_file()
        }
        return sorted(versions)

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

    def context_dirs(self, context_name: str) -> list[Path]:
        """Return context roots from lowest to highest override priority."""

        roots = [default_config_dir()]
        studio_dir = studio_config_dir()
        if studio_dir and studio_dir != default_config_dir():
            roots.append(studio_dir)
        if self.project_config.config_dir not in roots:
            roots.append(self.project_config.config_dir)
        return [root / "contexts" / context_name for root in roots]

    def load_context(self, context_name: str = "asset", version: str | None = None) -> dict[str, Any]:
        resolved_version = version or self.active_context_version(context_name)
        paths = [self._context_layer_path(context_dir, resolved_version) for context_dir in self.context_dirs(context_name)]
        existing_paths = [path for path in paths if path is not None]
        data: dict[str, Any] = {}
        for path in existing_paths:
            data = deep_merge(data, load_config(path))
        if not data:
            searched = ", ".join(str(path) for path in paths)
            raise FileNotFoundError(
                f"Context config was not found or empty: {context_name}/{resolved_version}. "
                f"Searched: {searched}"
            )
        data.setdefault("name", context_name)
        data["_version_label"] = resolved_version
        data["_path"] = str(existing_paths[-1])
        data["_source_paths"] = [str(path) for path in existing_paths]
        return data

    @staticmethod
    def _context_layer_path(context_dir: Path, resolved_version: str) -> Path | None:
        """Resolve the newest context layer not newer than the requested version.

        Project context versions are independent from the bundled default version.
        For example, project ``v004`` must still inherit bundled ``v001`` when no
        bundled ``v004`` exists.
        """

        exact = context_dir / f"{resolved_version}.yml"
        if exact.is_file():
            return exact
        requested = parse_version(resolved_version)
        candidates: list[tuple[int, Path]] = []
        if context_dir.is_dir():
            for path in context_dir.glob("v*.yml"):
                candidate = parse_version(path.stem)
                if candidate is None or (requested is not None and candidate > requested):
                    continue
                candidates.append((candidate, path))
        return max(candidates, key=lambda item: item[0])[1] if candidates else None

    def quality_profiles(self, context_name: str = "asset", version: str | None = None) -> list[str]:
        context = self.load_context(context_name, version)
        return list((context.get("quality_profiles") or {}).keys())

    def quality_profiles_for_asset(
        self,
        identity: AssetIdentity,
        context_name: str = "asset",
        version: str | None = None,
    ) -> list[str]:
        context = self.load_context(context_name, version)
        _asset_class, profiles = self._profiles_for_identity(identity, context)
        return list(profiles.keys())

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
        asset_class, profiles = self._profiles_for_identity(identity, context)
        profile = profiles.get(quality_profile)
        if not isinstance(profile, dict):
            raise KeyError(f"Quality profile was not found: {context_name}/{version_label}/{quality_profile}")

        entries = []
        errors = []
        for publish_type, requested_subset in profile.items():
            requested = self._requested_subset_for_identity(identity, requested_subset)
            if self._is_disabled_representation(requested_subset, requested):
                entry = AssetContextEntry(
                    publish_type=str(publish_type),
                    requested_subset="none",
                    resolved_subset="",
                    version="",
                    status="SKIPPED",
                    path="",
                    files={},
                    message="Disabled by the quality profile.",
                )
            else:
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
                "asset_class": asset_class,
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

    def pack(
        self,
        assembly: AssetContextAssembly,
        *,
        assembled: AssembledAssetContext | None = None,
        maya_usd_builder: Callable[[Path, Path, str], Path] | None = None,
    ) -> PackedAssetContext:
        if assembly.errors:
            raise RuntimeError("Context pack is blocked by unresolved representations.")
        if not self.has_pack_changes(assembly):
            raise RuntimeError("Context pack is unchanged from the latest pack.")
        assembled = assembled or self.current_assembly(assembly)
        if not assembled or not self.is_current_assembly(assembly, assembled):
            raise RuntimeError("Assemble and verify this Context before packing it.")
        subset = self._pack_subset(assembly)
        base_dir = self.paths.asset_publish_dir(assembly.identity, "asset", subset)
        versions = [
            parse_version(path.name)
            for path in base_dir.glob("v*")
            if path.is_dir() and parse_version(path.name) is not None
        ]
        version_label = format_version(next_version([version for version in versions if version]))
        version_dir = base_dir / version_label
        manifest_path = write_json(version_dir / "build_manifest.json", assembly.manifest)
        assembled_suffix = assembled.scene_path.suffix.lower()
        is_maya_snapshot = assembled_suffix in {".ma", ".mb"}
        scene_path = None
        if is_maya_snapshot:
            scene_path = version_dir / f"asset{assembled_suffix}"
            scene_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(assembled.scene_path, scene_path)
        usd_path = (
            maya_usd_builder(
                assembled.scene_path,
                version_dir / "asset.usda",
                assembly.identity.name,
            )
            if maya_usd_builder and is_maya_snapshot
            else self._write_asset_usd(assembly, version_dir / "asset.usda")
        )
        assembly_record = read_json(assembled.assembly_json, {}) or {}
        composition = dict(assembly_record.get("composition") or {})
        composition["assembly"] = str(assembled.assembly_dir.as_posix())
        composition["usd_pack_revision"] = self.USD_PACK_REVISION
        files = {
            "usd": usd_path.name,
            "build_manifest": manifest_path.name,
        }
        if scene_path:
            files[assembled_suffix.lstrip(".")] = scene_path.name
        publish_data = {
            "asset": assembly.identity.name,
            "publish_type": "asset",
            "subset": subset,
            "variant": assembly.identity.variant,
            "version": version_label,
            "files": files,
            "context": assembly.manifest["context"],
            "composition": composition,
        }
        for optional_name in ("payload.usd", "validation.json"):
            optional_path = version_dir / optional_name
            if optional_path.is_file():
                publish_data["files"][optional_name.rsplit(".", 1)[0]] = optional_name
        publish_json = write_json(version_dir / "publish.json", publish_data)
        latest_data = {
            "version": version_label,
            "path": f"{version_label}/{scene_path.name if scene_path else usd_path.name}",
            "usd": f"{version_label}/{usd_path.name}",
        }
        if scene_path:
            latest_data["scene"] = f"{version_label}/{scene_path.name}"
        write_json(base_dir / "latest.json", latest_data)
        self._update_versions(base_dir / "versions.json", version_label)
        self._set_assembly_status(assembled, "packed", version_label)
        return PackedAssetContext(
            version_dir=version_dir,
            manifest_path=manifest_path,
            scene_path=scene_path or usd_path,
            usd_path=usd_path,
            publish_json=publish_json,
        )

    @staticmethod
    def _write_asset_usd(assembly: AssetContextAssembly, target: Path) -> Path:
        """Compose resolved atomic USD representations into the Asset entry layer."""

        strength = {"look": 0, "groom": 1, "rig": 2, "assembly": 3, "model": 3, "geometry": 3}
        layers: list[tuple[int, str, Path]] = []
        missing: list[str] = []
        for entry in assembly.entries:
            entry_layer = None
            for key, raw_path in (entry.files or {}).items():
                clean_path = str(raw_path or "").strip()
                if not clean_path:
                    continue
                path = Path(clean_path)
                if key.lower() not in {"usd", "usda", "usdc"} and path.suffix.lower() not in {
                    ".usd", ".usda", ".usdc",
                }:
                    continue
                if path.is_file():
                    entry_layer = path
                    break
            if entry_layer:
                layers.append((strength.get(entry.publish_type, 10), entry.publish_type, entry_layer))
            elif entry.status in {"RESOLVED", "FALLBACK"}:
                missing.append(f"{entry.publish_type}/{entry.resolved_subset or entry.requested_subset}")
        if missing:
            raise RuntimeError(
                "Context pack requires USD for every resolved representation.\n"
                "Missing USD publish: " + ", ".join(missing)
            )
        if not layers:
            raise RuntimeError("Context pack has no USD representations to compose asset.usda.")

        target.parent.mkdir(parents=True, exist_ok=True)
        refs = [
            os.path.relpath(path, target.parent).replace("\\", "/")
            for _order, _publish_type, path in sorted(layers, key=lambda row: (row[0], row[1]))
        ]
        target.write_text(
            "\n".join(
                ["#usda 1.0", "(", "    subLayers = ["]
                + [f"        @{ref}@{',' if index < len(refs) - 1 else ''}" for index, ref in enumerate(refs)]
                + ["    ]", ")", ""]
            ),
            encoding="utf-8",
        )
        return target

    def _profiles_for_identity(
        self,
        identity: AssetIdentity,
        context: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        recipes = context.get("asset_context_recipes") or {}
        metadata = read_json(self.paths.asset_root(identity) / "asset.json", {}) or {}
        values = {
            "asset_type": str(metadata.get("asset_type") or metadata.get("type") or identity.category),
            "category": identity.category,
            "group": identity.group,
        }
        for asset_class, recipe in recipes.items():
            if not isinstance(recipe, dict) or not self._recipe_matches(recipe.get("match"), values):
                continue
            profiles = recipe.get("profiles") or {}
            if isinstance(profiles, dict) and profiles:
                return str(asset_class), profiles
        return "default", context.get("quality_profiles") or {}

    @staticmethod
    def _recipe_matches(match: Any, values: dict[str, str]) -> bool:
        if not isinstance(match, dict) or not match:
            return False
        for key, expected in match.items():
            candidates = expected if isinstance(expected, (list, tuple, set)) else [expected]
            normalized = {str(value).strip().lower() for value in candidates if str(value).strip()}
            if normalized and values.get(str(key), "").strip().lower() not in normalized:
                return False
        return True

    def write_assembly(
        self,
        assembly: AssetContextAssembly,
        *,
        maya_scene_builder: Callable[[Path, Path], Path] | None = None,
    ) -> AssembledAssetContext:
        if assembly.errors:
            raise RuntimeError("Context assembly is blocked by unresolved representations.")
        assembly_dir = self._assembly_dir(assembly)
        manifest_path = write_json(assembly_dir / "build_manifest.json", assembly.manifest)
        maya_source = self._maya_scene_entry_source(assembly)
        if maya_source:
            scene_path, scene_source = self._pack_maya_scene_entry(
                assembly,
                assembly_dir,
                maya_scene_builder=maya_scene_builder,
            )
            composition = {
                "source": str(scene_source.as_posix()),
                "maya_scene_source": str(scene_source.as_posix()),
                "mode": "maya_reference_snapshot" if maya_scene_builder else "scene_entry_copy",
                "maya_snapshot_revision": self.MAYA_SNAPSHOT_REVISION if maya_scene_builder else 0,
            }
        else:
            scene_source = self._usd_scene_entry_source(assembly)
            scene_path = self._write_asset_usd(assembly, assembly_dir / "asset.usda")
            composition = {
                "source": str(scene_source.as_posix()) if scene_source else "",
                "mode": "usd_composition_snapshot",
                "usd_pack_revision": self.USD_PACK_REVISION,
            }
        assembly_json = write_json(
            assembly_dir / "assembly.json",
            {
                "asset": assembly.identity.name,
                "publish_type": "asset",
                "subset": self._pack_subset(assembly),
                "variant": assembly.identity.variant,
                "status": "verifying",
                "files": {
                    scene_path.suffix.lower().lstrip("."): scene_path.name,
                    "build_manifest": manifest_path.name,
                },
                "context": assembly.manifest["context"],
                "composition": composition,
            },
        )
        return AssembledAssetContext(
            assembly_dir=assembly_dir,
            manifest_path=manifest_path,
            scene_path=scene_path,
            assembly_json=assembly_json,
        )

    def current_assembly(self, assembly: AssetContextAssembly) -> AssembledAssetContext | None:
        assembly_dir = self._assembly_dir(assembly)
        manifest_path = assembly_dir / "build_manifest.json"
        assembly_json = assembly_dir / "assembly.json"
        record = read_json(assembly_json, {}) or {}
        files = record.get("files") or {}
        scene_name = str(
            files.get("ma") or files.get("mb") or files.get("usd")
            or files.get("usda") or files.get("usdc") or ""
        )
        if scene_name:
            scene_path = assembly_dir / Path(scene_name).name
        else:
            scene_path = next(
                (
                    path
                    for path in (
                        assembly_dir / "asset.ma",
                        assembly_dir / "asset.mb",
                        assembly_dir / "asset.usda",
                        assembly_dir / "asset.usd",
                        assembly_dir / "asset.usdc",
                    )
                    if path.exists()
                ),
                assembly_dir / "asset.usda",
            )
        if not manifest_path.exists() or not scene_path.exists() or not assembly_json.exists():
            return None
        return AssembledAssetContext(
            assembly_dir=assembly_dir,
            manifest_path=manifest_path,
            scene_path=scene_path,
            assembly_json=assembly_json,
        )

    def is_current_assembly(
        self,
        assembly: AssetContextAssembly,
        assembled: AssembledAssetContext | None = None,
    ) -> bool:
        assembled = assembled or self.current_assembly(assembly)
        if not assembled:
            return False
        manifest = read_json(assembled.manifest_path, {}) or {}
        if self._representation_signature(assembly.manifest) != self._representation_signature(manifest):
            return False
        record = read_json(assembled.assembly_json, {}) or {}
        composition = record.get("composition") or {}
        if composition.get("mode") == "maya_reference_snapshot":
            return int(composition.get("maya_snapshot_revision") or 0) == self.MAYA_SNAPSHOT_REVISION
        return True

    def has_pack_changes(self, assembly: AssetContextAssembly) -> bool:
        packs = self.list_packs(
            assembly.identity,
            quality_profile=assembly.quality_profile,
            context_name=assembly.context_name,
        )
        if not packs:
            return True
        latest_version_dir = Path(packs[0]["manifest_path"]).parent
        publish_record = read_json(latest_version_dir / "publish.json", {}) or {}
        publish_files = publish_record.get("files") or {}
        assembled = self.current_assembly(assembly)
        requires_maya_scene = bool(assembled and assembled.scene_path.suffix.lower() in {".ma", ".mb"})
        if requires_maya_scene:
            scene_name = str(publish_files.get("ma") or publish_files.get("mb") or "")
            if not scene_name or not (latest_version_dir / Path(scene_name).name).exists():
                return True
        if not (latest_version_dir / "asset.usda").exists():
            return True
        composition = publish_record.get("composition") or {}
        if int(composition.get("usd_pack_revision") or 0) != self.USD_PACK_REVISION:
            return True
        if composition.get("mode") == "maya_reference_snapshot":
            if int(composition.get("maya_snapshot_revision") or 0) != self.MAYA_SNAPSHOT_REVISION:
                return True
        latest_manifest = packs[0]["manifest"]
        return self._representation_signature(assembly.manifest) != self._representation_signature(latest_manifest)

    def _pack_maya_scene_entry(
        self,
        assembly: AssetContextAssembly,
        version_dir: Path,
        *,
        maya_scene_builder: Callable[[Path, Path], Path] | None = None,
    ) -> tuple[Path, Path]:
        source = self._maya_scene_entry_source(assembly)
        if not source:
            raise RuntimeError("Context pack has no Maya scene representation to write an asset scene.")
        scene_suffix = source.suffix.lower() if source.suffix.lower() in {".ma", ".mb"} else ".ma"
        scene_path = version_dir / f"asset{scene_suffix}"
        scene_path.parent.mkdir(parents=True, exist_ok=True)
        if maya_scene_builder:
            maya_scene_builder(source, scene_path)
        else:
            shutil.copy2(source, scene_path)
        return scene_path, source

    def _assembly_dir(self, assembly: AssetContextAssembly) -> Path:
        return self.paths.asset_publish_dir(assembly.identity, "asset", self._pack_subset(assembly)) / "_assembly"

    @staticmethod
    def _pack_subset(assembly: AssetContextAssembly) -> str:
        profile = assembly.quality_profile.lower()
        if assembly.context_name == "asset":
            return profile
        return f"{assembly.context_name}_{profile}"

    @staticmethod
    def _set_assembly_status(assembled: AssembledAssetContext, status: str, version: str = "") -> None:
        record = read_json(assembled.assembly_json, {}) or {}
        record["status"] = status
        if version:
            record["packed_version"] = version
        write_json(assembled.assembly_json, record)

    @staticmethod
    def _maya_scene_entry_source(assembly: AssetContextAssembly) -> Path | None:
        by_type = {"rig": 0, "asset": 1, "model": 2, "look": 3, "groom": 4}
        entries = sorted(assembly.entries, key=lambda entry: (by_type.get(entry.publish_type, 99), entry.publish_type))
        for entry in entries:
            for key in ("ma", "mb"):
                raw_path = str((entry.files or {}).get(key) or "").strip()
                if not raw_path:
                    continue
                path = Path(raw_path)
                if path.is_file():
                    return path
        return None

    @staticmethod
    def _usd_scene_entry_source(assembly: AssetContextAssembly) -> Path | None:
        by_type = {"assembly": 0, "asset": 1, "model": 2, "geometry": 2, "look": 3, "groom": 4}
        entries = sorted(assembly.entries, key=lambda entry: (by_type.get(entry.publish_type, 99), entry.publish_type))
        for entry in entries:
            for key, raw_path in (entry.files or {}).items():
                path = Path(str(raw_path or "").strip())
                if not str(path):
                    continue
                if key.lower() in {"usd", "usda", "usdc"} or path.suffix.lower() in {".usd", ".usda", ".usdc"}:
                    if path.is_file():
                        return path
        return None

    def list_packs(self, identity: AssetIdentity, *, quality_profile: str, context_name: str = "asset") -> list[dict]:
        subset = quality_profile.lower() if context_name == "asset" else f"{context_name}_{quality_profile.lower()}"
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

    @staticmethod
    def _requested_subset_for_identity(identity: AssetIdentity, requested_subset: Any) -> str:
        if isinstance(requested_subset, dict):
            for key in (identity.category, identity.group, "default"):
                value = requested_subset.get(key)
                if value:
                    return str(value)
            return ""
        return str(requested_subset)

    @staticmethod
    def _is_disabled_representation(requested_subset: Any, resolved_subset: str) -> bool:
        if requested_subset is None or requested_subset is False:
            return True
        return str(resolved_subset).strip().lower() in {"none", "null", "off", "disabled"}

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

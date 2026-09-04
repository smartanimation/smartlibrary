from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable

from smartlib.apps.shot_manager import ShotIdentity, ShotManagerService
from smartlib.core.config_loader import ProjectConfig, load_config, studio_config_path
from smartlib.core.asset_categories import canonical_asset_category
from smartlib.core.metadata import read_json, write_json
from smartlib.delivery import (
    AssetContext, DeliveryEngine, DeliveryInput, DeliveryPlanner, DeliveryProfile,
    EditorialPackageBuilder, PackageProfile, ShotContext, VendorPackageBuilder,
    resolve_editorial_package_source,
)
from smartlib.core.versioning import parse_version
from smartlib.review.decisions import ReviewDecisionService
from smartlib.delivery.after_effects import AfterEffectsDeliveryAdapter


FRAME_RE = re.compile(r"(?P<frame>\d{4,})(?=\.[^.]+$)")


class SmartDeliveryService:
    def __init__(self, config_dir: str | Path):
        self.config = ProjectConfig(config_dir)
        self.shots = ShotManagerService(self.config)
        self.profile_path = self._resolve_profile_path(Path(config_dir))
        self.profile = DeliveryProfile.load(self.profile_path)

    def package_profile_names(self) -> list[str]:
        root = Path(__file__).resolve().parents[4] / "config" / "delivery" / "package_profiles"
        return [path.stem for path in sorted(root.glob("*.json"))]

    def delivery_preferences(self) -> dict:
        path = studio_config_path()
        data = load_config(path) if path else {}
        configured = dict(data.get("smart_delivery") or {})
        studio = dict(data.get("studio") or {})
        client = dict(data.get("client") or {})
        studio_id = str(studio.get("id") or configured.get("vendor") or "vendor").strip()
        client_id = str(client.get("id") or "").strip()
        return {
            "package_profile": str(configured.get("package_profile") or "vendor"),
            "studio_id": studio_id,
            "studio_name": str(studio.get("name") or studio_id),
            "client_id": client_id,
            "client_name": str(client.get("name") or client_id),
            "vendor": studio_id,
            "asset_workflow": str(configured.get("asset_workflow") or "Package ZIP"),
            "shot_workflow": str(configured.get("shot_workflow") or "Package ZIP"),
        }

    def manifest_delivery_defaults(self, manifest_path: str | Path) -> dict:
        path = Path(manifest_path)
        data = read_json(path, None)
        if not isinstance(data, dict):
            raise ValueError(f"Context manifest is not valid JSON: {path}")
        if str(data.get("schema") or "") in {
            "smartpipeline.editorial_insert.v1", "smartpipeline.editorial_insert.v2",
        }:
            return {
                "delivery_type": "Editorial",
                "episode": str(data.get("episode") or ""),
                "revision": str(data.get("timeline_revision") or data.get("revision") or ""),
                "manifest": path.as_posix(),
            }
        context = dict(data.get("context") or {})
        target = dict(data.get("target") or {})
        metadata = dict(context.get("metadata") or {})
        kind = str(data.get("package_type") or context.get("kind") or context.get("name") or "asset").lower()
        category = canonical_asset_category(target.get("category") or data.get("category") or metadata.get("category") or "character", strict=True)
        group = str(target.get("group") or data.get("group") or metadata.get("group") or "main")
        entity = str(target.get("asset") or data.get("asset") or context.get("entity") or metadata.get("asset") or "")
        variant = str(target.get("variant") or data.get("variant") or metadata.get("variant") or "default")
        scene = str((data.get("source_inputs") or {}).get("scene") or data.get("source_scene") or context.get("scene_path") or "")
        if not scene:
            for row in data.get("resolved_representations") or []:
                if str((row or {}).get("publish_type") or "") != "current_scene": continue
                files = dict((row or {}).get("files") or {})
                scene = str(files.get("ma") or files.get("mb") or "")
                if scene: break
        if kind == "shot":
            return {"delivery_type": "Shot", "scene": scene, "episode": str(target.get("episode") or metadata.get("episode") or ""),
                    "sequence": str(target.get("sequence") or metadata.get("sequence") or ""),
                    "shot": str(target.get("shot") or metadata.get("shot") or entity), "manifest": path.as_posix()}
        if not entity:
            raise ValueError("Manifest does not contain an Asset identity")
        return {"delivery_type": "Asset", "scene": scene, "category": category, "group": group,
                "asset": entity, "variant": variant, "manifest": path.as_posix()}

    def suggested_package_output(self, entity: str, *, profile: str | None = None) -> Path:
        preferences = self.delivery_preferences()
        if profile == "editorial":
            selected = self.package_profile(profile)
            recipient = selected.delivery_recipient
            process = selected.delivery_process
            for label, value in (("Editorial recipient", recipient), ("Editorial process", process)):
                if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value):
                    raise ValueError(f"Invalid {label} in package profile: {value!r}")
            batch_root = self.shots.paths.delivery_editorial_recipient_root(recipient)
            delivery_batch = self._next_dated_batch(batch_root)
            return self.shots.paths.delivery_editorial_package(
                recipient, delivery_batch, process, entity
            )

        studio_id = preferences["studio_id"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", studio_id):
            raise ValueError(f"Invalid Studio ID in studio.yml: {studio_id!r}")
        vendor_root = self.shots.paths.delivery_vendor_root(studio_id)
        delivery_batch = self._next_dated_batch(vendor_root)
        return self.shots.paths.delivery_vendor_package(studio_id, delivery_batch, entity)

    @staticmethod
    def _next_dated_batch(root: Path) -> str:
        date = datetime.now().strftime("%Y%m%d")
        numbers = []
        for row in root.glob(f"{date}_*") if root.is_dir() else []:
            suffix = row.name.rsplit("_", 1)[-1]
            if suffix.isdigit():
                numbers.append(int(suffix))
        return f"{date}_{max(numbers, default=0) + 1:02d}"

    def package_profile(self, name: str) -> PackageProfile:
        path = Path(__file__).resolve().parents[4] / "config" / "delivery" / "package_profiles" / f"{name}.json"
        if not path.is_file():
            raise FileNotFoundError(f"Smart Delivery package profile was not found: {path}")
        return PackageProfile.load(path)

    def editorial_mapping_options(self) -> list[dict]:
        root = self.shots.paths.editorial_publish_root()
        options = []
        if not root.is_dir():
            return options
        for episode_dir in sorted((path for path in root.iterdir() if path.is_dir()), key=lambda path: path.name.lower()):
            episode = episode_dir.name
            metadata_root = self.shots.paths.editorial_revisions_metadata_root(episode)
            candidates = [
                self.shots.paths.editorial_revision_mapping_path(episode, path.name)
                for path in metadata_root.glob("v*") if path.is_dir()
            ] if metadata_root.is_dir() else []
            legacy_root = self.shots.paths.editorial_episode_revisions_root(episode)
            if legacy_root.is_dir():
                candidates.extend(
                    self.shots.paths.legacy_editorial_revision_mapping_path(episode, path.name)
                    for path in legacy_root.glob("v*") if path.is_dir()
                )
            seen = set()
            for mapping in candidates:
                if not mapping.is_file() or mapping.resolve() in seen:
                    continue
                seen.add(mapping.resolve())
                try:
                    source = resolve_editorial_package_source(mapping, paths=self.shots.paths)
                except (OSError, ValueError):
                    continue
                options.append({
                    "label": f"{source.episode} / {source.timeline_revision}",
                    "episode": source.episode,
                    "timeline_revision": source.timeline_revision,
                    "path": source.mapping_path,
                    "mtime": source.mapping_path.stat().st_mtime,
                })
        return sorted(options, key=lambda row: (row["mtime"], row["episode"]), reverse=True)
    def editorial_package_summary(self, mapping_path: str | Path) -> dict:
        source = resolve_editorial_package_source(mapping_path, paths=self.shots.paths)
        return {
            "mapping": source.mapping_path,
            "episode": source.episode,
            "timeline_revision": source.timeline_revision,
            "media": source.media,
            "shots": source.shots,
            "registry": source.registry_path,
        }

    def editorial_delivery_context(self, mapping_path: str | Path) -> dict:
        summary = self.editorial_package_summary(mapping_path)
        profile = self.package_profile("editorial")
        index_path = self.shots.paths.delivery_editorial_revision_index(
            profile.delivery_recipient, profile.delivery_process,
            summary["episode"], summary["timeline_revision"],
        )
        history = read_json(index_path, {}) or {}
        numbers = []
        for row in history.get("deliveries") or []:
            value = str(row.get("delivery_revision") or "")
            if value.startswith("d") and value[1:].isdigit():
                numbers.append(int(value[1:]))
        pattern = f"{summary['episode']}_{summary['timeline_revision']}_d*.zip"
        recipient_root = self.shots.paths.delivery_editorial_recipient_root(
            profile.delivery_recipient
        )
        if recipient_root.is_dir():
            for archive in recipient_root.rglob(pattern):
                match = re.search(r"_d(\d+)\.zip$", archive.name, re.IGNORECASE)
                if match:
                    numbers.append(int(match.group(1)))
        delivery_revision = f"d{max(numbers, default=0) + 1:03d}"
        delivery_batch = self._next_dated_batch(recipient_root)
        entity = (
            f"{summary['episode']}_{summary['timeline_revision']}_{delivery_revision}"
        )
        output = self.shots.paths.delivery_editorial_package(
            profile.delivery_recipient, delivery_batch,
            profile.delivery_process, entity,
        )
        delivery_history = {"deliveries": []}
        history_root = index_path.parent
        history_paths = sorted(history_root.glob("v*.json")) if history_root.is_dir() else []
        if index_path not in history_paths:
            history_paths.append(index_path)
        for history_path in history_paths:
            data = history if history_path == index_path else (read_json(history_path, {}) or {})
            delivery_history["deliveries"].extend(data.get("deliveries") or [])
        delivery_history["deliveries"].sort(
            key=lambda row: str(row.get("created_at") or "")
        )
        delivery_shots = self._editorial_shot_delivery_states(
            summary["shots"], delivery_history
        )
        return {
            **summary,
            "delivery_shots": delivery_shots,
            "recipient": profile.delivery_recipient,
            "process": profile.delivery_process,
            "delivery_revision": delivery_revision,
            "delivery_batch": delivery_batch,
            "index_path": index_path,
            "output": output,
        }

    def _editorial_shot_delivery_states(self, shots, history: dict) -> list[dict]:
        last_by_key = {}
        for delivery in history.get("deliveries") or []:
            delivered_at = str(delivery.get("created_at") or "")
            records = list(delivery.get("shots") or [])
            if not records and delivery.get("selected_shot_keys"):
                try:
                    prior = resolve_editorial_package_source(
                        delivery.get("source_mapping"), paths=self.shots.paths,
                    )
                    selected = set(delivery.get("selected_shot_keys") or [])
                    records = [
                        {
                            "shot_key": shot.key,
                            "media_version": shot.media_version,
                            "source": shot.source.as_posix() if shot.source else "",
                        }
                        for shot in prior.shots if shot.key in selected
                    ]
                except (OSError, TypeError, ValueError):
                    records = []
            for record in records:
                key = str(record.get("shot_key") or "")
                if key:
                    last_by_key[key] = {
                        "version": str(record.get("media_version") or ""),
                        "delivered_at": delivered_at,
                        "delivery_revision": str(delivery.get("delivery_revision") or ""),
                    }

        result = []
        for shot in shots:
            last = last_by_key.get(shot.key) or {}
            delivered_version = str(last.get("version") or "")
            if not shot.available:
                status = "MISSING"
                needs_delivery = False
            elif not delivered_version:
                status = "NEVER DELIVERED"
                needs_delivery = True
            elif delivered_version != shot.media_version:
                status = f"UPDATE REQUIRED  {delivered_version} -> {shot.media_version}"
                needs_delivery = True
            else:
                status = f"DELIVERED  {delivered_version}"
                needs_delivery = False
            result.append({
                "shot": shot,
                "latest_media_version": shot.media_version,
                "last_delivered_version": delivered_version,
                "last_delivered_at": str(last.get("delivered_at") or ""),
                "last_delivery_revision": str(last.get("delivery_revision") or ""),
                "needs_delivery": needs_delivery,
                "status": status,
            })
        return result

    def suggested_editorial_output(self, mapping_path: str | Path) -> Path:
        return self.editorial_delivery_context(mapping_path)["output"]

    def build_editorial_package(
        self, *, mapping_path: str | Path, output: str | Path,
        selected_shot_keys: set[str] | None = None,
    ):
        context = self.editorial_delivery_context(mapping_path)
        result = EditorialPackageBuilder().build(
            mapping_path=mapping_path, output=output,
            recipient=context["recipient"], process=context["process"],
            delivery_revision=context["delivery_revision"],
            delivery_batch=context["delivery_batch"],
            selected_shot_keys=selected_shot_keys, paths=self.shots.paths,
        )
        archive_sha256 = hashlib.sha256(result.archive.read_bytes()).hexdigest()
        index = read_json(context["index_path"], {}) or {}
        index.setdefault("schema", "smart_delivery.editorial_history.v1")
        index["key"] = {
            "episode": context["episode"],
            "timeline_revision": context["timeline_revision"],
            "recipient": context["recipient"],
            "process": context["process"],
        }
        index.setdefault("deliveries", []).append({
            "delivery_revision": context["delivery_revision"],
            "delivery_batch": context["delivery_batch"],
            "archive": result.archive.as_posix(),
            "archive_sha256": archive_sha256,
            "source_mapping": Path(mapping_path).as_posix(),
            "selected_shot_keys": list((result.manifest.get("selection") or {}).get("selected_shot_keys") or []),
            "shots": [
                {
                    "shot_key": row.get("shot_key"),
                    "shot": row.get("shot"),
                    "media_version": row.get("media_version"),
                    "source": row.get("source"),
                }
                for row in result.manifest.get("files") or []
                if row.get("role") == "editorial_hud"
            ],
            "created_at": datetime.now().astimezone().isoformat(),
        })
        write_json(context["index_path"], index)
        return result
    def build_exchange_asset(self, *, profile: str, scene: str | Path,
                             texture_root: str | Path | None, output: str | Path,
                             category: str, group: str, asset: str, variant: str,
                             subset: str = "", comment: str = "", assembly: bool = False):
        selected = self.package_profile(profile)
        return VendorPackageBuilder(selected).build_asset(
            scene=scene, texture_root=texture_root, output=output,
            project=self.config.project_name, category=category, group=group,
            asset=asset, variant=variant, subset=subset or None, comment=comment, assembly=assembly,
        )

    def build_exchange_shot(self, *, profile: str, sources: Iterable[str | Path],
                            output: str | Path, identity: ShotIdentity,
                            department: str, subset: str = "", comment: str = ""):
        selected = self.package_profile(profile)
        target = {
            "target_type": "Shot", "project": self.config.project_name,
            "episode": identity.episode, "sequence": identity.sequence, "shot": identity.shot,
            "department": department, "subset": subset or selected.asset_subset,
        }
        return VendorPackageBuilder(selected).build_shot(
            sources=sources, output=output, target=target, comment=comment,
        )

    @staticmethod
    def _resolve_profile_path(config_dir: Path) -> Path:
        root = config_dir / "delivery" / "clients"
        preferred = root / "dandelione_v003.yml"
        if preferred.is_file():
            return preferred
        profiles = sorted((*root.glob("*.yml"), *root.glob("*.yaml"))) if root.is_dir() else []
        if profiles:
            return profiles[0]
        raise FileNotFoundError(
            "No Client Delivery Profile was found. Expected a .yml file under: "
            f"{root}"
        )

    def list_shots(self) -> list[ShotIdentity]:
        return self.shots.list_shots()

    def review_layers(self, identity: ShotIdentity) -> list[str]:
        return list(self.shots.review_layers(identity).keys())

    def review_source(self, identity: ShotIdentity, department: str = "anim", profile: str = "internal") -> dict:
        root = self.shots.shot_root(identity) / "review" / department / profile
        approval = ReviewDecisionService.approval(root)
        latest = read_json(root / "latest.json", {}) or {}
        latest_path = root / str(latest.get("path") or "")
        latest_review = read_json(latest_path, {}) if latest_path.is_file() else {}
        latest_version = str((latest_review or {}).get("version") or latest.get("version") or "")
        review_path = ReviewDecisionService.approved_review(root)
        if review_path is None:
            return {
                "approved": False,
                "version": "",
                "state": "NOT_APPROVED",
                "latest_version": latest_version,
                "approval": approval,
            }
        if not review_path.is_file():
            return {}
        version_root = review_path.parent
        review = read_json(review_path, {}) or {}
        manifest_path = version_root / "source_manifest.json"
        manifest = read_json(manifest_path, {}) or {}
        layers = {
            str(layer): str((data or {}).get("sequence") or "")
            for layer, data in (manifest.get("layers") or {}).items()
            if str((data or {}).get("sequence") or "")
        }
        movie = version_root / str(review.get("movie") or "review.mov")
        return {
            "approved": True,
            "version": str(review.get("version") or version_root.name),
            "state": "APPROVED",
            "review": str(movie) if movie.is_file() else "",
            "maya": str(manifest.get("construct") or ""),
            "aep": str(manifest.get("precomp") or ""),
            "image_sequences": layers,
            "manifest": str(manifest_path),
            "approval": approval,
            "latest_version": latest_version,
        }

    def review_status(self, identity: ShotIdentity, department: str = "anim") -> str:
        source = self.review_source(identity, department)
        if source.get("approved"):
            approved = source.get("version") or "-"
            latest = source.get("latest_version") or "-"
            return f"APPROVED {approved} | LATEST {latest}"
        version = source.get("latest_version") or "no review"
        return f"NOT APPROVED | LATEST {version}"

    def suggested_sources(self, identity: ShotIdentity, department: str = "anim") -> dict[str, str]:
        review_source = self.review_source(identity, department)
        if review_source.get("approved"):
            return {kind: str(review_source.get(kind) or "") for kind in ("maya", "aep", "review")}
        if review_source:
            return {kind: "" for kind in ("maya", "aep", "review")}
        root = self.shots.shot_root(identity)
        return {
            "maya": _latest(root, {".ma", ".mb"}),
            "aep": _latest(root, {".aep"}),
            "review": _latest(root, {".mov", ".mp4"}, prefer=("review", "finalimage", "precomp")),
        }

    def suggested_image_sequences(self, identity: ShotIdentity, department: str = "anim") -> dict[str, str]:
        review_source = self.review_source(identity, department)
        if review_source.get("approved") and review_source.get("image_sequences"):
            return dict(review_source["image_sequences"])
        if review_source:
            return {}
        outputs = self.shots.latest_preview_render_outputs(identity, department=department)
        result = {}
        for layer, data in outputs.items():
            pattern = str(data.get("pattern") or data.get("first_file") or "")
            if pattern:
                result[str(layer)] = pattern
        return result

    def build_plan(
        self,
        identity: ShotIdentity,
        *,
        task: str,
        version: int,
        package_root: str | Path,
        sources: dict[str, str],
        layer_sequences: dict[str, str],
    ):
        review_source = self.review_source(identity)
        if not review_source.get("approved"):
            raise RuntimeError(f"{identity.code} has no approved Internal Review. Approve a version in Smart Review first.")
        context = ShotContext(identity.episode, identity.sequence, identity.shot, task, int(version))
        job_id = f"DLV-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8].upper()}"
        staging_root = self.shots.paths.delivery_staging_root() / job_id
        delivery_batch = self._next_delivery_batch()
        delivery_id = f"{job_id}-{identity.code}"
        archive_root = self.shots.paths.delivery_root() / "client" / self.profile.client / delivery_batch
        inputs = []
        for kind in ("maya", "aep", "review"):
            value = str(sources.get(kind) or "").strip()
            if value:
                inputs.append(DeliveryInput(f"{kind}.primary", kind if kind != "maya" else "maya_scene", Path(value), kind))
        for layer, pattern in layer_sequences.items():
            for frame_path in expand_sequence(pattern):
                match = FRAME_RE.search(frame_path.name)
                frame = match.group("frame") if match else ""
                inputs.append(
                    DeliveryInput(
                        f"image_sequence.{layer}.{frame or frame_path.name}",
                        "image_sequence",
                        frame_path,
                        "image_sequence",
                        metadata={"review_layer": layer, "frame": frame},
                    )
                )
        return DeliveryPlanner(self.profile).plan(
            context,
            inputs,
            staging_root,
            job_id=job_id,
            metadata={
                "review_approval": dict(review_source.get("approval") or {}),
                "client": self.profile.client,
                "client_shot_root": self.profile.root,
                "deployment_root": str(Path(package_root)),
                "archive_root": str(archive_root),
                "delivery_batch": delivery_batch,
                "delivery_id": delivery_id,
            },
        )

    def asset_package_summary(self, manifest_path: str | Path) -> dict:
        path = Path(manifest_path)
        data = read_json(path, None)
        if not isinstance(data, dict):
            raise ValueError(f"Asset package manifest is not valid JSON: {path}")
        if data.get("schema") != "smart_ingest.asset_package.v1" or data.get("package_type") != "asset":
            raise ValueError("Expected smart_ingest.asset_package.v1 with package_type=asset")
        target = dict(data.get("target") or {})
        required = ("category", "group", "asset", "variant")
        missing = [key for key in required if not str(target.get(key) or "").strip()]
        if missing:
            raise ValueError(f"Asset package target is missing: {', '.join(missing)}")
        return {"path": path, "data": data, "target": target}

    def build_asset_package_plan(
        self,
        manifest_path: str | Path,
        *,
        package_root: str | Path,
        version: int | None = None,
    ):
        summary = self.asset_package_summary(manifest_path)
        path, data, target = summary["path"], summary["data"], summary["target"]
        inferred_version = parse_version(path.parent.name) or 1
        delivery = dict(data.get("delivery") or {})
        task = str(delivery.get("received_from") or "client")
        context = AssetContext(
            str(target["category"]), str(target["group"]), str(target["asset"]),
            str(target.get("variant") or "default"), task, int(version or inferred_version),
        )
        inputs: list[DeliveryInput] = []
        for index, row in enumerate(data.get("files") or []):
            entry = dict(row or {})
            role = str(entry.get("role") or "file")
            relative = Path(str(entry.get("path") or ""))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Asset package file must be package-relative: {relative}")
            source = path.parent / relative
            required = bool(entry.get("required", True))
            if role == "scene":
                inputs.append(DeliveryInput(
                    f"asset.scene.{index}", "asset_scene", source, "asset_scene",
                    required=required, metadata={"source_role": role},
                ))
                continue
            if role in {"texture", "texture_root"}:
                files = [source] if source.is_file() else sorted(item for item in source.rglob("*") if item.is_file()) if source.is_dir() else []
                patterns = [str(value) for value in (entry.get("include_patterns") or []) if str(value)]
                if patterns and source.is_dir():
                    files = [
                        item for item in files
                        if any(
                            item.relative_to(source).match(pattern)
                            or item.relative_to(source).match(pattern.removeprefix("**/"))
                            for pattern in patterns
                        )
                    ]
                if not files and required:
                    inputs.append(DeliveryInput(
                        f"asset.texture.{index}.missing", "asset_texture", source,
                        "asset_texture", required=True, metadata={"relative_path": relative.name},
                    ))
                for item in files:
                    rel = item.relative_to(source).as_posix() if source.is_dir() else item.name
                    inputs.append(DeliveryInput(
                        f"asset.texture.{index}.{rel}", "asset_texture", item,
                        "asset_texture", required=required,
                        metadata={"relative_path": rel, "source_role": role},
                    ))
        if not any(item.kind == "asset_scene" for item in inputs):
            raise ValueError("Asset package manifest has no scene entry")
        job_id = f"DLV-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8].upper()}"
        delivery_batch = self._next_delivery_batch()
        entity_id = f"{context.category}/{context.group}/{context.asset_name}/{context.variant}"
        delivery_id = f"{job_id}-{context.code}"
        staging_root = self.shots.paths.delivery_staging_root() / job_id
        archive_root = self.shots.paths.delivery_root() / "client" / self.profile.client / delivery_batch
        return DeliveryPlanner(self.profile).plan(
            context, inputs, staging_root, job_id=job_id,
            metadata={
                "entity_type": "asset", "entity_id": entity_id,
                "source_ingest_manifest": path.as_posix(), "client": self.profile.client,
                "source_ingest_manifest_data": data,
                "delivery_batch": delivery_batch, "delivery_id": delivery_id,
                "archive_root": archive_root.as_posix(),
                "deployment_root": Path(package_root).as_posix(),
            },
        )

    def execute(self, plan, *, ffmpeg: str = ""):
        if isinstance(plan.context, AssetContext):
            return DeliveryEngine().construct(plan, ffmpeg=ffmpeg, after_effects_adapter=None)
        return DeliveryEngine().construct(
            plan,
            ffmpeg=ffmpeg,
            after_effects_adapter=AfterEffectsDeliveryAdapter(self.config),
        )

    def _next_delivery_batch(self) -> str:
        date = datetime.now().strftime("%Y%m%d")
        root = self.shots.paths.delivery_root() / "client" / self.profile.client
        numbers = []
        for path in root.glob(f"{date}_*") if root.is_dir() else []:
            suffix = path.name.rsplit("_", 1)[-1]
            if suffix.isdigit():
                numbers.append(int(suffix))
        return f"{date}_{max(numbers, default=0) + 1:02d}"


def expand_sequence(value: str) -> list[Path]:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return []
    path = Path(text)
    if path.is_dir():
        return sorted(item for item in path.iterdir() if item.is_file())
    if "####" in text:
        path = Path(text)
        regex = re.compile("^" + re.escape(path.name).replace(r"\#\#\#\#", r"\d{4}") + "$")
        return sorted(item for item in path.parent.iterdir() if item.is_file() and regex.match(item.name)) if path.parent.is_dir() else []
    if "%04d" in text:
        path = Path(text)
        regex = re.compile("^" + re.escape(path.name).replace("%04d", r"\d{4}") + "$")
        return sorted(item for item in path.parent.iterdir() if item.is_file() and regex.match(item.name)) if path.parent.is_dir() else []
    return [path]


def _latest(root: Path, extensions: set[str], prefer: Iterable[str] = ()) -> str:
    if not root.is_dir():
        return ""
    rows = [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in extensions]
    if not rows:
        return ""
    preferred = [path for path in rows if any(token in path.as_posix().lower() for token in prefer)]
    rows = preferred or rows
    return str(max(rows, key=lambda path: path.stat().st_mtime))

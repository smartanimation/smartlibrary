from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable

from smartlib.apps.shot_manager import ShotIdentity, ShotManagerService
from smartlib.core.config_loader import ProjectConfig, load_config, studio_config_path
from smartlib.core.metadata import read_json
from smartlib.delivery import (
    AssetContext, DeliveryEngine, DeliveryInput, DeliveryPlanner, DeliveryProfile,
    PackageProfile, ShotContext, VendorPackageBuilder,
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
        return {
            "package_profile": str(configured.get("package_profile") or "vendor"),
            "vendor": str(configured.get("vendor") or "vendor"),
            "asset_workflow": str(configured.get("asset_workflow") or "Package ZIP"),
            "shot_workflow": str(configured.get("shot_workflow") or "Package ZIP"),
            "output_template": str(configured.get("output_template") or
                                   "{project_root}/incoming/vendors/{vendor}/{delivery_batch}/{entity}.zip"),
        }

    def manifest_delivery_defaults(self, manifest_path: str | Path) -> dict:
        path = Path(manifest_path)
        data = read_json(path, None)
        if not isinstance(data, dict):
            raise ValueError(f"Context manifest is not valid JSON: {path}")
        context = dict(data.get("context") or {})
        target = dict(data.get("target") or {})
        metadata = dict(context.get("metadata") or {})
        kind = str(data.get("package_type") or context.get("kind") or context.get("name") or "asset").lower()
        category = str(target.get("category") or data.get("category") or metadata.get("category") or "")
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
        return {"delivery_type": "Asset", "scene": scene, "category": category or "CH", "group": group,
                "asset": entity, "variant": variant, "manifest": path.as_posix()}

    def suggested_package_output(self, entity: str, *, profile: str | None = None) -> Path:
        preferences = self.delivery_preferences()
        vendor = preferences["vendor"]
        incoming = (self.config.project_root or Path.cwd()) / "incoming" / "vendors" / vendor
        date = datetime.now().strftime("%Y%m%d")
        numbers = []
        for row in incoming.glob(f"{date}_*") if incoming.is_dir() else []:
            suffix = row.name.rsplit("_", 1)[-1]
            if suffix.isdigit(): numbers.append(int(suffix))
        delivery_batch = f"{date}_{max(numbers, default=0) + 1:02d}"
        value = preferences["output_template"].format(
            project_root=(self.config.project_root or Path.cwd()).as_posix(), vendor=vendor,
            delivery_batch=delivery_batch, entity=entity, profile=profile or preferences["package_profile"],
        )
        return Path(value)

    def package_profile(self, name: str) -> PackageProfile:
        path = Path(__file__).resolve().parents[4] / "config" / "delivery" / "package_profiles" / f"{name}.json"
        if not path.is_file():
            raise FileNotFoundError(f"Smart Delivery package profile was not found: {path}")
        return PackageProfile.load(path)

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

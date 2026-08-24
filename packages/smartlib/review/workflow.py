from __future__ import annotations

"""Filesystem contracts for incremental shot review generation.

The module deliberately contains no DCC calls.  Maya, Houdini and After Effects
workers consume the same immutable snapshots and publish their results through
this service.
"""

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable
import uuid
import os
import time

from smartlib.core.config_loader import ProjectConfig, deep_merge
from smartlib.core.metadata import read_json, write_json
from smartlib.core.versioning import format_version, next_version, parse_version


DEFAULT_REVIEW_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "review_profiles": {
        "work_default": {
            "stage": "WORK",
            "dcc": "maya",
            "renderer": "maya_playblast",
            "image_format": "png",
            "bit_depth": 8,
            "alpha": True,
            "resolution_scale": 0.5,
            "fps": "project",
            "frame_range": "shot",
            "overscan": 1.0,
            "precomp": "latest_approved",
        },
        "rend_default": {
            "extends": "work_default",
            "stage": "REND",
            "renderer": "maya_render",
            "image_format": "exr",
            "bit_depth": 16,
            "compression": "zip",
        },
    },
    "delivery_profiles": {
        "internal": {
            "review_profile": "work_default",
            "container": "mov",
            "codec": "prores_422_proxy",
            "filename": "{shot}_{department}_review_{version}.mov",
            "target_template": "{shot_root}/review/{department}/{profile}/{version}",
            "include": ["movie", "thumbnail", "review_metadata", "source_manifest"],
        }
    },
    "missing_precomp_policy": "allow_project_default",
    "default_precomp": "{project_root}/templates/review/base_comp.aep",
    "jobs": {
        "retain_success_days": 3,
        "retain_failed_days": 30,
        "retain_logs_days": 90,
    },
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_fingerprint(path: str | Path | None) -> str:
    source = Path(path) if path else None
    if not source or not source.is_file():
        return ""
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_slug(value: Any, fallback: str = "layer") -> str:
    clean = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(value or "").strip()).strip("._-")
    return clean or fallback


def _versions(root: Path) -> list[int]:
    if not root.is_dir():
        return []
    return [
        parsed
        for parsed in (parse_version(path.name) for path in root.iterdir() if path.is_dir())
        if parsed is not None
    ]


def _resolve_profile(profiles: dict[str, Any], profile_id: str) -> dict[str, Any]:
    profile_id = str(profile_id or "").strip()
    if profile_id not in profiles:
        raise KeyError(f"Unknown profile: {profile_id}")
    resolving: set[str] = set()

    def resolve(current: str) -> dict[str, Any]:
        if current in resolving:
            raise ValueError(f"Profile inheritance cycle: {current}")
        resolving.add(current)
        data = dict(profiles.get(current) or {})
        parent = str(data.pop("extends", "") or "").strip()
        result = deep_merge(resolve(parent), data) if parent else data
        resolving.remove(current)
        return result

    result = resolve(profile_id)
    result["id"] = profile_id
    result["fingerprint"] = content_fingerprint(result)
    return result


class ReviewProfileService:
    def __init__(self, project_config: ProjectConfig):
        self.project_config = project_config

    def config(self) -> dict[str, Any]:
        return deep_merge(DEFAULT_REVIEW_CONFIG, self.project_config.load("review.yml"))

    def review_profile(self, profile_id: str = "work_default") -> dict[str, Any]:
        profile = _resolve_profile(
            self.config().get("review_profiles") or {}, profile_id
        )
        scale_value = profile.get("resolution_scale")
        if scale_value is not None:
            scale = float(scale_value)
            if scale <= 0:
                raise ValueError(
                    f"Review profile resolution_scale must be positive: {profile_id}"
                )
            profile["resolution_scale"] = scale
            anchors = self.project_config.base.get("anchors") or {}
            source = anchors.get("resolution") or profile.get("resolution") or [1920, 1080]
            if not isinstance(source, (list, tuple)) or len(source) < 2:
                raise ValueError(
                    f"Project resolution must contain width and height: {source}"
                )
            profile["resolution"] = [
                max(1, int(float(source[0]) * scale + 0.5)),
                max(1, int(float(source[1]) * scale + 0.5)),
            ]
            profile["fingerprint"] = content_fingerprint(
                {key: value for key, value in profile.items() if key != "fingerprint"}
            )
        return profile

    def delivery_profile(self, profile_id: str = "internal") -> dict[str, Any]:
        return _resolve_profile(self.config().get("delivery_profiles") or {}, profile_id)

    def review_profile_ids(self) -> list[str]:
        return list((self.config().get("review_profiles") or {}).keys())

    def delivery_profile_ids(self) -> list[str]:
        return list((self.config().get("delivery_profiles") or {}).keys())


@dataclass(frozen=True)
class LayerCacheResult:
    state: str
    version: str
    directory: Path
    fingerprint: str
    manifest: Path | None = None


class ReviewWorkflowService:
    """Version and cache operations for one shot."""

    def __init__(self, shot_root: str | Path, workspace_shot_root: str | Path):
        self.shot_root = Path(shot_root)
        self.workspace_shot_root = Path(workspace_shot_root)

    @property
    def composition_root(self) -> Path:
        return self.shot_root / "data" / "shot_composition"

    @property
    def assembly_root(self) -> Path:
        """Compatibility API; new data is stored as Shot Composition."""
        return self.composition_root

    @property
    def layer_definition_root(self) -> Path:
        return self.shot_root / "data" / "review_layers"

    @property
    def precomp_root(self) -> Path:
        return self.shot_root / "publish" / "precomp"

    def construct_root(self, department: str, dcc: str = "maya", task: str = "main") -> Path:
        return (
            self.workspace_shot_root / "build" / "review" / safe_slug(department)
            / safe_slug(dcc) / safe_slug(task)
        )

    def next_construct_version(
        self, department: str, dcc: str = "maya", task: str = "main"
    ) -> str:
        return format_version(
            next_version(_versions(self.construct_root(department, dcc, task)))
        )

    @staticmethod
    def canonical_construct_fingerprint(
        *,
        construct_snapshot: dict[str, Any],
        assembly_definition: dict[str, Any],
        layer_definition: dict[str, Any],
        builder_version: str = "review_builder_v2",
    ) -> str:
        return content_fingerprint({
            "construct_snapshot": construct_snapshot,
            "assembly_members": assembly_definition.get("members") or [],
            "review_layers": layer_definition.get("layers") or [],
            "builder_version": builder_version,
        })

    def find_canonical_construct(
        self,
        department: str,
        dcc: str,
        task: str,
        fingerprint: str,
    ) -> dict[str, str] | None:
        root = self.construct_root(department, dcc, task)
        for number in sorted(_versions(root), reverse=True):
            version = format_version(number)
            version_dir = root / version
            manifest = read_json(version_dir / "build_manifest.json", {}) or {}
            snapshot = read_json(version_dir / "input_snapshot.json", {}) or {}
            record = manifest or snapshot
            scene = Path(str(record.get("scene") or ""))
            if (
                record.get("canonical_fingerprint") == fingerprint
                and record.get("status") in {"validated", "warning", "ready"}
                and scene.is_file()
            ):
                return {
                    "version": version,
                    "scene": str(scene),
                    "manifest": str(version_dir / "build_manifest.json"),
                }
        return None

    @property
    def jobs_root(self) -> Path:
        return self.workspace_shot_root / "jobs" / "review"

    def layer_root(self, department: str, layer_slug: str) -> Path:
        return self.shot_root / "render" / safe_slug(department) / "layers" / safe_slug(layer_slug)

    def review_root(self, department: str, profile: str) -> Path:
        return self.shot_root / "review" / safe_slug(department) / safe_slug(profile)

    def review_destination_root(
        self,
        department: str,
        profile: str,
        delivery_settings: dict[str, Any] | None = None,
    ) -> Path:
        template = str((delivery_settings or {}).get("target_template") or "").strip()
        if not template:
            return self.review_root(department, profile)
        rendered = template.format(
            shot_root=self.shot_root.as_posix(),
            department=safe_slug(department),
            profile=safe_slug(profile),
            version="{version}",
        )
        if "{version}" in rendered:
            rendered = rendered.rsplit("/{version}", 1)[0]
        return Path(rendered)

    @staticmethod
    def normalize_assembly(payload: dict[str, Any]) -> dict[str, Any]:
        result = {"schema": "smartpipeline.shot_composition.v1", "entity_type": "shot", "members": []}
        for index, raw in enumerate(payload.get("members") or []):
            member = dict(raw or {})
            name = str(member.get("name") or member.get("asset") or f"member_{index + 1}")
            member["uid"] = str(member.get("uid") or f"member-{uuid.uuid4()}")
            member["name"] = name
            member["asset"] = str(member.get("asset") or name)
            member["variant"] = str(member.get("variant") or "default")
            member["behavior"] = str(member.get("behavior") or "STATIC").upper()
            if member["behavior"] not in {"STATIC", "CURVE"}:
                raise ValueError(f"Unsupported member behavior: {member['behavior']}")
            member["version_policy"] = str(
                member.get("version_policy") or "latest_approved"
            ).lower()
            member["enabled"] = bool(member.get("enabled", True))
            curve = dict(member.get("animation_curve") or {})
            curve["required"] = member["behavior"] == "CURVE"
            member["animation_curve"] = curve
            result["members"].append(member)
        return result

    @staticmethod
    def normalize_layers(payload: dict[str, Any], member_uids: Iterable[str] = ()) -> dict[str, Any]:
        known = set(member_uids)
        result = {"schema": "smartpipeline.review_layers.v1", "layers": []}
        slugs: set[str] = set()
        for index, raw in enumerate(payload.get("layers") or []):
            layer = dict(raw or {})
            name = str(layer.get("name") or f"Layer {index + 1}")
            slug = safe_slug(layer.get("slug") or name)
            if slug in slugs:
                raise ValueError(f"Duplicate Review Layer slug: {slug}")
            slugs.add(slug)
            members = [str(value) for value in (layer.get("members") or [])]
            missing = [value for value in members if known and value not in known]
            if missing:
                raise ValueError(f"Unknown Shot Composition members in {name}: {', '.join(missing)}")
            layer["uid"] = str(layer.get("uid") or f"layer-{uuid.uuid4()}")
            layer["name"] = name
            layer["slug"] = slug
            layer["members"] = members
            layer["enabled"] = bool(layer.get("enabled", True))
            layer["order"] = int(layer.get("order", index * 10))
            layer["precomp_placeholder"] = str(
                layer.get("precomp_placeholder") or slug.upper()
            )
            result["layers"].append(layer)
        return result

    def _publish_data(self, root: Path, filename: str, payload: dict[str, Any], comment: str) -> Path:
        version = format_version(next_version(_versions(root)))
        version_dir = root / version
        data = dict(payload)
        data.update({
            "version": version,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "comment": comment,
        })
        path = write_json(version_dir / filename, data)
        write_json(version_dir / "publish.json", {
            "schema_version": 1,
            "version": version,
            "status": "published",
            "files": {Path(filename).stem: filename},
            "comment": comment,
        })
        write_json(root / "latest.json", {"version": version, "path": f"{version}/{filename}"})
        return path

    @staticmethod
    def _latest(root: Path, fallback: dict[str, Any]) -> tuple[dict[str, Any], Path | None]:
        latest = read_json(root / "latest.json", {}) or {}
        path = root / str(latest.get("path") or "")
        return (read_json(path, fallback) or fallback, path) if path.is_file() else (fallback, None)

    def publish_composition(self, payload: dict[str, Any], comment: str = "") -> Path:
        return self._publish_data(
            self.composition_root, "shot_composition.json", self.normalize_assembly(payload), comment
        )

    def latest_composition(self) -> tuple[dict[str, Any], Path | None]:
        return self._latest(
            self.composition_root, {"schema": "smartpipeline.shot_composition.v1", "entity_type": "shot", "members": []}
        )

    def publish_assembly(self, payload: dict[str, Any], comment: str = "") -> Path:
        return self.publish_composition(payload, comment)

    def latest_assembly(self) -> tuple[dict[str, Any], Path | None]:
        return self.latest_composition()

    def publish_layer_definition(self, payload: dict[str, Any], comment: str = "") -> Path:
        assembly, _path = self.latest_assembly()
        member_uids = [row.get("uid") for row in assembly.get("members") or []]
        return self._publish_data(
            self.layer_definition_root,
            "review_layers.json",
            self.normalize_layers(payload, member_uids),
            comment,
        )

    def latest_layer_definition(self) -> tuple[dict[str, Any], Path | None]:
        return self._latest(
            self.layer_definition_root, {"schema": "smartpipeline.review_layers.v1", "layers": []}
        )

    @staticmethod
    def layer_fingerprint(
        layer: dict[str, Any],
        *,
        member_snapshots: dict[str, Any],
        camera_snapshot: dict[str, Any],
        light_snapshot: dict[str, Any] | None,
        frame_range: list[int],
        review_profile: dict[str, Any],
        builder_version: str = "1",
    ) -> tuple[str, dict[str, Any]]:
        dependencies = {
            "layer": layer,
            "members": {
                uid: member_snapshots.get(uid, {}) for uid in layer.get("members") or []
            },
            "camera": camera_snapshot,
            "light": light_snapshot or {},
            "frame_range": [int(frame_range[0]), int(frame_range[1])],
            "review_profile": review_profile,
            "builder_version": str(builder_version),
        }
        return content_fingerprint(dependencies), dependencies

    def find_layer_cache(
        self, department: str, layer_slug: str, fingerprint: str
    ) -> LayerCacheResult:
        root = self.layer_root(department, layer_slug)
        for number in sorted(_versions(root), reverse=True):
            version = format_version(number)
            manifest = root / version / "review_layer.json"
            data = read_json(manifest, {}) or {}
            if data.get("fingerprint") == fingerprint and data.get("state") == "COMPLETE":
                return LayerCacheResult("HIT", version, manifest.parent, fingerprint, manifest)
        version = format_version(next_version(_versions(root)))
        return LayerCacheResult("MISS", version, root / version, fingerprint)

    def reserve_layer_cache(
        self,
        result: LayerCacheResult,
        *,
        timeout_seconds: float = 300.0,
    ) -> tuple[LayerCacheResult, Path | None]:
        """Reserve a MISS, or reuse the cache produced by a concurrent job."""
        if result.state == "HIT":
            return result, None
        lock = result.directory.with_suffix(".lock")
        deadline = time.time() + max(0.0, timeout_seconds)
        while True:
            result.directory.parent.mkdir(parents=True, exist_ok=True)
            try:
                descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(descriptor, str(os.getpid()).encode("ascii", errors="ignore"))
                os.close(descriptor)
                return result, lock
            except FileExistsError:
                manifest = result.directory / "review_layer.json"
                manifest_data = read_json(manifest, {}) or {}
                if (
                    manifest_data.get("state") == "COMPLETE"
                    and manifest_data.get("fingerprint") == result.fingerprint
                ):
                    return LayerCacheResult(
                        "HIT", result.version, result.directory,
                        result.fingerprint, manifest,
                    ), None
                try:
                    if time.time() - lock.stat().st_mtime > 3600:
                        lock.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.time() >= deadline:
                    raise TimeoutError(f"Review Layer cache is locked: {lock}")
                time.sleep(0.25)

    @staticmethod
    def release_layer_cache(lock: Path | None) -> None:
        if lock:
            lock.unlink(missing_ok=True)

    def write_layer_cache_manifest(
        self,
        result: LayerCacheResult,
        *,
        layer: dict[str, Any],
        dependencies: dict[str, Any],
        frame_pattern: str,
        frame_count: int,
        validation: dict[str, Any] | None = None,
    ) -> Path:
        if result.state == "HIT" and result.manifest:
            return result.manifest
        payload = {
            "schema": "smartpipeline.review_layer_cache.v1",
            "state": "COMPLETE",
            "version": result.version,
            "uid": layer.get("uid"),
            "name": layer.get("name"),
            "slug": layer.get("slug"),
            "fingerprint": result.fingerprint,
            "dependencies": dependencies,
            "frames": {"pattern": frame_pattern, "count": int(frame_count)},
            "validation": validation or {"status": "passed", "results": []},
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        destination = result.directory / "review_layer.json"
        temporary = destination.with_suffix(".json.tmp")
        write_json(temporary, payload)
        temporary.replace(destination)
        return destination

    def precomp_version_dir(self, version: str) -> Path:
        return self.precomp_root / str(version)

    def publish_precomp(
        self,
        source_aep: str | Path,
        *,
        input_schema: dict[str, Any],
        composition: dict[str, Any],
        validation: dict[str, Any],
        dependency_snapshot: dict[str, Any] | None = None,
        author: str = "",
        comment: str = "",
    ) -> Path:
        """Publish a shot-wide human-authored composition.

        AE-side inspection supplies ``validation``; blocking structural errors
        are rejected here so non-AE callers cannot bypass the publish contract.
        """
        source_aep = Path(source_aep)
        if not source_aep.is_file():
            raise FileNotFoundError(f"PreComp project was not found: {source_aep}")
        results = validation.get("results") or []
        errors = [
            row for row in results
            if str(row.get("severity") or "").upper() == "ERROR"
        ]
        if errors or str(validation.get("status") or "passed").lower() == "failed":
            raise ValueError("PreComp structural validation failed.")
        placeholders = input_schema.get("inputs") or {}
        if not isinstance(placeholders, dict) or not placeholders:
            raise ValueError("PreComp input_schema must define at least one placeholder.")
        required_composition = {
            "comp": composition.get("comp") or composition.get("name"),
            "fps": composition.get("fps"),
            "resolution": composition.get("resolution"),
            "duration": composition.get("duration") or composition.get("frame_range"),
        }
        missing_composition = [
            key for key, value in required_composition.items()
            if value in (None, "", [], {})
        ]
        if missing_composition:
            raise ValueError(
                "PreComp composition metadata is missing: "
                + ", ".join(missing_composition)
            )
        version = format_version(next_version(_versions(self.precomp_root)))
        destination = self.precomp_root / version
        (destination / "aftereffects").mkdir(parents=True)
        import shutil
        shutil.copy2(source_aep, destination / "aftereffects" / "precomp.aep")
        metadata = destination / "metadata"
        write_json(metadata / "input_schema.json", input_schema)
        write_json(metadata / "composition.json", composition)
        write_json(metadata / "validation.json", validation)
        write_json(metadata / "dependency_snapshot.json", dependency_snapshot or {})
        write_json(metadata / "publish.json", {
            "schema": "smartpipeline.precomp_publish.v1",
            "publish_type": "precomp",
            "version": version,
            "status": "published",
            "author": author,
            "comment": comment,
            "source": str(source_aep),
            "files": {
                "project": "aftereffects/precomp.aep",
                "input_schema": "metadata/input_schema.json",
                "composition": "metadata/composition.json",
                "validation": "metadata/validation.json",
                "dependency_snapshot": "metadata/dependency_snapshot.json",
            },
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
        write_json(self.precomp_root / "latest.json", {
            "version": version, "path": f"{version}/metadata/publish.json"
        })
        return destination

    def latest_precomp(self, *, approved_only: bool = True) -> Path | None:
        for number in sorted(_versions(self.precomp_root), reverse=True):
            root = self.precomp_root / format_version(number)
            publish = read_json(root / "metadata" / "publish.json", {}) or {}
            if approved_only and str(publish.get("status") or "").lower() not in {
                "approved", "published"
            }:
                continue
            project = root / "aftereffects" / "precomp.aep"
            if project.is_file():
                return project
        return None

    def create_job(self, payload: dict[str, Any]) -> tuple[str, Path]:
        job_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        job_dir = self.jobs_root / job_id
        data = dict(payload)
        data.update({"job_id": job_id, "state": "QUEUED", "created_at": datetime.now().isoformat()})
        write_json(job_dir / "job.json", data)
        return job_id, job_dir

    def cleanup_jobs(self, policy: dict[str, Any], *, now: float | None = None) -> None:
        """Apply retention without deleting the durable submitted Review."""
        now = float(now if now is not None else time.time())
        success_days = int(policy.get("retain_success_days", 3))
        failed_days = int(policy.get("retain_failed_days", 30))
        logs_days = int(policy.get("retain_logs_days", 90))
        if not self.jobs_root.is_dir():
            return
        import shutil
        for job_dir in self.jobs_root.iterdir():
            if not job_dir.is_dir():
                continue
            job_path = job_dir / "job.json"
            job = read_json(job_path, {}) or {}
            age_days = (now - job_dir.stat().st_mtime) / 86400.0
            state = str(job.get("state") or "").upper()
            payload_days = success_days if state in {"COMPLETE", "SUBMITTED"} else failed_days
            if age_days >= payload_days:
                for name in ("runtime.aep", "runtime.relink.jsx", "output"):
                    target = job_dir / name
                    if target.is_dir():
                        shutil.rmtree(target)
                    elif target.exists():
                        target.unlink()
            if age_days >= logs_days:
                logs = job_dir / "logs"
                if logs.is_dir():
                    shutil.rmtree(logs)

    def next_review_version(
        self,
        department: str,
        delivery_profile: str,
        delivery_settings: dict[str, Any] | None = None,
    ) -> str:
        return format_version(next_version(_versions(
            self.review_destination_root(department, delivery_profile, delivery_settings)
        )))

    def submit_review(
        self,
        *,
        department: str,
        delivery_profile: str,
        movie: str | Path,
        thumbnail: str | Path,
        review_data: dict[str, Any],
        source_manifest: dict[str, Any],
        delivery_settings: dict[str, Any] | None = None,
    ) -> Path:
        movie = Path(movie)
        thumbnail = Path(thumbnail)
        if not movie.is_file():
            raise FileNotFoundError(f"Review movie was not generated: {movie}")
        if not thumbnail.is_file():
            raise FileNotFoundError(f"Review thumbnail was not generated: {thumbnail}")
        if thumbnail.suffix.lower() in {".jpg", ".jpeg"}:
            with thumbnail.open("rb") as stream:
                if stream.read(2) != b"\xff\xd8":
                    raise ValueError(
                        "Review thumbnail has a JPEG extension but is not JPEG data: "
                        f"{thumbnail}"
                    )
        destination_root = self.review_destination_root(
            department, delivery_profile, delivery_settings
        )
        version = self.next_review_version(
            department, delivery_profile, delivery_settings
        )
        destination = destination_root / version
        temporary = destination.with_name(f".{destination.name}.submitting")
        if temporary.exists():
            import shutil
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True)
        import shutil
        movie_name = f"review{movie.suffix.lower()}"
        shutil.copy2(movie, temporary / movie_name)
        shutil.copy2(thumbnail, temporary / "thumbnail.jpg")
        payload = dict(review_data)
        payload.update({
            "schema": "smartpipeline.formal_review.v1",
            "version": version,
            "profile": delivery_profile,
            "state": "SUBMITTED",
            "movie": movie_name,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
        write_json(temporary / "review.json", payload)
        write_json(temporary / "source_manifest.json", source_manifest)
        temporary.replace(destination)
        write_json(destination.parent / "latest.json", {
            "version": version, "path": f"{version}/review.json"
        })
        return destination

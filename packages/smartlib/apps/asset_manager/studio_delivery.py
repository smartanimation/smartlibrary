"""On-demand, asset-wide FBX releases independent of Pipeline Profile."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from smartlib.apps.asset_manager.context import AssetContextService
from smartlib.core.asset_categories import canonical_asset_category
from smartlib.core.asset_publish_resolver import AssetPublishResolver


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    return path


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


class StudioDeliveryService:
    def __init__(self, config):
        self.config = config
        self.contexts = AssetContextService(config)
        self.paths = self.contexts.paths
        self.resolver = AssetPublishResolver(config)

    def settings(self, identity):
        context = self.contexts.load_context("studio_delivery")
        if context.get("enabled") is not True:
            raise ValueError("Studio Delivery is disabled. Enable contexts/studio_delivery in the project YAML.")
        category = canonical_asset_category(identity.category)
        recipe = (context.get("categories") or {}).get(category)
        if not isinstance(recipe, dict):
            raise ValueError(f"Studio Delivery is not configured for {category}.")
        if not recipe.get("source_context") or not recipe.get("geometry_set"):
            raise ValueError("source_context and geometry_set are required.")
        if category == "character" and (recipe.get("skins") is not True or not recipe.get("skeleton_set")):
            raise ValueError("Characters require skeleton_set and skins: true.")
        fbx = context.get("fbx") or {}
        if fbx.get("units") not in {"mm", "cm", "m"} or fbx.get("up_axis") not in {"y", "z"}:
            raise ValueError("FBX units must be mm/cm/m and up_axis must be y/z.")
        if fbx.get("file_version") not in {"FBX201800", "FBX201900", "FBX202000"}:
            raise ValueError("Unsupported FBX file_version.")
        if fbx.get("animation") is not False:
            raise ValueError("Studio asset delivery requires animation: false.")
        return {"context": "studio_delivery", "context_version": context["_version_label"],
                "label": context.get("label", "Studio Delivery"), "category": category,
                "recipe": recipe, "fbx": fbx}

    def sources(self, identity):
        settings = self.settings(identity)
        return self.resolver.list_context_versions(
            self.paths.asset_variant_root(identity), settings["recipe"]["source_context"], formats=("ma", "mb"),
            publish_root=self.paths.asset_publish_dir(identity, "asset", ""))

    def prepare(self, identity, source_version):
        self.paths.pipeline_token(identity.variant)
        self.paths.pipeline_version(source_version)
        settings = self.settings(identity)
        source = next((row for row in self.sources(identity) if row["version"] == source_version), None)
        if source is None:
            raise ValueError("Select an existing, explicit packed Maya asset version.")
        run = uuid.uuid4().hex
        folder = self.paths.studio_delivery_path(identity, "jobs", run)
        folder.mkdir(parents=True, exist_ok=False)
        job = {"schema": "smartpipeline.studio_delivery_job.v1", "run_id": run,
               "identity": asdict(identity), "settings": settings,
               "source": {"path": source["path"], "version": source_version,
                          "context": settings["recipe"]["source_context"], "sha256": digest(source["path"])},
               "output": str(self.paths.artifact_file(folder, "asset.fbx")),
               "report": str(self.paths.artifact_file(folder, "validation.json")), "created_at": now()}
        return write_new(self.paths.artifact_file(folder, "job.json"), job)

    def job_path(self, identity, run):
        return self.paths.studio_delivery_path(identity, "jobs", run, "job.json")

    def validate_job(self, identity, run):
        job = read(self.job_path(identity, run))
        if job["identity"] != asdict(identity) or job["run_id"] != run:
            raise ValueError("Job does not match the selected asset/variant.")
        output = self.paths.studio_delivery_path(identity, "jobs", run, "asset.fbx")
        report = read(self.paths.studio_delivery_path(identity, "jobs", run, "validation.json"))
        if report.get("status") != "passed" or report.get("run_id") != run:
            raise ValueError("Maya FBX validation has not passed.")
        if not output.is_file() or not output.stat().st_size or report.get("sha256") != digest(output):
            raise ValueError("Generated FBX is missing or changed after validation.")
        if report.get("source_sha256") != job["source"]["sha256"]:
            raise ValueError("Validation does not match the selected source.")
        if digest(job["source"]["path"]) != job["source"]["sha256"]:
            raise ValueError("Source publish changed. Generate again.")
        if self.settings(identity) != job["settings"]:
            raise ValueError("Delivery settings changed. Generate again.")
        for dependency in report.get("dependencies", []):
            if digest(dependency["path"]) != dependency["sha256"]:
                raise ValueError(f"Source reference changed: {dependency['path']}")
        return job, report, output

    def releases(self, identity):
        result = []
        for folder in self.paths.studio_delivery_path(identity, "publish").glob("v[0-9]*"):
            if not folder.name[1:].isdigit():
                continue
            manifest = self.paths.artifact_file(folder, "delivery.json")
            if manifest.is_file():
                result.append(read(manifest))
        return sorted(result, key=lambda row: int(row["version"][1:]), reverse=True)

    def finalize(self, identity, run, comment=""):
        job, report, output = self.validate_job(identity, run)
        root = self.paths.studio_delivery_path(identity, "publish")
        root.mkdir(parents=True, exist_ok=True)
        lock = self.paths.artifact_file(root, "release.lock")
        try:
            handle = lock.open("x")
        except FileExistsError:
            raise RuntimeError("Another release is being finalized. Retry after it finishes.") from None
        stage = self.paths.studio_delivery_path(identity, "publish", "pending_" + uuid.uuid4().hex)
        try:
            with handle:
                handle.write(run)
            for release in self.releases(identity):
                if release["run_id"] == run:
                    return release
            number = max([int(p.name[1:]) for p in root.glob("v[0-9]*") if p.name[1:].isdigit()] or [0]) + 1
            version = f"v{number:03d}"
            destination = self.paths.studio_delivery_path(identity, "publish", version)
            filename = f"{identity.name}_{version}.fbx"
            target = self.paths.artifact_file(destination, filename)
            stage.mkdir(exist_ok=False)
            staged_fbx = self.paths.artifact_file(stage, filename)
            shutil.copyfile(output, staged_fbx)
            if digest(staged_fbx) != report["sha256"]:
                raise ValueError("FBX changed while finalizing. Generate again.")
            release = {"schema": "smartpipeline.studio_delivery.v1", "identity": job["identity"],
                       "version": version, "run_id": run, "source": job["source"],
                       "settings": job["settings"], "validation": report, "fbx": str(target),
                       "sha256": report["sha256"], "created_at": now(), "comment": comment}
            write_new(self.paths.artifact_file(stage, "delivery.json"), release)
            os.rename(stage, destination)
            return release
        finally:
            if stage.exists():
                shutil.rmtree(stage)
            lock.unlink()

    def release(self, identity, version):
        self.paths.pipeline_version(version)
        result = read(self.paths.studio_delivery_path(identity, "publish", version, "delivery.json"))
        fbx = self.paths.studio_delivery_path(identity, "publish", version, f"{identity.name}_{version}.fbx")
        if result["version"] != version or result["sha256"] != digest(fbx):
            raise ValueError("Delivery release is missing or has been modified.")
        return result

    def record_sent(self, identity, version, studio, note=""):
        if not studio.strip():
            raise ValueError("Studio name is required.")
        release = self.release(identity, version)
        event = {"kind": "sent", "version": version, "studio": studio.strip(), "note": note,
                 "recorded_at": now(), "delivery_sha256": release["sha256"]}
        write_new(self.paths.studio_delivery_path(identity, "events", filename=uuid.uuid4().hex + ".json"), event)
        return event

    def record_received(self, identity, version, studio, source, take, note=""):
        """Keep a received take immutable and linked to the exact delivery skeleton."""
        if not studio.strip() or not take.strip():
            raise ValueError("Studio and take are required.")
        release = self.release(identity, version)
        source = Path(source)
        if not source.is_file() or source.suffix.lower() != ".fbx" or not source.stat().st_size:
            raise ValueError("Select a non-empty received FBX.")
        receipt_id = uuid.uuid4().hex
        target = self.paths.studio_delivery_path(identity, "events", receipt_id, "motion.fbx")
        target.parent.mkdir(parents=True, exist_ok=False)
        checksum = digest(source)
        shutil.copyfile(source, target)
        if digest(target) != checksum:
            raise ValueError("Received FBX changed while copying. Register it again.")
        event = {"kind": "received", "version": version, "studio": studio.strip(), "take": take.strip(),
                 "recorded_at": now(), "note": note, "delivery_sha256": release["sha256"],
                 "path": str(target), "sha256": checksum, "original_path": str(source.resolve())}
        write_new(self.paths.studio_delivery_path(identity, "events", filename=receipt_id + ".json"), event)
        return event

    def events(self, identity):
        return sorted([read(p) for p in self.paths.studio_delivery_path(identity, "events").glob("*.json")],
                      key=lambda item: item["recorded_at"], reverse=True)

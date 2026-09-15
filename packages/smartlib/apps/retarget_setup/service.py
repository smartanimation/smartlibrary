from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from smartlib.core.asset_publish_resolver import AssetPublishResolver
from smartlib.core.path_resolver import AssetIdentity, ProjectPaths
from smartlib.retarget.profile import load_retarget_profile


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def file_stamp(path):
    path = Path(path).resolve()
    stat = path.stat()
    return {"path": path.as_posix(), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


class RetargetService:
    """Character configuration only; test clips are separate run inputs.

    No Qt or Maya imports. Scene Build consumers can use resolve_published().
    """

    def __init__(self, paths: ProjectPaths, identity: AssetIdentity, config=None):
        self.paths = paths
        self.identity = AssetIdentity(identity.category, identity.group, identity.name, "")
        self.config = config

    def path(self, area, version="", filename=""):
        return self.paths.retarget_path(self.identity, area, version, filename)

    def clean(self, profile):
        if not isinstance(profile, dict):
            raise ValueError("Profile must be a JSON object")
        result = json.loads(json.dumps(profile))
        if result.get("asset") not in (None, "", self.identity.name):
            raise ValueError("Profile belongs to another character")
        result.update(asset=self.identity.name, profile_kind="asset_profile", schema_version=1)
        for key in ("variant", "mocap_fbx", "frame_range"):
            result.pop(key, None)
        if isinstance(result.get("template"), dict):
            result["template"].pop("path", None)
        return result

    def import_profile(self, path):
        path = Path(path)
        result = load_retarget_profile(path)
        for key in ("mcr_scene", "animation_rig_scene"):
            if result.get(key) and not Path(result[key]).is_absolute():
                result[key] = (path.parent / result[key]).resolve().as_posix()
        return self.clean(result)

    def template_profile(self):
        templates = sorted(self.paths.retarget_library("templates").glob("*_v*.json"))
        template = templates[-1] if templates else Path(__file__).resolve().parents[4] / "config" / "maya" / "retarget" / "templates" / "elcd_humanoid_v001.json"
        profile = load_retarget_profile(template)
        profile["template"] = {"id": profile.get("template_id", template.stem), "version": profile.get("template_version", "v001")}
        profile.pop("asset", None)
        return self.clean(profile)

    def new_profile(self):
        profile = self.template_profile()
        profile.update(self.resolve_rigs())
        return profile

    def resolve_rigs(self):
        result = {}
        resolver = AssetPublishResolver(self.config) if self.config is not None else None
        for key, role, contexts in (("mcr_scene", "MCR", ("mcp", "mcr", "mocap")), ("animation_rig_scene", "ANM", ("anim", "anm", "animation"))):
            selected = None
            if resolver:
                default = AssetIdentity(self.identity.category, self.identity.group, self.identity.name)
                for context in contexts:
                    selected = resolver.resolve_context(self.paths.asset_variant_root(default), context)
                    if selected:
                        break
            if selected is None:
                legacy = self.paths.legacy_retarget_rig(self.identity, role)
                selected = legacy if legacy.is_file() else None
            result[key] = selected.resolve().as_posix() if selected else ""
        return result

    def history(self, area):
        roots = [self.path(area), *self.paths.legacy_retarget_roots(self.identity, area)]
        result = []
        for root in dict.fromkeys(roots):
            for folder in root.glob("v[0-9]*"):
                if not folder.is_dir() or not folder.name[1:].isdigit():
                    continue
                manifest_path = folder / ("data.json" if area == "data" else "publish.json")
                if not manifest_path.is_file():
                    continue
                manifest = read(manifest_path)
                profile_name = manifest.get("profile", f"{self.identity.name}_retarget.json")
                if Path(profile_name).name != profile_name:
                    continue
                profile = folder / profile_name
                if profile.is_file():
                    result.append({"area": area, "version": folder.name, "profile": profile, "manifest": manifest, "legacy": root != self.path(area)})
        return sorted(result, key=lambda row: (row["legacy"], -int(row["version"][1:])))

    def load_initial(self):
        draft = self.path("work", filename="profile.json")
        if draft.is_file():
            return self.import_profile(draft)
        versions = self.history("data") or self.history("publish")
        if versions:
            return self.import_profile(versions[0]["profile"])
        for path in self.paths.legacy_retarget_profiles(self.identity):
            if path.is_file():
                return self.import_profile(path)
        return self.new_profile()

    def save_draft(self, profile):
        return write(self.path("work", filename="profile.json"), self.clean(profile))

    def validate(self, profile):
        profile = self.clean(profile)
        errors = []
        for key in ("source_skeleton", "transfer_nodes", "time_unit"):
            if not profile.get(key):
                errors.append(f"Missing setting: {key}")
        for key in ("mcr_scene", "animation_rig_scene"):
            if not profile.get(key) or not Path(profile[key]).is_file():
                errors.append(f"Missing rig: {key}")
        for plugin in profile.get("required_plugins", []):
            if not Path(plugin).is_file():
                errors.append(f"Missing plug-in: {plugin}")
        return errors

    def fingerprint(self, profile):
        profile = self.clean(profile)
        dependencies = [file_stamp(profile[key]) for key in ("mcr_scene", "animation_rig_scene")]
        dependencies.extend(file_stamp(p) for p in profile.get("required_plugins", []))
        return hashlib.sha256(json.dumps([profile, dependencies], sort_keys=True).encode()).hexdigest()

    def reserve(self, area):
        number = max([int(p.name[1:]) for p in self.path(area).glob("v[0-9]*") if p.name[1:].isdigit()] or [0]) + 1
        while True:
            version = f"v{number:03d}"
            try:
                self.path(area, version).mkdir(parents=True, exist_ok=False)
                return version
            except FileExistsError:
                number += 1

    def save_data(self, profile, comment=""):
        profile = self.clean(profile)
        errors = self.validate(profile)
        if errors:
            raise ValueError("\n".join(errors))
        fingerprint = self.fingerprint(profile)
        version = self.reserve("data")
        target = write(self.path("data", version, f"{self.identity.name}_retarget.json"), profile)
        manifest = {"schema_version": 1, "data_type": "retarget", "asset": self.identity.name, "version": int(version[1:]), "profile": target.name, "fingerprint": fingerprint, "comment": comment, "created_at": timestamp()}
        write(self.path("data", version, "data.json"), manifest)
        write(self.path("data", filename="latest.json"), {"version": version, "profile": target.relative_to(self.path("data")).as_posix()})
        return target

    def prepare_test(self, profile, motion, start, end):
        errors = self.validate(profile)
        if errors:
            raise ValueError("\n".join(errors))
        motion = Path(motion)
        if not motion.is_file() or motion.suffix.lower() != ".fbx":
            raise ValueError("Select a received or standard motion FBX")
        if end < start:
            raise ValueError("End frame must be >= start frame")
        run_id = uuid.uuid4().hex
        profile = self.clean(profile)
        payload = {**profile, "mocap_fbx": motion.resolve().as_posix(), "frame_range": [start, end]}
        job = {"run_id": run_id, "fingerprint": self.fingerprint(profile), "motion": file_stamp(motion), "frame_range": [start, end], "status": "running", "reviewed": False}
        write(self.path("test", run_id, "profile.json"), payload)
        write(self.path("test", run_id, "test.json"), job)
        return job

    def complete_test(self, job, exit_code):
        job = read(self.path("test", job["run_id"], "test.json"))
        report_path = self.path("test", job["run_id"], "report.json")
        report = read(report_path) if report_path.is_file() else {}
        output = self.path("test", job["run_id"], "result.mb")
        # Locked channels are intentionally not keyed; unclassified skips remain failures.
        unexpected = set(report.get("skipped_plugs", [])) - set(report.get("locked_plugs", []))
        passed = (exit_code == 0 and output.is_file() and report.get("keyed_plugs", 0) > 0
                  and not unexpected and not report.get("missing_plugs") and not report.get("failed_plugs"))
        job.update(status="passed" if passed else "failed", reviewed=False, completed_at=timestamp(), report=report)
        write(self.path("test", job["run_id"], "test.json"), job)
        return job

    def review_test(self, job, reviewed):
        path = self.path("test", job["run_id"], "test.json")
        persisted = read(path)
        if persisted["status"] != "passed":
            raise ValueError("A successful Maya test is required")
        persisted["reviewed"] = bool(reviewed)
        write(path, persisted)
        return persisted

    def publish(self, data_path, job, comment=""):
        entry = next((row for row in self.history("data") if row["profile"].resolve() == Path(data_path).resolve() and not row["legacy"]), None)
        if entry is None:
            raise ValueError("Save a character-wide Data Version before publishing")
        profile = self.import_profile(data_path)
        current = self.fingerprint(profile)
        persisted = read(self.path("test", job["run_id"], "test.json"))
        if persisted["status"] != "passed" or not persisted["reviewed"] or persisted["fingerprint"] != current or entry["manifest"].get("fingerprint") != current:
            raise ValueError("Settings or rig dependencies changed. Save and test the current settings, then review the result.")
        if file_stamp(persisted["motion"]["path"]) != persisted["motion"]:
            raise ValueError("Test motion changed; run the test again")
        version = self.reserve("publish")
        target = write(self.path("publish", version, f"{self.identity.name}_retarget.json"), profile)
        manifest = {"schema_version": 1, "publish_type": "retarget", "asset": self.identity.name, "version": int(version[1:]), "profile": target.name, "source_data": {"version": entry["version"], "path": Path(data_path).as_posix()}, "fingerprint": current, "test_motion": persisted, "validation_status": "passed", "comment": comment, "published_at": timestamp()}
        write(self.path("publish", version, "publish.json"), manifest)
        write(self.path("publish", filename="latest.json"), {"version": version, "profile": target.relative_to(self.path("publish")).as_posix()})
        return target

    def resolve_published(self, version=None):
        rows = self.history("publish")
        if version:
            rows = [row for row in rows if row["version"] == version and not row["legacy"]]
        if not rows:
            raise FileNotFoundError(f"No Retarget publish for {self.identity.name}")
        return rows[0]

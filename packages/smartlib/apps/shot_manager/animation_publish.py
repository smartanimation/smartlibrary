"""Publish final animation artifacts and immutable department compositions.

All pipeline paths are supplied by the existing ProjectPaths resolver. Build
files are validated before promotion; only a complete composition is discoverable.
"""
from __future__ import annotations

import json

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import re
import shutil

from smartlib.core.metadata import read_json, write_json
from smartlib.core.pipeline_profile import profile_from_settings
from smartlib.core.versioning import format_version

BUILD_SCHEMA = "smartpipeline.animation_build.v1"
SCHEMA = "smartpipeline.animation_composition.v1"


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class AnimationCompositionService:
    def __init__(self, shot_service):
        self.shots = shot_service
        self.paths = shot_service.paths
        self.config = shot_service.project_config
        self._publish_roots = {self.paths.production_root().resolve()}

    @staticmethod
    def _identity(identity):
        return (identity.episode, identity.sequence, identity.shot)

    def _root(self, identity, department="animation", version=""):
        self._publish_roots.add(self.paths.shot_publish_root(*self._identity(identity)).resolve())
        return self.paths.composition_dir(*self._identity(identity), department, version)

    def _file(self, root, name):
        return self.paths.artifact_file(root, name)

    def _reserve(self, root):
        root.mkdir(parents=True, exist_ok=True)
        version = max(
            (int(p.name[1:]) for p in root.iterdir() if p.is_dir() and re.fullmatch(r"v[0-9]{3,}", p.name)),
            default=0,
        ) + 1
        while True:
            label = format_version(version)
            directory = self._file(root, label)
            try:
                directory.mkdir()
                return label, directory
            except FileExistsError:
                version += 1

    def _fixed_dependency(self, value):
        path = self.paths.project_dependency(value)
        if not any(path.is_relative_to(root) for root in self._publish_roots):
            raise ValueError(f"Dependency must be a Production publish: {path}")
        if not path.is_file() or not any(re.fullmatch(r"v[0-9]{3,}", p) for p in path.parts):
            raise ValueError(f"Dependency must exist at a fixed version: {path}")
        return {"path": path.as_posix(), "sha256": file_hash(path)}

    def _check_dependency(self, ref):
        checked = self._fixed_dependency(ref["path"])
        if checked["sha256"] != ref.get("sha256"):
            raise ValueError(f"Published dependency changed: {ref['path']}")
        return Path(checked["path"])

    def load(self, path, *, identity=None):
        path = Path(path)
        data = read_json(path, {})
        if data.get("schema") != SCHEMA or data.get("status") != "published":
            raise ValueError(f"Not a completed Composition Snapshot: {path}")
        if identity and data.get("shot") != dict(zip(("episode", "sequence", "shot"), self._identity(identity))):
            raise ValueError("Composition belongs to a different shot")
        shot = data.get("shot", {})
        self._publish_roots.add(self.paths.shot_publish_root(
            *(self.paths.pipeline_token(shot[key]) for key in ("episode", "sequence", "shot"))
        ).resolve())
        if data.get("source"):
            self._check_dependency(data["source"])
        for member in data["members"]:
            for artifact in member["products"].values():
                self._check_dependency(artifact)
                if artifact.get("transfer_manifest"):
                    self._check_dependency(artifact["transfer_manifest"])
                for dependency in artifact.get("dependencies", []):
                    self._check_dependency(dependency)
        for ref in data.get("dependencies", {}).values():
            self._check_dependency(ref)
        if data.get("base"):
            self._check_dependency(data["base"])
        if data.get("previous"):
            self._check_dependency(data["previous"])
        for look in data.get("looks", {}).values():
            self._check_dependency(look)
            for dependency in look.get("dependencies", []):
                self._check_dependency(dependency)
        self._check_dependency(data["entrypoint"])
        return data

    def list_snapshots(self, identity, department="animation"):
        rows = []
        for directory in self._root(identity, department).glob("v[0-9]*"):
            path = self._file(directory, "manifest.json")
            data = read_json(path, {})
            if data.get("schema") == SCHEMA and data.get("status") == "published":
                rows.append((path, data))
        return sorted(rows, key=lambda row: int(row[1]["version"][1:]), reverse=True)

    def latest(self, identity, department="animation"):
        rows = self.list_snapshots(identity, department)
        return rows[0][0] if rows else None

    def _validate_product(self, manifest, row, product, profile, frame_range):
        item = deepcopy(row["products"][product])
        source = self.paths.manifest_source(manifest, item.get("source", ""))
        if not source.is_file() or not source.stat().st_size:
            raise ValueError(f"Missing or empty {product}: {source}")
        expected = {"deform": {".usd", ".usda", ".usdc"} if profile.representation == "usd" else {".abc"},
                    "transfer": {".atom"}, "rend": {".ma"}}[product]
        if source.suffix.lower() not in expected:
            raise ValueError(f"Unexpected {product} representation: {source}")
        if item.get("sha256") != file_hash(source):
            raise ValueError(f"Build source changed; rebuild before publishing: {source}")
        validation = item.get("validation") or {}
        if validation.get("ok") is not True or validation.get("source_sha256") != item["sha256"]:
            raise ValueError(f"Missing validated Build receipt: {source}")
        if list(item.get("frame_range", [])) != frame_range:
            raise ValueError(f"Frame range mismatch: {row['instance_id']}/{product}")
        dependencies = [self._fixed_dependency(v) for v in item.get("dependencies", [])]
        result = {"source": source, "sha256": item["sha256"], "dependencies": dependencies,
                  "validation": validation, "representation": source.suffix[1:],
                  "frame_range": frame_range}
        if product == "deform":
            if item.get("evaluation") != "final_deform":
                raise ValueError("Final deformation is required; Skeleton-only and sculpt deltas are not publishable")
            if not item.get("topology_signature"):
                raise ValueError("Final Deform requires a topology signature")
            result["topology_signature"] = item["topology_signature"]
            result["evaluation"] = "final_deform"
            if profile.representation == "usd":
                from smartlib.dcc.maya.animation_build import validate_deform_usd
                result["prim_paths"] = validate_deform_usd(source)
        elif product == "transfer":
            transfer = item.get("transfer_manifest") or {}
            if transfer.get("schema") != "smartpipeline.animation_atom.v3" or transfer.get("payload_sha256") != item["sha256"]:
                raise ValueError("ATOM requires its v3 transfer manifest and mapping")
            result["transfer_data"] = transfer
            if not source.read_text(encoding="utf-8-sig").lstrip().startswith("atomVersion"):
                raise ValueError("Invalid ATOM payload")
        elif product == "rend":
            if validation.get("atom_applied") is not True:
                raise ValueError("REND scene must have a successful ATOM apply receipt")
            transfer = row["products"]["transfer"]
            if validation.get("atom_sha256") != transfer.get("sha256"):
                raise ValueError("REND receipt belongs to a different ATOM payload")
        return result

    def publish_build(self, identity, manifest_path, *, comment=""):
        self._root(identity)
        profile = self.config.pipeline_profile
        if not profile:
            raise ValueError("Select a Project Profile in Config Creator first")
        manifest_path = Path(manifest_path).resolve()
        build = read_json(manifest_path, {})
        if build.get("schema") != BUILD_SCHEMA:
            raise ValueError("Select an Animation Build Manifest")
        if profile_from_settings(build) != profile:
            raise ValueError("Build profile differs from Project Profile; rebuild before publishing")
        shot = dict(zip(("episode", "sequence", "shot"), self._identity(identity)))
        if build.get("shot") != shot:
            raise ValueError("Build Manifest belongs to a different shot")
        frame_range = list(self.shots.shot_frame_range(identity))
        if build.get("fps") != self.shots.project_fps:
            raise ValueError("Build FPS differs from the current project")
        if build.get("frame_range") != frame_range:
            raise ValueError("Build timing differs from the current shot")
        members = build.get("members") or []
        ids = [self.paths.pipeline_token(row["instance_id"]) for row in members]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("Build requires unique, nonempty cast instance IDs")
        cast = (self.shots.load_cast(identity).get("cast") or {})
        required = {key for key, entry in cast.items() if entry.get("animation_required", True)}
        excluded = set(build.get("excluded_members") or [])
        if not excluded <= required or excluded & set(ids):
            raise ValueError("Invalid explicit member exclusions")
        required -= excluded
        if set(ids) != required:
            raise ValueError(f"Build cast mismatch. Missing: {sorted(required-set(ids))}; unexpected: {sorted(set(ids)-required)}")
        source_scene = self.paths.manifest_source(manifest_path, build.get("source_workfile", ""))
        if source_scene.suffix.lower() not in {".ma", ".mb"} or not source_scene.is_file() or file_hash(source_scene) != build.get("source_sha256"):
            raise ValueError("Source scene is missing or changed; rebuild before publishing")
        if build.get("review_source"):
            from smartlib.apps.review_build_manager.composition_sources import validate_review_source
            validate_review_source(build["review_source"], source_scene)
        validated = []
        for row in members:
            if row.get("asset") != cast[row["instance_id"]].get("asset"):
                raise ValueError(f"Cast asset changed: {row['instance_id']}")
            if set(row.get("products", {})) != set(profile.products):
                raise ValueError(f"{row['instance_id']} requires {profile.products}")
            products = {p: self._validate_product(manifest_path, row, p, profile, frame_range) for p in profile.products}
            validated.append((row, products))
        dependencies = {k: self._fixed_dependency(v) for k, v in (build.get("dependencies") or {}).items()}
        # All source files have passed validation. Each promotion has its own immutable version.
        source_root = self.paths.animation_artifact_dir(*self._identity(identity), "source", "maya")
        source_version, source_dir = self._reserve(source_root)
        source_dest = self._file(source_dir, "animation" + source_scene.suffix.lower())
        shutil.copy2(source_scene, source_dest)
        if file_hash(source_dest) != build["source_sha256"]:
            raise ValueError("Source scene changed during promotion")
        source_ref = self._fixed_dependency(source_dest)
        write_json(self._file(source_dir, "manifest.json"), {
            "source": source_ref, "dependencies": dependencies,
            "build_manifest": manifest_path.as_posix(), "profile": profile.to_dict(),
        })
        published = []
        for row, products in validated:
            output = {"instance_id": row["instance_id"], "asset": row["asset"],
                      "variant": row.get("variant", "default"), "products": {}}
            for product, item in products.items():
                root = self.paths.animation_artifact_dir(*self._identity(identity), row["instance_id"], product)
                version, directory = self._reserve(root)
                filename = ("rend_animation" if product == "rend" else "animation" if product == "transfer" else "deform") + item["source"].suffix.lower()
                destination = self._file(directory, filename)
                shutil.copy2(item["source"], destination)
                if file_hash(destination) != item["sha256"]:
                    raise RuntimeError(f"Artifact copy verification failed: {destination}")
                artifact = {k: v for k, v in item.items() if k != "source"}
                artifact.update(path=destination.as_posix(), version=version,
                                artifact_id=f"{'/'.join(self._identity(identity))}/animation/{row['instance_id']}/{product}/{version}")
                transfer_data = artifact.pop("transfer_data", None)
                if transfer_data:
                    transfer_data["payload"] = filename
                    transfer_file = write_json(self._file(directory, "animation_manifest.json"), transfer_data)
                    artifact["transfer_manifest"] = self._fixed_dependency(transfer_file)
                write_json(self._file(directory, "validation.json"), item["validation"])
                write_json(self._file(directory, "manifest.json"), {
                    **artifact, "schema": "smartpipeline.animation_artifact.v1",
                    "profile": profile.to_dict(), "build_source": item["source"].as_posix(),
                    "source_workfile": source_scene.as_posix(), "source_sha256": build["source_sha256"],
                })
                output["products"][product] = artifact
            published.append(output)
        data = {
            "schema": SCHEMA, "status": "published", "department": "animation",
            "shot": shot, "profile": profile.to_dict(), "frame_range": frame_range,
            "fps": self.shots.project_fps, "members": published, "dependencies": dependencies,
            "cast": deepcopy(cast), "looks": {}, "comment": comment,
            "source_workfile": source_scene.as_posix(), "source_sha256": build["source_sha256"],
            "source": source_ref,
            "review_source": deepcopy(build.get("review_source") or {}),
            "excluded_members": sorted(excluded),
        }
        return self._commit(identity, data)

    def _commit(self, identity, data):
        version, directory = self._reserve(self._root(identity, data["department"]))
        data = deepcopy(data)
        data["version"] = version
        entry = self._file(directory, data["profile"]["entrypoint"])
        # Generate in Workspace, then promote the fully built entrypoint.
        workspace_root = self.paths.animation_build_dir(*self._identity(identity))
        _build_version, build_dir = self._reserve(workspace_root)
        staged_entry = self._file(build_dir, data["profile"]["entrypoint"])
        self._write_entrypoint(staged_entry, data)
        shutil.copy2(staged_entry, entry)
        if file_hash(staged_entry) != file_hash(entry):
            raise RuntimeError("Shot entrypoint copy verification failed")
        data["entrypoint"] = {"path": entry.as_posix(), "sha256": file_hash(entry)}
        manifest = self._file(directory, "manifest.json")
        # The manifest is the commit marker. Failed/incomplete versions are never listed.
        temporary = self._file(directory, "manifest.pending.json")
        write_json(temporary, data)
        os.replace(temporary, manifest)
        return manifest

    def adopt(self, identity, base_path, *, department, looks=None, comment=""):
        if department not in {"effects", "lighting"}:
            raise ValueError("Only Effects and Lighting can adopt an upstream composition")
        base = self.load(base_path, identity=identity)
        allowed = {"effects": {"animation"}, "lighting": {"animation", "effects"}}
        if base["department"] not in allowed[department]:
            raise ValueError("Invalid department inheritance")
        data = deepcopy(base)
        data.update(department=department, base=self._fixed_dependency(base_path), comment=comment)
        if looks is not None:
            data["looks"] = self._validate_looks(base, looks)
        return self._commit(identity, data)

    def revise_members(self, identity, snapshot_path, selections):
        """Commit a new composition from compatible, fixed member bundles."""
        data = self.load(snapshot_path, identity=identity)
        original = {m["instance_id"]: m for m in data["members"]}
        if set(selections) != set(original):
            raise ValueError("Draft members differ from the selected snapshot")
        members, excluded = [], set(data.get("excluded_members") or [])
        for target, chosen in selections.items():
            if not chosen:
                excluded.add(target)
                continue
            candidate = self.load(chosen, identity=identity)
            if any(candidate[key] != data[key] for key in ("profile", "frame_range", "fps")):
                raise ValueError(f"Incompatible snapshot timing/profile: {target}")
            if candidate["department"] != data["department"]:
                raise ValueError("Select a version from the same department")
            member = next((m for m in candidate["members"] if m["instance_id"] == target), None)
            if not member or any(member.get(k) != original[target].get(k) for k in ("asset", "variant")):
                raise ValueError(f"Incompatible member identity: {target}")
            products = member["products"]
            if "transfer" in products and products["rend"]["validation"].get("atom_sha256") != products["transfer"]["sha256"]:
                raise ValueError("ATOM and applied REND must be selected together")
            members.append(deepcopy(member))
            data.setdefault("dependencies", {})["member_snapshot_" + target] = self._fixed_dependency(chosen)
        if not members:
            raise ValueError("Enable at least one member")
        data["members"] = members
        data["excluded_members"] = sorted(excluded)
        active = {m["instance_id"] for m in members}
        data["looks"] = self._validate_looks(data, {k: v for k, v in data.get("looks", {}).items() if k in active})
        data["previous"] = self._fixed_dependency(snapshot_path)
        data["comment"] = "Composition selection revised"
        return self._commit(identity, data)

    def revise_looks(self, identity, snapshot_path, looks, *, comment=""):
        data = self.load(snapshot_path, identity=identity)
        if data["department"] not in {"effects", "lighting"}:
            raise ValueError("Adopt the Animation Snapshot into Effects or Lighting before overriding Look")
        data["looks"] = self._validate_looks(data, looks)
        data["previous"] = self._fixed_dependency(snapshot_path)
        data["comment"] = comment
        return self._commit(identity, data)

    def _validate_looks(self, data, looks):
        if data["profile"]["representation"] != "usd" and looks:
            raise ValueError("Look layer overrides currently require the USD profile")
        members = {row["instance_id"]: row for row in data["members"]}
        result = {}
        for target, look in looks.items():
            if target not in members:
                raise ValueError(f"Unknown Look target: {target}")
            topology = members[target]["products"]["deform"]["topology_signature"]
            if look.get("topology_signature") != topology:
                raise ValueError(f"Look topology mismatch: {target}")
            ref = self._fixed_dependency(look["path"])
            from smartlib.dcc.maya.animation_build import validate_look_usd
            assets = validate_look_usd(Path(ref["path"]), look.get("prim_path", ""), look.get("variants", {}))
            result[target] = {**ref, "prim_path": look["prim_path"],
                              "variants": deepcopy(look.get("variants", {})), "topology_signature": topology,
                              "dependencies": [self._fixed_dependency(asset) for asset in assets]}
        return result

    def _write_entrypoint(self, path, data):
        representation = data["profile"]["representation"]
        if representation == "abc":
            write_json(path, {**data, "schema": "smartpipeline.shot_manifest.v1"})
        elif representation == "maya":
            # Maya references contain only fixed, validated, applied REND scenes.
            lines = ['//Maya ASCII scene', 'requires maya "2024";',
                     f'currentUnit -l centimeter -a degree -t "{data["fps"]}fps";']
            for member in data["members"]:
                namespace = member["instance_id"]
                scene = member["products"]["rend"]["path"].replace("\\", "/").replace('"', '\\"')
                reference_node = namespace + "RN"
                lines.append(f'file -rdi 1 -ns "{namespace}" -rfn "{reference_node}" -typ "mayaAscii" "{scene}";')
                for ref in sorted(member["products"]["rend"]["validation"].get("references", []), key=lambda r: r["depth"]):
                    # Maya requires nested reference declarations when opening an
                    # ASCII scene; loadReferenceDepth on file -r alone is insufficient.
                    quote = lambda value: json.dumps(str(value), ensure_ascii=False)
                    lines.append(
                        f'file -rdi {int(ref["depth"]) + 1} -ns {quote(ref["namespace"])} '
                        f'-rfn {quote(namespace + ":" + ref["node"])} '
                        f'-typ {quote(ref["type"])} {quote(ref["path"])};')
                lines.append(f'file -r -ns "{namespace}" -dr 1 -rfn "{reference_node}" -typ "mayaAscii" "{scene}";')
            start, end = (int(v) for v in data["frame_range"])
            lines.extend([
                'createNode script -n "sceneConfigurationScriptNode";',
                f'setAttr ".b" -type "string" "playbackOptions -min {start} -max {end} -ast {start} -aet {end};";',
                'setAttr ".st" 6;',
            ])
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            from smartlib.dcc.maya.animation_build import compose_deform_usd
            compose_deform_usd(path, data)

    def snapshot_rows(self, path, *, data=None):
        data = data if data is not None else self.load(path)
        rows = []
        for member in data["members"]:
            for product, artifact in member["products"].items():
                rows.append({"target": member["instance_id"], "product": product,
                             "version": artifact["version"], "path": artifact["path"],
                             "state": "Validated", "inherited_from": (data.get("base") or {}).get("path", "")})
        for target, look in data.get("looks", {}).items():
            rows.append({"target": target, "product": "look", "version": "",
                         "path": look["path"], "state": "Validated", "inherited_from": ""})
        return rows

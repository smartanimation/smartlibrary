from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json, write_json
from smartlib.core.path_resolver import AssemblyIdentity, AssetIdentity, ProjectPaths
from smartlib.core.versioning import format_version, next_version, parse_version


@dataclass(frozen=True)
class AssemblyMember:
    uid: str
    entity_type: str
    entity_id: str
    variant: str = "default"
    version: str = ""
    namespace: str = ""
    purpose: str = "render"
    transform: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AssemblyValidationIssue:
    severity: str
    code: str
    message: str
    uid: str = ""


class AssemblyManagerService:
    """Create and publish reusable compositions of Assets and Assemblies."""

    def __init__(self, project_config: ProjectConfig):
        self.config = project_config
        if project_config.project_root is None:
            raise RuntimeError("project_root is not set in templates_base.yml")
        self.paths = ProjectPaths(
            project_config.project_root,
            templates=project_config.templates,
            project_name=project_config.project_name,
            shot_dept_partitions={
                str(k): str(v)
                for k, v in (project_config.base.get("shot_dept_partitions") or {}).items()
            },
        )

    def list_assemblies(self) -> list[AssemblyIdentity]:
        root = self.paths.assemblies_root()
        if not root.is_dir():
            return []
        results = []
        for metadata in root.glob("*/*/*/assembly.json"):
            data = read_json(metadata, {}) or {}
            results.append(AssemblyIdentity(
                str(data.get("category") or metadata.parents[2].name),
                str(data.get("group") or metadata.parents[1].name),
                str(data.get("assembly") or metadata.parent.name),
                str(data.get("default_variant") or "default"),
            ))
        return sorted(results, key=lambda x: (x.category.lower(), x.group.lower(), x.name.lower()))

    def create_assembly(self, identity: AssemblyIdentity, description: str = "") -> Path:
        root = self.paths.assembly_root(identity)
        variant_root = self.paths.assembly_variant_root(identity)
        for path in (
            root, variant_root, self.paths.assembly_data_root(identity),
            self.paths.assembly_publish_root(identity), self.paths.assembly_work_root(identity),
        ):
            path.mkdir(parents=True, exist_ok=True)
        write_json(root / "assembly.json", {
            "schema": "smartpipeline.assembly.v1", "entity_type": "assembly",
            "assembly": identity.name, "category": identity.category,
            "group": identity.group, "default_variant": identity.variant,
            "description": description,
        })
        variant_json = variant_root / "variant.json"
        if not variant_json.exists():
            write_json(variant_json, {"assembly": identity.name, "variant": identity.variant})
        draft = self.composition_path(identity)
        if not draft.exists():
            self.save_composition(identity, [], purpose="blockout")
        return root

    def composition_path(self, identity: AssemblyIdentity) -> Path:
        return self.paths.assembly_work_root(identity) / "assembly_composition.json"

    def load_composition(self, identity: AssemblyIdentity) -> dict[str, Any]:
        return read_json(self.composition_path(identity), self._composition(identity, [], "blockout")) or {}

    def save_composition(
        self, identity: AssemblyIdentity, members: list[AssemblyMember | dict[str, Any]],
        *, purpose: str = "layout",
    ) -> Path:
        normalized = [asdict(item) if isinstance(item, AssemblyMember) else dict(item) for item in members]
        payload = self._composition(identity, normalized, purpose)
        issues = self.validate(identity, payload, require_versions=False)
        errors = [item.message for item in issues if item.severity == "ERROR"]
        if errors:
            raise ValueError("Invalid assembly composition: " + "; ".join(errors))
        return write_json(self.composition_path(identity), payload)

    def validate(
        self, identity: AssemblyIdentity, composition: dict[str, Any] | None = None,
        *, require_versions: bool = True,
    ) -> list[AssemblyValidationIssue]:
        data = composition or self.load_composition(identity)
        issues: list[AssemblyValidationIssue] = []
        seen: set[str] = set()
        own_id = self.entity_id(identity)
        for member in data.get("members") or []:
            uid = str(member.get("uid") or "").strip()
            entity_type = str(member.get("entity_type") or "").strip().lower()
            entity_id = str(member.get("entity_id") or "").strip()
            version = str(member.get("version") or "").strip()
            if not uid:
                issues.append(AssemblyValidationIssue("ERROR", "MISSING_UID", "Member UID is required."))
                continue
            if uid in seen:
                issues.append(AssemblyValidationIssue("ERROR", "DUPLICATE_UID", f"Duplicate member UID: {uid}", uid))
            seen.add(uid)
            if entity_type not in {"asset", "assembly"}:
                issues.append(AssemblyValidationIssue("ERROR", "INVALID_ENTITY_TYPE", f"Unsupported entity_type: {entity_type}", uid))
                continue
            if entity_type == "assembly" and entity_id == own_id:
                issues.append(AssemblyValidationIssue("ERROR", "ASSEMBLY_CYCLE", "Assembly cannot reference itself.", uid))
            root = self.reference_root(entity_type, entity_id, str(member.get("variant") or "default"))
            if root is None or not root.exists():
                issues.append(AssemblyValidationIssue("ERROR", "MISSING_ENTITY", f"Entity does not exist: {entity_type}:{entity_id}", uid))
                continue
            if require_versions and not version:
                issues.append(AssemblyValidationIssue("ERROR", "UNPINNED_VERSION", f"Publish version is not pinned: {uid}", uid))
            elif version and not any(path.is_dir() for path in root.glob(f"**/{version}")):
                issues.append(AssemblyValidationIssue("ERROR", "MISSING_VERSION", f"Publish version does not exist: {entity_id} {version}", uid))
        return issues

    def publish(self, identity: AssemblyIdentity, *, comment: str = "") -> Path:
        composition = self.load_composition(identity)
        errors = [issue for issue in self.validate(identity, composition) if issue.severity == "ERROR"]
        if errors:
            raise ValueError("Assembly publish failed: " + "; ".join(issue.message for issue in errors))
        root = self.paths.assembly_publish_root(identity) / "assembly" / str(composition.get("purpose") or "layout")
        versions = [parse_version(path.name) for path in root.glob("v*") if path.is_dir()]
        version = format_version(next_version([value for value in versions if value is not None]))
        version_dir = root / version
        version_dir.mkdir(parents=True, exist_ok=False)
        manifest = dict(composition)
        manifest.update({
            "version": version, "comment": comment,
            "published_at": datetime.now(timezone.utc).isoformat(),
        })
        write_json(version_dir / "assembly_composition.json", manifest)
        write_json(version_dir / "manifest.json", {
            "schema": "smartpipeline.publish_manifest.v1", "entity_type": "assembly",
            "entity_id": self.entity_id(identity), "variant": identity.variant,
            "version": version, "dependencies": manifest.get("members") or [],
            "files": {"composition": "assembly_composition.json"},
        })
        write_json(root / "latest.json", {"version": version, "path": f"{version}/manifest.json"})
        return version_dir

    def construct_maya(self, identity: AssemblyIdentity) -> Path:
        """Generate a reproducible Maya reference scene from the pinned draft."""
        composition = self.load_composition(identity)
        errors = [issue for issue in self.validate(identity, composition) if issue.severity == "ERROR"]
        if errors:
            raise ValueError("Assembly construct failed: " + "; ".join(issue.message for issue in errors))
        root = self.paths.assembly_work_root(identity) / "construct" / str(composition.get("purpose") or "layout")
        numbers = [parse_version(path.name) for path in root.glob("v*") if path.is_dir()]
        version = format_version(next_version([value for value in numbers if value is not None]))
        version_dir = root / version; version_dir.mkdir(parents=True, exist_ok=False)
        references = []
        lines = ["//Maya ASCII 2024 scene", 'requires maya "2024";', 'currentUnit -l centimeter -a degree -t film;']
        for member in composition.get("members") or []:
            publish_root = self.reference_root(str(member.get("entity_type")), str(member.get("entity_id")), str(member.get("variant") or "default"))
            candidates = sorted(
                file for directory in (publish_root.glob(f"**/{member.get('version')}") if publish_root else [])
                if directory.is_dir() for file in directory.rglob("*") if file.suffix.lower() in {".ma", ".mb"}
            )
            if not candidates:
                raise FileNotFoundError(f"No Maya publish found for {member.get('uid')} {member.get('version')}")
            source = candidates[0]
            namespace = str(member.get("namespace") or member.get("uid")).replace('"', "_")
            group_name = f"{namespace}_GRP"
            source_text = source.as_posix().replace('"', '\\"')
            maya_type = "mayaBinary" if source.suffix.lower() == ".mb" else "mayaAscii"
            lines.append(f'file -r -type "{maya_type}" -ignoreVersion -mergeNamespacesOnClash false -namespace "{namespace}" -groupReference -groupName "{group_name}" -options "v=0;" "{source_text}";')
            transform = dict(member.get("transform") or {})
            for attribute, default in (("translate", [0, 0, 0]), ("rotate", [0, 0, 0]), ("scale", [1, 1, 1])):
                values = transform.get(attribute, default)
                if isinstance(values, (list, tuple)) and len(values) == 3:
                    lines.append(f'setAttr "{group_name}.{attribute}" -type "double3" {float(values[0])} {float(values[1])} {float(values[2])};')
            references.append({"uid": member.get("uid"), "source": source.as_posix(), "namespace": namespace})
        scene = version_dir / f"{identity.name}_{identity.variant}_{composition.get('purpose') or 'layout'}_{version}.ma"
        scene.write_text("\n".join(lines) + "\n", encoding="utf-8")
        write_json(version_dir / "construct_manifest.json", {
            "schema": "smartpipeline.assembly_construct.v1", "entity_id": self.entity_id(identity),
            "version": version, "source_composition": self.composition_path(identity).as_posix(),
            "scene": scene.as_posix(), "references": references,
        })
        return scene

    def reference_root(self, entity_type: str, entity_id: str, variant: str) -> Path | None:
        parts = [part for part in entity_id.replace("\\", "/").split("/") if part]
        if len(parts) != 3:
            return None
        if entity_type == "asset":
            return self.paths.asset_publish_root(AssetIdentity(*parts, variant))
        if entity_type == "assembly":
            return self.paths.assembly_publish_root(AssemblyIdentity(*parts, variant))
        return None

    @staticmethod
    def entity_id(identity: AssemblyIdentity) -> str:
        return f"{identity.category}/{identity.group}/{identity.name}"

    def _composition(self, identity: AssemblyIdentity, members: list[dict[str, Any]], purpose: str) -> dict[str, Any]:
        return {
            "schema": "smartpipeline.assembly_composition.v1",
            "entity_type": "assembly", "entity_id": self.entity_id(identity),
            "variant": identity.variant, "purpose": purpose, "members": members,
        }

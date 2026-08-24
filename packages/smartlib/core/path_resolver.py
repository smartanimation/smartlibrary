from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smartlib.core.tokens import resolve_token_string


@dataclass(frozen=True)
class AssetIdentity:
    category: str
    group: str
    name: str
    variant: str = "default"


@dataclass(frozen=True)
class AssemblyIdentity:
    category: str
    group: str
    name: str
    variant: str = "default"


@dataclass(frozen=True)
class ProjectPaths:
    project_root: Path
    templates: dict[str, str] | None = None
    project_name: str = ""
    shot_dept_partitions: dict[str, str] | None = None

    def production_root(self) -> Path:
        template = self._template("production_root")
        return self._path_from_template(template) if template else self.project_root / "production"

    def incoming_root(self) -> Path:
        template = self._template("incoming_root")
        return self._path_from_template(template) if template else self.project_root / "incoming"

    def delivery_root(self) -> Path:
        template = self._template("delivery_root")
        return self._path_from_template(template) if template else self.project_root / "delivery"

    def delivery_staging_root(self) -> Path:
        template = self._template("delivery_staging_root")
        return self._path_from_template(template) if template else self.workspace_root() / "delivery"

    def workspace_partition(self, department: str) -> str:
        department_name = str(department or "").strip()
        configured = self.shot_dept_partitions or {}
        return str(configured.get(department_name) or configured.get("default") or "cg").strip()

    def assets_root(self) -> Path:
        template = self._template("assets_root")
        if template:
            return self._path_from_template(template)
        asset_root = self._template("asset_root")
        if asset_root and "{category}" in asset_root:
            return self._path_from_template(asset_root.split("{category}", 1)[0].rstrip("/\\"))
        return self.production_root() / "assets"

    def shots_root(self) -> Path:
        template = self._template("shots_root")
        if template:
            return self._path_from_template(template)
        shot_root = self._template("shot_root")
        if shot_root and "{episode}" in shot_root:
            return self._path_from_template(shot_root.split("{episode}", 1)[0].rstrip("/\\"))
        return self.production_root() / "shots"

    def sequences_root(self) -> Path:
        template = self._template("sequences_root")
        if template:
            return self._path_from_template(template)
        return self.production_root() / "sequences"

    def assemblies_root(self) -> Path:
        template = self._template("assemblies_root")
        return self._path_from_template(template) if template else self.production_root() / "assemblies"

    def workspace_root(self) -> Path:
        template = self._template("workspace_root")
        return (
            self._path_from_template(template)
            if template
            else self.project_root / "workspace"
        )

    def asset_root(self, identity: AssetIdentity) -> Path:
        template = self._template("asset_root")
        if template:
            return self._path_from_template(
                template,
                category=identity.category,
                group=identity.group,
                asset_name=identity.name,
                asset=identity.name,
                name=identity.name,
                variant=identity.variant,
            )
        return self.assets_root() / identity.category / identity.group / identity.name

    def asset_variant_root(self, identity: AssetIdentity) -> Path:
        return self.asset_root(identity) / identity.variant

    def asset_work_dir(self, identity: AssetIdentity, department: str) -> Path:
        template = self._template("asset_work")
        if template:
            return self._path_from_template(
                template,
                category=identity.category,
                group=identity.group,
                asset=identity.name,
                asset_name=identity.name,
                variant=identity.variant,
                department=department,
                dept=department,
                workspace_partition=self.workspace_partition(department),
            )
        return self.asset_variant_root(identity) / "work" / department

    def assembly_root(self, identity: AssemblyIdentity) -> Path:
        template = self._template("assembly_root")
        if template:
            return self._path_from_template(
                template, category=identity.category, group=identity.group,
                assembly=identity.name, assembly_name=identity.name,
                name=identity.name, variant=identity.variant,
            )
        return self.assemblies_root() / identity.category / identity.group / identity.name

    def assembly_variant_root(self, identity: AssemblyIdentity) -> Path:
        return self.assembly_root(identity) / identity.variant

    def assembly_work_root(self, identity: AssemblyIdentity, department: str = "layout") -> Path:
        template = self._template("assembly_work_root")
        if template:
            return self._path_from_template(
                template, category=identity.category, group=identity.group,
                assembly=identity.name, assembly_name=identity.name,
                name=identity.name, variant=identity.variant,
                department=department, dept=department,
                workspace_partition=self.workspace_partition(department),
            )
        return self.workspace_root() / self.workspace_partition(department) / "assemblies" / identity.category / identity.group / identity.name / identity.variant / "work"

    def assembly_data_root(self, identity: AssemblyIdentity) -> Path:
        template = self._template("assembly_data_root")
        return self._path_from_template(
            template, category=identity.category, group=identity.group,
            assembly=identity.name, assembly_name=identity.name,
            name=identity.name, variant=identity.variant,
        ) if template else self.assembly_variant_root(identity) / "data"

    def assembly_publish_root(self, identity: AssemblyIdentity) -> Path:
        template = self._template("assembly_publish_root")
        return self._path_from_template(
            template, category=identity.category, group=identity.group,
            assembly=identity.name, assembly_name=identity.name,
            name=identity.name, variant=identity.variant,
        ) if template else self.assembly_variant_root(identity) / "publish"

    def asset_work_root(self, identity: AssetIdentity, department: str = "") -> Path:
        template = self._template("asset_work_root")
        if template:
            return self._path_from_template(
                template,
                category=identity.category,
                group=identity.group,
                asset=identity.name,
                asset_name=identity.name,
                variant=identity.variant,
                department=department,
                dept=department,
                workspace_partition=self.workspace_partition(department),
            )
        return self.asset_variant_root(identity) / "work"

    def asset_data_root(self, identity: AssetIdentity) -> Path:
        return self._asset_area_root("asset_data_root", identity, "data")

    def asset_publish_root(self, identity: AssetIdentity) -> Path:
        return self._asset_area_root("asset_publish_root", identity, "publish")

    def asset_reference_root(self, identity: AssetIdentity) -> Path:
        return self._asset_area_root("asset_reference_root", identity, "reference")

    def _asset_area_root(self, template_name: str, identity: AssetIdentity, fallback: str) -> Path:
        template = self._template(template_name)
        if template:
            return self._path_from_template(
                template,
                category=identity.category,
                group=identity.group,
                asset=identity.name,
                asset_name=identity.name,
                variant=identity.variant,
            )
        return self.asset_variant_root(identity) / fallback

    def asset_data_dir(self, identity: AssetIdentity, data_type: str, subset: str) -> Path:
        return self.asset_data_root(identity) / data_type / subset

    def asset_publish_dir(self, identity: AssetIdentity, publish_type: str, subset: str) -> Path:
        return self.asset_publish_root(identity) / publish_type / subset

    def asset_work_scene_dir(self, identity: AssetIdentity, department: str) -> Path:
        return self.asset_variant_root(identity) / "work" / department

    def legacy_asset_work_dir(self, identity: AssetIdentity, department: str) -> Path:
        return self.asset_variant_root(identity) / department / "work"

    def asset_data_version_dir(self, identity: AssetIdentity, data_type: str, subset: str, version: str) -> Path:
        return self.asset_data_dir(identity, data_type, subset) / version

    def asset_publish_version_dir(
        self,
        identity: AssetIdentity,
        publish_type: str,
        subset: str,
        version: str,
    ) -> Path:
        return self.asset_publish_dir(identity, publish_type, subset) / version

    def shot_root(self, episode: str, sequence: str, shot: str) -> Path:
        template = self._template("shot_root")
        if template:
            return self._path_from_template(
                template,
                episode=episode,
                sequence=sequence,
                seq=sequence,
                shot=shot,
            )
        return self.shots_root() / episode / sequence / shot

    def sequence_root(self, episode: str, sequence: str) -> Path:
        template = self._template("shot_root")
        if template and "{shot}" in template:
            return self._path_from_template(
                template.split("{shot}", 1)[0].rstrip("/\\"),
                episode=episode,
                sequence=sequence,
                seq=sequence,
            )
        return self.shots_root() / episode / sequence

    def sequence_workspace_root(self, episode: str, sequence: str) -> Path:
        return self.sequences_root() / episode / sequence

    def sequence_build_root(self, episode: str, sequence: str, department: str = "") -> Path:
        template = self._template("sequence_build_root")
        if template:
            return self._path_from_template(
                template, episode=episode, sequence=sequence, seq=sequence,
                department=department, dept=department,
                workspace_partition=self.workspace_partition(department),
            )
        return self.project_root / "workspace" / episode / sequence / "build"

    def sequence_build_dir(
        self, episode: str, sequence: str, department: str, dcc: str, task: str, version: str
    ) -> Path:
        template = self._template("sequence_build")
        if template:
            return self._path_from_template(
                template, episode=episode, sequence=sequence, seq=sequence,
                department=department, dept=department, dcc=dcc, task=task, version=version,
                workspace_partition=self.workspace_partition(department),
            )
        return self.sequence_build_root(episode, sequence, department) / department / dcc / task / version

    def sequence_work_dir(self, episode: str, sequence: str, department: str, dcc: str) -> Path:
        return self.sequence_workspace_root(episode, sequence) / department / "work" / dcc

    def sequence_publish_dir(self, episode: str, sequence: str, publish_type: str) -> Path:
        return self.sequence_workspace_root(episode, sequence) / "publish" / publish_type

    def sequence_publish_version_dir(self, episode: str, sequence: str, publish_type: str, version: str) -> Path:
        return self.sequence_publish_dir(episode, sequence, publish_type) / version

    def shot_work_dir(self, episode: str, sequence: str, shot: str, department: str, tool_name: str = "maya") -> Path:
        template = self._template("shot_work")
        if template:
            return self._path_from_template(
                template,
                episode=episode,
                sequence=sequence,
                seq=sequence,
                shot=shot,
                department=department,
                dept=department,
                workspace_partition=self.workspace_partition(department),
                dcc=tool_name,
                tool=tool_name,
            )
        return self.shot_root(episode, sequence, shot) / "work" / department / tool_name

    def shot_work_root(
        self, episode: str, sequence: str, shot: str, department: str = ""
    ) -> Path:
        template = self._template("shot_work_root")
        if template:
            return self._path_from_template(
                template, episode=episode, sequence=sequence, seq=sequence, shot=shot,
                department=department, dept=department,
                workspace_partition=self.workspace_partition(department),
            )
        return self.shot_root(episode, sequence, shot) / "work"

    def shot_build_root(
        self, episode: str, sequence: str, shot: str, department: str = ""
    ) -> Path:
        template = self._template("shot_build_root")
        if template:
            return self._path_from_template(
                template, episode=episode, sequence=sequence, seq=sequence, shot=shot,
                department=department, dept=department,
                workspace_partition=self.workspace_partition(department),
            )
        return self.project_root / "workspace" / episode / sequence / shot / "build"

    def shot_build_dir(
        self, episode: str, sequence: str, shot: str, department: str,
        dcc: str, task: str, version: str,
    ) -> Path:
        template = self._template("shot_build")
        if template:
            return self._path_from_template(
                template, episode=episode, sequence=sequence, seq=sequence, shot=shot,
                department=department, dept=department, dcc=dcc, task=task, version=version,
                workspace_partition=self.workspace_partition(department),
            )
        return self.shot_build_root(episode, sequence, shot, department) / department / dcc / task / version

    def shot_data_root(self, episode: str, sequence: str, shot: str) -> Path:
        return self._shot_area_root("shot_data_root", episode, sequence, shot, "data")

    def shot_publish_root(self, episode: str, sequence: str, shot: str) -> Path:
        return self._shot_area_root("shot_publish_root", episode, sequence, shot, "publish")

    def shot_output_root(self, episode: str, sequence: str, shot: str) -> Path:
        return self._shot_area_root("shot_output_root", episode, sequence, shot, "output")

    def shot_render_root(self, episode: str, sequence: str, shot: str) -> Path:
        return self._shot_area_root("shot_render_root", episode, sequence, shot, "render")

    def _shot_area_root(
        self, template_name: str, episode: str, sequence: str, shot: str, fallback: str
    ) -> Path:
        template = self._template(template_name)
        if template:
            return self._path_from_template(
                template, episode=episode, sequence=sequence, seq=sequence, shot=shot
            )
        return self.shot_root(episode, sequence, shot) / fallback

    def legacy_shot_work_dir(self, episode: str, sequence: str, shot: str, department: str) -> Path:
        return self.shot_root(episode, sequence, shot) / department / "work"

    def legacy_shot_tool_work_dir(
        self,
        episode: str,
        sequence: str,
        shot: str,
        department: str,
        tool_name: str = "maya",
    ) -> Path:
        return self.legacy_shot_work_dir(episode, sequence, shot, department) / tool_name

    def shot_data_dir(self, episode: str, sequence: str, shot: str, data_type: str, target: str, subset: str) -> Path:
        return self.shot_data_root(episode, sequence, shot) / data_type / target / subset

    def shot_data_version_dir(
        self,
        episode: str,
        sequence: str,
        shot: str,
        data_type: str,
        target: str,
        subset: str,
        version: str,
    ) -> Path:
        return self.shot_data_dir(episode, sequence, shot, data_type, target, subset) / version

    def shot_publish_dir(self, episode: str, sequence: str, shot: str, publish_type: str, subset: str) -> Path:
        return self.shot_publish_root(episode, sequence, shot) / publish_type / subset

    def shot_publish_version_dir(
        self,
        episode: str,
        sequence: str,
        shot: str,
        publish_type: str,
        subset: str,
        version: str,
    ) -> Path:
        return self.shot_publish_dir(episode, sequence, shot, publish_type, subset) / version

    def _template(self, name: str) -> str:
        return str((self.templates or {}).get(name) or "").strip()

    def _template_fields(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "project_root": self.project_root.as_posix(),
            "project_name": self.project_name or self.project_root.name,
        }
        for key, value in (self.templates or {}).items():
            fields.setdefault(key, value)
        # Resolve root aliases before a domain template (for example shot_root)
        # consumes them. Project-specific test configs may only define shots_root.
        root_defaults = {
            "production_root": self.project_root / "production",
            "workspace_root": self.project_root / "workspace",
            "incoming_root": self.project_root / "incoming",
            "delivery_root": self.project_root / "delivery",
        }
        for key, fallback in root_defaults.items():
            fields[key] = self._expand_template(
                self._template(key) or fallback.as_posix(), fields
            )
        domain_defaults = {
            "assets_root": Path(fields["production_root"]) / "assets",
            "assemblies_root": Path(fields["production_root"]) / "assemblies",
            "shots_root": Path(fields["production_root"]) / "shots",
            "sequences_root": Path(fields["production_root"]) / "sequences",
        }
        for key, fallback in domain_defaults.items():
            fields[key] = self._expand_template(
                self._template(key) or fallback.as_posix(), fields
            )
        if extra:
            fields.update(extra)
        return fields

    def _expand_template(self, value: str, fields: dict[str, Any]) -> str:
        expanded = str(value)
        for _ in range(8):
            next_value = resolve_token_string(expanded, fields)
            if next_value == expanded:
                break
            expanded = next_value
        return expanded

    def _path_from_template(self, value: str, **fields: Any) -> Path:
        expanded = self._expand_template(value, self._template_fields(fields))
        if "{" in expanded or "}" in expanded:
            raise ValueError(f"Unresolved path template token: {expanded}")
        return Path(expanded)


def configured_project_paths(project_root: str | Path, project_config=None) -> ProjectPaths:
    """Return config-aware roots for code that only received a project path."""

    if project_config is None:
        from smartlib.core.config_loader import current_project_config

        project_config = current_project_config()
    templates = {}
    project_name = Path(project_root).name
    partitions = {}
    if project_config is not None:
        configured_root = getattr(project_config, "project_root", None)
        if configured_root is None or Path(configured_root).resolve() == Path(project_root).resolve():
            templates = dict(getattr(project_config, "templates", {}) or {})
            project_name = str(getattr(project_config, "project_name", project_name) or project_name)
            partitions = dict((getattr(project_config, "base", {}) or {}).get("shot_dept_partitions") or {})
    return ProjectPaths(Path(project_root), templates, project_name, partitions)

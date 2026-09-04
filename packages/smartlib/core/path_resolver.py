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

    def delivery_vendors_root(self) -> Path:
        template = self._template("deliveries_vendors_dir")
        return self._path_from_template(template) if template else self.delivery_root() / "vendors"

    def delivery_vendor_root(self, studio_id: str) -> Path:
        template = self._template("delivery_vendor_root")
        if template:
            return self._path_from_template(template, studio_id=studio_id)
        return self.delivery_vendors_root() / studio_id

    def delivery_vendor_batch(self, studio_id: str, delivery_batch: str) -> Path:
        template = self._template("delivery_vendor_batch")
        if template:
            return self._path_from_template(template, studio_id=studio_id, delivery_batch=delivery_batch)
        return self.delivery_vendor_root(studio_id) / delivery_batch

    def delivery_vendor_package(self, studio_id: str, delivery_batch: str, entity: str) -> Path:
        template = self._template("delivery_vendor_package")
        if template:
            return self._path_from_template(template, studio_id=studio_id, delivery_batch=delivery_batch, entity=entity)
        return self.delivery_vendor_batch(studio_id, delivery_batch) / f"{entity}.zip"

    def delivery_editorial_root(self) -> Path:
        template = self._template("deliveries_editorial_dir")
        return self._path_from_template(template) if template else self.delivery_root() / "editorial"

    def delivery_editorial_recipient_root(self, recipient: str) -> Path:
        template = self._template("delivery_editorial_recipient_root")
        if template:
            return self._path_from_template(template, recipient=recipient)
        return self.delivery_editorial_root() / recipient

    def delivery_editorial_batch(self, recipient: str, delivery_batch: str, process: str) -> Path:
        template = self._template("delivery_editorial_batch")
        if template:
            return self._path_from_template(
                template, recipient=recipient, delivery_batch=delivery_batch, process=process
            )
        return self.delivery_editorial_recipient_root(recipient) / delivery_batch / process

    def delivery_editorial_package(
        self, recipient: str, delivery_batch: str, process: str, entity: str
    ) -> Path:
        template = self._template("delivery_editorial_package")
        if template:
            return self._path_from_template(
                template, recipient=recipient, delivery_batch=delivery_batch,
                process=process, entity=entity,
            )
        return self.delivery_editorial_batch(recipient, delivery_batch, process) / f"{entity}.zip"

    def delivery_editorial_index_root(self, recipient: str) -> Path:
        template = self._template("delivery_editorial_index_root")
        if template:
            return self._path_from_template(template, recipient=recipient)
        return self.delivery_editorial_recipient_root(recipient) / "index"

    def delivery_editorial_revision_index(
        self, recipient: str, process: str, episode: str, timeline_revision: str
    ) -> Path:
        template = self._template("delivery_editorial_revision_index")
        if template:
            return self._path_from_template(
                template, recipient=recipient, process=process,
                episode=episode, timeline_revision=timeline_revision,
            )
        return (
            self.delivery_editorial_index_root(recipient) / process / episode
            / f"{timeline_revision}.json"
        )
    def delivery_staging_root(self) -> Path:
        template = self._template("delivery_staging_root")
        return self._path_from_template(template) if template else self.workspace_root() / "delivery"

    def editorial_data_root(self) -> Path:
        template = self._template("editorial_data_root")
        return self._path_from_template(template) if template else self.production_root() / "editorial" / "data"

    def editorial_root(self) -> Path:
        template = self._template("editorial_root")
        return self._path_from_template(template) if template else self.production_root() / "editorial"

    def editorial_work_root(self) -> Path:
        template = self._template("editorial_work_root")
        return self._path_from_template(template) if template else self.editorial_root() / "work"

    def editorial_publish_root(self) -> Path:
        template = self._template("editorial_publish_root")
        return self._path_from_template(template) if template else self.editorial_root() / "publish"

    def editorial_sequence_publish_root(self, episode: str, sequence: str) -> Path:
        template = self._template("editorial_sequence_publish_root")
        if template:
            return self._path_from_template(
                template, episode=episode, sequence=sequence, seq=sequence
            )
        return self.editorial_publish_root() / episode / sequence

    def legacy_editorial_sequence_publish_roots(
        self, episode: str, sequence: str
    ) -> tuple[Path, ...]:
        """Resolve pre-contract Editorial publish locations for read compatibility."""
        return (
            self.workspace_root() / "editorial" / "publish" / episode / sequence,
            self.project_root / "editorial" / "publish" / episode / sequence,
            self.project_root / "editorial" / episode / sequence,
        )

    def editorial_episode_publish_root(self, episode: str) -> Path:
        """Resolve one episode-level Editorial Publish container."""
        return self.editorial_publish_root() / episode

    def editorial_identity_registry_path(self, episode: str) -> Path:
        """Resolve the immutable CG Shot identity registry for an Editorial unit."""
        return self.editorial_episode_publish_root(episode) / "identity" / "shot_registry.json"

    def editorial_episode_revisions_root(self, episode: str) -> Path:
        """Resolve append-only Editorial revisions for an episode/unit."""
        return self.editorial_episode_publish_root(episode) / "revisions"

    def editorial_revisions_metadata_root(self, episode: str) -> Path:
        return self.editorial_episode_revisions_root(episode) / "metadata"

    def editorial_revision_dir(self, episode: str, revision: str) -> Path:
        return self.editorial_revisions_metadata_root(episode) / revision

    def editorial_revision_mapping_path(self, episode: str, revision: str) -> Path:
        return self.editorial_revision_dir(episode, revision) / "editorial_mapping.json"

    def editorial_revisions_media_root(self, episode: str) -> Path:
        return self.editorial_episode_revisions_root(episode) / "media"

    def editorial_event_media_root(self, episode: str, event_storage_id: str) -> Path:
        return self.editorial_revisions_media_root(episode) / event_storage_id

    def editorial_event_media_version_dir(
        self, episode: str, event_storage_id: str, media_version: str
    ) -> Path:
        return self.editorial_event_media_root(episode, event_storage_id) / media_version

    def editorial_event_media_clean_dir(
        self, episode: str, event_storage_id: str, media_version: str
    ) -> Path:
        return self.editorial_event_media_version_dir(
            episode, event_storage_id, media_version
        ) / "clean"

    def editorial_event_media_edit_dir(
        self, episode: str, event_storage_id: str, media_version: str
    ) -> Path:
        return self.editorial_event_media_version_dir(
            episode, event_storage_id, media_version
        ) / "edit"

    def legacy_editorial_revision_mapping_path(self, episode: str, revision: str) -> Path:
        return (
            self.editorial_episode_revisions_root(episode)
            / revision / "metadata" / "editorial_mapping.json"
        )

    def editorial_revision_clean_dir(self, episode: str, revision: str) -> Path:
        """Legacy global-revision Clean directory retained for read compatibility."""
        return self.editorial_episode_revisions_root(episode) / revision / "media" / "clean"

    def editorial_revision_edit_dir(self, episode: str, revision: str) -> Path:
        """Legacy global-revision HUD directory retained for read compatibility."""
        return self.editorial_episode_revisions_root(episode) / revision / "media" / "edit"

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

    def asset_work_dir(
        self, identity: AssetIdentity, department: str, dcc: str = "maya"
    ) -> Path:
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
                dcc=dcc,
                tool=dcc,
                workspace_partition=self.workspace_partition(department),
            )
        return self.asset_work_root(identity, department) / department / dcc

    def assembly_root(self, identity: AssemblyIdentity) -> Path:
        template = self._template("assembly_root")
        if template:
            return self._path_from_template(
                template, category=identity.category, group=identity.group,
                assembly=identity.name, assembly_name=identity.name,
                asset_name=identity.name, name=identity.name, variant=identity.variant,
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
                asset_name=identity.name, name=identity.name, variant=identity.variant,
                department=department, dept=department,
                workspace_partition=self.workspace_partition(department),
            )
        return self.workspace_root() / self.workspace_partition(department) / "assemblies" / identity.category / identity.group / identity.name / identity.variant / "work"

    def assembly_work_dir(
        self, identity: AssemblyIdentity, department: str, dcc: str = "maya"
    ) -> Path:
        template = self._template("assembly_work")
        if template:
            return self._path_from_template(
                template, category=identity.category, group=identity.group,
                assembly=identity.name, assembly_name=identity.name,
                asset_name=identity.name, name=identity.name,
                variant=identity.variant, department=department, dept=department,
                dcc=dcc, tool=dcc,
                workspace_partition=self.workspace_partition(department),
            )
        return self.assembly_work_root(identity, department) / department / dcc

    def assembly_data_root(self, identity: AssemblyIdentity) -> Path:
        template = self._template("assembly_data_root")
        return self._path_from_template(
            template, category=identity.category, group=identity.group,
            assembly=identity.name, assembly_name=identity.name,
            asset_name=identity.name, name=identity.name, variant=identity.variant,
        ) if template else self.assembly_variant_root(identity) / "data"

    def assembly_publish_root(self, identity: AssemblyIdentity) -> Path:
        template = self._template("assembly_publish_root")
        return self._path_from_template(
            template, category=identity.category, group=identity.group,
            assembly=identity.name, assembly_name=identity.name,
            asset_name=identity.name, name=identity.name, variant=identity.variant,
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

    def asset_work_scene_dir(
        self, identity: AssetIdentity, department: str, dcc: str = "maya"
    ) -> Path:
        return self.asset_work_dir(identity, department, dcc)

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

    def context_root_from_scene_path(self, scene_path: str | Path, department: str = "") -> tuple[str, Path] | None:
        """Resolve a scene path to the canonical shot or sequence context root."""

        shot_root = self.shot_root_from_scene_path(scene_path, department)
        if shot_root is not None:
            return "shot", shot_root
        sequence_root = self.sequence_root_from_scene_path(scene_path, department)
        if sequence_root is not None:
            return "sequence", sequence_root
        return None

    def shot_root_from_scene_path(self, scene_path: str | Path, department: str = "") -> Path | None:
        scene = Path(scene_path).resolve()
        for root in (self.shots_root(), self._workspace_shots_root(department)):
            try:
                relative = scene.relative_to(root.resolve())
            except Exception:
                continue
            if len(relative.parts) >= 3:
                return self.shot_root(relative.parts[0], relative.parts[1], relative.parts[2])
        return None

    def sequence_root_from_scene_path(self, scene_path: str | Path, department: str = "") -> Path | None:
        scene = Path(scene_path).resolve()
        for root in (self.sequences_root(), self._workspace_sequences_root(department)):
            try:
                relative = scene.relative_to(root.resolve())
            except Exception:
                continue
            if len(relative.parts) >= 2:
                return self.sequence_workspace_root(relative.parts[0], relative.parts[1])
        return None

    def _workspace_shots_root(self, department: str = "") -> Path:
        for template_name in ("shot_workspace_root", "shot_work_root"):
            template = self._template(template_name)
            if template and "{episode}" in template:
                return self._path_from_template(
                    template.split("{episode}", 1)[0].rstrip("/\\"),
                    department=department,
                    dept=department,
                    workspace_partition=self.workspace_partition(department),
                )
        return self.workspace_root() / self.workspace_partition(department) / "shots"

    def _workspace_sequences_root(self, department: str = "") -> Path:
        template = self._template("sequence_work_root")
        if template and "{episode}" in template:
            return self._path_from_template(
                template.split("{episode}", 1)[0].rstrip("/\\"),
                department=department,
                dept=department,
                workspace_partition=self.workspace_partition(department),
            )
        return self.workspace_root() / self.workspace_partition(department) / "sequences"

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
        template = self._template("sequence_work")
        if template:
            return self._path_from_template(
                template, episode=episode, sequence=sequence, seq=sequence,
                department=department, dept=department, dcc=dcc, tool=dcc,
                workspace_partition=self.workspace_partition(department),
            )
        root_template = self._template("sequence_work_root")
        if root_template:
            root = self._path_from_template(
                root_template, episode=episode, sequence=sequence, seq=sequence,
                department=department, dept=department,
                workspace_partition=self.workspace_partition(department),
            )
            return root / department / dcc
        return self.workspace_root() / self.workspace_partition(department) / "sequences" / episode / sequence / "work" / department / dcc

    def sequence_publish_dir(self, episode: str, sequence: str, publish_type: str) -> Path:
        return self.sequence_workspace_root(episode, sequence) / "publish" / publish_type

    def sequence_publish_version_dir(self, episode: str, sequence: str, publish_type: str, version: str) -> Path:
        return self.sequence_publish_dir(episode, sequence, publish_type) / version

    def shot_work_dir(self, episode: str, sequence: str, shot: str, department: str, tool_name: str = "maya") -> Path:
        template = self._template("shot_work")
        if template:
            resolved = self._path_from_template(
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
            if "{dcc}" not in template and "{tool}" not in template:
                return resolved / tool_name
            return resolved
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

    def shot_construct_build_manifests(
        self,
        episode: str,
        sequence: str,
        shot: str,
        department: str = "",
    ) -> tuple[Path, ...]:
        """List Construct build manifests for a shot across DCCs and tasks."""
        departments = [department] if department else [
            "",
            *(self.shot_dept_partitions or {}).keys(),
        ]
        canonical_roots = {
            self.shot_build_root(episode, sequence, shot, name)
            for name in departments
        }
        compatibility_roots = {
            self.workspace_root() / episode / sequence / shot / "build",
            self.shot_output_root(episode, sequence, shot) / "scene_build",
        }
        patterns = (
            "*/*/*/v*/build_manifest.json",
            "*/*/v*/build_manifest.json",
        )
        manifests = {
            manifest
            for root in (*canonical_roots, *compatibility_roots)
            if root.is_dir()
            for pattern in patterns
            for manifest in root.glob(pattern)
            if manifest.is_file()
        }
        return tuple(sorted(manifests, key=lambda path: str(path).lower()))

    def shot_data_root(self, episode: str, sequence: str, shot: str) -> Path:
        return self._shot_area_root("shot_data_root", episode, sequence, shot, "data")

    def shot_publish_root(self, episode: str, sequence: str, shot: str) -> Path:
        return self._shot_area_root("shot_publish_root", episode, sequence, shot, "publish")

    def shot_workspace_root(
        self, episode: str, sequence: str, shot: str, department: str = ""
    ) -> Path:
        template = self._template("shot_workspace_root")
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
            )
        return (
            self.workspace_root()
            / self.workspace_partition(department)
            / "shots"
            / episode
            / sequence
            / shot
        )

    def shot_output_root(
        self, episode: str, sequence: str, shot: str, department: str = ""
    ) -> Path:
        return self._shot_area_root(
            "shot_output_root", episode, sequence, shot, "output", department
        )

    def shot_render_root(
        self, episode: str, sequence: str, shot: str, department: str = ""
    ) -> Path:
        return self._shot_area_root(
            "shot_render_root", episode, sequence, shot, "render", department
        )

    def shot_review_root(
        self, episode: str, sequence: str, shot: str, department: str = ""
    ) -> Path:
        return self._shot_area_root(
            "shot_review_root", episode, sequence, shot, "review", department
        )

    def shot_review_movie_dir(
        self, episode: str, sequence: str, shot: str, department: str
    ) -> Path:
        """Resolve the working review movie directory for a department."""
        template = self._template("shot_review_movie")
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
            )
        return self.shot_review_root(
            episode, sequence, shot, department
        ) / department / "mov"

    def shot_render_layers_root(
        self, episode: str, sequence: str, shot: str, department: str
    ) -> Path:
        template = self._template("shot_render_layers_root")
        if template:
            return self._path_from_template(
                template, episode=episode, sequence=sequence, seq=sequence, shot=shot,
                department=department, dept=department,
                workspace_partition=self.workspace_partition(department),
            )
        return self.shot_render_root(episode, sequence, shot, department) / department / "layers"

    def shot_render_layer_version_dir(
        self, episode: str, sequence: str, shot: str, department: str,
        layer: str, version: str,
    ) -> Path:
        template = self._template("shot_render_layer_version")
        if template:
            return self._path_from_template(
                template, episode=episode, sequence=sequence, seq=sequence, shot=shot,
                department=department, dept=department, layer=layer, version=version,
                workspace_partition=self.workspace_partition(department),
            )
        return self.shot_render_layers_root(episode, sequence, shot, department) / layer / version

    def shot_review_build_dir(
        self, episode: str, sequence: str, shot: str, department: str,
        version: str, take: str,
    ) -> Path:
        template = self._template("shot_review_build")
        if template:
            return self._path_from_template(
                template, episode=episode, sequence=sequence, seq=sequence, shot=shot,
                department=department, dept=department, version=version, take=take,
                workspace_partition=self.workspace_partition(department),
            )
        return self.shot_review_root(episode, sequence, shot, department) / "review_build" / version / take

    def shot_composition_data_root(self, episode: str, sequence: str, shot: str) -> Path:
        """Resolve the version container for Shot Composition data."""
        return self.shot_data_root(episode, sequence, shot) / "shot_composition"

    def shot_review_layers_data_root(self, episode: str, sequence: str, shot: str) -> Path:
        """Resolve the version container for Review Layer definitions."""
        return self.shot_data_root(episode, sequence, shot) / "review_layers"

    def shot_precomp_publish_root(self, episode: str, sequence: str, shot: str) -> Path:
        """Resolve the shot-wide PreComp publish container (no department axis)."""
        return self.shot_publish_root(episode, sequence, shot) / "precomp"

    def shot_review_construct_root(
        self, episode: str, sequence: str, shot: str, department: str,
        dcc: str = "maya", task: str = "main",
    ) -> Path:
        """Resolve the canonical, reusable Review construct container."""
        return self.shot_build_root(episode, sequence, shot, department) / "review" / department / dcc / task

    def shot_review_jobs_root(
        self, episode: str, sequence: str, shot: str, department: str = ""
    ) -> Path:
        """Resolve transient Review worker jobs for a shot."""
        return self.shot_workspace_root(episode, sequence, shot, department) / "jobs" / "review"

    def shot_review_output_root(
        self, episode: str, sequence: str, shot: str, department: str,
        audience: str,
    ) -> Path:
        """Resolve the submission container for an Internal/Client audience."""
        return self.shot_output_root(
            episode, sequence, shot, department
        ) / "review" / audience

    def shot_review_publish_root(
        self, episode: str, sequence: str, shot: str, department: str
    ) -> Path:
        return self.shot_publish_root(
            episode, sequence, shot
        ) / "review" / department

    def shot_animation_review_output_root(
        self, episode: str, sequence: str, shot: str, department: str
    ) -> Path:
        return self.shot_output_root(
            episode, sequence, shot, department
        ) / "review" / "animation"

    def legacy_preview_render_layers_root(
        self, episode: str, sequence: str, shot: str, department: str
    ) -> Path:
        """Resolve the pre-architecture Preview Render location for read compatibility."""
        return self.shot_root(episode, sequence, shot) / "output" / "preview_render" / department / "layers"

    def _shot_area_root(
        self, template_name: str, episode: str, sequence: str, shot: str,
        fallback: str, department: str = ""
    ) -> Path:
        template = self._template(template_name)
        if template:
            return self._path_from_template(
                template, episode=episode, sequence=sequence, seq=sequence, shot=shot,
                department=department, dept=department,
                workspace_partition=self.workspace_partition(department),
            )
        if template_name in {"shot_output_root", "shot_render_root", "shot_review_root"}:
            return self.shot_workspace_root(episode, sequence, shot, department) / fallback
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

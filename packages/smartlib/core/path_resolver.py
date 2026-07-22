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
class ProjectPaths:
    project_root: Path
    templates: dict[str, str] | None = None
    project_name: str = ""

    def assets_root(self) -> Path:
        template = self._template("assets_root")
        if template:
            return self._path_from_template(template)
        asset_root = self._template("asset_root")
        if asset_root and "{category}" in asset_root:
            return self._path_from_template(asset_root.split("{category}", 1)[0].rstrip("/\\"))
        return self.project_root / "assets"

    def shots_root(self) -> Path:
        template = self._template("shots_root")
        if template:
            return self._path_from_template(template)
        shot_root = self._template("shot_root")
        if shot_root and "{episode}" in shot_root:
            return self._path_from_template(shot_root.split("{episode}", 1)[0].rstrip("/\\"))
        return self.project_root / "shots"

    def sequences_root(self) -> Path:
        template = self._template("sequences_root")
        if template:
            return self._path_from_template(template)
        return self.project_root / "sequences"

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
        return self.asset_variant_root(identity) / "work" / department

    def asset_data_dir(self, identity: AssetIdentity, data_type: str, subset: str) -> Path:
        return self.asset_variant_root(identity) / "data" / data_type / subset

    def asset_publish_dir(self, identity: AssetIdentity, publish_type: str, subset: str) -> Path:
        return self.asset_variant_root(identity) / "publish" / publish_type / subset

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

    def sequence_work_dir(self, episode: str, sequence: str, department: str, dcc: str) -> Path:
        return self.sequence_workspace_root(episode, sequence) / department / "work" / dcc

    def sequence_publish_dir(self, episode: str, sequence: str, publish_type: str) -> Path:
        return self.sequence_workspace_root(episode, sequence) / "publish" / publish_type

    def sequence_publish_version_dir(self, episode: str, sequence: str, publish_type: str, version: str) -> Path:
        return self.sequence_publish_dir(episode, sequence, publish_type) / version

    def shot_work_dir(self, episode: str, sequence: str, shot: str, department: str, tool_name: str = "maya") -> Path:
        return self.shot_root(episode, sequence, shot) / "work" / department / tool_name

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
        return self.shot_root(episode, sequence, shot) / "data" / data_type / target / subset

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
        return self.shot_root(episode, sequence, shot) / "publish" / publish_type / subset

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
        return Path(self._expand_template(value, self._template_fields(fields)))

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AssetIdentity:
    category: str
    group: str
    name: str
    variant: str = "default"


@dataclass(frozen=True)
class ProjectPaths:
    project_root: Path

    def assets_root(self) -> Path:
        return self.project_root / "assets"

    def shots_root(self) -> Path:
        return self.project_root / "shots"

    def asset_root(self, identity: AssetIdentity) -> Path:
        return self.assets_root() / identity.category / identity.group / identity.name

    def asset_variant_root(self, identity: AssetIdentity) -> Path:
        return self.asset_root(identity) / identity.variant

    def asset_work_dir(self, identity: AssetIdentity, department: str) -> Path:
        return self.asset_variant_root(identity) / department / "work"

    def asset_data_dir(self, identity: AssetIdentity, data_type: str, subset: str) -> Path:
        return self.asset_variant_root(identity) / "data" / data_type / subset

    def asset_publish_dir(self, identity: AssetIdentity, publish_type: str, subset: str) -> Path:
        return self.asset_variant_root(identity) / "publish" / publish_type / subset

    def asset_work_scene_dir(self, identity: AssetIdentity, department: str) -> Path:
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
        return self.shots_root() / episode / sequence / shot

    def sequence_root(self, episode: str, sequence: str) -> Path:
        return self.shots_root() / episode / sequence

    def shot_work_dir(self, episode: str, sequence: str, shot: str, department: str) -> Path:
        return self.shot_root(episode, sequence, shot) / department / "work"

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

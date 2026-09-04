from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smartlib.apps.shot_manager import SequenceIdentity, ShotIdentity, ShotManagerService
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json
from smartlib.core.versioning import format_version, next_version, parse_version


@dataclass(frozen=True)
class CastCandidate:
    cast_key: str
    asset: str
    namespace: str
    entry: dict[str, Any]


class SequenceCastPublisherService:
    """Resolve Sequence Cast candidates and publish reviewed per-Shot Cast data."""

    def __init__(self, project_config: ProjectConfig):
        self.project_config = project_config
        self.shots = ShotManagerService(project_config)

    def identity_from_scene(self) -> SequenceIdentity:
        try:
            import maya.cmds as cmds
        except ImportError as exc:
            raise RuntimeError("Sequence Cast Publisher is available inside Maya.") from exc
        scene = Path(cmds.file(query=True, sceneName=True) or "")
        if not scene:
            raise RuntimeError("Save or open a Sequence scene first.")
        departments = ["layout", *self.shots.shot_departments, ""]
        for department in dict.fromkeys(departments):
            root = self.shots.paths.sequence_root_from_scene_path(scene, department)
            if root is not None:
                return SequenceIdentity(root.parent.name, root.name)
        raise RuntimeError(f"Could not resolve Sequence identity from the open scene: {scene}")

    def candidates(self, identity: SequenceIdentity) -> list[CastCandidate]:
        data = self.shots.load_sequence_cast(identity.episode, identity.sequence)
        rows = []
        for cast_key, raw in sorted((data.get("cast") or {}).items(), key=lambda item: str(item[0]).lower()):
            if not isinstance(raw, dict):
                continue
            entry = dict(raw)
            rows.append(CastCandidate(
                cast_key=str(cast_key),
                asset=str(entry.get("asset") or entry.get("entity_id") or cast_key),
                namespace=str(entry.get("namespace") or cast_key),
                entry=entry,
            ))
        known_namespaces = {row.namespace.casefold() for row in rows}
        for candidate in self._scene_reference_candidates():
            if candidate.namespace.casefold() not in known_namespaces:
                rows.append(candidate)
        return sorted(rows, key=lambda row: row.cast_key.lower())

    def _scene_reference_candidates(self) -> list[CastCandidate]:
        try:
            import maya.cmds as cmds
            from smartlib.dcc.maya.smart_shot import list_sequencer_shots
        except ImportError:
            return []
        camera_namespaces = {
            str(shot.camera or "").split(":", 1)[0].strip(":").casefold()
            for shot in list_sequencer_shots()
            if ":" in str(shot.camera or "")
        }
        rows = []
        for reference_node in cmds.ls(type="reference") or []:
            if reference_node == "sharedReferenceNode":
                continue
            try:
                namespace = str(cmds.referenceQuery(reference_node, namespace=True) or "").strip(":")
                path = Path(str(cmds.referenceQuery(reference_node, filename=True, withoutCopyNumber=True) or ""))
            except RuntimeError:
                continue
            if not namespace or namespace.casefold() in camera_namespaces:
                continue
            asset_identity = self.shots.asset_publish_resolver.identity_from_publish_path(path)
            if asset_identity is None:
                continue
            entity_id = "/".join((asset_identity.category, asset_identity.group, asset_identity.name))
            version = next((part for part in reversed(path.parts) if parse_version(part) is not None), "approved")
            entry = {
                "asset": asset_identity.name,
                "entity_type": "asset",
                "entity_id": entity_id,
                "variant": asset_identity.variant,
                "category": asset_identity.category,
                "namespace": namespace,
                "asset_publish": version,
                "required": True,
                "note": "Detected from open Sequence scene reference",
            }
            rows.append(CastCandidate(namespace, asset_identity.name, namespace, entry))
        return rows

    def thumbnail_path(self, identity: ShotIdentity) -> Path | None:
        return self.shots.shot_thumbnail_path(identity)

    def shot_identity(self, sequence: SequenceIdentity, shot: str) -> ShotIdentity:
        return ShotIdentity(sequence.episode, sequence.sequence, str(shot))

    def current_publish_version(self, identity: ShotIdentity) -> str:
        base = self.shots.shot_publish_root(identity) / "cast" / "main"
        latest = read_json(base / "latest.json", {}) or {}
        return str(latest.get("version") or "")

    def next_publish_version(self, identity: ShotIdentity) -> str:
        base = self.shots.shot_publish_root(identity) / "cast" / "main"
        versions = [parse_version(path.name) for path in base.glob("v*") if path.is_dir()]
        return format_version(next_version([value for value in versions if value]))

    def publish(self, identity: ShotIdentity, cast_keys: list[str], *, comment: str = "") -> Path:
        sequence = SequenceIdentity(identity.episode, identity.sequence)
        available = {row.cast_key: row for row in self.candidates(sequence)}
        selected = []
        for cast_key in cast_keys:
            candidate = available.get(str(cast_key))
            if candidate is None:
                raise ValueError(f"Sequence Cast member was not found: {cast_key}")
            selected.append({"cast_key": candidate.cast_key, **candidate.entry})
        if not selected:
            raise ValueError("Select at least one Cast candidate.")
        cast_data = self.shots.build_cast_data(selected)
        self.shots.write_cast(identity, cast_data)
        return self.shots.publish_shot_cast_from_sequence(identity, comment=comment)

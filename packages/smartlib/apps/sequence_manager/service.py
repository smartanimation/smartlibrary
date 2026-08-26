from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smartlib.apps.shot_manager import SequenceIdentity, ShotManagerService
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json, write_json


@dataclass(frozen=True)
class SequenceSummary:
    identity: SequenceIdentity
    shots: list[dict[str, Any]]
    default_assemblies: list[dict[str, Any]]


class SequenceManagerService:
    """Sequence navigation, editorial order and shared assembly recommendations."""

    def __init__(self, project_config: ProjectConfig):
        self.config = project_config
        self.shots = ShotManagerService(project_config)

    def list_sequences(self) -> list[SequenceIdentity]:
        return self.shots.list_sequences()

    def sequence_path(self, identity: SequenceIdentity) -> Path:
        return self.shots.paths.sequence_root(identity.episode, identity.sequence) / "sequence.json"

    def load(self, identity: SequenceIdentity) -> SequenceSummary:
        data = read_json(self.sequence_path(identity), {}) or {}
        rows = list(data.get("shots") or [])
        known = {str(row.get("shot")) for row in rows}
        for shot in self.shots.list_shots():
            if shot.episode == identity.episode and shot.sequence == identity.sequence and shot.shot not in known:
                shot_data = self.shots.load_shot(shot)
                editorial = shot_data.get("editorial") or {}
                rows.append({"shot": shot.shot, "order": len(rows) * 10 + 10, "cut_in": editorial.get("cut_in"), "cut_out": editorial.get("cut_out")})
        rows.sort(key=lambda row: (int(row.get("order") or 0), str(row.get("shot") or "")))
        return SequenceSummary(identity, rows, list((data.get("defaults") or {}).get("assemblies") or []))

    def save(self, summary: SequenceSummary) -> Path:
        path = self.sequence_path(summary.identity)
        return write_json(path, {
            "schema": "smartpipeline.sequence.v2", "entity_type": "sequence",
            "episode": summary.identity.episode, "sequence": summary.identity.sequence,
            "shots": summary.shots, "defaults": {"assemblies": summary.default_assemblies},
        })


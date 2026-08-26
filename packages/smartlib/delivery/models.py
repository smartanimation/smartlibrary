from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ShotContext:
    episode: str
    sequence: str
    shot: str
    task: str
    version: int

    def tokens(self) -> dict[str, Any]:
        return {
            "episode": self.episode,
            "sequence": self.sequence,
            "shot": self.shot,
            "task": self.task,
            "version": self.version,
        }


@dataclass(frozen=True)
class AssetContext:
    category: str
    group: str
    asset_name: str
    variant: str
    task: str
    version: int

    @property
    def code(self) -> str:
        return f"{self.category}_{self.group}_{self.asset_name}_{self.variant}"

    def tokens(self) -> dict[str, Any]:
        return {
            "category": self.category, "group": self.group,
            "asset_name": self.asset_name, "asset": self.asset_name,
            "variant": self.variant, "task": self.task,
            "version": int(self.version),
        }


@dataclass(frozen=True)
class DeliveryInput:
    id: str
    kind: str
    source: Path
    template: str
    required: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeliveryItem:
    id: str
    kind: str
    source: Path
    destination: Path
    required: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source"] = self.source.as_posix()
        data["destination"] = self.destination.as_posix()
        return data


@dataclass(frozen=True)
class ValidationResult:
    code: str
    severity: str
    message: str
    item_id: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class DeliveryPlan:
    job_id: str
    profile_id: str
    profile_version: int
    context: ShotContext | AssetContext
    items: list[DeliveryItem]
    package_root: Path
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "smart_delivery_plan/v1",
            "job_id": self.job_id,
            "profile": {"id": self.profile_id, "version": self.profile_version},
            "context": asdict(self.context),
            "package_root": self.package_root.as_posix(),
            "items": [item.to_dict() for item in self.items],
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class DeliveryResult:
    package_root: Path
    manifest: Path
    validation_report: Path
    archive: Path | None
    contact_sheet: Path | None
    results: tuple[ValidationResult, ...]

    @property
    def blocked(self) -> bool:
        return any(row.severity == "ERROR" for row in self.results)

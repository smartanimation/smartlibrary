from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from smartlib.crowd.schema import BehaviorSchema


Vector3 = tuple[float, float, float]


@dataclass
class InteractionPoint:
    id: str
    interaction_type: str
    position: Vector3 = (0.0, 0.0, 0.0)
    rotation: Vector3 = (0.0, 0.0, 0.0)
    approach_position: Vector3 = (0.0, 0.0, -1.0)
    enabled: bool = True
    priority: int = 0
    occupied: bool = False
    reserved_by: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "InteractionPoint":
        return cls(
            id=str(data.get("id") or ""),
            interaction_type=str(data.get("interaction_type") or data.get("type") or ""),
            position=_vector(data.get("position")),
            rotation=_vector(data.get("rotation")),
            approach_position=_vector(data.get("approach_position")),
            enabled=bool(data.get("enabled", True)),
            priority=int(data.get("priority", 0)),
            occupied=bool(data.get("occupied", False)),
            reserved_by=str(data.get("reserved_by") or data.get("reservedBy") or ""),
            metadata=dict(data.get("metadata") or {}),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "interaction_type": self.interaction_type,
            "position": _vector_mapping(self.position),
            "rotation": _vector_mapping(self.rotation),
            "approach_position": _vector_mapping(self.approach_position),
            "enabled": self.enabled,
            "priority": self.priority,
            "occupied": self.occupied,
            "reserved_by": self.reserved_by,
            "metadata": dict(self.metadata),
        }


class InteractionSystem:
    """Owns world affordances. It does not decide character intent."""

    def __init__(self, schema: BehaviorSchema):
        self.schema = schema
        self._points: dict[str, InteractionPoint] = {}

    @classmethod
    def from_yaml_data(cls, schema: BehaviorSchema, data: dict[str, Any]) -> "InteractionSystem":
        system = cls(schema)
        for interaction in data.get("interactions") or []:
            parent_id = str(interaction.get("id") or "")
            for point in interaction.get("points") or []:
                normalized = dict(point)
                metadata = dict(normalized.get("metadata") or {})
                if parent_id:
                    metadata["interaction_id"] = parent_id
                    metadata["asset_type"] = interaction.get("type", "")
                normalized["metadata"] = metadata
                system.add_point(InteractionPoint.from_mapping(normalized))
        return system

    def add_point(self, point: InteractionPoint) -> None:
        self.schema.require_option("interaction_types", point.interaction_type)
        if not point.id:
            raise ValueError("Interaction point id is required.")
        self._points[point.id] = point

    def points(self) -> list[InteractionPoint]:
        return list(self._points.values())

    def get(self, point_id: str) -> InteractionPoint:
        return self._points[point_id]

    def find_available(self, interaction_type: str) -> InteractionPoint | None:
        self.schema.require_option("interaction_types", interaction_type)
        candidates = [
            point
            for point in self._points.values()
            if point.interaction_type == interaction_type and point.enabled and not point.occupied and not point.reserved_by
        ]
        candidates.sort(key=lambda point: (-point.priority, point.id))
        return candidates[0] if candidates else None

    def reserve(self, point_id: str, agent_id: str = "") -> InteractionPoint:
        point = self._points[point_id]
        if not point.enabled:
            raise ValueError(f"Interaction point is disabled: {point_id}")
        if point.occupied:
            raise ValueError(f"Interaction point is already occupied: {point_id}")
        if point.reserved_by and point.reserved_by != agent_id:
            raise ValueError(f"Interaction point is already reserved: {point_id}")
        point.reserved_by = agent_id or "__reserved__"
        return point

    def occupy(self, point_id: str, agent_id: str = "") -> InteractionPoint:
        point = self._points[point_id]
        if not point.enabled:
            raise ValueError(f"Interaction point is disabled: {point_id}")
        if point.occupied and (not agent_id or point.reserved_by == agent_id):
            return point
        if point.occupied:
            raise ValueError(f"Interaction point is already occupied: {point_id}")
        if point.reserved_by and agent_id and point.reserved_by != agent_id:
            raise ValueError(f"Interaction point is reserved by another agent: {point_id}")
        point.occupied = True
        point.reserved_by = ""
        return point

    def release(self, point_id: str) -> None:
        self._points[point_id].occupied = False
        self._points[point_id].reserved_by = ""


def _vector(value: Any) -> Vector3:
    if isinstance(value, dict):
        return (float(value.get("x", 0.0)), float(value.get("y", 0.0)), float(value.get("z", 0.0)))
    if isinstance(value, (list, tuple)):
        padded = list(value[:3]) + [0.0, 0.0, 0.0]
        return (float(padded[0]), float(padded[1]), float(padded[2]))
    return (0.0, 0.0, 0.0)


def _vector_mapping(value: Vector3) -> dict[str, float]:
    return {"x": float(value[0]), "y": float(value[1]), "z": float(value[2])}

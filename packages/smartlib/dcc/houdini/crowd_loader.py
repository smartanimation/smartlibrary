from __future__ import annotations

from pathlib import Path
from typing import Any

from smartlib.crowd.schema import load_behavior_schema
from smartlib.crowd.yamlio import load_yaml


def load_interaction_points(
    interaction_yaml: str | Path,
    *,
    schema_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    schema = load_behavior_schema(schema_path)
    data = load_yaml(interaction_yaml)
    return interaction_points_from_data(data, schema_path=schema.path)


def interaction_points_from_data(
    data: dict[str, Any],
    *,
    schema_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    schema = load_behavior_schema(schema_path)
    points = []
    for interaction in data.get("interactions") or []:
        for point in interaction.get("points") or []:
            interaction_type = str(point.get("interaction_type") or "")
            schema.require_option("interaction_types", interaction_type)
            point_id = str(point.get("id") or "")
            points.append(
                {
                    "P": _position_tuple(point.get("position")),
                    "interaction_type": interaction_type,
                    "enabled": bool(point.get("enabled", True)),
                    "priority": int(point.get("priority", 0)),
                    "seat_id": point_id,
                    "interaction_id": str(interaction.get("id") or ""),
                    "approach_position": _position_tuple(point.get("approach_position")),
                    "occupied": bool(point.get("occupied", False)),
                    "reserved_by": str(point.get("reserved_by") or point.get("reservedBy") or ""),
                }
            )
    return points


def load_animation_data(animation_yaml: str | Path) -> dict[str, Any]:
    return load_yaml(animation_yaml).get("animation") or {}


def create_interaction_point_geometry(
    interaction_yaml: str | Path,
    *,
    schema_path: str | Path | None = None,
):
    """Create Houdini point geometry with semantic point attributes."""

    import hou

    points = load_interaction_points(interaction_yaml, schema_path=schema_path)
    geo = hou.Geometry()
    attrs = {
        "interaction_type": geo.addAttrib(hou.attribType.Point, "interaction_type", ""),
        "enabled": geo.addAttrib(hou.attribType.Point, "enabled", 1),
        "priority": geo.addAttrib(hou.attribType.Point, "priority", 0),
        "seat_id": geo.addAttrib(hou.attribType.Point, "seat_id", ""),
        "interaction_id": geo.addAttrib(hou.attribType.Point, "interaction_id", ""),
        "occupied": geo.addAttrib(hou.attribType.Point, "occupied", 0),
        "reserved_by": geo.addAttrib(hou.attribType.Point, "reserved_by", ""),
        "approach_position": geo.addAttrib(hou.attribType.Point, "approach_position", (0.0, 0.0, 0.0)),
    }
    for item in points:
        point = geo.createPoint()
        point.setPosition(item["P"])
        point.setAttribValue(attrs["interaction_type"], item["interaction_type"])
        point.setAttribValue(attrs["enabled"], int(item["enabled"]))
        point.setAttribValue(attrs["priority"], int(item["priority"]))
        point.setAttribValue(attrs["seat_id"], item["seat_id"])
        point.setAttribValue(attrs["interaction_id"], item["interaction_id"])
        point.setAttribValue(attrs["occupied"], int(item["occupied"]))
        point.setAttribValue(attrs["reserved_by"], item["reserved_by"])
        point.setAttribValue(attrs["approach_position"], item["approach_position"])
    return geo


def _position_tuple(value: Any) -> tuple[float, float, float]:
    if isinstance(value, dict):
        return (float(value.get("x", 0.0)), float(value.get("y", 0.0)), float(value.get("z", 0.0)))
    if isinstance(value, (list, tuple)):
        padded = list(value[:3]) + [0.0, 0.0, 0.0]
        return (float(padded[0]), float(padded[1]), float(padded[2]))
    return (0.0, 0.0, 0.0)

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smartlib.crowd.yamlio import load_yaml


SCHEMA_ENV = "SMART_CROWD_BEHAVIOR_SCHEMA"
SCHEMA_VERSION = "smart_crowd_behavior.v1"
SCHEMA_GROUPS = ("interaction_types", "animation_types", "animation_styles")


@dataclass(frozen=True)
class BehaviorSchema:
    path: Path
    data: dict[str, Any]

    def options(self, group: str) -> dict[str, dict[str, Any]]:
        if group not in SCHEMA_GROUPS:
            raise KeyError(f"Unknown behavior schema group: {group}")
        values = self.data.get(group) or {}
        if not isinstance(values, dict):
            raise ValueError(f"Schema group must be a mapping: {group}")
        return {str(key): dict(value or {}) for key, value in values.items()}

    def option_ids(self, group: str) -> list[str]:
        return list(self.options(group).keys())

    def option_labels(self, group: str) -> dict[str, str]:
        labels = {}
        for key, value in self.options(group).items():
            labels[key] = str(value.get("label") or key)
        return labels

    def require_option(self, group: str, option_id: str) -> str:
        option_id = str(option_id or "").strip()
        if option_id not in self.options(group):
            raise ValueError(f"Unknown {group} value in {self.path}: {option_id}")
        return option_id


def default_schema_path() -> Path:
    env_value = os.environ.get(SCHEMA_ENV)
    if env_value:
        return Path(env_value)
    return Path(__file__).resolve().parents[3] / "config" / "behavior_schema.yaml"


def load_behavior_schema(path: str | os.PathLike[str] | None = None) -> BehaviorSchema:
    schema_path = Path(path) if path else default_schema_path()
    data = load_yaml(schema_path)
    if data.get("schema") != SCHEMA_VERSION:
        raise ValueError(f"Unexpected behavior schema version in {schema_path}: {data.get('schema')}")
    for group in SCHEMA_GROUPS:
        values = data.get(group)
        if not isinstance(values, dict) or not values:
            raise ValueError(f"Behavior schema requires a non-empty '{group}' mapping: {schema_path}")
    return BehaviorSchema(path=schema_path, data=data)

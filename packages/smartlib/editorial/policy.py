from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from smartlib.core.config_loader import ProjectConfig


@dataclass(frozen=True)
class EditorialHandlePolicy:
    head: int
    tail: int


def editorial_handle_policy(project_config: ProjectConfig) -> EditorialHandlePolicy:
    editorial = project_config.base.get("editorial") or {}
    handles: dict[str, Any] = editorial.get("handle_policy") or {}
    return EditorialHandlePolicy(
        head=_non_negative_int(handles.get("head"), 8),
        tail=_non_negative_int(handles.get("tail"), 8),
    )


def _non_negative_int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default

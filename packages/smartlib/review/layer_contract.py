from __future__ import annotations

from typing import Any

from smartlib.core.validation import ValidationIssue


def validate_review_layer_contract(
    review_spec: dict[str, Any],
    cast_data: dict[str, Any] | None = None,
) -> list[ValidationIssue]:
    """Validate review_spec.json against the independently managed cast."""

    issues: list[ValidationIssue] = []
    cast_keys = set(((cast_data or {}).get("cast") or {}).keys())
    layers = review_spec.get("layers") or {}
    if not isinstance(layers, dict):
        return [ValidationIssue("review_layers_type", "layers must be an object.", "error")]
    for layer_name, layer in layers.items():
        layer = layer or {}
        for member in layer.get("members") or []:
            if cast_keys and member not in cast_keys:
                issues.append(
                    ValidationIssue(
                        "review_member_missing",
                        f"{layer_name} references missing cast member: {member}",
                        "error",
                    )
                )
        resolution = layer.get("resolution") or {}
        if isinstance(resolution, dict):
            width = resolution.get("width")
            height = resolution.get("height")
            if width is not None and int(width) <= 0:
                issues.append(
                    ValidationIssue("review_width", f"{layer_name} width must be positive.", "error")
                )
            if height is not None and int(height) <= 0:
                issues.append(
                    ValidationIssue("review_height", f"{layer_name} height must be positive.", "error")
                )
    return issues

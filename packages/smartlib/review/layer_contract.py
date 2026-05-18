from __future__ import annotations

from typing import Any

from smartlib.apps.shot_manager.service import validate_cast_data
from smartlib.core.validation import ValidationIssue


def validate_review_layer_contract(cast_data: dict[str, Any]) -> list[ValidationIssue]:
    """Validate cast.json review_layers as the source-of-truth layer contract."""

    return validate_cast_data(cast_data)

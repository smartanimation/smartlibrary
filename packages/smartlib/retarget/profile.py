"""Load Retarget profiles with optional project-template inheritance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ProfileError(ValueError):
    """Raised when a Retarget profile or its template cannot be resolved."""


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_retarget_profile(path: str | Path) -> dict[str, Any]:
    """Return a materialized profile, recursively resolving ``template.path``."""
    return _load(Path(path).resolve(), set())


def _load(path: Path, loading: set[Path]) -> dict[str, Any]:
    if path in loading:
        raise ProfileError(f"Circular Retarget template reference: {path}")
    if not path.is_file():
        raise ProfileError(f"Retarget profile was not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"Could not read Retarget profile {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProfileError(f"Retarget profile must be a JSON object: {path}")
    template = payload.get("template")
    template_path = template.get("path") if isinstance(template, dict) else None
    if not template_path:
        return payload
    dependency = Path(str(template_path))
    if not dependency.is_absolute():
        dependency = path.parent / dependency
    loading.add(path)
    base = _load(dependency.resolve(), loading)
    loading.remove(path)
    return _merge(base, payload)

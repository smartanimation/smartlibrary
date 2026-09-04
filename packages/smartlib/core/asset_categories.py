from __future__ import annotations

from typing import Any, Mapping


ASSET_CATEGORIES = ("character", "environment", "prop", "vehicle")

_CATEGORY_ALIASES = {
    "ch": "character",
    "char": "character",
    "character": "character",
    "characters": "character",
    "hero": "character",
    "bg": "environment",
    "bga": "environment",
    "env": "environment",
    "environment": "environment",
    "environments": "environment",
    "set": "environment",
    "sets": "environment",
    "bp": "prop",
    "cp": "prop",
    "prop": "prop",
    "props": "prop",
    "car": "vehicle",
    "veh": "vehicle",
    "vehicle": "vehicle",
    "vehicles": "vehicle",
}


def canonical_asset_category(value: Any, *, strict: bool = False) -> str:
    """Return the Smart Pipeline category used for internal identities and paths."""
    text = str(value or "").strip()
    canonical = _CATEGORY_ALIASES.get(text.lower(), text.lower())
    if strict and canonical not in ASSET_CATEGORIES:
        raise ValueError(
            f"Unsupported asset category: {text or '<empty>'}. "
            f"Expected one of: {', '.join(ASSET_CATEGORIES)}"
        )
    return canonical


def mapped_asset_category(
    value: Any,
    mapping: Mapping[str, Any] | None,
    *,
    canonical_fallback: bool = True,
) -> str:
    """Apply a case-insensitive profile mapping to an asset category."""
    text = str(value or "").strip()
    normalized = {str(key).strip().lower(): str(item).strip() for key, item in (mapping or {}).items()}
    result = normalized.get(text.lower(), text)
    return canonical_asset_category(result) if canonical_fallback else result

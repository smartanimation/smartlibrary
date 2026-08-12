from __future__ import annotations

from dataclasses import dataclass
from typing import Any


REFERENCE_MODES = {"reference", "maya_reference", "maya"}
PAYLOAD_MODES = {"payload", "usd_payload", "usd"}


@dataclass(frozen=True)
class AssetLoadDecision:
    mode: str
    context: str
    reason: str

    @property
    def component_type(self) -> str:
        return "usd" if self.mode == "payload" else "rig"


def resolve_asset_load_policy(
    metadata: dict[str, Any] | None,
    *,
    role: str = "",
    requested_context: str = "WORK",
    policy: dict[str, Any] | None = None,
) -> AssetLoadDecision:
    """Choose the artist-scene representation for one cast asset.

    Asset/variant metadata is authoritative. Project policy supplies category
    aliases and conservative fallbacks for assets that predate the metadata.
    """

    metadata = dict(metadata or {})
    policy = dict(policy or {})
    explicit = metadata.get("workspace_representation") or metadata.get("load_policy") or {}
    if isinstance(explicit, str):
        explicit = {"mode": explicit}
    if not isinstance(explicit, dict):
        explicit = {}

    explicit_mode = _normalize_mode(explicit.get("mode"))
    explicit_context = str(explicit.get("context") or requested_context or "WORK").upper()
    if explicit_mode:
        return AssetLoadDecision(explicit_mode, explicit_context, "metadata override")

    capabilities = metadata.get("capabilities") or {}
    if not isinstance(capabilities, dict):
        capabilities = {}
    if _as_bool(capabilities.get("rigged")) or _as_bool(capabilities.get("animated")):
        return AssetLoadDecision("reference", explicit_context, "rigged asset")

    category = str(metadata.get("category") or "").strip().lower()
    asset_type = str(metadata.get("asset_type") or metadata.get("type") or "").strip().lower()
    category_class = _category_class(category, asset_type, policy)
    defaults = policy.get("defaults") or {}
    if category_class:
        configured = defaults.get(category_class) or {}
        if isinstance(configured, str):
            configured = {"mode": configured}
        mode = _normalize_mode(configured.get("mode")) if isinstance(configured, dict) else ""
        context = (
            str(configured.get("context") or explicit_context).upper()
            if isinstance(configured, dict)
            else explicit_context
        )
        if mode:
            return AssetLoadDecision(mode, context, f"{category_class} policy")

    normalized_role = str(role or "").strip().upper()
    if normalized_role in {"BG", "BGA", "ENV", "BACKGROUND", "SET"}:
        return AssetLoadDecision("payload", explicit_context, "background role fallback")

    unknown = defaults.get("unknown") or {"mode": "reference"}
    if isinstance(unknown, str):
        unknown = {"mode": unknown}
    return AssetLoadDecision(
        _normalize_mode(unknown.get("mode")) or "reference",
        str(unknown.get("context") or explicit_context).upper(),
        "safe fallback",
    )


def _category_class(category: str, asset_type: str, policy: dict[str, Any]) -> str:
    aliases = policy.get("category_classes") or {}
    values = {category, asset_type}
    for class_name, names in aliases.items():
        if isinstance(names, str):
            names = [names]
        if values.intersection({str(name).strip().lower() for name in (names or [])}):
            return str(class_name)

    if values.intersection({"ch", "char", "character", "characters", "hero"}):
        return "character"
    if values.intersection({"bg", "env", "environment", "set", "sets"}):
        return "environment"
    if values.intersection({"prop", "props", "bp", "cp"}):
        return "prop"
    return ""


def _normalize_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode in REFERENCE_MODES:
        return "reference"
    if mode in PAYLOAD_MODES:
        return "payload"
    return ""


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

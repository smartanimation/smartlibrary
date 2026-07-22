from __future__ import annotations

from typing import Any


DEFAULT_ASSET_SUBSETS = {
    "model": ["proxy", "render", "low", "high"],
    "rig": ["layout", "anim"],
    "look": ["low", "high"],
    "groom": [],
}


def asset_subset_catalog(asset_config: dict[str, Any] | None) -> dict[str, list[str]]:
    """Return the shared subset catalog used by Asset Manager and Context UI."""

    config = asset_config or {}
    explicit = config.get("asset_subsets") or {}
    category_rules = config.get("work_subsets_by_category") or {}
    departments = list(DEFAULT_ASSET_SUBSETS)
    for source in (category_rules, explicit):
        for department in source:
            if department not in departments:
                departments.append(str(department))

    result: dict[str, list[str]] = {}
    for department in departments:
        values: list[str] = []
        configured = explicit.get(department)
        if isinstance(configured, str):
            configured = [configured]
        if isinstance(configured, list):
            _extend_unique(values, configured)
        else:
            rules = category_rules.get(department) or {}
            if isinstance(rules, dict):
                for rule_values in rules.values():
                    if isinstance(rule_values, str):
                        rule_values = [rule_values]
                    if isinstance(rule_values, list):
                        _extend_unique(values, rule_values)
            _extend_unique(values, DEFAULT_ASSET_SUBSETS.get(department, []))
        result[department] = values
    return result


def subsets_for_asset(
    asset_config: dict[str, Any] | None,
    department: str,
    *,
    category: str = "",
    group: str = "",
) -> list[str]:
    """Resolve category-specific subsets, falling back to the shared catalog."""

    config = asset_config or {}
    rules = (config.get("work_subsets_by_category") or {}).get(department) or {}
    if isinstance(rules, dict):
        for key in (category, group, "default"):
            values = rules.get(key)
            if isinstance(values, str):
                return [values]
            if isinstance(values, list):
                return [str(value) for value in values if str(value)]
    return list(asset_subset_catalog(config).get(department, []))


def _extend_unique(target: list[str], values) -> None:
    for value in values:
        text = str(value).strip()
        if text and text not in target:
            target.append(text)

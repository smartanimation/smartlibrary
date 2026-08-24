from __future__ import annotations

from pathlib import Path

from .models import DeliveryPlan, ValidationResult


def validate_plan_sources(plan: DeliveryPlan) -> list[ValidationResult]:
    results = []
    for item in plan.items:
        if item.source.exists():
            results.append(ValidationResult("SOURCE_EXISTS", "PASS", str(item.source), item.id))
        elif item.required:
            results.append(ValidationResult("MISSING_SOURCE", "ERROR", str(item.source), item.id))
        else:
            results.append(ValidationResult("MISSING_OPTIONAL_SOURCE", "WARNING", str(item.source), item.id))
    return results


def validate_constructed_package(plan: DeliveryPlan) -> list[ValidationResult]:
    results = []
    expected = set()
    for item in plan.items:
        target = plan.package_root / item.destination
        expected.add(item.destination.as_posix())
        if not target.is_file():
            results.append(ValidationResult("MISSING_PACKAGE_FILE", "ERROR", str(target), item.id))
            results.extend(_validate_exact_case(plan.package_root, item.destination, item.id))
            continue
        if target.stat().st_size == 0:
            results.append(ValidationResult("EMPTY_PACKAGE_FILE", "ERROR", str(target), item.id))
        else:
            results.append(ValidationResult("PACKAGE_FILE_VALID", "PASS", str(target), item.id))
        results.extend(_validate_exact_case(plan.package_root, item.destination, item.id))
    delivery_roots = {item.destination.parts[0] for item in plan.items if item.destination.parts}
    actual = set()
    for root_name in delivery_roots:
        delivery_root = plan.package_root / root_name
        if not delivery_root.is_dir():
            continue
        actual.update(
            path.relative_to(plan.package_root).as_posix()
            for path in delivery_root.rglob("*")
            if path.is_file()
        )
    for extra in sorted(actual - expected):
        results.append(ValidationResult("UNEXPECTED_PACKAGE_FILE", "WARNING", extra))
    return results


def _validate_exact_case(root: Path, relative: Path, item_id: str) -> list[ValidationResult]:
    current = root
    for part in relative.parts:
        if not current.is_dir():
            return []
        names = {child.name for child in current.iterdir()}
        if part not in names:
            case_match = next((name for name in names if name.casefold() == part.casefold()), "")
            if case_match:
                return [ValidationResult("PATH_CASE_MISMATCH", "ERROR", f"expected '{part}', found '{case_match}'", item_id)]
            return []
        current = current / part
    return []

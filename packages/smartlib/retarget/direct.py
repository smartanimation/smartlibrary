"""Explicit received-MCR to ANIM profile contract (no Maya dependency)."""
import math

MODE = "mcr_to_anim"


def validate_mappings(profile):
    errors, targets = [], set()
    mappings = profile.get("mappings", [])
    if not isinstance(mappings, list) or any(not isinstance(row, dict) for row in mappings):
        return ["Mappings must be a list of Source / Target objects"]
    rows = [row for row in mappings if row.get("enabled", True)]
    if not rows:
        errors.append("At least one enabled MCR → ANIM mapping is required")
    for index, row in enumerate(rows):
        if not row.get("source") or not row.get("target"):
            errors.append(f"Mapping {index + 1}: Source and Target are required")
        if row.get("method") not in {"orient", "parent", "point"}:
            errors.append(f"Mapping {index + 1}: unsupported transfer method")
        if row.get("target") in targets:
            errors.append(f"Duplicate ANIM target: {row['target']}")
        targets.add(row.get("target"))
    if "reference_frame" not in profile:
        errors.append("Reference frame is required for offset calibration")
    else:
        try:
            if not math.isfinite(float(profile["reference_frame"])):
                raise ValueError()
        except (ValueError, TypeError):
            errors.append("Reference frame must be a finite number")
    return errors

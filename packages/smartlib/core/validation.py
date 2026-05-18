from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: str = "warning"


def has_errors(issues: list[ValidationIssue]) -> bool:
    return any(issue.severity == "error" for issue in issues)

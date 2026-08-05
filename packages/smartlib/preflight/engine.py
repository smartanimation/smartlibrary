from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Iterable

from .models import (
    CheckDefinition,
    CheckResult,
    OutputDefinition,
    PreflightContext,
    PreflightProfile,
    PreflightReport,
    ProgressCallback,
    Severity,
)


class PreflightEngine:
    def __init__(self, adapter, profile: PreflightProfile):
        self.adapter = adapter
        self.profile = profile

    def run(
        self,
        context: PreflightContext,
        *,
        selected_outputs: Iterable[str] | None = None,
        only: Iterable[str] | None = None,
        on_progress: ProgressCallback | None = None,
        attempt_id: str | None = None,
    ) -> PreflightReport:
        output_keys = set(selected_outputs or self._default_output_keys())
        outputs = tuple(
            row for row in self.profile.outputs if row.required or row.key in output_keys
        )
        check_keys = set(only or ())
        checks = [
            row
            for row in self.profile.checks
            if (not check_keys or row.key in check_keys)
            and (not row.outputs or output_keys.intersection(row.outputs))
        ]
        report = PreflightReport(
            attempt_id=attempt_id or f"PF-{uuid.uuid4().hex[:8].upper()}",
            context=context,
            profile=self.profile.key,
            outputs=outputs,
        )
        for index, check in enumerate(checks, start=1):
            result = self._run_check(check, context)
            report.results.append(result)
            if on_progress:
                on_progress(index, len(checks), result)
        return report

    def _default_output_keys(self) -> set[str]:
        return {row.key for row in self.profile.outputs if row.required or row.selected}

    def _run_check(self, check: CheckDefinition, context: PreflightContext) -> CheckResult:
        started = time.perf_counter()
        try:
            result = check.run(self.adapter, context)
        except Exception as exc:
            result = CheckResult(
                key=check.key,
                label=check.label,
                severity=Severity.ERROR,
                message=f"Check failed unexpectedly: {exc}",
            )
        result.key = check.key
        result.label = check.label
        result.duration = time.perf_counter() - started
        return result

    @staticmethod
    def write_report(report: PreflightReport, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return target

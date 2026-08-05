from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol


class Severity(str, Enum):
    PASS = "PASS"
    WARNING = "WARNING"
    ERROR = "ERROR"
    RUNNING = "RUNNING"
    WAITING = "WAITING"


@dataclass(frozen=True)
class PreflightContext:
    kind: str
    project: str = ""
    entity: str = ""
    task: str = ""
    subset: str = "main"
    version: str = ""
    scene_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        parts = [self.entity, self.task, self.subset, self.version]
        return " / ".join(part for part in parts if part)


@dataclass(frozen=True)
class OutputDefinition:
    key: str
    label: str
    summary: str
    required: bool = False
    selected: bool = True


@dataclass
class CheckResult:
    key: str
    label: str
    severity: Severity
    message: str = ""
    nodes: tuple[str, ...] = ()
    duration: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["severity"] = self.severity.value
        data["nodes"] = list(self.nodes)
        return data


class CheckCallable(Protocol):
    def __call__(self, adapter: Any, context: PreflightContext) -> CheckResult: ...


@dataclass(frozen=True)
class CheckDefinition:
    key: str
    label: str
    run: CheckCallable
    outputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreflightProfile:
    key: str
    label: str
    publish_label: str
    outputs: tuple[OutputDefinition, ...]
    checks: tuple[CheckDefinition, ...]


@dataclass
class PreflightReport:
    attempt_id: str
    context: PreflightContext
    profile: str
    outputs: tuple[OutputDefinition, ...]
    results: list[CheckResult] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(row.severity == Severity.ERROR for row in self.results)

    @property
    def counts(self) -> dict[str, int]:
        return {
            state.value: sum(row.severity == state for row in self.results)
            for state in (Severity.PASS, Severity.WARNING, Severity.ERROR)
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "smart_preflight/v1",
            "attempt_id": self.attempt_id,
            "profile": self.profile,
            "context": asdict(self.context),
            "outputs": [asdict(row) for row in self.outputs],
            "results": [row.to_dict() for row in self.results],
            "blocked": self.blocked,
            "counts": self.counts,
        }


ProgressCallback = Callable[[int, int, CheckResult], None]

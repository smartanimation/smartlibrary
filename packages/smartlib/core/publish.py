from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from smartlib.core.metadata import write_json
from smartlib.core.versioning import format_version


@dataclass(frozen=True)
class PublishRecord:
    publish_type: str
    subset: str
    version: int
    files: dict[str, str]
    source_workfile: str = ""
    comment: str = ""
    status: str = "published"

    @property
    def version_label(self) -> str:
        return format_version(self.version)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["version"] = self.version_label
        return data


def write_publish_json(version_dir: str | Path, record: PublishRecord) -> Path:
    return write_json(Path(version_dir) / "publish.json", record.to_dict())

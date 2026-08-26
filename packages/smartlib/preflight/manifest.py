from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .models import PreflightReport


class PreflightManifestStore:
    """Persist immutable Preflight attempts and a lightweight latest pointer."""

    def __init__(self, data_root: str | Path):
        self.data_root = Path(data_root)

    def write(self, report: PreflightReport) -> Path:
        attempt = _token(report.attempt_id)
        entity_root = self.data_root / "manifests" / "preflight"
        manifest = entity_root / attempt / "manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        payload = report.to_dict()
        payload["created_at"] = datetime.now().isoformat(timespec="seconds")
        payload["manifest_path"] = manifest.as_posix()
        _write_json(manifest, payload)
        _write_json(
            entity_root / "latest.json",
            {
                "schema": "smart_preflight_latest/v1",
                "attempt_id": report.attempt_id,
                "path": manifest.relative_to(entity_root).as_posix(),
                "blocked": report.blocked,
                "updated_at": payload["created_at"],
            },
        )
        return manifest


def _token(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return clean.strip("._-") or "unknown"


def _write_json(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

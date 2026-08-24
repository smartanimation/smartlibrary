from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from string import Formatter
from typing import Any

from smartlib.core.config_loader import load_config


class _VersionedFormatter(Formatter):
    def format_field(self, value: Any, format_spec: str) -> str:
        if format_spec and format_spec.endswith("d"):
            return super().format_field(int(value), format_spec)
        return super().format_field(value, format_spec)


@dataclass(frozen=True)
class DeliveryProfile:
    id: str
    version: int
    client: str
    root: str
    paths: dict[str, str]
    validation: dict[str, Any]
    archive: dict[str, Any]
    package: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path) -> "DeliveryProfile":
        data = load_config(path)
        profile = data.get("profile") or {}
        return cls(
            id=str(profile.get("id") or ""),
            version=int(profile.get("version") or 1),
            client=str(profile.get("client") or ""),
            root=str(data.get("root") or (data.get("paths") or {}).get("client_shot_root") or "shot"),
            paths={str(key): str(value) for key, value in (data.get("paths") or {}).items()},
            validation=dict(data.get("validation") or {}),
            archive=dict(data.get("archive") or {}),
            package=dict(data.get("package") or {}),
        )

    def render(self, key: str, tokens: dict[str, Any]) -> PurePosixPath:
        if key not in self.paths:
            raise KeyError(f"Delivery path template was not found: {key}")
        rendered = _VersionedFormatter().format(self.paths[key], **tokens).replace("\\", "/")
        path = PurePosixPath(rendered)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Delivery destination must remain package-relative: {rendered}")
        expected_root = self.client_root_for(key)
        if not path.parts or path.parts[0] != expected_root:
            raise ValueError(f"Delivery destination must use the formal '{expected_root}' root: {rendered}")
        return path

    def client_root_for(self, key: str) -> str:
        if key.startswith("asset_") or key == "asset":
            return str(self.paths.get("client_asset_root") or "asset")
        return str(self.paths.get("client_shot_root") or self.root or "shot")
